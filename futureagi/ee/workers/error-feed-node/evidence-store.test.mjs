import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp, readFile, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {randomUUID, createHash} from 'node:crypto';
import {storeEvidence, createEvidenceReader, downloadEvidence, downloadTraceEvidence} from './evidence-store.mjs';

export function makeClaim() {
  return {organization_id: randomUUID(), workspace_id: null, project_id: randomUUID(), trace_id: randomUUID(),
    job_id: randomUUID(), attempt_id: randomUUID(), generation: 1, feature_enabled: true,
    contract_version: 'omega-investigation/v1', read_cutoff: '2026-09-12T12:00:00Z',
    limits: {deadline_seconds: 60, max_model_calls: 12, max_children: 2,
      max_input_tokens_total: 100000, max_output_tokens_total: 8000, max_evidence_bytes: 65536, max_tool_result_bytes: 4096}};
}
test('stream stores exact bytes, indexes every span and issues host-owned citations', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'omega-evidence-test-'));
  try {
    const claim = makeClaim();
    const row = {id: '1234567890abcdef', project_id: claim.project_id, trace_id: claim.trace_id, input: '你好', output: 'complete content'};
    const bytes = Buffer.from(JSON.stringify(row) + '\n');
    const path = join(dir, 'trace.jsonl');
    const store = await storeEvidence([bytes.subarray(0, 25), bytes.subarray(25)], path, claim);
    assert.deepEqual(await readFile(path), bytes);
    assert.equal(store.digest, 'sha256:' + createHash('sha256').update(bytes).digest('hex'));
    const reader = createEvidenceReader(store, {maxResultBytes: 4096, maxTotalBytes: 4096});
    assert.equal(reader.inventory().spans.length, 1);
    const read = await reader.read(row.id, 0, 4096);
    assert.equal(read.text, bytes.subarray(0, -1).toString('utf8'));
    assert.equal(read.more, false);
    assert.equal(reader.receipts()[0].evidence_id, read.evidence_id);
    await assert.rejects(reader.read('../another-project', 0, 10));
    await assert.rejects(reader.read(row.id, 0, 4097));
  } finally { await rm(dir, {recursive: true, force: true}); }
});
test('scope errors, byte/row overflow and incomplete responses cannot become complete evidence', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'omega-evidence-test-'));
  try {
    const claim = makeClaim();
    const row = {id: '1234567890abcdef', project_id: claim.project_id, trace_id: claim.trace_id};
    for (const [name, bytes, options] of [
      ['scope', JSON.stringify({...row, project_id: randomUUID()}) + '\n', {}],
      ['bytes', JSON.stringify(row) + '\n', {maxBytes: 4}],
      ['rows', JSON.stringify(row) + '\n', {maxRows: 0}],
      ['partial', JSON.stringify(row) + '\nCode: 241 memory exceeded', {}],
      ['duplicate', (JSON.stringify(row) + '\n').repeat(2), {}],
    ]) await assert.rejects(storeEvidence([Buffer.from(bytes)], join(dir, name), claim, options));
  } finally { await rm(dir, {recursive: true, force: true}); }
});
test('query binds the claimed tenant and cutoff and never silently truncates payloads', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'omega-evidence-test-'));
  try {
    const claim = makeClaim();
    const row = {id: '1234567890abcdef', project_id: claim.project_id, trace_id: claim.trace_id};
    await downloadEvidence(claim, join(dir, 'trace'), {baseUrl: 'http://clickhouse:8123', database: 'default', username: 'readonly', password: 'fixture',
      fetchImpl: async (url, request) => {
        assert.equal(url.searchParams.get('param_project'), claim.project_id);
        assert.equal(url.searchParams.get('param_org'), claim.organization_id);
        assert.match(request.body, /SELECT \*/);
        assert.match(request.body, /FROM spans FINAL\s+PREWHERE project_id = \{project:UUID\} AND trace_id = \{trace:String\}\s+WHERE \(org_id/);
        // Mutable deletion/version filters must remain after FINAL: otherwise
        // an older live row could reappear when a newer tombstone is filtered.
        const prewhere = request.body.split('PREWHERE')[1].split('WHERE')[0];
        assert.doesNotMatch(prewhere, /is_deleted|updated_at|created_at|org_id/);
        assert.match(request.body, /max_threads=1, max_memory_usage=268435456/);
        assert.match(request.body, /use_skip_indexes_if_final=1/);
        assert.match(request.body, /optimize_move_to_prewhere_if_final=0/);
        assert.match(request.body, /ignore_data_skipping_indices='auto_minmax_index_is_deleted,auto_minmax_index_created_at'/);
        assert.match(request.body, /result_overflow_mode='throw'/);
        assert.doesNotMatch(request.body, /\breadonly\s*=/);
        assert.doesNotMatch(request.body, /substring|summary/i);
        return new Response(JSON.stringify(row) + '\n');
      }});
  } finally { await rm(dir, {recursive: true, force: true}); }
});

test('ClickHouse query memory override stays within a bounded range', async () => {
  const claim = makeClaim();
  for (const maxQueryMemoryBytes of [0, 127 * 1024 * 1024, 1025 * 1024 * 1024, NaN]) {
    await assert.rejects(downloadEvidence(claim, '/tmp/unopened-evidence', {baseUrl: 'http://clickhouse:8123',
      database: 'default', username: 'readonly', password: 'fixture', maxQueryMemoryBytes}),
    /Invalid ClickHouse query memory budget/);
  }
});

const clickhouse = {baseUrl: 'http://clickhouse:8123', database: 'default', username: 'readonly', password: 'fixture'};
const evidenceWindow = {start: '2026-09-11T10:00:00Z', end: '2026-09-13T11:00:00Z'};
const windowPredicate = /AND start_time >= \{window_start:DateTime64\(6\)\} AND start_time < \{window_end:DateTime64\(6\)\}/;

test('a claimed evidence window bounds the read before FINAL and a complete result needs one query', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'omega-evidence-test-'));
  try {
    const claim = {...makeClaim(), evidence_window: evidenceWindow};
    const root = {id: 'root', project_id: claim.project_id, trace_id: claim.trace_id, parent_span_id: ''};
    const child = {id: 'child', project_id: claim.project_id, trace_id: claim.trace_id, parent_span_id: 'root'};
    const requests = [];
    const store = await downloadEvidence(claim, join(dir, 'trace'), {...clickhouse,
      fetchImpl: async (url, request) => {
        requests.push(request.body);
        assert.equal(url.searchParams.get('param_window_start'), '2026-09-11 10:00:00.000');
        assert.equal(url.searchParams.get('param_window_end'), '2026-09-13 11:00:00.000');
        return new Response(JSON.stringify(root) + '\n' + JSON.stringify(child) + '\n');
      }});
    assert.equal(requests.length, 1);
    assert.match(requests[0].split('PREWHERE')[1].split('WHERE')[0], windowPredicate);
    assert.equal(store.coverage.observed_span_count, 2);
  } finally { await rm(dir, {recursive: true, force: true}); }
});

test('a windowed read that misses a parent or every row runs again without the window', async () => {
  for (const windowed of [[{id: 'child', parent_span_id: 'root'}], []]) {
    const dir = await mkdtemp(join(tmpdir(), 'omega-evidence-test-'));
    try {
      const claim = {...makeClaim(), evidence_window: evidenceWindow};
      const scope = {project_id: claim.project_id, trace_id: claim.trace_id};
      const full = [{id: 'root', parent_span_id: ''}, {id: 'child', parent_span_id: 'root'}];
      const requests = [];
      const store = await downloadEvidence(claim, join(dir, 'trace'), {...clickhouse,
        fetchImpl: async (_url, request) => {
          requests.push(request.body);
          const rows = windowPredicate.test(request.body) ? windowed : full;
          return new Response(rows.map(row => JSON.stringify({...row, ...scope}) + '\n').join(''));
        }});
      assert.equal(requests.length, 2);
      assert.match(requests[0], windowPredicate);
      assert.doesNotMatch(requests[1], /window_start|window_end/);
      // The second read replaces the first on disk; nothing from the cut read survives.
      assert.deepEqual([...store.index.keys()], ['root', 'child']);
      assert.equal((await readFile(join(dir, 'trace'), 'utf8')).trim().split('\n').length, 2);
    } finally { await rm(dir, {recursive: true, force: true}); }
  }
});

test('an evidence window must be a whole-hour range', async () => {
  for (const evidence_window of [{start: '2026-09-11T10:30:00Z', end: '2026-09-13T11:00:00Z'},
    {start: '2026-09-13T11:00:00Z', end: '2026-09-11T10:00:00Z'}, {start: 'yesterday', end: 'today'}, {}]) {
    await assert.rejects(downloadEvidence({...makeClaim(), evidence_window}, '/tmp/unopened-evidence',
      {...clickhouse, fetchImpl: async () => assert.fail('an invalid window must not reach ClickHouse')}),
    /Invalid evidence window/);
  }
});

test('a rejected read keeps the ClickHouse exception code and none of the response text', async () => {
  for (const [headers, code] of [[{'X-ClickHouse-Exception-Code': '241'}, 241], [{}, null],
    [{'X-ClickHouse-Exception-Code': 'SELECT secret'}, null]]) {
    await assert.rejects(downloadEvidence(makeClaim(), '/tmp/unopened-evidence', {...clickhouse,
      fetchImpl: async () => new Response('Code: 241. DB::Exception: Memory limit exceeded: SELECT secret',
        {status: 500, headers})}),
    error => {
      assert.equal(error.message, 'ClickHouse evidence read failed');
      assert.equal(error.clickhouseCode, code);
      assert.ok(!JSON.stringify({...error, message: error.message}).includes('secret'));
      return true;
    });
  }
});

test('a rejected read keeps its ClickHouse code when the response body is already broken', async () => {
  const broken = reason => new ReadableStream({start(controller) { controller.error(reason); }});
  const headers = {'X-ClickHouse-Exception-Code': '241'};
  await assert.rejects(downloadEvidence(makeClaim(), '/tmp/unopened-evidence', {...clickhouse,
    fetchImpl: async () => new Response(broken(new TypeError('terminated')), {status: 500, headers})}),
  error => {
    assert.equal(error.message, 'ClickHouse evidence read failed');
    assert.equal(error.clickhouseCode, 241);
    return true;
  });
  // A cancelled or timed-out read still keeps its own error.
  const stop = AbortSignal.abort();
  await assert.rejects(downloadEvidence(makeClaim(), '/tmp/unopened-evidence', {...clickhouse, signal: stop,
    fetchImpl: async () => new Response(broken(stop.reason), {status: 500, headers})}), error => error === stop.reason);
});

test('an error ClickHouse raises after the first block is a failed read with its code', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'omega-evidence-test-'));
  try {
    const claim = makeClaim();
    const row = {id: 'root', project_id: claim.project_id, trace_id: claim.trace_id, parent_span_id: ''};
    for (const [exception, code] of [['Code: 241. DB::Exception: Memory limit exceeded: SELECT secret', 241],
      ['SELECT secret', null]]) {
      await assert.rejects(downloadEvidence(claim, join(dir, String(code)), {...clickhouse,
        fetchImpl: async () => new Response(JSON.stringify(row) + '\n' + JSON.stringify({exception}) + '\n')}),
      error => {
        assert.equal(error.message, 'ClickHouse evidence read failed');
        assert.equal(error.clickhouseCode, code);
        assert.ok(!JSON.stringify({...error, message: error.message}).includes('secret'));
        return true;
      });
    }
  } finally { await rm(dir, {recursive: true, force: true}); }
});

test('a connection that fails before or during the response is a failed read', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'omega-evidence-test-'));
  try {
    const claim = makeClaim();
    const row = JSON.stringify({id: 'root', project_id: claim.project_id, trace_id: claim.trace_id}) + '\n';
    const cut = new ReadableStream({start(controller) {
      controller.enqueue(Buffer.from(row));
      controller.error(new TypeError('terminated'));
    }});
    for (const [name, fetchImpl] of [['refused', async () => { throw new TypeError('fetch failed'); }],
      ['cut', async () => new Response(cut)]]) {
      await assert.rejects(downloadEvidence(claim, join(dir, name), {...clickhouse, fetchImpl}),
        error => error.message === 'ClickHouse evidence read failed' && error.clickhouseCode === null);
    }
    // A cancelled or timed-out read keeps its own error, so the attempt is not blamed on ClickHouse.
    const stop = AbortSignal.abort();
    await assert.rejects(downloadEvidence(claim, join(dir, 'aborted'), {...clickhouse, signal: stop,
      fetchImpl: async () => { throw stop.reason; }}), error => error === stop.reason);
  } finally { await rm(dir, {recursive: true, force: true}); }
});

test('a claim that names conversation evidence reads the compact row from the backend', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'omega-evidence-test-'));
  try {
    const claim = {...makeClaim(), lease_token: 'fixture-lease', evidence_source: 'conversation'};
    const row = {id: 'call-span', project_id: claim.project_id, trace_id: claim.trace_id, parent_span_id: '',
      name: 'Call Log', conversation: {turns: [{i: 0, role: 'agent', text: 'Hello', spoken: true}]}};
    const calls = [];
    const store = await downloadTraceEvidence(claim, join(dir, 'trace'), {...clickhouse,
      control: async (path, body) => { calls.push([path, body]); return {rows: [row]}; },
      fetchImpl: async () => assert.fail('the stored span must not be read')});
    assert.deepEqual(calls, [[`/attempts/${claim.attempt_id}/conversation-evidence/`, {lease_token: 'fixture-lease'}]]);
    assert.deepEqual([...store.index.keys()], ['call-span']);
    assert.equal(await readFile(join(dir, 'trace'), 'utf8'), JSON.stringify(row) + '\n');
    // A served row is held to the claim's scope like any stored span.
    await assert.rejects(downloadTraceEvidence(claim, join(dir, 'foreign'), {...clickhouse,
      control: async () => ({rows: [{...row, project_id: randomUUID()}]}),
      fetchImpl: async () => assert.fail('the stored span must not be read')}), /Evidence scope mismatch/);
  } finally { await rm(dir, {recursive: true, force: true}); }
});

test('conversation evidence falls back to the stored spans when the backend has none', async () => {
  const stored = claim => new Response(JSON.stringify(
    {id: 'stored-span', project_id: claim.project_id, trace_id: claim.trace_id, parent_span_id: ''}) + '\n');
  for (const [name, evidenceSource, control, asked] of [
    ['no rows', 'conversation', async () => ({rows: []}), 1],
    ['a backend without the route', 'conversation', async () => { throw Object.assign(new Error('Control request failed'), {status: 404}); }, 1],
    ['a backend that is unavailable', 'conversation', async () => { throw Object.assign(new Error('Control request failed'), {status: 503}); }, 1],
    ['a claim that does not name it', undefined, async () => assert.fail('the backend must not be asked'), 0]]) {
    const dir = await mkdtemp(join(tmpdir(), 'omega-evidence-test-'));
    try {
      const claim = {...makeClaim(), lease_token: 'fixture-lease', evidence_source: evidenceSource};
      let controlCalls = 0, reads = 0;
      const store = await downloadTraceEvidence(claim, join(dir, 'trace'), {...clickhouse,
        control: async (...args) => { controlCalls++; return control(...args); },
        fetchImpl: async () => { reads++; return stored(claim); }});
      assert.deepEqual([controlCalls, reads, [...store.index.keys()]], [asked, 1, ['stored-span']], name);
    } finally { await rm(dir, {recursive: true, force: true}); }
  }
});

test('bounded tool pages reconstruct escaped and multibyte payloads without dropped content', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'omega-evidence-test-'));
  try {
    const claim = makeClaim();
    const row = {id: '1234567890abcdef', project_id: claim.project_id, trace_id: claim.trace_id,
      input: ('你好\\"line\n').repeat(1000)};
    const raw = JSON.stringify(row);
    const store = await storeEvidence([Buffer.from(raw + '\n')], join(dir, 'trace'), claim);
    const reader = createEvidenceReader(store, {maxResultBytes: 1024, maxTotalBytes: 65536});
    assert.equal(reader.allSpansRead(), false);
    assert.deepEqual(reader.unreadSpanIds(), [row.id]);
    let offset = 0, reconstructed = '';
    for (;;) {
      const part = await reader.read(row.id, offset, 1024);
      assert.ok(Buffer.byteLength(JSON.stringify(part)) <= 1024);
      assert.ok(part.next_offset > offset);
      reconstructed += part.text;
      if (!part.more) break;
      offset = part.next_offset;
    }
    assert.equal(reconstructed, raw);
    assert.equal(reader.allSpansRead(), true);
    assert.deepEqual(reader.unreadSpanIds(), []);
  } finally { await rm(dir, {recursive: true, force: true}); }
});

test('absent, null and empty external payload URLs preserve complete coverage', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'omega-evidence-test-'));
  try {
    const claim = makeClaim();
    const rows = [
      {id: 'span-absent', project_id: claim.project_id, trace_id: claim.trace_id},
      {id: 'span-null', project_id: claim.project_id, trace_id: claim.trace_id,
        input_gcs_url: null, output_gcs_url: null},
      {id: 'span-empty', project_id: claim.project_id, trace_id: claim.trace_id,
        input_gcs_url: '', output_gcs_url: '   '},
    ];
    const store = await storeEvidence(
      [Buffer.from(rows.map(row => JSON.stringify(row)).join('\n') + '\n')],
      join(dir, 'trace'), claim);
    const reader = createEvidenceReader(store, {maxResultBytes: 4096, maxTotalBytes: 4096});

    assert.equal(store.coverage.read_complete, true);
    assert.equal(reader.inventory().spans.every(row => !('unresolved_external_payloads' in row)), true);
  } finally { await rm(dir, {recursive: true, force: true}); }
});

test('external payload references are visible as unresolved without fetching URLs', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'omega-evidence-test-'));
  const originalFetch = globalThis.fetch;
  const networkCalls = [];
  globalThis.fetch = async url => { networkCalls.push(String(url)); throw new Error('Unexpected network call'); };
  try {
    const claim = makeClaim();
    const row = {id: 'span-external', project_id: claim.project_id, trace_id: claim.trace_id,
      input: 'inline request', output: 'inline response',
      input_gcs_url: 'https://untrusted.invalid/input', output_gcs_url: 'gs://tenant-bucket/output'};
    const store = await storeEvidence(
      [Buffer.from(JSON.stringify(row) + '\n')], join(dir, 'trace'), claim);
    const reader = createEvidenceReader(store, {maxResultBytes: 4096, maxTotalBytes: 4096});

    assert.equal(store.coverage.read_complete, false);
    assert.deepEqual(reader.inventory().spans[0].unresolved_external_payloads,
      ['input_gcs_url', 'output_gcs_url']);
    assert.deepEqual(networkCalls, []);
  } finally {
    globalThis.fetch = originalFetch;
    await rm(dir, {recursive: true, force: true});
  }
});
