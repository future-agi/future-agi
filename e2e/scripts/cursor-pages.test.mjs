import assert from 'node:assert/strict';
import test from 'node:test';
import { CursorPageAccumulator } from '../lib/cursor-pages.ts';

const rows = Array.from({ length: 13 }, (_, i) => ({ id: `trace-${i}` }));
const root = { page_size: 10, page_number: 0, cursor_mode: true, filters: 'tokens > 20' };
const accumulator = (options) => new CursorPageAccumulator(10, row => row.id, options);
const chunk = (table, total, cursor) => ({ table, metadata: {
  total_rows: total, total_rows_is_lower_bound: cursor !== undefined,
  has_more: cursor !== undefined, next_cursor: cursor ?? null,
  // A proven chunk is complete even when it does not fill a page.
  query_complete: true,
} });

function walk(sizes) {
  const list = accumulator();
  const pages = [];
  let offset = 0;
  for (const [i, size] of sizes.entries()) {
    assert.deepEqual(list.nextQuery(root), i === 0 ? root : {
      page_size: 10, cursor_mode: true, filters: root.filters, cursor: `checkpoint-${i}`,
    });
    list.append(chunk(rows.slice(offset, offset + size), offset + size,
      i === sizes.length - 1 ? undefined : `checkpoint-${i + 1}`));
    offset += size;
    while (list.pageReady && list.bufferedCount) pages.push(list.takePage());
  }
  list.assertTerminal();
  assert.deepEqual(list.allRows, rows);
  assert.equal(list.metadata.total_rows, 13);
  assert.equal(list.metadata.has_more, false);
  return pages;
}

for (const sizes of [[10, 3], [5, 5, 3], [5, 0, 5, 3], [7, 6]]) {
  test(`logical pages are invariant under ${sizes.join('+')} transport chunks`, () => {
    assert.deepEqual(walk(sizes), [rows.slice(0, 10), rows.slice(10)]);
  });
}

test('a chunk larger than page_size is rejected, so an ignored page_size fails', () => {
  assert.throws(() => accumulator().append(chunk(rows, 13)), /exceeds the requested page_size 10/);
  const list = accumulator();
  list.append(chunk(rows.slice(0, 2), 2, 'a'));
  assert.throws(() => list.append(chunk(rows.slice(2, 13), 13)), /11 rows exceeds/);
});

test('a nonterminal lower bound must track the rows seen so far', () => {
  for (const total of [0, 9, 12, 999]) {
    assert.throws(() => accumulator().append(chunk(rows.slice(0, 10), total, 'a')),
      new RegExp(`lower bound ${total} must be 10 seen rows or 11`));
  }
  // Exactly the seen rows, or one more when an unseen match is proven.
  for (const total of [10, 11]) accumulator().append(chunk(rows.slice(0, 10), total, 'a'));
  const empty = accumulator();
  empty.append(chunk(rows.slice(0, 4), 4, 'a'));
  empty.append(chunk([], 4, 'b'));
  assert.throws(() => empty.append(chunk([], 3, 'c')), /lower bound 3 must be 4 seen rows or 5/);
});

test('overflow survives a nonterminal page boundary', () => {
  const list = accumulator();
  list.append(chunk(rows.slice(0, 7), 7, 'a'));
  assert.throws(() => list.takePage(), /not filled/);
  list.append(chunk(rows.slice(7, 12), 12, 'b'));
  assert.deepEqual(list.takePage(), rows.slice(0, 10));
  assert.equal(list.bufferedCount, 2);
  assert.equal(list.terminal, false);
  list.append(chunk(rows.slice(12), 13));
  assert.deepEqual(list.takePage(), rows.slice(10));
  list.assertTerminal();
});

test('duplicate IDs are rejected within and across chunks', () => {
  assert.throws(() => accumulator().append(chunk([rows[0], rows[0]], 2, 'a')), /Duplicate/);
  const list = accumulator();
  list.append(chunk(rows.slice(0, 5), 5, 'a'));
  assert.throws(() => list.append(chunk(rows.slice(4, 10), 11, 'b')), /Duplicate/);
});

for (const cursors of [['a', 'a'], ['a', 'b', 'a']]) {
  test(`rejects stalled/cycling checkpoint ${cursors.join(' -> ')}`, () => {
    const list = accumulator();
    for (const cursor of cursors.slice(0, -1)) list.append(chunk([], 0, cursor));
    assert.throws(() => list.append(chunk([], 0, cursors.at(-1))), /Stalled or cycling/);
  });
}

test('an advancing empty chain cannot pass the finite hop bound as terminal', () => {
  const list = accumulator();
  for (let hop = 0; hop < 8; hop++) list.append(chunk([], 0, `checkpoint-${hop}`));
  assert.throws(() => list.nextQuery(root), /terminal response within 8 transport hops/);
  assert.throws(() => list.append(chunk(rows, 13)), /terminal response within 8 transport hops/);
  assert.throws(() => list.assertTerminal(), /without a terminal response/);
});

test('a complete but nonterminal chunk cannot satisfy completion', () => {
  const list = accumulator();
  list.append(chunk(rows.slice(0, 10), 10, 'a'));
  assert.deepEqual(list.takePage(), rows.slice(0, 10));
  assert.throws(() => list.assertTerminal(), /without a terminal response/);
});

test('empty terminal response is not evidence of the expected cohort', () => {
  const list = accumulator();
  list.append(chunk([], 0));
  list.assertTerminal();
  assert.throws(() => assert.deepEqual(list.allRows, rows), assert.AssertionError);
});

test('rejects missing continuations and inaccurate terminal totals', () => {
  const missing = chunk(rows.slice(0, 5), 5, 'a');
  delete missing.metadata.next_cursor;
  assert.throws(() => accumulator().append(missing), /requires a continuation cursor/);
  assert.throws(() => accumulator().append(chunk(rows.slice(0, 3), 2)), /exact accumulated total/);
  const lowerBound = chunk(rows.slice(0, 3), 3);
  lowerBound.metadata.total_rows_is_lower_bound = true;
  assert.throws(() => accumulator().append(lowerBound), /exact accumulated total/);
});

test('numbered usage sorts require an explicit opt-in and an exact complete total', () => {
  const first = { table: rows.slice(0, 10), metadata: { total_rows: 13 } };
  assert.throws(() => accumulator().append(first), /explicit has_more/);
  const list = accumulator({ allowNumbered: true });
  list.append(first);
  assert.deepEqual(list.takePage(), rows.slice(0, 10));
  assert.deepEqual(list.nextQuery({ page_size: 10, page_number: 0 }), { page_size: 10, page_number: 1 });
  list.append({ table: rows.slice(10), metadata: { total_rows: 13 } });
  assert.deepEqual(list.takePage(), rows.slice(10));
  list.assertTerminal();
});

test('numbered fallback rejects a short nonterminal page', () => {
  assert.throws(() => accumulator({ allowNumbered: true }).append({
    table: rows.slice(0, 5), metadata: { total_rows: 13 },
  }), /does not advance/);
});
