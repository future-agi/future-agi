import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { ownerApiCalls, assertNoTypeSafeKey } from './typesafe-live-spec-guard.mjs';

const spec = readFileSync(new URL('../harness/typesafe-live.spec.ts', import.meta.url), 'utf8');
const shapes = [
  "owner.api.get<{ result?: unknown; results?: unknown }>('/providers/');",
  "owner.api.post('/providers/', { credentials: { api_key: 'placeholder' } });",
  "owner.api.post<{ result: { id: string; key: string; name: string } }>('/keys/', { name: 'main' });",
  "owner.api.get<{ results: Array<Record<string, unknown>> }>('/logs/', { request_id: id });",
  "owner.api.post<{ result: { key: string } }>('/keys/', { name: 'narrow', allowed_providers: ['openai'] });",
  "owner.api.post('/configs/', { ...cfg, change_description: `test-${suffix}` });",
  "owner.api.post<{ result: { key: string } }>('/keys/', { name: `rpm-${suffix}` });",
  "owner.api.post<{ result: { key: string; name: string } }>('/keys/', { name: `budget-${suffix}` });",
  "tryApi(() => owner.api.post(new URL(providerCfg.saveUrl).pathname, { config: { api_format: 'systemone' } }));",
  "tryApi(() => owner.api.post('/providers/', { credentials: { api_key: '' } }));",
  "tryApi(() => owner.api.patch(`/providers/${providerCfg.id as string}/`, { models_list: models }));",
];
for (const [index, source] of shapes.entries()) {
  test(`existing call shape ${index + 1}: counts safe call and rejects a secret argument`, () => {
    assert.equal(ownerApiCalls(source).length, 1);
    assert.doesNotThrow(() => assertNoTypeSafeKey(source));
    // Perturb only the request argument, not the guard or its expected result.
    const changed = source.replace(/'\/providers\/'|'\/keys\/'|'\/logs\/'|'\/configs\/'|new URL\(providerCfg.saveUrl\).pathname|`\/providers\/\$\{providerCfg.id as string\}\/`/, 'TYPESAFE_KEY');
    assert.notEqual(changed, source);
    assert.throws(() => assertNoTypeSafeKey(changed), /Playwright request carries/);
  });
}

for (const method of ['get', 'post', 'put', 'patch', 'delete']) {
  test(`${method}: nested multiline generic arguments are not a guard bypass`, () => {
    const source = `owner.api.${method}<\n  { result: Array<Record<string, Promise<{ key: string }>>> }\n>('/route/', { value: 'placeholder' });`;
    assert.equal(ownerApiCalls(source).length, 1);
    assert.doesNotThrow(() => assertNoTypeSafeKey(source));
    assert.throws(() => assertNoTypeSafeKey(source.replace("'placeholder'", 'TYPESAFE_KEY')), /Playwright request carries/);
  });
}

test('comments, strings, other receivers and argument boundaries do not invent calls', () => {
  const source = `// owner.api.post('/ignored', TYPESAFE_KEY);
    const comment = "owner.api.get<T>(TYPESAFE_KEY);";
    other.api.post('/ignored', TYPESAFE_KEY);
    owner.api.post<{ result: string }>('/safe', { message: ');' });
    http('/node', TYPESAFE_KEY);`;
  assert.equal(ownerApiCalls(source).length, 1);
  assert.doesNotThrow(() => assertNoTypeSafeKey(source));
});

test('all 10 actual call sites are counted, and each rejects an injected secret', () => {
  const calls = ownerApiCalls(spec);
  assert.equal(calls.length, 10);
  assert.deepEqual(calls.map(c => c.method), ['get', 'post', 'post', 'get', 'post', 'post', 'post', 'post', 'post', 'patch']);
  assert.doesNotThrow(() => assertNoTypeSafeKey(spec));
  for (const call of calls) {
    const original = spec.slice(call.start, call.end);
    // Calls end in ')', regardless of wrapping tryApi/await or semicolons.
    const poisoned = original.slice(0, -1) + ', TYPESAFE_KEY)';
    assert.throws(() => assertNoTypeSafeKey(poisoned), /Playwright request carries/, original);
  }
});

test('element access and optional-call syntax remain guarded', () => {
  for (const source of ["owner.api['delete']<unknown>('/p', TYPESAFE_KEY)", "owner.api.post?.<unknown>('/p', TYPESAFE_KEY)"]) {
    assert.equal(ownerApiCalls(source).length, 1);
    assert.throws(() => assertNoTypeSafeKey(source), /Playwright request carries/);
  }
});
