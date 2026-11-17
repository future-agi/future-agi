// Synthetic unit controls; execute the actual helper, never a reimplementation.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { isNativeError } from 'node:util/types';
import { readFileSync } from 'node:fs';
import { stripTypeScriptTypes } from 'node:module';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

const filename = fileURLToPath(new URL('../lib/walk/guardrails.ts', import.meta.url));
const source = stripTypeScriptTypes(readFileSync(filename, 'utf8'), { mode: 'strip' })
  .replace(/^import .*;\r?$/gm, '').replace(/^export /gm, '');
const context = { createHash, isNativeError, Error, expect() { throw new Error('unexpected matcher call'); }, POLL: {} };
vm.runInNewContext(source + '\nglobalThis.checked = checked;', context, { filename });
const { checked } = context;
const feature = 'synthetic checked step';
const marker = '; assertion=';
const canary = 'SYNTHETIC_PRIVATE_TEXT_DO_NOT_EMIT';
const origin = { file: 'e2e/flows/synthetic.spec.ts', line: 41, column: 7 };
const stack = `Error: ${canary}\n    at asserted (/private/synthetic/${origin.file}:41:7)\n    at checked (/private/synthetic/e2e/lib/walk/guardrails.ts:18:25)`;
function assertion(name, expected, actual) {
  return Object.assign(new Error(canary), {
    stack, matcherResult: { name, expected, actual, message: () => canary },
  });
}
async function fail(error, receiptOverride) {
  const rows = [];
  let calls = 0, cleaned = 0, thrown;
  try {
    await checked(receiptOverride ?? { verdict: (row) => rows.push(row) }, feature, 'api', async () => {
      calls++; throw error;
    });
  } catch (caught) { thrown = caught; }
  finally { cleaned++; }
  assert.equal(calls, 1);
  assert.equal(cleaned, 1);
  assert.ok(thrown);
  assert.ok(thrown.message.startsWith(`W18 assertion failed: ${feature}`));
  assert.equal(thrown.cause, undefined);
  if (!receiptOverride) {
    assert.equal(rows.length, 1);
    assert.equal(rows[0].verdict, 'FAIL');
    assert.equal(rows[0].feature, feature);
    assert.match(rows[0].detail.observed, /^sha256:[a-f0-9]{64}$/);
  }
  assert.ok(thrown.message.includes(marker), 'safe originating assertion representation is required');
  const detail = JSON.parse(thrown.message.split(marker)[1]);
  assert.ok(!JSON.stringify({ message: thrown.message, rows }).includes(canary));
  return { detail, rows, thrown };
}

test('CK01 ordinary pass retains value and fingerprint exactly once', async () => {
  const value = { observed: true }, rows = [];
  let calls = 0;
  assert.equal(await checked({ verdict: (row) => rows.push(row) }, feature, 'api', async () => {
    calls++; return value;
  }), value);
  assert.equal(calls, 1);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].verdict, 'PASS');
  assert.equal(rows[0].detail.observed, 'sha256:' + createHash('sha256').update(JSON.stringify(value)).digest('hex'));
});
for (const [id, value] of [['CK02', false], ['CK03', undefined]]) {
  test(`${id} ${String(value)} evidence remains FAIL without retry`, async () => {
    const rows = [];
    let calls = 0;
    await assert.rejects(checked({ verdict: (row) => rows.push(row) }, feature, 'api', async () => {
      calls++; return value;
    }), /W18 assertion failed: synthetic checked step/);
    assert.equal(calls, 1);
    assert.equal(rows.length, 1);
    assert.equal(rows[0].verdict, 'FAIL');
  });
}
test('CK04 count matcher retains bounded count evidence', async () => {
  const { detail, rows } = await fail(assertion('toHaveCount', 2, 1));
  assert.equal(detail.matcher, 'toHaveCount');
  assert.equal(detail.message, 'matcher assertion failed; original text withheld');
  assert.equal(detail.expected, 'count:2');
  assert.equal(detail.actual, 'count:1');
  assert.equal(rows[0].detail.observed, 'sha256:' + createHash('sha256').update(JSON.stringify(`Error: ${canary}`)).digest('hex'));
});
test('CK05 length matcher retains array length without array values', async () => {
  const { detail } = await fail(assertion('toHaveLength', 2, [canary]));
  assert.equal(detail.expected, 'count:2');
  assert.equal(detail.actual, 'count:1');
});
test('CK06 length matcher retains string length without string values', async () => {
  const { detail } = await fail(assertion('toHaveLength', 0, canary));
  assert.equal(detail.actual, `count:${canary.length}`);
});
test('CK07 sensitive strings and custom assertion message are withheld', async () => {
  const error = assertion('toEqual', canary, `https://synthetic.invalid/${canary}`);
  error.matcherResult.message = canary;
  const { detail } = await fail(error);
  assert.equal(detail.matcher, 'toEqual');
  assert.equal(detail.expected, 'type:string; value withheld');
  assert.equal(detail.actual, 'type:string; value withheld');
  assert.match(detail.message, /original text withheld/);
});
test('CK08 originating relative location survives separately from wrapper', async () => {
  const { detail, rows, thrown } = await fail(assertion('toBe', true, false));
  assert.deepEqual(detail.origin, origin);
  assert.deepEqual(JSON.parse(JSON.stringify(rows[0].detail.locations[0])), origin);
  assert.match(thrown.stack, /guardrails\.ts/);
  assert.ok(!thrown.message.includes('/private/'));
});
test('CK09 absent metadata has explicit unavailable fields', async () => {
  const { detail } = await fail(new Error(canary));
  assert.match(detail.matcher, /unavailable/);
  assert.match(detail.message, /unavailable/);
  assert.match(detail.expected, /unavailable/);
  assert.match(detail.actual, /unavailable/);
});
test('CK10 diagnostic getters are not called', async () => {
  let reads = 0;
  const error = {};
  for (const key of ['stack', 'message', 'name', 'matcherResult', 'cause']) {
    Object.defineProperty(error, key, { get() { reads++; throw new Error(canary); } });
  }
  const { detail } = await fail(error);
  assert.equal(reads, 0);
  assert.match(detail.origin, /unavailable/);
});
test('CK11 throwing descriptor trap does not replace intended FAIL', async () => {
  const error = new Proxy({}, { get() { throw new Error(canary); }, getOwnPropertyDescriptor() { throw new Error(canary); } });
  const { detail } = await fail(error);
  assert.match(detail.matcher, /unavailable/);
});
test('CK12 arbitrary toString and serialization hooks are never called', async () => {
  let calls = 0;
  const error = { toString() { calls++; throw new Error(canary); }, toJSON() { calls++; throw new Error(canary); } };
  const { detail } = await fail(error);
  assert.equal(calls, 0);
  assert.match(detail.expected, /unavailable/);
});
test('CK13 unknown matcher and arbitrary numbers are not echoed', async () => {
  const { detail, thrown } = await fail(assertion(canary, 928374, 837492));
  assert.match(detail.matcher, /unavailable/);
  assert.equal(detail.expected, 'type:number; value withheld');
  assert.equal(detail.actual, 'type:number; value withheld');
  assert.ok(!thrown.message.includes('928374'));
  assert.ok(!thrown.message.includes('837492'));
});
test('CK14 invalid and oversized counts are withheld', async () => {
  for (const count of [-1, 1.5, Infinity, NaN, 1000001, canary]) {
    const { detail } = await fail(assertion('toHaveCount', count, count));
    assert.ok(!detail.expected.startsWith('count:'));
    assert.ok(!detail.actual.startsWith('count:'));
    assert.match(detail.expected, /withheld/);
  }
});
test('CK15 locations are bounded and reject traversal or noncode paths', async () => {
  const error = assertion('toBe', true, false);
  error.stack = `Error: ${canary}\n    at /private/synthetic/e2e/flows/../../outside.ts:1:2\n    at /private/synthetic/other.ts:1:2\n    at /private/synthetic/e2e/flows/bad.ts:0:2\n    at /private/synthetic/e2e/flows/bad.ts:9999999:2\n` +
    Array.from({ length: 20 }, (_, i) => `    at /private/synthetic/e2e/flows/synthetic.spec.ts:${i + 1}:2`).join('\n');
  const { detail, rows, thrown } = await fail(error);
  assert.deepEqual(detail.origin, { file: origin.file, line: 1, column: 2 });
  assert.equal(rows[0].detail.locations.length, 6);
  assert.ok(!thrown.message.includes('outside'));
  assert.ok(!thrown.message.includes('bad.ts'));
});
test('CK16 receipt diagnostic failure preserves intended FAIL and cleanup', async () => {
  const { detail } = await fail(assertion('toHaveCount', 2, 1), { verdict() { throw new Error(canary); } });
  assert.equal(detail.matcher, 'toHaveCount');
});
test('CK17 expected actual and cause objects are never traversed', async () => {
  let reads = 0;
  const value = { toJSON() { reads++; throw new Error(canary); } };
  Object.defineProperty(value, 'nested', { get() { reads++; throw new Error(canary); } });
  const error = assertion('toEqual', value, value);
  Object.defineProperty(error, 'cause', { get() { reads++; throw new Error(canary); } });
  const { detail } = await fail(error);
  assert.equal(reads, 0);
  assert.equal(detail.expected, 'type:object; value withheld');
  assert.equal(detail.actual, 'type:object; value withheld');
});
test('CK18 booleans null and explicit undefined retain safe presence', async () => {
  for (const [value, text] of [[true, 'boolean:true'], [false, 'boolean:false'], [null, 'null'], [undefined, 'undefined']]) {
    const { detail } = await fail(assertion('toBe', value, value));
    assert.equal(detail.expected, text);
    assert.equal(detail.actual, text);
  }
});
test('CK19 negation is retained only when structured metadata supplies it', async () => {
  const error = assertion('toBe', true, true);
  error.matcherResult.isNot = true;
  assert.equal((await fail(error)).detail.negated, true);
  assert.match((await fail(assertion('toBe', true, false))).detail.negated, /unavailable/);
});
test('CK20 throwing undefined retains FAIL with unavailable origin', async () => {
  const { detail } = await fail(undefined);
  assert.match(detail.origin, /unavailable/);
  assert.match(detail.expected, /unavailable/);
});
test('CK22 native stack fallback does not invoke an error name getter', async () => {
  let reads = 0;
  const error = assertion('toBe', true, false);
  Object.defineProperty(error, 'name', { get() { reads++; throw new Error(canary); } });
  await fail(error);
  assert.equal(reads, 0);
});
test('CK23 absent real-style length fields stay unavailable rather than parsed from message text', async () => {
  const error = assertion('toHaveLength', undefined, undefined);
  delete error.matcherResult.expected; delete error.matcherResult.actual;
  error.message = `Expected length: 2\nReceived length: 1\nReceived array: ${canary}`;
  const { detail } = await fail(error);
  assert.match(detail.expected, /unavailable/);
  assert.match(detail.actual, /unavailable/);
});
test('CK21 safe origin and comparison survive the existing short-line extraction contract', async () => {
  const { thrown } = await fail(assertion('toHaveCount', 2, 1));
  const lines = thrown.message.split('\n');
  const originParts = lines.filter(value => /^Error: origin part\d+: /.test(value)).map(value => value.replace(/^Error: origin part\d+: /, ''));
  assert.equal(originParts.join(''), origin.file);
  assert.ok(originParts.every(part => part.length <= 24));
  for (const line of lines.filter(value => /^Error: origin part/.test(value))) assert.doesNotMatch(line, /[A-Za-z0-9+/_=-]{28,}/);
  for (const expected of ['Error: assertion toHaveCount;', 'Expected: count:2', 'Received: count:1', 'Error: origin line=41 column=7']) {
    const line = lines.find(value => value.startsWith(expected));
    assert.ok(line, `missing retained line ${expected}`);
    assert.ok(line.length <= 240, 'existing extractor must not truncate this evidence');
    assert.doesNotMatch(line, /password|secret|token|api[_-]?key|credential|bearer|authorization|private|cookie|session/i);
  }
});
