import { createHash } from 'node:crypto';
import { readCensusResult } from '../../lib/walk/census-result';
import { currentCensusBinding } from '../../lib/walk/census-runtime';
import { CAT2_IDS, cat2ObservationIsOff, cat2CreditsRefused } from '../../lib/walk/census-contract';
import { JOURNEY_PUBLICATION_ACCEPTED, JOURNEY_PUBLICATION_GAP } from './publication-contract';
import { test, expect } from '../../lib/walk/fixtures';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';
import type { TestInfo } from '@playwright/test';
import type { ScopeActors } from '../../lib/scope-actors';
import type { Walk } from '../../lib/walk/fixtures';
import { CJ_STAGE_A, J00ConsoleCoreBlocked as ConsoleCoreBlocked, loadConsoleCoreSeams, requireJ00Runtime,
  assertPreservingBlock, requireJ00PhaseRolloutBinding, requireJ00ReviewedRouteInventory,
  type CoreSeams, type J00Runtime, type J00Identity, type VerifiedExchange, type HostClient, type HostObservation,
  type BoundReceipt, type Engine, type Phase, type Tenant, type AuthMethod, type LM708 } from './console-core-seams';
import { CONSOLE_ORIGIN_SOURCE, consoleOriginAppListener, consoleOriginBindingUrl, evaluateConsoleOriginMatrix,
  localConsoleOriginReds, planConsoleOriginMatrix, runConsoleOriginMatrix, type ConsoleOriginCellReceipt } from './console-origin-matrix';

// Source: JOURNEYS-708 v2, J00. These are release
// obligations, NOT claims that the harness can already execute them. In
// particular, if_absent is an assertion branch, never permission to skip.
// The gateway lane owns lib/ and stack/; do not implement its missing readers
// or import another spec here. W00 must execute once, not be copied into J00.
const FLOW = 'JRN-E2E-000';
// tfc/capabilities/views.py: CapabilitiesView.get returns features, not result.
type Capabilities = { features: Record<string, { allowed: boolean }> };
const fingerprint = (value: unknown) => `sha256:${createHash('sha256').update(JSON.stringify(value)).digest('hex')}`;
// Gateway stock is a rehearsal profile, not an LM-708 release tuple. The cloud
// command binds it to the exact product archive; no absence is inferred from 404.
const UI_READY = 60_000;
test.use({ scopeRoster: { orgB: [3] }, trace: 'off', screenshot: 'off', video: 'off' });
// Definition-time skip also protects direct main-config invocations before any fixture.
test.skip(!JOURNEY_PUBLICATION_ACCEPTED, JOURNEY_PUBLICATION_GAP);

// J00.L2/L3 packet oracle inventory. These local mutants document fallibility,
// not live red/restored receipts. Product fault injection belongs to the accepted
// Stage B adapter, never page.route(), Docker commands or a forged JWT here.
const J00_REDS = {
  'L2.01': 'missing phase / changed LM digest / unapproved allowed omega_part_*',
  'L2.02': 'admit fresh F during REHEARSAL or deny a named admitted tenant',
  'L2.03': 'leave one WITHDRAWN tenant admitted or mint a usable console claim',
  'L2.04': 'wrong signature, product, person, org, workspace, role or audience; public-origin iframe / wrong handshake origin',
  'L2.05': 'revoked key or removed/deactivated member mints; downgraded actor retains old authority; omit J04.L1',
  'L2.06': 'GA A/B-only allowlist; omit fresh F; declared deep link returns 404',
  'L2.07': 'Console GA accidentally enables an independently withheld product',
  'L2.08': 'sandbox disabled/full also disables Console; restore and repeat both exchange methods',
  'L2.09': 'preload admitted state then delay/fail capabilities or exchange; stale UI/claim survives',
  'L2.10': 'one registered route, switcher or allowed omega_* remains in absent-shell arm',
  'L3.01': '401/403/302/200 on any public cell, omit anonymous/B/third path, including edge-served',
  'L3.02': 'unhide undeclared method/path or drop it from reviewed inventory',
  'L3.03': 'substitute D-2 receipt or change WITHDRAWN baseline bytes',
  'L3.04': 'omit enabled-family receipt; skipped/zero-assertion/wrong-arm receipt',
  'L3.05': 'register learning or version-evaluation route',
  'L3.06': 'swap rehearsal/GA expectations or admit unauthorized principal',
  'L3.07': 'alter either deployed EE copy or edge copy; remeasure after restoration',
  'L3.08': 'add DNS/ingress-to-EE mapping while HTTP remains 404',
  'L3.09': 'Console origin (744 vectors): 200 on any 404 cell or SPA fallback outside /omega/console; HEAD dropped or with body '
    + '(incl. a body after a quiet pause, or no EOF); GET/HEAD disagree; vector omitted/altered; SPA/asset/document positive '
    + 'missing, 404 or wrong/empty body; CSP report-uri off CSP_REPORT_PATH; 1xx taken as the answer; listener API '
    + '401/200/403 or CSP-report 204 changed; normalized target or altered request headers/body; coded (gzip/br/deflate/'
    + 'unknown) body or leak, incl. chunked; CSP3 duplicate/no-space/case/comma/report-to directive; app listener '
    + 'sandboxes 200 or Access-Control-Allow-Origin changed, or app-listener static route not 404',
} as const;
// These are provider-free auth/ingress checks. No model call, model-grant
// fallback, mock response in a positive path, or replacement browser runner.
const ENGINES = ['chromium', 'firefox', 'webkit'] as const;
const PHASES = ['REHEARSAL', 'WITHDRAWN', 'TUESDAY_GA'] as const;
const PUBLIC_PATHS = ['/omega/api/workspace/capabilities', '/omega/console/', '/omega/j00-unregistered-probe'] as const;
const FAMILY_INVENTORY = ['integrations', 'usage', 'workforce-import', 'attachment-upload', 'preview-approvals',
  'custom-connectors', 'skills', 'schedules', 'signed-triggers'] as const;
const isDigest = (value: unknown): value is string => typeof value === 'string' && /^sha256:[a-f0-9]{64}$/.test(value);
class J00Assertion extends Error {
  readonly check: string;
  constructor(check: string) { super(`J00 assertion ${check} failed (values withheld)`); this.check = check; }
}
function requireCheck(condition: boolean, check: string): void { if (!condition) throw new J00Assertion(check); }
function legOutcome(failure: ConsoleCoreBlocked | J00Assertion | undefined, completed: boolean) {
  return {
    status: failure instanceof ConsoleCoreBlocked ? 'BLOCKED' : failure ? 'FAILED' : completed ? 'EXECUTED' : 'NOT_RUN',
    execution: failure instanceof ConsoleCoreBlocked ? failure.execution : completed ? 'EXECUTED' : 'INCOMPLETE',
    reason: failure instanceof ConsoleCoreBlocked ? failure.seam : failure?.check ?? null,
  };
}
function isLocalOrigin(value: string): boolean {
  try {
    const url = new URL(value);
    return ['http:', 'https:'].includes(url.protocol) && ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)
      && url.origin === value && !url.username && !url.password;
  } catch { return false; }
}
function claimsMatch(value: VerifiedExchange, identity: J00Identity, auth: AuthMethod): boolean {
  return value.signatureVerified === true && value.audience === 'omega-squad' && value.products.includes('console')
    && value.organizationId === identity.actor.organizationId && value.workspaceId === identity.actor.workspaceId
    && value.personId === identity.actor.userId && value.role === identity.role
    && value.authMethod === (auth === 'key' ? 'api_key' : 'session') && value.edgeStatus === 200
    && value.edgeOrganizationId === identity.actor.organizationId && value.edgePersonId === identity.actor.userId;
}
function receiptMatches(receipt: BoundReceipt | undefined, runtime: Pick<J00Runtime, 'lmDigest' | 'binding'>,
  subject: string, kind: 'capability' | 'absence'): boolean {
  return !!receipt && receipt.subject === subject && receipt.kind === kind && receipt.evidence === 'live'
    && isDigest(receipt.digest) && receipt.lm708Digest === runtime.lmDigest
    && receipt.compositionDigest === runtime.binding.compositionDigest && receipt.browser === runtime.binding.browser
    && receipt.imageDigest === runtime.binding.imageDigest && receipt.configDigest === runtime.binding.configDigest
    && receipt.phase === runtime.binding.phase && Number.isSafeInteger(receipt.executed) && receipt.executed > 0
    && receipt.skipped === 0 && receipt.failed === 0;
}
type WallCell = { caller: 'anonymous' | 'A' | 'B'; path: string; status: number };
function publicWallMatches(rows: WallCell[]): boolean {
  const required = ['anonymous', 'A', 'B'].flatMap(caller => PUBLIC_PATHS.map(path => `${caller}:${path}`));
  return rows.length === required.length && new Set(rows.map(row => `${row.caller}:${row.path}`)).size === required.length
    && required.every(key => rows.some(row => `${row.caller}:${row.path}` === key && row.status === 404));
}
function securityAbsenceMatches(rows: WallCell[], retainedWallPassed: boolean, cleanupFailed: boolean): boolean {
  return publicWallMatches(rows) && retainedWallPassed && !cleanupFailed;
}
function localWallReds(): void {
  const restored: WallCell[] = (['anonymous', 'A', 'B'] as const).flatMap(caller => PUBLIC_PATHS.map(path => ({ caller, path, status: 404 })));
  for (const status of [200, 401, 403, 302]) for (let cell = 0; cell < restored.length; cell++) {
    const mutant = restored.map((row, index) => index === cell ? { ...row, status } : row);
    requireCheck(!publicWallMatches(mutant), 'L3.01-red-status');
  }
  for (let cell = 0; cell < restored.length; cell++)
    requireCheck(!publicWallMatches(restored.filter((_, index) => index !== cell)), 'L3.01-red-omission');
  requireCheck(publicWallMatches(restored), 'L3.01-local-restored');
  // A complete nine-cell matrix cannot erase a failure in the retained checks.
  requireCheck(!securityAbsenceMatches(restored, false, false), 'L3.01-retained-wall-red');
  requireCheck(!securityAbsenceMatches(restored, true, true), 'L3.01-cleanup-red');
  requireCheck(securityAbsenceMatches(restored, true, false), 'L3.01-security-restored');
}
function localClaimReds(identity: J00Identity): void {
  // Synthetic oracle fixtures only; no token, external request or release claim.
  const restored: VerifiedExchange = { signatureVerified: true, audience: 'omega-squad', products: ['console'],
    organizationId: identity.actor.organizationId, workspaceId: identity.actor.workspaceId, personId: identity.actor.userId,
    role: identity.role, authMethod: 'api_key', edgeStatus: 200,
    edgeOrganizationId: identity.actor.organizationId, edgePersonId: identity.actor.userId };
  const mutations: Partial<VerifiedExchange>[] = [{ signatureVerified: false }, { audience: 'wrong' }, { products: ['studio_tab'] },
    { organizationId: '' }, { workspaceId: '' }, { personId: '' }, { role: '' }, { authMethod: 'session' },
    { edgeStatus: 403 }, { edgeOrganizationId: '' }, { edgePersonId: '' }];
  for (const mutation of mutations) requireCheck(!claimsMatch({ ...restored, ...mutation }, identity, 'key'), 'L2.04-local-red');
  requireCheck(claimsMatch(restored, identity, 'key'), 'L2.04-local-restored');
}

function localReceiptReds(): void {
  // Test-local value-free fixture, never passed to a runtime factory or emitted
  // as an original. Exercise the SAME join predicate used by the live consumers.
  const one = `sha256:${'1'.repeat(64)}`, two = `sha256:${'2'.repeat(64)}`;
  const runtime: Pick<J00Runtime, 'lmDigest' | 'binding'> = { lmDigest: one, binding: {
    phase: 'TUESDAY_GA', browser: 'chromium', qualifiedEngines: [...ENGINES], compositionDigest: one,
    imageDigest: one, configDigest: one, stageAExportDigest: one, stageBDerivationDigest: one, runtimeAcceptanceDigest: one,
  } };
  const restored: BoundReceipt = { subject: 'fixture-family', kind: 'capability', evidence: 'live', digest: one,
    lm708Digest: one, compositionDigest: one, browser: 'chromium', phase: 'TUESDAY_GA', imageDigest: one,
    configDigest: one, executed: 1, skipped: 0, failed: 0 };
  const mutations: Partial<BoundReceipt>[] = [{ subject: 'other-family' }, { kind: 'absence' }, { lm708Digest: two },
    { compositionDigest: two }, { browser: 'webkit' }, { phase: 'WITHDRAWN' }, { imageDigest: two },
    { configDigest: two }, { executed: 0 }, { skipped: 1 }, { failed: 1 }];
  requireCheck(!receiptMatches(undefined, runtime, 'fixture-family', 'capability'), 'receipt-local-missing');
  for (const mutation of mutations)
    requireCheck(!receiptMatches({ ...restored, ...mutation }, runtime, 'fixture-family', 'capability'), 'receipt-local-red');
  requireCheck(receiptMatches(restored, runtime, 'fixture-family', 'capability'), 'receipt-local-restored');
}

function requireReaders(runtime: J00Runtime, readers: readonly (keyof J00Runtime)[]): void {
  for (const reader of readers) if (typeof runtime[reader] !== 'function')
    throw new ConsoleCoreBlocked(`J00_${reader.replace(/([A-Z])/g, '_$1').toUpperCase()}`);
}
async function cleanupRuntime(runtime: Pick<J00Runtime, 'dispose'> | undefined): Promise<J00Assertion | undefined> {
  try {
    // requireReaders owns a missing/non-callable seam's typed refusal. Do not
    // replace it with a TypeError from cleanup; real disposal failures still fail.
    if (typeof runtime?.dispose === 'function') await runtime.dispose();
    return undefined;
  } catch { return new J00Assertion('J00-cleanup'); }
}
async function localDisposalReds(): Promise<void> {
  // Deliberately malformed oracle fixtures only; never installed as live proof.
  for (const candidate of [{}, { dispose: undefined }, { dispose: null }, { dispose: false },
    { dispose: 'not-callable' }, { dispose: {} }]) {
    const runtime = candidate as unknown as J00Runtime;
    let failure: ConsoleCoreBlocked | J00Assertion | undefined;
    try { requireReaders(runtime, ['dispose']); }
    catch (error) { failure = error instanceof ConsoleCoreBlocked ? error : new J00Assertion('dispose-control-validation'); }
    requireCheck(failure instanceof ConsoleCoreBlocked && failure.seam === 'J00_DISPOSE', 'dispose-local-missing-seam');
    const cleanupFailure = await cleanupRuntime(runtime);
    const receipt = legOutcome(cleanupFailure ?? failure, false);
    requireCheck(!cleanupFailure && receipt.status === 'BLOCKED' && receipt.execution === 'NOT_RUN'
      && receipt.reason === 'J00_DISPOSE', 'dispose-local-block-preserved');
  }
  requireCheck(await cleanupRuntime(undefined) === undefined, 'dispose-local-no-runtime');
  const restored = { calls: 0, async dispose() { await Promise.resolve(); this.calls++; } };
  const restoredFailure = await cleanupRuntime(restored);
  requireCheck(!restoredFailure && restored.calls === 1, 'dispose-local-restored');
  for (const asynchronous of [false, true]) {
    let calls = 0;
    const runtime = { dispose(): Promise<void> {
      calls++;
      const fault = new Error('local disposal fault (values withheld)');
      if (asynchronous) return Promise.reject(fault);
      throw fault;
    } };
    const failure = await cleanupRuntime(runtime);
    const receipt = legOutcome(failure, false);
    requireCheck(calls === 1 && failure instanceof J00Assertion && receipt.status === 'FAILED'
      && receipt.execution === 'INCOMPLETE' && receipt.reason === 'J00-cleanup', 'dispose-local-callable-failure');
  }
}

// Principal keys stay in memory. Receipts expose only fixture slots, never IDs.
type PositivePrincipal = { tenant: Tenant; principal: string; role: string; admitted: boolean };
type PositiveCell = Omit<PositivePrincipal, 'admitted'> & { auth: AuthMethod; framePassed: boolean; exchangePassed: boolean };
function positivePrincipal(identity: J00Identity): PositivePrincipal {
  return { tenant: identity.tenant, role: identity.role, admitted: identity.admitted,
    principal: JSON.stringify([identity.actor.organizationId, identity.actor.workspaceId, identity.actor.userId]) };
}
function samePositiveCells(expected: PositiveCell[], actual: PositiveCell[]): boolean {
  return actual.length === expected.length && expected.every(cell => actual.filter(row => row.tenant === cell.tenant
    && row.principal === cell.principal && row.role === cell.role && row.auth === cell.auth
    && row.framePassed && row.exchangePassed).length === 1);
}
function positiveMatrixComplete(phase: Phase, identities: PositivePrincipal[], cells: PositiveCell[]): boolean {
  if (phase !== 'REHEARSAL' && phase !== 'TUESDAY_GA') return false;
  const tenants: Tenant[] = phase === 'TUESDAY_GA' ? ['A', 'B', 'F'] : ['A', 'B'];
  const required = identities.filter(row => tenants.includes(row.tenant));
  if (!tenants.every(tenant => required.some(row => row.tenant === tenant))
    || !(['A', 'B'] as const).every(tenant => ['owner', 'member'].every(role => required.some(row => row.tenant === tenant && row.role === role)))
    || required.some(row => !row.admitted) || new Set(required.map(row => row.principal)).size !== required.length) return false;
  return samePositiveCells(required.flatMap(({ admitted: _, ...row }) => (['session', 'key'] as const)
    .map(auth => ({ ...row, auth, framePassed: true, exchangePassed: true }))), cells);
}
function localPositiveMatrixReds(): void {
  const identities: PositivePrincipal[] = (['A', 'B', 'F'] as const).flatMap(tenant => ['owner', 'member']
    .map(role => ({ tenant, role, principal: `${tenant}-${role}`, admitted: true })));
  const cells: PositiveCell[] = identities.flatMap(({ admitted: _, ...row }) => (['session', 'key'] as const)
    .map(auth => ({ ...row, auth, framePassed: true, exchangePassed: true })));
  const rehearsal = identities.map(row => ({ ...row, admitted: row.tenant !== 'F' }));
  const abCells = cells.filter(row => row.tenant !== 'F');
  requireCheck(positiveMatrixComplete('REHEARSAL', rehearsal, abCells), 'L2-matrix-rehearsal-restored');
  requireCheck(!positiveMatrixComplete('REHEARSAL', rehearsal.map(row => ({ ...row, admitted: row.tenant === 'A' })),
    abCells.filter(row => row.tenant === 'A')), 'L2-matrix-a-only-red');
  requireCheck(!positiveMatrixComplete('TUESDAY_GA', identities, abCells), 'L2-matrix-fresh-f-red');
  requireCheck(!positiveMatrixComplete('WITHDRAWN', identities, cells), 'L2-matrix-withdrawn-red');
  for (let index = 0; index < cells.length; index++) {
    requireCheck(!positiveMatrixComplete('TUESDAY_GA', identities, cells.filter((_, i) => i !== index)), 'L2-matrix-omission-red');
    const mutations: Partial<PositiveCell>[] = [{ framePassed: false }, { exchangePassed: false },
      { principal: 'other' }, { role: 'other' }, { tenant: cells[index].tenant === 'A' ? 'B' : 'A' },
      { auth: cells[index].auth === 'key' ? 'session' : 'key' }];
    for (const mutation of mutations)
      requireCheck(!positiveMatrixComplete('TUESDAY_GA', identities, cells.map((row, i) => i === index ? { ...row, ...mutation } : row)), 'L2-matrix-cell-red');
  }
  requireCheck(!positiveMatrixComplete('TUESDAY_GA', identities, [...cells, cells[0]]), 'L2-matrix-duplicate-red');
  requireCheck(positiveMatrixComplete('TUESDAY_GA', identities, cells), 'L2-matrix-ga-restored');
}

async function localNestedBlockReds(): Promise<void> {
  // Oracle-only client models pinned host-assertion.ts:87-95 swallowing route
  // exceptions. It performs no request and cannot supply positive live proof.
  const observation: HostObservation = { status: 200, contentType: 'application/json',
    url: 'http://localhost/', headers: {}, body: Buffer.from('{}') };
  const swallowed = new Error('local response-contract mismatch');
  const client: HostClient = { dispose: async () => {}, assert: async (_host, _path, expected) => {
    let matched = false;
    try { matched = await expected.route.matches(observation); } catch { /* pinned-client behavior */ }
    if (!matched) throw swallowed;
    return observation;
  } };
  const missing = new ConsoleCoreBlocked('J00_VERIFY_AND_CONSUME_EXCHANGE');
  for (const fault of [missing, new J00Assertion('L2.04-signature')]) {
    let caught: unknown;
    try {
      await assertPreservingBlock(client, 'api', '/', { status: 200, contentType: 'application/json',
        route: { label: 'local-control', matches: async () => { throw fault; } } });
    } catch (error) { caught = error; }
    if (fault === missing) {
      requireCheck(caught === missing, 'L2.04-nested-block-preserved');
      const receipt = legOutcome(caught as ConsoleCoreBlocked, false);
      requireCheck(receipt.status === 'BLOCKED' && receipt.execution === 'NOT_RUN'
        && receipt.reason === 'J00_VERIFY_AND_CONSUME_EXCHANGE', 'L2.04-nested-block-receipt');
    } else requireCheck(caught === swallowed, 'L2.04-signature-remains-failed');
  }
  requireCheck(await assertPreservingBlock(client, 'api', '/', { status: 200, contentType: 'application/json',
    route: { label: 'local-control', matches: () => true } }) === observation, 'L2.04-nested-block-restored');
}

async function positiveConsole(runtime: J00Runtime, seams: CoreSeams, walk: Walk, identity: J00Identity): Promise<PositiveCell[]> {
  localClaimReds(identity);
  const context = await walk.open(identity.actor, 'j00-console-positive');
  const page = await context.newPage();
  try {
    // Accepted reader attaches BEFORE navigation, observing actual document
    // responses AND real postMessage handshake. A config string is not proof.
    const frame = await runtime.observeFrame(page, async () => {
      await page.goto('/dashboard/console', { waitUntil: 'domcontentloaded' });
    });
    const configured = new URL(runtime.consoleUrl);
    const src = new URL(frame.src), document = new URL(frame.documentUrl);
    requireCheck(!src.username && !src.password && !document.username && !document.password
      && src.origin === runtime.origins.console && document.origin === runtime.origins.console
      && src.origin !== runtime.origins.public && src.pathname === configured.pathname && src.search === configured.search
      && document.pathname === configured.pathname && document.search === configured.search && frame.documentStatus === 200
      && frame.handshakeOrigin === runtime.origins.console && frame.handshakeCompleted === true, 'L2.04-frame');
    const cells: PositiveCell[] = [];
    const { tenant, principal, role } = positivePrincipal(identity);
    for (const auth of ['session', 'key'] as const) {
      const client = await seams.HostAssertionClient.open({ api: runtime.origins.api }, auth === 'session'
        ? { kind: 'session', actor: identity.actor } : identity.personalKey);
      try {
        let verified: VerifiedExchange | undefined;
        await assertPreservingBlock(client, 'api', '/accounts/omega/session-exchange/', {
          status: 200, contentType: 'application/json', route: { label: 'j00-console-exchange', matches: async response => {
            verified = await runtime.verifyAndConsumeExchange(response, identity, auth);
            return claimsMatch(verified, identity, auth);
          } },
        }, { method: 'POST', data: { audience: 'omega-squad' } });
        requireCheck(!!verified && claimsMatch(verified, identity, auth), 'L2.04-exchange');
        cells.push({ tenant, principal, role, auth, framePassed: true, exchangePassed: true });
      } finally { await client.dispose(); }
    }
    return cells;
  } finally { await context.close(); }
}

type AdmissionProgress = { positiveCells: PositiveCell[]; matrixComplete: boolean; corePassed: boolean };
async function consoleAdmission(runtime: J00Runtime, seams: CoreSeams, manifest: LM708, walk: Walk,
  progress: AdmissionProgress): Promise<number> {
  requireReaders(runtime, ['readServerAdmission', 'observeFrame', 'verifyAndConsumeExchange', 'navigation',
    'readRouteCopies', 'denialControls', 'capabilityFaults', 'withSandboxState']);
  if (typeof runtime.admissionInput?.orm !== 'function') throw new ConsoleCoreBlocked('HR_CJ_01_COMPOSED_ORM');
  const server = await runtime.readServerAdmission();
  requireCheck(server.phase === runtime.binding.phase, 'L2.01-phase');
  if (server.phase === 'REHEARSAL') requireCheck(server.scope === 'named'
    && runtime.identities.filter(row => row.tenant === 'F').every(row => !server.allowlist.includes(row.actor.organizationId)), 'L2.02-rehearsal-scope');
  if (server.phase === 'TUESDAY_GA') requireCheck(server.scope === 'all' && server.allowlist.length === 0, 'L2.06-all-org');
  if (server.phase === 'WITHDRAWN') requireCheck(server.scope === 'none' && server.admittedOrganizations.length === 0, 'L2.03-s0');
  const positiveCells: PositiveCell[] = [];
  for (const identity of runtime.identities) {
    const expected = server.phase === 'TUESDAY_GA' || (server.phase === 'REHEARSAL' && identity.tenant !== 'F' && identity.admitted);
    requireCheck(identity.admitted === expected && (server.scope === 'all' || server.admittedOrganizations.includes(identity.actor.organizationId) === expected), 'L2.01-admission');
    const client = await seams.HostAssertionClient.open({ api: runtime.origins.api }, { kind: 'session', actor: identity.actor });
    try {
      await client.assert('api', '/api/capabilities/', { status: 200, contentType: 'application/json', route: {
        label: 'j00-capabilities', matches: response => {
          const body = JSON.parse(response.body.toString()) as Capabilities;
          return !!body.features && Object.keys(body.features).length > 0 && body.features.omega_console?.allowed === expected
            && Object.entries(body.features).every(([name, decision]) => typeof decision.allowed === 'boolean'
              && (!name.startsWith('omega_part_') || !decision.allowed || manifest.approvedParts!.includes(name.slice('omega_part_'.length))));
        },
      } });
    } finally { await client.dispose(); }
    const navigation = await runtime.navigation(identity);
    requireCheck(['deep-link', 'search', 'palette'].every(kind => navigation.some(row => row.kind === kind)), 'L2.06-navigation-inventory');
    requireCheck(navigation.every(row => row.expectedAllowed === expected && row.actualAllowed === expected), 'L2.02-navigation');
    if (expected) {
      positiveCells.push(...await positiveConsole(runtime, seams, walk, identity));
      progress.positiveCells = positiveCells;
    } else {
      const context = await walk.open(identity.actor, 'j00-console-denied');
      try {
        const page = await context.newPage();
        await page.goto('/dashboard/console', { waitUntil: 'domcontentloaded' });
        await page.waitForURL(/\/(?:404|dashboard\/home)\/?$/, { timeout: UI_READY });
        requireCheck(await page.locator('[data-testid="omega-tab-console"] iframe').count() === 0, 'L2.03-no-admitted-frame');
      } finally { await context.close(); }
    }
  }
  const positives = positiveCells.length;
  progress.matrixComplete = positiveMatrixComplete(runtime.binding.phase, runtime.identities.map(positivePrincipal), positiveCells);
  // Exercise the real shared admission mutation/restoration seam separately.
  // Its A-only positive is not added to our A/B matrix. Its host named "public"
  // MUST be the dedicated origin, never FA public.
  if (positives > 0) {
    const a = runtime.identities.find(row => row.tenant === 'A')!, b = runtime.identities.find(row => row.tenant === 'B')!;
    const proof = await seams.verifyConsoleAdmission({ ...runtime.admissionInput, routeCopies: await runtime.readRouteCopies(),
      manifest, stack: runtime.stack, actors: [a.actor, b.actor], hosts: { api: runtime.origins.api, public: runtime.origins.console } });
    requireCheck(proof.lm708Digest === manifest.digest && proof.phases.join(',') === 'both-denied,a-admitted-b-denied,both-denied-again,restored', 'L2.04-admission-restored');
  }
  const denial = await runtime.denialControls();
  requireCheck(receiptMatches(denial.j04L1, runtime, 'J04.L1', positives > 0 ? 'capability' : 'absence'), 'L2.05-j04-binding');
  const controls = ['anonymous', 'removed', 'revoked', 'deactivated', 'downgraded'];
  for (const tenant of ['A', 'B'] as const) for (const auth of ['session', 'key'] as const) for (const control of controls) {
    const rows = denial.observations.filter(row => row.control === control && row.tenant === tenant && row.auth === auth);
    requireCheck(rows.length > 0 && rows.every(row => row.status === row.expectedStatus && row.staleAuthorityUses === 0
      && (control === 'downgraded' && positives > 0 ? row.status === 200
        : row.usableConsoleClaims === 0 && ([401, 403].includes(row.status)
          || (control === 'non-admitted' || control === 'downgraded') && row.status === 200 && row.omittedClaimSignatureVerified))), 'L2.05-denied-stale');
  }
  // F is not a substitute for A/B. Conversely, denied A/B controls cannot
  // substitute for fresh-F denial in rehearsal or all-org withdrawal.
  for (const tenant of new Set(runtime.identities.filter(row => !row.admitted).map(row => row.tenant))) {
    for (const auth of ['session', 'key'] as const) {
      const rows = denial.observations.filter(row => row.tenant === tenant && row.auth === auth && row.control === 'non-admitted');
      requireCheck(rows.length > 0 && rows.every(row => row.status === row.expectedStatus && row.usableConsoleClaims === 0
        && row.staleAuthorityUses === 0 && ([401, 403].includes(row.status)
          || row.status === 200 && row.omittedClaimSignatureVerified)), 'L2.02-fresh-denial');
    }
  }
  const faults = await runtime.capabilityFaults();
  for (const tenant of ['A', 'B'] as const) for (const source of ['capabilities', 'exchange'] as const) for (const state of ['loading', 'error'] as const) {
    const rows = faults.filter(row => row.tenant === tenant && row.source === source && row.state === state);
    requireCheck(rows.length === 1 && rows[0].priorPositive && !rows[0].admittedUI && rows[0].usableClaims === 0 && rows[0].restored, 'L2.09-fail-closed');
  }
  if (positives > 0) for (const state of ['disabled', 'capacity-full'] as const) {
    const repeated: PositiveCell[] = [];
    const capacity = await runtime.withSandboxState(state, async () => {
      for (const identity of runtime.identities.filter(row => row.admitted)) repeated.push(...await positiveConsole(runtime, seams, walk, identity));
    });
    requireCheck(capacity.state === state && capacity.creationRefused && capacity.restored
      && samePositiveCells(positiveCells, repeated), 'L2.08-independent');
  }
  requireCheck(runtime.binding.phase === 'WITHDRAWN' ? positives === 0 : positives > 0, 'L2-positive-not-vacuous');
  // A one-tenant rehearsal may be a valid census, but is incomplete mandatory
  // core evidence. Count each required principal's frame AND both exchanges;
  // neither another tenant's denial nor the helper's A-positive fills a cell.
  progress.corePassed = progress.matrixComplete;
  // Separate disposition: a failed independent product census must not turn
  // actual Console core success into an S0 instruction. Full-leg PASS still fails.
  requireReaders(runtime, ['otherProducts']);
  const products = await runtime.otherProducts();
  for (const tenant of ['A', 'B', 'F'] as const) for (const product of ['studio', 'deployments', 'sandboxes', 'hub', 'home']) {
    const rows = products.filter(row => row.tenant === tenant && row.product === product);
    requireCheck(rows.length === 1 && (!rows[0].expectedAllowed || rows[0].declared)
      && [rows[0].capabilityAllowed, rows[0].backendAllowed, rows[0].uiAllowed, rows[0].deepLinkAllowed,
        rows[0].searchAllowed, rows[0].paletteAllowed].every(value => value === rows[0].expectedAllowed), 'L2.07-independent-products');
  }
  return positives;
}

async function publicWall(runtime: J00Runtime, seams: CoreSeams, scopes: ScopeActors, walk: Walk): Promise<WallCell[]> {
  localWallReds();
  const rows: WallCell[] = [];
  // A/B sessions must work on FA first: bad auth is not tenancy evidence.
  for (const actor of [scopes.member, scopes.orgBMembers[0]]) {
    const info = await scopes.send<{ id: string; organization: { id: string } }>(actor, 'GET', '/accounts/user-info/');
    requireCheck(info.status === 200 && info.body.id === actor.userId && info.body.organization.id === actor.organizationId, 'L3.01-session-positive');
  }
  for (const caller of ['anonymous', 'A', 'B'] as const) {
    const actor = caller === 'anonymous' ? undefined : caller === 'A' ? scopes.member : scopes.orgBMembers[0];
    // Separate contexts: the anonymous arm never inherits a session's cookies.
    const context = actor ? await walk.open(actor, 'j00-wall-member') : await walk.anonymous('j00-wall-anonymous');
    try {
      for (const path of PUBLIC_PATHS) {
        const response = await seams.publicIngressStatus(context.request, runtime.origins.public, path, actor);
        rows.push({ caller, path, status: response.status });
        requireCheck(response.status === 404, 'L3.01-exact-404');
      }
    } finally { await context.close(); }
  }
  requireCheck(publicWallMatches(rows), 'L3.01-complete-matrix');
  return rows;
}

async function routeCensus(runtime: J00Runtime, seams: CoreSeams, manifest: LM708, edgeServed: boolean): Promise<void> {
  requireReaders(runtime, ['readRoutingEvidence']);
  const evidence = await runtime.readRoutingEvidence();
  if (!edgeServed) {
    requireCheck(evidence.publicNames.length > 0 && new Set(evidence.publicNames).size === evidence.publicNames.length, 'L3.08-inventory');
    for (const name of evidence.publicNames) {
      const dns = evidence.dns.filter(row => row.name === name), ingress = evidence.ingress.filter(row => row.name === name);
      requireCheck(dns.length === 1 && ingress.length === 1 && dns[0].resolvesToEE === false && ingress[0].routesToEE === false, 'L3.08-no-ee');
    }
    return;
  }
  requireReaders(runtime, ['readRouteCopies']);
  if (!Array.isArray(runtime.identities) || !['A', 'B', 'F'].every(tenant => runtime.identities.some(row => row.tenant === tenant)))
    throw new ConsoleCoreBlocked('J00_ROUTE_PHASE_TENANTS');
  const copies = seams.compareRouteManifests(await runtime.readRouteCopies(), manifest);
  requireCheck(copies.lm708Digest === manifest.digest && new Set(Object.values(copies.digests)).size === 1, 'L3.07-copies');
  // Byte parity does not authorize the observation's route/family expectations.
  // The pinned producer lacks this contract: stop rather than let omission from
  // both observation arrays or an undeclared family withdrawal certify itself.
  requireJ00ReviewedRouteInventory();
  requireCheck(evidence.requiredHidden.length > 0 && new Set(evidence.requiredHidden).size === evidence.requiredHidden.length
    && evidence.hidden.length === evidence.requiredHidden.length && new Set(evidence.hidden.map(row => row.id)).size === evidence.hidden.length
    && evidence.requiredHidden.every(id => evidence.hidden.some(row => row.id === id)), 'L3.02-complete-inventory');
  requireCheck(['learning', 'version-evaluation'].every(family => evidence.requiredHidden.some(id => id === family || id.startsWith(`${family}/`))), 'L3.05-hidden');
  for (const identity of runtime.identities) {
    const client = await seams.HostAssertionClient.open({ edge: runtime.origins.console }, { kind: 'session', actor: identity.actor });
    try {
      for (const route of evidence.hidden) {
        requireCheck(route.expected.status === 404, 'L3.02-exact-404-contract');
        await client.assert('edge', route.path, route.expected, { method: route.method });
      }
    } finally { await client.dispose(); }
  }
  if (runtime.binding.phase === 'WITHDRAWN') {
    requireCheck(receiptMatches(evidence.withdrawnBaseline, runtime, 'J00.L3.WITHDRAWN', 'absence')
      && evidence.withdrawnBaseline?.unchanged === true, 'L3.03-unchanged-baseline');
  } else {
    requireCheck(evidence.enabledFamilies.length > 0 && new Set(evidence.enabledFamilies).size === evidence.enabledFamilies.length, 'L3.04-enabled-inventory');
    for (const family of evidence.enabledFamilies)
      requireCheck(receiptMatches(evidence.familyReceipts[family], runtime, family, 'capability'), 'L3.04-executed-family');
    // Complete packet inventory must be represented as enabled or hidden.
    for (const family of FAMILY_INVENTORY) requireCheck(evidence.enabledFamilies.includes(family)
      || evidence.requiredHidden.some(id => id === family || id.startsWith(`${family}/`)), 'L3.04-family-inventory');
  }
  const phaseFamilies = [...new Set([...FAMILY_INVENTORY, ...evidence.enabledFamilies])];
  for (const tenant of ['A', 'B', 'F', 'anonymous'] as const) for (const family of phaseFamilies) {
    const rows = evidence.phaseCases.filter(row => row.tenant === tenant && row.family === family);
    requireCheck(rows.length > 0 && rows.some(row => !row.authorized)
      && (tenant === 'anonymous' || rows.some(row => row.authorized)), 'L3.06-phase-inventory');
    for (const row of rows) {
      requireCheck(row.declared === evidence.enabledFamilies.includes(family), 'L3.06-declared-family');
      const admitted = runtime.binding.phase === 'TUESDAY_GA' || (runtime.binding.phase === 'REHEARSAL' && tenant !== 'F'
        && runtime.identities.some(identity => identity.tenant === tenant && identity.admitted));
      const allowed = tenant !== 'anonymous' && admitted && row.authorized && row.declared;
      requireCheck(allowed ? row.status >= 200 && row.status < 300
        : [401, 403, 404].includes(row.status), 'L3.06-phase-status');
      requireCheck(row.status === row.expectedStatus, 'L3.06-route-contract');
    }
  }
}

function localConsoleOriginWallReds(): void {
  // Offline provenance self-check (copied vectors vs source-file sha256) plus the
  // L3.09 mutants, over the SAME evaluator the live cells use. No socket is opened.
  const broken = localConsoleOriginReds();
  requireCheck(broken.length === 0, broken[0] ?? 'L3.09-local');
}
type ConsoleOriginProgress = { status: 'NOT_RUN' | 'NOT_APPLICABLE' | 'PASSED' | 'FAILED' | 'BLOCKED'; passed: boolean;
  cells: ConsoleOriginCellReceipt[] };
async function consoleOriginWall(runtime: J00Runtime, progress: ConsoleOriginProgress): Promise<void> {
  try {
    // Stage A-bound listener origin ONLY; never origins.console, consoleUrl or an env default.
    const bound: unknown = runtime.binding.consoleOrigin;
    const origin = consoleOriginBindingUrl(bound);
    if (!origin) throw new ConsoleCoreBlocked('J00_CONSOLE_ORIGIN_BINDING');
    // Same rig rule as J00-origin-binding; it can never be the FA public or API origin.
    requireCheck(isLocalOrigin(origin) && origin !== runtime.origins.public && origin !== runtime.origins.api, 'L3.09-origin-binding');
    // Raw targets go over node:net (see rawExchange); no TLS client, no URL-based fallback.
    if (new URL(origin).protocol !== 'http:') throw new ConsoleCoreBlocked('J00_CONSOLE_ORIGIN_RAW_TRANSPORT');
    // App listener (source 445-471): its OWN Stage A-bound origin, never origins.public/api or
    // an env default. Unbound/malformed => its cells are BLOCKED value-free inside the matrix.
    const appListener = consoleOriginAppListener(bound).url;
    if (appListener !== null) requireCheck(isLocalOrigin(appListener) && appListener !== origin, 'L3.09-app-listener-binding');
    // Bearer values for the listener API cases are read from the binding at send time
    // only; cells, verdicts and receipts never carry them.
    const input = { binding: bound, publicOrigin: runtime.origins.public };
    const observations = await runConsoleOriginMatrix(input, planConsoleOriginMatrix(bound));
    const verdict = evaluateConsoleOriginMatrix(input, observations);
    progress.cells = verdict.cells;
    // FAILED dominates BLOCKED: an unbound asset/document/credential never masks a defect.
    requireCheck(verdict.failures.length === 0, verdict.failures[0] ?? 'L3.09');
    if (verdict.blocked.length > 0) throw new ConsoleCoreBlocked(verdict.blocked[0]);
    requireCheck(verdict.passed, 'L3.09-console-origin-matrix');
    progress.status = 'PASSED';
    progress.passed = true;
  } catch (error) {
    progress.status = error instanceof ConsoleCoreBlocked ? 'BLOCKED' : 'FAILED';
    throw error;
  }
}

async function boundConsoleLeg(leg: 'L2' | 'L3', scopes: ScopeActors, walk: Walk, browserName: string,
  testInfo: TestInfo, unchangedAbsence: () => Promise<void>): Promise<void> {
  let runtime: J00Runtime | undefined, lmDigest: string | null = null;
  let branch: 'capability' | 'absence' | null = null;
  const admission: AdmissionProgress = { positiveCells: [], matrixComplete: false, corePassed: false };
  let wall: WallCell[] = [], retainedWallPassed = false, completed = false, cleanupFailed = false;
  const consoleOrigin: ConsoleOriginProgress = { status: 'NOT_RUN', passed: false, cells: [] };
  let failure: ConsoleCoreBlocked | J00Assertion | undefined;
  try {
    if (!ENGINES.includes(browserName as Engine)) throw new ConsoleCoreBlocked('J00_BROWSER_ENGINE');
    localReceiptReds();
    localPositiveMatrixReds();
    localConsoleOriginWallReds();
    await localNestedBlockReds();
    await localDisposalReds();
    const seams = await loadConsoleCoreSeams();
    runtime = await requireJ00Runtime({ scopes, browser: browserName as Engine, testInfo });
    requireReaders(runtime, ['dispose']);
    if (isDigest(runtime.lmDigest)) lmDigest = runtime.lmDigest;
    const binding = runtime.binding;
    if (!binding || !PHASES.includes(binding.phase) || binding.browser !== browserName
      || !Array.isArray(binding.qualifiedEngines) || !ENGINES.every(engine => binding.qualifiedEngines.includes(engine)))
      throw new ConsoleCoreBlocked('J00_PHASE_BROWSER_BINDING');
    if (![runtime.lmDigest, binding.compositionDigest, binding.imageDigest, binding.configDigest,
      binding.stageBDerivationDigest, binding.runtimeAcceptanceDigest].every(isDigest)
      || binding.stageAExportDigest !== CJ_STAGE_A.exportDigest) throw new ConsoleCoreBlocked('J00_STAGE_A_B_BINDING');
    requireCheck(Object.values(runtime.origins).every(isLocalOrigin) && runtime.origins.public === E2E.appUrl
      && runtime.origins.api === E2E.apiUrl && runtime.origins.console !== runtime.origins.public
      && runtime.origins.console !== runtime.origins.api, 'J00-origin-binding');
    const consoleUrl = new URL(runtime.consoleUrl);
    requireCheck(!consoleUrl.username && !consoleUrl.password && !consoleUrl.hash
      && consoleUrl.origin === runtime.origins.console && consoleUrl.pathname === '/omega/console/'
      && consoleUrl.search === '?embed=1', 'L2.04-console-url');
    if (!scopes.orgBMembers[0]) throw new ConsoleCoreBlocked('J00_TENANT_B_MEMBER');
    let manifest: LM708;
    try {
      manifest = seams.readLM708(runtime.lmPath, runtime.lmDigest);
      seams.bindLM708(manifest, runtime.stack);
      // Applies to L2 AND L3, including absence. Supplied phase metadata and a
      // matching server label do not resolve the LM's effective rollout flags.
      requireJ00PhaseRolloutBinding();
      branch = seams.evaluateApplicability(leg === 'L2' ? 'served:omega-shell' : 'served:omega-edge', manifest, runtime.stack, runtime.tuples);
    } catch (error) {
      if (error instanceof ConsoleCoreBlocked) throw error;
      throw new ConsoleCoreBlocked('HR_CJ_01_LM_APPLICABILITY_BINDING');
    }
    if (leg === 'L2' && branch === 'capability') {
    if (!Array.isArray(runtime.identities) || !['A', 'B', 'F'].every(tenant => runtime!.identities.some(row => row.tenant === tenant)))
      throw new ConsoleCoreBlocked('J00_TWO_TENANTS_AND_FRESH_ORG');
    const identities = runtime.identities;
    requireCheck(identities.every(row => ['A', 'B', 'F'].includes(row.tenant))
      && new Set(identities.map(row => positivePrincipal(row).principal)).size === identities.length, 'L2.01-distinct-principals');
    for (const tenant of ['A', 'B', 'F'] as const) {
      const rows = identities.filter(row => row.tenant === tenant);
      requireCheck(new Set(rows.map(row => row.actor.organizationId)).size === 1, 'L2.01-tenant-consistency');
      if (tenant !== 'F') requireCheck(['owner', 'member'].every(role => rows.some(row => row.role === role)), 'L2.01-role-inventory');
    }
    requireCheck(new Set(['A', 'B', 'F'].map(tenant => identities.find(row => row.tenant === tenant)!.actor.organizationId)).size === 3, 'L2.01-distinct-orgs');
    requireCheck(identities.some(row => row.tenant === 'A' && row.actor.organizationId === scopes.ownerA.organizationId)
      && identities.some(row => row.tenant === 'B' && row.actor.organizationId === scopes.ownerB.organizationId), 'J00-scope-fixture-binding');
    for (const identity of identities) {
      if (!identity.personalKey?.apiKey || !identity.personalKey.secretKey || !identity.actor.tokens.access || !identity.actor.userId)
        throw new ConsoleCoreBlocked('J00_PERSONAL_KEY_FIXTURE');
      requireCheck(identity.personalKey.kind === 'key' && identity.keyOwnerId === identity.actor.userId
        && identity.personalKey.organizationId === identity.actor.organizationId && identity.personalKey.workspaceId === identity.actor.workspaceId, 'L2.04-key-owner');
    }
    }
    if (leg === 'L3') {
      // SECURITY ABSENCE in BOTH arms. No admission predicate guards this wall.
      wall = await publicWall(runtime, seams, scopes, walk);
      await unchangedAbsence(); // Retain every original authenticated exact-404 assertion.
      // Set only after the entire retained wall returns without hard OR soft
      // failures. Later ordinary family failures do not erase this security proof.
      retainedWallPassed = testInfo.errors.length === 0;
      requireCheck(retainedWallPassed, 'L3.01-retained-wall');
      // The dedicated Console-origin listener exists only where the edge is served;
      // the absent arm's proof is L3.08 DNS/ingress, never a BLOCKED listener matrix.
      if (branch === 'capability') await consoleOriginWall(runtime, consoleOrigin);
      else consoleOrigin.status = 'NOT_APPLICABLE';
      await routeCensus(runtime, seams, manifest, branch === 'capability');
    } else if (branch === 'absence') {
      await unchangedAbsence(); // Original capability + runtime + five UI denials, unchanged.
      requireReaders(runtime, ['shellAbsence']);
      const absence = await runtime.shellAbsence();
      requireCheck(absence.registeredOmegaRoutes === 0 && absence.usableConsoleClaims === 0 && absence.switchers === 0, 'L2.10-absence');
    } else {
      // Only resolved, observed admission may enter a positive branch.
      await consoleAdmission(runtime, seams, manifest, walk, admission);
    }
    requireCheck(testInfo.errors.length === 0, 'J00-no-soft-failures');
    completed = true;
  } catch (error) {
    failure = error instanceof ConsoleCoreBlocked || error instanceof J00Assertion ? error : new J00Assertion('J00-execution');
  } finally {
    const cleanupFailure = await cleanupRuntime(runtime);
    if (cleanupFailure) { failure = cleanupFailure; completed = false; cleanupFailed = true; }
    await testInfo.attach(`j00-${leg}-console-core`, { contentType: 'application/json', body: JSON.stringify({
      schema: 'j00-console-core-authoring/v1', leg, lm708Digest: lmDigest,
      phase: runtime?.binding?.phase && PHASES.includes(runtime.binding.phase) ? runtime.binding.phase : null,
      browser: ENGINES.includes(browserName as Engine) ? browserName : null,
      compositionDigest: isDigest(runtime?.binding?.compositionDigest) ? runtime!.binding.compositionDigest : null,
      stageAExportDigest: CJ_STAGE_A.exportDigest, branch,
      ...legOutcome(failure, completed),
      capabilityPassed: leg === 'L2' && admission.corePassed && !cleanupFailed && testInfo.errors.length === 0,
      securityAbsencePassed: leg === 'L3' && securityAbsenceMatches(wall, retainedWallPassed, cleanupFailed),
      fullLegPassed: completed && !failure, exchangeCells: admission.positiveCells.length,
      // Role/person/organization values remain private. An integer slot joins
      // both auth methods for the same in-memory fixture principal.
      positiveMatrix: admission.positiveCells.map(cell => ({
        tenant: cell.tenant, principalSlot: admission.positiveCells.findIndex(row => row.principal === cell.principal),
        auth: cell.auth, framePassed: cell.framePassed, exchangePassed: cell.exchangePassed,
      })),
      coreMatrixComplete: admission.matrixComplete,
      retainedPublicOriginPassed: retainedWallPassed, publicOriginMatrix: wall,
      // Separate from securityAbsencePassed (public origin). Cells are value-free:
      // a status appears only on cells main ruled 'allow' (recorded, never asserted).
      consoleOriginWallPassed: leg === 'L3' && consoleOrigin.passed && !cleanupFailed,
      consoleOriginMatrix: leg === 'L3' ? { source: CONSOLE_ORIGIN_SOURCE.ref, vectorSha256: CONSOLE_ORIGIN_SOURCE.vectorSha256,
        listenerVectorSha256: CONSOLE_ORIGIN_SOURCE.listenerVectorSha256, appListenerVectorSha256: CONSOLE_ORIGIN_SOURCE.appListenerVectorSha256,
        status: consoleOrigin.status, cells: consoleOrigin.cells } : null,
      imageDigest: isDigest(runtime?.binding?.imageDigest) ? runtime!.binding.imageDigest : null,
      configDigest: isDigest(runtime?.binding?.configDigest) ? runtime!.binding.configDigest : null,
      requiredEngines: ENGINES, requiredPhases: PHASES, matrixComplete: false, releaseAccepted: false,
      documentedRedControls: Object.keys(J00_REDS).filter(id => id.startsWith(`${leg}.`)),
      liveRedRestoredQualified: false,
    }) });
  }
  if (failure) throw failure;
}

const legs = [
  {
    id: 'L1', title: 'Gateway default-OFF census W00', applies_when: 'served:gateway',
    flags_required: ['AGENTCC_MANAGED_MODELS_ON_FOR_NEW_ORGS unset', 'managed_models.enabled',
      'budgets.admission_caps_enabled', 'privacy.trace_content', 'mcp.search', 'public_price_feed.enabled'],
    steps: ['Run the existing W00 once on every gateway profile in use, in Chromium and Firefox with separate org sets',
      'Before any console action, every A role reads Models, Billing, Budgets, MCP, Security, virtual servers, Integrations, Guardrails and the K2 catalogue',
      'Read the public price route anonymously; Owner creates the first key in the UI; repeat the census for every role at 1440/390 px in light/dark'],
    pass_criteria: ['Before the key: no agentcc_org_config for A or B; after: exactly zero active A versions and null gateway mirror on default-off stock, exactly one where AGENTCC_MANAGED_MODELS_ON_FOR_NEW_ORGS is declared (708 add.32); budgets.admission_caps_enabled and privacy.trace_content absent or false, mcp.search absent, no servers or virtual_servers',
      'GatewayAdmin.orgConfig(A) mirrors the saved config; no managed credit account, MCP credential/connect/approval, tool call, integration, credit entry, cap activation or trace_content_changed audit row for A',
      'Redis has no `mc:{A}:*` and A is not in mc:outbox_orgs; /-/config public_price_feed.enabled is false and the public feed refuses',
      'Each exact CAT-2 ID is absent or blocked with every input/output/billed price null in both catalogues; credits requests refused before upstream calls (708 add.38); org B rows and Redis state byte-identical before/after'],
    if_absent: null,
    gap: 'HR-JRN-03 accepted at 3ddeb78b3a: consume w00-result-v2 through the per-browser census dependency; absent/stale/mismatched/partial evidence fails. Pre-708i composition uses v2 per 708 add.41 placement. HR-JRN-01 separately blocks provisioning until accepted value-free publication; L8-L10 cover virtual servers, connectors and guardrail history',
  },
  {
    id: 'L2', title: 'Phase-aware Console admission and dual-credential exchange', applies_when: 'served:omega-shell',
    flags_required: ['VITE_OMEGA_CONSOLE (runtime config)', 'console rollout stage', 'studio_tab rollout stage',
      'sandboxes rollout stage', 'deployments rollout stage', 'hub rollout stage', 'OMEGA_RELEASED_PARTS'],
    steps: ['Bind resolved LM, phase, running stack, approved parts and all three browser engines; read fresh F and existing A/B capabilities',
      'REHEARSAL denies F and positively exercises named admitted tenants; WITHDRAWN keeps every org at S0; TUESDAY_GA admits all orgs without an allowlist',
      'Open the exact-origin dedicated Console frame and exchange by browser session AND personal key; verify current org/person/role and consume on edge',
      'Exercise deep links/search/palette, J04.L1 denied/stale controls, loading/errors, independent products, and disabled/full sandbox states'],
    pass_criteria: ['Positive admission/session exchange for the evaluated org scope, including fresh F on Tuesday; never denial-only or studio-only core evidence',
      'No unapproved omega_part_*; denied/revoked/stale authority refuses; Console does not depend on sandbox admission/capacity; other products retain independent gates',
      'Loading/errors fail closed; genuine shell absence is withdrawal evidence only, never capabilityPassed'],
    if_absent: {
      steps: ['Verify runtime VITE_OMEGA_CONSOLE unset/false or no sections/omega build',
        'Open all five Omega dashboard paths and read capabilities; no Omega switcher or Agent infrastructure nav'],
      pass_criteria: 'Plain FA 404 for each Omega path; no omega_* capability or only allowed:false; no Omega route registered or product admitted for any org',
    },
    gap: 'HR-JRN-04: require runtime/build identity and product-admission readers, plus controlled loading/error fixture for the capability gate. The absent branch must run on T-DARK rather than treating shell-not-served as a skip',
  },
  {
    id: 'L3', title: 'Unconditional public-origin 404 and dedicated-edge route census', applies_when: 'served:omega-edge',
    flags_required: ['console-route-manifest hidden list', 'console rollout stage'],
    steps: ['In EVERY phase, edge served AND absent, require exact public-origin 404 for three /omega paths as anonymous and A/B; never follow redirects',
      'Enumerate the complete reviewed hidden/undeclared method-path inventory; join unchanged WITHDRAWN and nonzero enabled-family positives separately',
      'Assert dedicated-edge phase/admission, independently compare three deployed manifests with LM, and census absent-arm DNS/ingress'],
    pass_criteria: ['Public-origin exact 404 is mandatory SECURITY ABSENCE, never a capability walk or an authentication refusal',
      'No WITHDRAWN/D-2 receipt substitution; all enabled families execute positives; learning/version-evaluation remain hidden',
      'Every manifest copy matches the same arm LM; no public-origin name routes/resolves to EE in the absent arm'],
    if_absent: {
      steps: ['As a signed-in member of each org, GET /omega/api/workspace/capabilities and /omega/console/ on the public ingress',
        'Inspect LM-708 public DNS and ingress names without contacting production'],
      pass_criteria: 'Every request is 404 for every org; no public name resolves to an EE host',
    },
    gap: 'HR-JRN-05: require public-ingress authenticated status reader (including non-JSON 404), manifest-copy reader and isolated DNS/ingress inventory. A Django API 404 alone cannot prove the edge or DNS absent',
  },
  {
    id: 'L4', title: 'Shared release line', applies_when: 'served:ee-daemon',
    flags_required: ['OMEGA_RELEASED_PARTS', 'FA PEER_PARTS'],
    steps: ['Read FA and EE OMEGA_RELEASED_PARTS and FA logs after union regeneration'],
    pass_criteria: ['Values equal the approved list (or both empty); no unknown-name log for console_emergency_stop, agent_ownership or console_otlp'],
    if_absent: {
      steps: ['Read OMEGA_RELEASED_PARTS on FA'],
      pass_criteria: 'Only FA-owned approved parts; no EE-owned name',
    },
    gap: 'HR-JRN-06: require read-only server release-env, PEER_PARTS and redacted unknown-part log readers plus approved part ownership from LM-708; client env is not server evidence',
  },
  {
    id: 'L5', title: 'DSX and Results default state', applies_when: 'always',
    flags_required: ['VITE_DSX_EXPERIMENTS_BASIC', 'MADE_REAL registry', 'Results unavailable-path enforcement'],
    steps: ['In a non-prototype build with VITE_DSX_EXPERIMENTS_BASIC unset, require experimentsBasic ON, dataset.splits and exp.new.splitScope shown in both hosted and self-host builds',
      'Require exp.new, dataset.tab.experiments and dataset.experimentDefaults shown by default; in a separate explicit false build require these NEW surfaces off while classic creation, the classic list and classic Data/Summary pages stay visible',
      'Request Results screens and unavailable backend paths as the fresh org'],
    pass_criteria: ['Both split surfaces shown regardless of experimentsBasic; experimentsBasic defaults ON and only explicit false disables it; classic experiment creation/list/pages visible even with explicit false; only registry-backed DSX surfaces visible; no unregistered DSX surface or Results screen reachable'],
    if_absent: null,
    gap: 'HR-JRN-07: require deployed non-prototype build/registry and frontend contract-exception readers. These are unconditional checks even on a profile without Results; source-code absence is not runtime proof',
  },
  {
    id: 'L6', title: 'Hosted sandboxes and deployments withheld', applies_when: 'always',
    flags_required: ['hosted_sandboxes', 'hosted_deployments'],
    steps: ['Read FA readiness and capabilities for every seeded org'],
    pass_criteria: ['hosted_sandboxes and hosted_deployments undeclared; sandboxes/deployments denied for every org by service.py `_hosted_withheld`'],
    if_absent: null,
    gap: 'HR-JRN-04: require hosted readiness/declaration reader and capability-envelope contract for both orgs. Withheld capabilities must be asserted, not assumed from the standalone profile',
  },
  {
    id: 'L7', title: 'Engine isolation defaults', applies_when: 'served:ee-sandbox-engine',
    flags_required: ['OMEGA_WORKSPACE_DEPLOY_NETNS', 'OMEGA_WORKSPACE_SANDBOX_NETNS'],
    steps: ['Read both netns switches in the running engines and helper images in each engine image store',
      'Try an engine start with either switch off on disposable staging'],
    pass_criteria: ['Both switches ON; omega-deployment-egress:netns-v1 and omega-sandbox-egress:local present; either switch off refuses start'],
    if_absent: {
      steps: ['On hosted, ask EE for a sandbox if EE is served; list host processes'],
      pass_criteria: 'OMEGA_CLOUD_ONLY refuses with this server runs no local sandboxes, or EE is absent; no sandbox/deploy engine runs',
    },
    gap: 'HR-JRN-08: require read-only engine process/image/env inventory and disposable switch-off start control; absence requires a process census as well as HTTP refusal',
  },
] as const;

for (const leg of legs) {
  // The existing catalog rejects duplicate IDs. Keep the allocated journey ID
  // as the prefix and attach a leg suffix, rather than dropping @flow from six
  // tests or changing the shared catalog. HR-JRN-10 requests native leg support.
  const id = `${FLOW}.${leg.id}`;
  if (leg.id === 'L1') {
    test(`${id}: J00.L1 ${leg.title}`, {
      tag: ['@flow', '@journey', '@journey-J00'],
      annotation: [flowAnnotation({ id, area: 'journeys', userGoal: 'Consume the same-profile W00 default-OFF census without repeating it',
        steps: ['Read the single W00 result bound to this run, profile, browser and running stack'],
        backendChecks: [...leg.pass_criteria, leg.gap] }),
        { type: 'journey-flow-id', description: FLOW },
        { type: 'journey-contract', description: JSON.stringify({ journey: 'J00', ...leg }) }],
    }, async ({ browserName }, testInfo) => {
      test.setTimeout(120_000);
      // Accepted HR-JRN-03 interface at 3ddeb78b3a. No helper-presence skip,
      // dynamic import, fallback binding, or catch of the reader's refusals.
      if (browserName !== 'chromium' && browserName !== 'firefox') throw new Error('W00_RESULT_BROWSER');
      const binding = currentCensusBinding(browserName);
      const result = readCensusResult(binding);
      expect(result.schema).toBe('w00-result-v2');
      expect(result.ui).toHaveLength(224);
      expect(result.backend).toHaveLength(2);
      for (const phase of ['before-key', 'after-key'] as const) {
        const rows = result.backend.filter((row: { phase: string }) => row.phase === phase);
        expect(rows).toHaveLength(1);
        const expected = phase === 'after-key' && binding.newOrgDefault ? 1 : 0;
        expect(rows[0].configRows).toBe(expected);
        expect(rows[0].activeVersions).toBe(expected);
        expect(rows[0].mirrorNull).toBe(expected === 0);
        for (const lane of ['api', 'gateway'] as const) {
          const observations = rows[0].catalogues.cat2[lane];
          expect(observations.map((row: { id: string }) => row.id).sort()).toEqual([...CAT2_IDS].sort());
          expect(observations.every(cat2ObservationIsOff), 'CAT-2 absent or blocked and unpriced').toBe(true);
        }
      }
      expect(result.credits).toHaveLength(34);
      expect(result.credits.every(cat2CreditsRefused), 'CAT-2 refused before upstream').toBe(true);
      expect(result.backend[0].orgBSnapshot).toBe(result.backend[1].orgBSnapshot);
      await testInfo.attach('w00-consumed', { contentType: 'application/json',
        body: JSON.stringify({ digest: result.digest, orgSet: result.orgSet, binding: result.binding }) });
    });
    continue;
  }
  test(`${id}: J00.${leg.id} ${leg.title}`, {
    tag: ['@flow', '@journey', '@journey-J00'],
    annotation: [
      flowAnnotation({ id, area: 'journeys', userGoal: `Release obligation: ${leg.title}`,
        steps: leg.steps.map(step => `Full release contract (includes owed checks): ${step}`), backendChecks: [
          ...(['L2', 'L3'].includes(leg.id)
            ? ['Phase-bound Console bodies: missing composed seams are BLOCKED/NOT_RUN; publication remains gated; three-engine matrix is required']
            : ['L5', 'L6'].includes(leg.id) ? ['Executable rehearsal: authenticated capability/HTTP denial checks'] : []),
          `Full-contract dependency (never counted as green): ${leg.gap}`,
          ...leg.pass_criteria.map(check => `Release criterion: ${check}`),
        ] }),
      { type: 'journey-flow-id', description: FLOW },
      { type: 'journey-contract', description: JSON.stringify({ journey: 'J00', ...leg }) },
    ],
  }, async ({ scopeActors: scopes, request, walk, browserName }, testInfo) => {
    test.setTimeout(120_000);
    if (!['L2', 'L3', 'L5', 'L6'].includes(leg.id)) test.skip(true, leg.gap);
    // Keep the original denial assertions byte-for-byte. L2 calls these only
    // after evaluated shell absence; L3 calls its unconditional 404 checks in
    // both served/absent arms. Missing applicability is BLOCKED, never a skip.
    const unchangedAbsence = async () => {
    const actors = [scopes.ownerA, scopes.member, scopes.ownerB, ...scopes.orgBMembers];
    for (const [orgActor, actor] of actors.entries()) {
      const caps = await scopes.send<Capabilities>(actor, 'GET', '/api/capabilities/');
      expect(caps.status, 'authenticated capability read').toBe(200);
      expect(Object.keys(caps.body.features).length > 0, 'nonempty capability registry positive control').toBe(true);
      if (leg.id === 'L2' || leg.id === 'L6') {
        const entries = Object.entries(caps.body.features).filter(([name]) => leg.id === 'L2'
          ? name.startsWith('omega_')
          : ['omega_sandboxes', 'omega_deployments', 'omega_part_hosted_sandboxes', 'omega_part_hosted_deployments',
            'hosted_sandboxes', 'hosted_deployments'].includes(name));
        expect.soft(entries.every(([, decision]) => decision.allowed === false),
          `${leg.id} no forbidden capability admitted`).toBe(true);
        await testInfo.attach(`j00-${leg.id}-capabilities-${orgActor}`, { contentType: 'application/json',
          body: JSON.stringify({ org: fingerprint(actor.organizationId), checked: entries.length,
            denied: entries.every(([, decision]) => decision.allowed === false) }) });
      }
      if (leg.id === 'L3') {
        // These are public routes, not a check against the Django router alone.
        // request fixture retains non-JSON status; ScopeActors.send parses JSON.
        for (const origin of [E2E.appUrl, E2E.apiUrl]) {
          for (const path of ['/omega/api/workspace/capabilities', '/omega/console/']) {
            const response = await request.get(`${origin}${path}`, { headers: scopes.headers(actor), maxRedirects: 0 });
            expect.soft(response.status(), 'L3 absent edge route must be 404').toBe(404);
          }
        }
      }
    }
    if (leg.id === 'L2') {
      for (const actor of [scopes.ownerA, scopes.ownerB]) {
        const context = await walk.open(actor, 'j00-l2');
        const page = await context.newPage();
        for (const product of ['console', 'studio', 'deployments', 'sandboxes', 'hub']) {
          await page.goto(`/dashboard/${product}`, { waitUntil: 'domcontentloaded' });
          // config-global.js / runtime-config.js injection; flags are read, never patched.
          const shellOn = await page.evaluate(() => {
            const config = (window as unknown as { __FUTURE_AGI_CONFIG__?: Record<string, unknown> }).__FUTURE_AGI_CONFIG__;
            return config?.VITE_OMEGA_CONSOLE === true || config?.VITE_OMEGA_CONSOLE === 'true' || config?.VITE_OMEGA_CONSOLE === '1';
          });
          if (shellOn) {
            expect(testInfo.errors.length, 'L2 capabilities before served-shell dependency').toBe(0);
            throw new ConsoleCoreBlocked('HR_JRN_04_SHELL_ABSENCE_MISMATCH');
          }
          // routes/sections/index.jsx catch-all redirects to /404.
          await expect.soft(page, 'L2 absent Omega UI route').toHaveURL(/\/404\/?$/, { timeout: UI_READY });
          await expect.soft(page.getByText('Agent infrastructure', { exact: true })).toHaveCount(0);
        }
      }
    }
    if (leg.id === 'L5') {
      // JOURNEYS-708 J03.L4 pins these exact unavailable API paths. Use an owned
      // dataset, not a random nonexistent parent (which could hide a live route).
      const created = await scopes.send<{ result: { dataset_id: string } }>(scopes.ownerA, 'POST',
        '/model-hub/develops/create-dataset-manually/',
        { dataset_name: `e2e-j00-l5-${testInfo.workerIndex}-${Date.now().toString(36)}`, number_of_rows: 1, number_of_columns: 1 });
      expect(created.status).toBe(200);
      for (const actor of actors) for (const suffix of ['runs/', 'results/', 'results/analysis/']) {
        const response = await request.get(`${E2E.apiUrl}/model-hub/datasets/${created.body.result.dataset_id}/${suffix}`,
          { headers: scopes.headers(actor), maxRedirects: 0 });
        expect.soft(response.status(), 'L5 unavailable Results API').toBe(404);
      }
    }
    // Never let a partial-contract gap turn an already-observed defect into skip.
    expect(testInfo.errors.length, `${leg.id} executed assertions`).toBe(0);
    if (leg.id === 'L5') test.skip(true, 'HR-JRN-07: Results HTTP matrix executed; deployed registry and UI contract census remain owed');
    if (leg.id === 'L6') test.skip(true, 'HR-JRN-04: capabilities executed for both orgs and members; server-side declared-parts readiness reader remains owed');
    };
    if (leg.id === 'L2' || leg.id === 'L3') {
      await boundConsoleLeg(leg.id, scopes, walk, browserName, testInfo, unchangedAbsence);
    } else {
      await unchangedAbsence();
    }
  });
}
