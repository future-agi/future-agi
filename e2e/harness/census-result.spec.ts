import { readFileSync, writeFileSync } from 'node:fs';
import { test, expect } from '@playwright/test';
import { censusDigest, censusResultPath, publishCensusResult, readCensusResult, verifyCensusResult,
  CensusResultError, type CensusBinding, type CensusData, type CensusResult, type CensusReason } from '../lib/walk/census-result';
import { CAT2_IDS, CAT2_PRICE_FIELDS, observeCat2, CENSUS_PHASES, CENSUS_ROLES, CENSUS_PAGES, CENSUS_VIEWPORTS,
  CENSUS_SCHEMES, CENSUS_BACKEND_CHECKS, expectedFirstConfigVersions } from '../lib/walk/census-contract';
import { scopeAttachment } from '../lib/scope-actors';
import type { VerdictRow } from '../lib/walk/receipt';

const binding: CensusBinding = { run: censusDigest('synthetic-run'), profile: 'stock', browser: 'chromium',
  stack: censusDigest('synthetic-attestation'), newOrgDefault: false };
// The OFF witness: exact notice + absent control + server OFF readback.
const managedOff: VerdictRow = { lane: 'ui', feature: 'managed_models', where: 'synthetic fixture', verdict: 'OFF',
  detail: { kind: 'absent-with-notice', control: 'ABSENT',
    notice: { present: true, text: 'Future AGI credits are off for this organization.' },
    server: { source: '/agentcc/managed-models/', enabled: false } } };
const verdicts: VerdictRow[] = [managedOff,
  ...CENSUS_BACKEND_CHECKS.map(({ lane, feature }) => ({ lane: lane as VerdictRow['lane'], feature, where: 'synthetic fixture', verdict: 'OFF' as const,
    ...(feature === 'CAT-2 exact 34 IDs default-off' ? { detail: { checked: 34, absent: 0, blockedUnpriced: 34 } } : {}) }))];
const blockedModels = CAT2_IDS.map(id => ({ id, state: 'blocked' }));
function data(): CensusData {
  const result: CensusData = { orgs: { A: censusDigest('synthetic-A'), B: censusDigest('synthetic-B') }, verdicts: [],
    credits: CAT2_IDS.map(id => ({ id, verdict: 'refused', status: 403, code: 'permission_denied', upstreamBefore: 0, upstreamAfter: 0 })),
    ui: CENSUS_PHASES.flatMap(phase => CENSUS_ROLES.flatMap(role => CENSUS_PAGES.flatMap(page =>
      CENSUS_VIEWPORTS.flatMap(viewport => CENSUS_SCHEMES.map(scheme => ({ phase, role, page, viewport, scheme,
        verdicts: [page === 'models' ? structuredClone(managedOff) :
          { lane: 'ui' as const, feature: 'page visited', where: page, verdict: 'RECORDED' as const }] })))))),
    backend: CENSUS_PHASES.map(phase => ({ phase, verdicts: verdicts.filter(v => v.lane !== 'ui'), configRows: 0, activeVersions: 0, mirrorNull: true,
      catalogues: { api: 34, gateway: 34, cat2: { api: observeCat2(blockedModels, 'api'), gateway: observeCat2(blockedModels, 'gateway') } }, orgBSnapshot: censusDigest('unchanged') })) };
  result.verdicts = [...result.ui.flatMap(r => r.verdicts), ...result.backend.flatMap(r => r.verdicts),
    ...result.credits.map(row => ({ lane: 'gateway' as const, feature: 'CAT-2 credits refusal', where: row.id, verdict: 'OFF' as const, detail: row }))];
  return result;
}
function reason(fn: () => unknown, expected: CensusReason): void {
  try { fn(); } catch (error) {
    expect(error).toBeInstanceOf(CensusResultError);
    expect((error as CensusResultError).reason).toBe(expected);
    expect((error as Error).message).toBe(`W00_RESULT_${expected}`);
    return;
  }
  throw new Error(`expected W00_RESULT_${expected} refusal`);
}
const resign = (r: CensusResult) => { const { digest: _, ...payload } = r; r.digest = censusDigest(payload); };

test('offline W00 result: complete roundtrip and canonical property order', ({}, info) => {
  const directory = info.outputPath('results');
  const result = publishCensusResult(binding, data(), { fixture: true }, directory);
  expect(readCensusResult(binding, directory, result.orgSet)).toEqual(result);
  expect(result.ui).toHaveLength(224);
  expect(censusDigest({ z: 1, a: { y: 2, x: 3 } })).toBe(censusDigest({ a: { x: 3, y: 2 }, z: 1 }));
  reason(() => publishCensusResult(binding, data(), {}, directory), 'DUPLICATE');
});
test('offline W00 reader accepts absent and mixed blocked-unpriced catalogues', ({}, info) => {
  const fixture = structuredClone(data());
  for (const row of fixture.backend) {
    row.catalogues.cat2.api = observeCat2([], 'api');
    row.catalogues.cat2.gateway = observeCat2(blockedModels.slice(0, 7), 'gateway');
    for (const lane of ['api', 'gateway'] as const) {
      const blocked = lane === 'api' ? 0 : 7;
      const v = row.verdicts.find(v => v.lane === lane && v.feature === 'CAT-2 exact 34 IDs default-off')!;
      v.detail = { checked: 34, absent: 34 - blocked, blockedUnpriced: blocked };
    }
  }
  // Rebuild inventory after changing observations; the publisher must accept
  // actual absence, not accidentally require exactly 34 listed-blocked models.
  fixture.verdicts = [...fixture.ui.flatMap(r => r.verdicts), ...fixture.backend.flatMap(r => r.verdicts),
    ...fixture.verdicts.filter(v => v.feature === 'CAT-2 credits refusal')];
  const result = publishCensusResult(binding, fixture, {}, info.outputPath('mixed'));
  expect(readCensusResult(binding, info.outputPath('mixed'))).toEqual(result);
});
test('offline W00 result: absent is a reason-coded refusal', ({}, info) => {
  reason(() => readCensusResult(binding, info.outputPath('absent')), 'ABSENT');
});

const refusals: [string, CensusReason, (r: CensusResult) => void, boolean?][] = [
  ['tampered digest', 'DIGEST', r => { r.backend[0].configRows = 1; }, false],
  ['wrong profile', 'PROFILE', r => { r.binding.profile = 'r7alt'; }],
  ['wrong browser', 'BROWSER', r => { r.binding.browser = 'firefox'; }],
  ['stale run', 'RUN', r => { r.binding.run = censusDigest('previous-run'); }],
  ['wrong stack', 'STACK', r => { r.binding.stack = censusDigest('other-images'); }],
  ['wrong default', 'DEFAULT', r => { r.binding.newOrgDefault = true; }],
  ['missing role', 'PARTIAL', r => { r.ui = r.ui.filter(row => row.role !== 'admin'); }],
  ['missing phase', 'PARTIAL', r => { r.backend.pop(); }],
  ['missing UI phase', 'PARTIAL', r => { r.ui = r.ui.filter(row => row.phase !== 'after-key'); }],
  ['duplicate cell', 'PARTIAL', r => { r.ui[1] = r.ui[0]; }],
  ['missing backend lane', 'PARTIAL', r => { r.backend[0].verdicts = r.backend[0].verdicts.filter(v => v.lane !== 'redis'); }],
  ['truncated verdict inventory', 'PARTIAL', r => { r.verdicts.shift(); }],
  ['priced CAT-2 entry', 'VERDICT', r => { r.backend[0].catalogues.cat2.api[0].prices.billed_input_per_mtok = 0; }],
  ['available CAT-2 state', 'VERDICT', r => { r.backend[1].catalogues.cat2.gateway[0].state = 'available'; }],
  ['credits request served', 'VERDICT', r => { r.credits[0].status = 200; }],
  ['credits reaches upstream', 'VERDICT', r => { r.credits[0].upstreamAfter = 1; }],
  ['credits wrong refusal', 'VERDICT', r => { r.credits[0].code = 'invalid_api_key'; }],
  ['missing credits ID', 'PARTIAL', r => { r.credits.pop(); }],
  ['missing credits verdict detail', 'VERDICT', r => { delete r.verdicts.find(v => v.feature === 'CAT-2 credits refusal')!.detail; }],
  ['missing catalogue ID', 'PARTIAL', r => { r.backend[0].catalogues.cat2.api.pop(); }],
  ['duplicate catalogue ID', 'PARTIAL', r => { r.backend[0].catalogues.cat2.api[1] = r.backend[0].catalogues.cat2.api[0]; }],
  ['CAT-2 recorded not OFF', 'VERDICT', r => { r.backend[0].verdicts.find(v => v.feature === 'CAT-2 exact 34 IDs default-off')!.verdict = 'RECORDED'; }],
  ['CAT-2 count contradiction', 'VERDICT', r => { r.backend[0].verdicts.find(v => v.feature === 'CAT-2 exact 34 IDs default-off')!.detail = { checked: 34, absent: 34, blockedUnpriced: 0 }; }],
  ['missing managed OFF witness', 'VERDICT', r => { r.ui[0].verdicts = [{ lane: 'ui', feature: 'page visited', where: 'models', verdict: 'RECORDED' }]; }],
  ['present unchecked managed switch', 'VERDICT', r => { (r.ui[0].verdicts[0].detail as any).control = 'PRESENT'; }],
  ['absent without notice', 'VERDICT', r => { (r.ui[0].verdicts[0].detail as any).notice.present = false; }],
  ['server says enabled', 'VERDICT', r => { (r.ui[0].verdicts[0].detail as any).server.enabled = true; }],
  ['new kind on another surface', 'VERDICT', r => { r.ui[0].verdicts[0].feature = 'mcp'; }],
  ['kindless present control beside valid witness', 'VERDICT', r => {
    r.ui[0].verdicts.push({ lane: 'ui', feature: 'managed_models', where: 'models', verdict: 'RECORDED',
      detail: { control: 'PRESENT', switchState: 'unchecked' } });
  }],
  ['contradictory switchState on absent witness', 'VERDICT', r => { (r.ui[0].verdicts[0].detail as any).switchState = 'checked'; }],
  ['missing backend feature', 'PARTIAL', r => { r.backend[1].verdicts = r.backend[1].verdicts.filter(v => v.feature !== 'trace content consent audit'); }],
  ['ON verdict', 'VERDICT', r => { r.ui[0].verdicts[0].verdict = 'ON'; }],
  ['wrong exact count', 'VERDICT', r => { r.backend[1].activeVersions = 1; }],
  ['non-null mirror', 'VERDICT', r => { r.backend[1].mirrorNull = false; }],
  ['changed B', 'VERDICT', r => { r.backend[1].orgBSnapshot = censusDigest('changed'); }],
  ['wrong org set', 'ORG_SET', r => { r.orgSet = censusDigest('other-orgs'); }],
  ['incomplete', 'PARTIAL', r => { (r as any).complete = false; }],
  ['old absence-only schema', 'PARTIAL', r => { (r as any).schema = 'w00-result-v1'; }],
  ['raw identity', 'VALUE', r => { r.verdicts.push({ lane: 'ui', feature: 'synthetic', where: 'fixture', verdict: 'RECORDED', detail: ['actor', 'example.invalid'].join('@') }); }],
];
for (const [name, code, mutate, rehash = true] of refusals) test(`offline W00 reader refuses ${name} with ${code}`, ({}, info) => {
  const directory = info.outputPath('results');
  publishCensusResult(binding, data(), {}, directory);
  const path = censusResultPath(binding, directory);
  const result = JSON.parse(readFileSync(path, 'utf8')) as CensusResult;
  mutate(result);
  if (rehash) resign(result);
  writeFileSync(path, JSON.stringify(result));
  reason(() => readCensusResult(binding, directory), code);
});

test('offline W00 result: caller can pin the org set', ({}, info) => {
  publishCensusResult(binding, data(), {}, info.outputPath('results'));
  reason(() => readCensusResult(binding, info.outputPath('results'), censusDigest('foreign')), 'ORG_SET');
});
test('offline W00 result: malformed JSON refuses without quoting file content', ({}, info) => {
  const directory = info.outputPath('results');
  publishCensusResult(binding, data(), {}, directory);
  writeFileSync(censusResultPath(binding, directory), '{');
  reason(() => readCensusResult(binding, directory), 'MALFORMED');
});
test('offline W00 result: CAT-2 has exactly 34 unique exact identities', () => {
  expect(CAT2_IDS).toHaveLength(34);
  expect(new Set(CAT2_IDS).size).toBe(34);
  for (const lane of ['api', 'gateway'] as const) {
    expect(observeCat2([{ id: 'synthetic-allowed-model' }], lane).every(row => row.disposition === 'absent')).toBe(true);
    expect(observeCat2(blockedModels, lane).every(row => row.disposition === 'blocked-unpriced')).toBe(true);
    for (const id of CAT2_IDS) {
      expect(() => observeCat2([{ id, state: 'available' }], lane)).toThrow(`CAT2_OFFERED: ${lane}: ${id}`);
      for (const field of CAT2_PRICE_FIELDS) for (const price of [0, 1, '0']) {
        expect(() => observeCat2([{ id, state: 'blocked', [field]: price }], lane)).toThrow('CAT2_OFFERED');
      }
      expect(() => observeCat2([{ id, state: 'blocked' }, { id, state: 'available' }], lane)).toThrow('CAT2_OFFERED');
    }
  }
});
test('offline W00 result: product count is exact, off zero and declared/offered one', () => {
  expect(expectedFirstConfigVersions(false)).toBe(0);
  expect(expectedFirstConfigVersions(true)).toBe(1);
});
test('offline W00 compatibility: scope attachment default stays raw, digest mode is value-free', () => {
  const evidence = { email: ['synthetic', 'example.invalid'].join('@'), organizationId: 'synthetic-id' };
  expect(scopeAttachment(evidence)).toBe(JSON.stringify(evidence));
  const safe = scopeAttachment(evidence, 'digest');
  expect(JSON.parse(safe).digest).toMatch(/^sha256:[a-f0-9]{64}$/);
  expect(safe).not.toContain(evidence.email);
  expect(safe).not.toContain(evidence.organizationId);
});

// The same assertion as W00. Proof runner changes ONLY the input via this env,
// not the expected ID set, and requires this test to fail before restoring green.
test('offline W00 fallibility: catalogue response refuses offered CAT-2', () => {
  const models = [{ id: 'synthetic-allowed-model', state: 'available' }];
  if (process.env.E2E_CENSUS_PERTURB === '1') models.push({ id: 'google/lyria-3-pro-preview', state: 'available' });
  observeCat2(models, 'api');
});
