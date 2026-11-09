// HR-JRN-03: value-free W00 interchange. A digest is integrity/binding, not a
// signature. Only the successful W00 fixture publishes; consumers never replay
// the census or infer a pass from an absent file. No browser storage or glob.
import { createHash } from 'node:crypto';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import type { VerdictRow } from './receipt';
import { assertValueFreeReceipt } from './receipt-values.mjs';
import { CENSUS_PHASES, CENSUS_ROLES, CENSUS_PAGES, CENSUS_VIEWPORTS, CENSUS_SCHEMES, CENSUS_BACKEND_CHECKS,
  CAT2_IDS, cat2ObservationIsOff, cat2CreditsRefused, type Cat2Observation, type Cat2CreditsRefusal,
  type CensusPhase, type CensusRole, type CensusPage, expectedFirstConfigVersions } from './census-contract';

export type CensusBrowser = 'chromium' | 'firefox';
export interface CensusBinding {
  run: string; profile: string; browser: CensusBrowser; stack: string;
  newOrgDefault: boolean;
}
export interface CensusUIRow {
  phase: CensusPhase; role: CensusRole; page: CensusPage;
  viewport: { width: number; height: number }; scheme: 'light' | 'dark';
  verdicts: VerdictRow[];
}
export interface CensusBackendRow {
  phase: CensusPhase; verdicts: VerdictRow[]; configRows: number; activeVersions: number;
  mirrorNull: boolean; catalogues: { api: number; gateway: number; cat2: { api: Cat2Observation[]; gateway: Cat2Observation[] } };
  orgBSnapshot: string;
}
export interface CensusData {
  orgs: { A: string; B: string }; ui: CensusUIRow[]; backend: CensusBackendRow[];
  credits: Cat2CreditsRefusal[]; verdicts: VerdictRow[];
}
export interface CensusResult extends CensusData {
  schema: 'w00-result-v2'; complete: true; binding: CensusBinding; orgSet: string;
  receipt: string; digest: string;
}
export type CensusReason = 'ABSENT' | 'MALFORMED' | 'DIGEST' | 'RUN' | 'PROFILE' | 'BROWSER' |
  'STACK' | 'DEFAULT' | 'ORG_SET' | 'PARTIAL' | 'VERDICT' | 'VALUE' | 'DUPLICATE';
export class CensusResultError extends Error {
  readonly reason: CensusReason;
  constructor(reason: CensusReason) { super(`W00_RESULT_${reason}`); this.reason = reason; }
}
const refuse = (reason: CensusReason): never => { throw new CensusResultError(reason); };
const isDigest = (value: unknown): value is string => typeof value === 'string' && /^sha256:[a-f0-9]{64}$/.test(value);
const canonical = (value: any): any => Array.isArray(value) ? value.map(canonical) :
  value && typeof value === 'object' ? Object.fromEntries(Object.keys(value).sort().map(k => [k, canonical(value[k])])) : value;
export const censusDigest = (value: unknown): string => `sha256:${createHash('sha256').update(JSON.stringify(canonical(value))).digest('hex')}`;

/** Directory survives Playwright's per-project output cleanup. Run IDs are
 * generated/exported ONCE by the orchestrator and inherited by all projects. */
export function censusResultPath(binding: CensusBinding, directory = process.env.E2E_CENSUS_RESULTS): string {
  if (!directory) throw new Error('W00_RESULT_DIRECTORY_REQUIRED');
  return resolve(directory, `${censusDigest({ run: binding.run, profile: binding.profile, browser: binding.browser }).slice(7)}.json`);
}
function validateBinding(binding: CensusBinding): void {
  if (!binding || !isDigest(binding.run) || !isDigest(binding.stack) || typeof binding.profile !== 'string' ||
      !['chromium', 'firefox'].includes(binding.browser) || typeof binding.newOrgDefault !== 'boolean') refuse('MALFORMED');
}
// Main's 754 ruling: OFF is a state proof, never inferred from UI absence.
// Pinned to ManagedModelsCard.jsx at 3c20e189ea and the status list API's
// effective org managed_models.enabled. No product import or arbitrary notice.
export const MANAGED_MODELS_OFF_NOTICE = 'Future AGI credits are off for this organization.';
export const MANAGED_MODELS_STATUS_PATH = '/agentcc/managed-models/';
export interface ManagedModelsOffObservation {
  kind: 'absent-with-notice'; control: 'ABSENT';
  notice: { present: true; text: typeof MANAGED_MODELS_OFF_NOTICE };
  server: { source: typeof MANAGED_MODELS_STATUS_PATH; enabled: false };
}
function absentWithNotice(row: VerdictRow): boolean {
  return (row.detail as Partial<ManagedModelsOffObservation> | null)?.kind === 'absent-with-notice';
}
function exactKeys(value: unknown, keys: readonly string[]): boolean {
  return value !== null && typeof value === 'object' && !Array.isArray(value) &&
    Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
}
function managedModelsOff(row: VerdictRow): boolean {
  const detail = row.detail as Partial<ManagedModelsOffObservation> | null;
  // A closed witness cannot also carry a switchState or other contradictory
  // evidence, even when its required fields assert ABSENT and server OFF.
  return row.feature === 'managed_models' && row.lane === 'ui' && row.verdict === 'OFF' &&
    exactKeys(detail, ['kind', 'control', 'notice', 'server']) &&
    detail?.kind === 'absent-with-notice' && detail.control === 'ABSENT' &&
    exactKeys(detail.notice, ['present', 'text']) &&
    detail.notice?.present === true && detail.notice.text === MANAGED_MODELS_OFF_NOTICE &&
    exactKeys(detail.server, ['source', 'enabled']) &&
    detail.server?.source === MANAGED_MODELS_STATUS_PATH && detail.server.enabled === false;
}
function validateVerdicts(rows: unknown): asserts rows is VerdictRow[] {
  if (!Array.isArray(rows)) return refuse('PARTIAL');
  if (!rows.length) refuse('PARTIAL');
  for (const row of rows) {
    if (!row || typeof row.feature !== 'string' || !row.feature || typeof row.where !== 'string' ||
        !['ui', 'api', 'storage', 'gateway', 'redis'].includes(row.lane) ||
        !['OFF', 'ABSENT', 'RECORDED', 'ON'].includes(row.verdict)) refuse('MALFORMED');
    if (row.verdict === 'ON') refuse('VERDICT');
    // A switch witness in ANY state contradicts the current absent-control
    // contract. Every managed UI row must be the closed witness, including a
    // second row that omits kind. Backend config-state rows retain their shape.
    if (row.feature === 'managed_models switch' ||
        (((row.lane === 'ui' && row.feature === 'managed_models') || absentWithNotice(row)) && !managedModelsOff(row))) refuse('VERDICT');
  }
}

/** Recompute before trusting any result field, then verify the exact matrix.
 * expectedOrgSet is optional for consumers of W00's separately provisioned orgs;
 * callers with an independently pinned org set can require it explicitly. */
export function verifyCensusResult(value: unknown, expected: CensusBinding, expectedOrgSet?: string): CensusResult {
  validateBinding(expected);
  if (!value || typeof value !== 'object' || Array.isArray(value)) refuse('MALFORMED');
  const { digest, ...payload } = value as CensusResult;
  if (!isDigest(digest) || censusDigest(payload) !== digest) refuse('DIGEST');
  const result = value as CensusResult;
  validateBinding(result.binding);
  const checks = [['run', 'RUN'], ['profile', 'PROFILE'], ['browser', 'BROWSER'], ['stack', 'STACK'],
    ['newOrgDefault', 'DEFAULT']] as const;
  for (const [field, reason] of checks) if (result.binding[field] !== expected[field]) refuse(reason);
  if (result.schema !== 'w00-result-v2' || result.complete !== true || !isDigest(result.receipt)) refuse('PARTIAL');
  if (!result.orgs || !isDigest(result.orgs.A) || !isDigest(result.orgs.B) || result.orgs.A === result.orgs.B ||
      censusDigest(result.orgs) !== result.orgSet || (expectedOrgSet && expectedOrgSet !== result.orgSet)) refuse('ORG_SET');
  if (!Array.isArray(result.ui) || !Array.isArray(result.backend)) refuse('PARTIAL');
  const cells = new Set<string>();
  for (const row of result.ui) {
    if (!row || !row.viewport) refuse('MALFORMED');
    const cell = JSON.stringify([row.phase, row.role, row.page, row.viewport.width, row.viewport.height, row.scheme]);
    if (cells.has(cell)) refuse('PARTIAL');
    cells.add(cell);
    validateVerdicts(row.verdicts);
    if (row.verdicts.some(v => v.lane !== 'ui')) refuse('MALFORMED');
    if (row.page !== 'models' && row.verdicts.some(absentWithNotice)) refuse('VERDICT');
    if (row.page === 'models' && row.verdicts.filter(managedModelsOff).length !== 1) refuse('VERDICT');
  }
  const required = CENSUS_PHASES.flatMap(phase => CENSUS_ROLES.flatMap(role => CENSUS_PAGES.flatMap(page =>
    CENSUS_VIEWPORTS.flatMap(v => CENSUS_SCHEMES.map(s => JSON.stringify([phase, role, page, v.width, v.height, s]))))));
  if (cells.size !== required.length || required.some(cell => !cells.has(cell))) refuse('PARTIAL');
  if (result.backend.length !== CENSUS_PHASES.length) refuse('PARTIAL');
  for (const phase of CENSUS_PHASES) {
    const rows = result.backend.filter(row => row?.phase === phase);
    if (rows.length !== 1) refuse('PARTIAL');
    const row = rows[0];
    const count = phase === 'before-key' ? 0 : expectedFirstConfigVersions(expected.newOrgDefault);
    if (row.configRows !== count || row.activeVersions !== count || row.mirrorNull !== (count === 0)) refuse('VERDICT');
    if (!row.catalogues || !row.catalogues.cat2 ||
        !Number.isInteger(row.catalogues.api) || row.catalogues.api < 0 ||
        !Number.isInteger(row.catalogues.gateway) || row.catalogues.gateway < 0 || !isDigest(row.orgBSnapshot)) refuse('PARTIAL');
    validateVerdicts(row.verdicts);
    for (const lane of ['api', 'gateway'] as const) {
      const observations = row.catalogues.cat2[lane];
      if (!Array.isArray(observations) || observations.length !== CAT2_IDS.length ||
          CAT2_IDS.some(id => observations.filter(o => o?.id === id).length !== 1)) refuse('PARTIAL');
      if (observations.some(o => !cat2ObservationIsOff(o))) refuse('VERDICT');
      const blocked = observations.filter(o => o.disposition === 'blocked-unpriced').length;
      if (row.catalogues[lane] < blocked) refuse('VERDICT');
      const verdict = row.verdicts?.filter(v => v.lane === lane && v.feature === 'CAT-2 exact 34 IDs default-off');
      if (!verdict || verdict.length !== 1 || verdict[0].verdict !== 'OFF') refuse('VERDICT');
      const detail = verdict[0].detail as Record<string, unknown> | undefined;
      if (!detail || detail.checked !== 34 || detail.blockedUnpriced !== blocked || detail.absent !== 34 - blocked) refuse('VERDICT');
    }
    for (const check of CENSUS_BACKEND_CHECKS) {
      if (!row.verdicts.some(v => v.lane === check.lane && v.feature === check.feature)) refuse('PARTIAL');
    }
  }
  if (result.backend[0].orgBSnapshot !== result.backend[1].orgBSnapshot) refuse('VERDICT');
  if (!Array.isArray(result.credits) || result.credits.length !== CAT2_IDS.length ||
      CAT2_IDS.some(id => result.credits.filter(r => r?.id === id).length !== 1)) refuse('PARTIAL');
  if (result.credits.some(r => !cat2CreditsRefused(r))) refuse('VERDICT');
  validateVerdicts(result.verdicts);
  for (const refusal of result.credits) {
    const rows = result.verdicts.filter(v => v.lane === 'gateway' && v.feature === 'CAT-2 credits refusal' && v.where === refusal.id);
    if (rows.length !== 1 || rows[0].verdict !== 'OFF' || rows[0].detail == null ||
        censusDigest(rows[0].detail) !== censusDigest(refusal)) refuse('VERDICT');
  }
  try { assertValueFreeReceipt(JSON.stringify(result)); } catch { refuse('VALUE'); }
  // The global receipt inventory must include EVERY matrix/backend verdict,
  // including repeated equal observations; a truncated inventory is partial.
  const inventory = new Map<string, number>();
  for (const row of result.verdicts) { const key = censusDigest(row); inventory.set(key, (inventory.get(key) ?? 0) + 1); }
  for (const row of [...result.ui.flatMap(r => r.verdicts), ...result.backend.flatMap(r => r.verdicts)]) {
    const key = censusDigest(row), count = inventory.get(key) ?? 0;
    if (count === 0) refuse('PARTIAL');
    inventory.set(key, count - 1);
  }
  return result;
}

export function publishCensusResult(binding: CensusBinding, data: CensusData, receipt: unknown,
  directory = process.env.E2E_CENSUS_RESULTS): CensusResult {
  const payload = { schema: 'w00-result-v2' as const, complete: true as const, binding, ...data,
    orgSet: censusDigest(data.orgs), receipt: censusDigest(receipt) };
  const result = verifyCensusResult({ ...payload, digest: censusDigest(payload) }, binding);
  const path = censusResultPath(binding, directory);
  mkdirSync(dirname(path), { recursive: true });
  // Exclusive creation: a second W00 for a tuple never replaces first evidence.
  // Consumers are project dependencies and cannot run until this write closes.
  try { writeFileSync(path, JSON.stringify(result, null, 2) + '\n', { flag: 'wx' }); }
  catch (error) { if ((error as NodeJS.ErrnoException).code === 'EEXIST') refuse('DUPLICATE'); throw error; }
  return result;
}

export function readCensusResult(expected: CensusBinding, directory = process.env.E2E_CENSUS_RESULTS,
  expectedOrgSet?: string): CensusResult {
  let text: string;
  try { text = readFileSync(censusResultPath(expected, directory), 'utf8'); }
  catch (error) { if ((error as NodeJS.ErrnoException).code === 'ENOENT') refuse('ABSENT'); throw error; }
  let value: unknown;
  try { value = JSON.parse(text); } catch { refuse('MALFORMED'); }
  return verifyCensusResult(value, expected, expectedOrgSet);
}
