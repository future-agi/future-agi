// Console core seams: the composed union of Console consumer adapters
// (708J, e2e/oc-cj-union-3e-1031). Each section below keeps its own
// contract as authored; the sections share no runtime state or error class.
//   W3  e2e/cj-cont-744-w3 @ bbc9df1f1b      J03/J04 core consumer (first section).
//   W1  e2e/console-journeys-708 @ 63b3ba5fd3 J16 core consumer (second section).
//   J00 branch @ dbd88c5c57 J00 phase-bound consumer (third section).
// W1 and W3 both defined `resolveSeam` with incompatible contracts (W3: a pinned,
// closure-verified SYMBOL by producer-symbol name; W1: a memoized MODULE by seam
// registry name). Both are kept: W3's keeps the name, W1's is `resolveJ16Seam`
// and console-core-run.spec.ts imports it as `resolveSeam`.
// W3 and J00 both defined `ConsoleCoreBlocked` incompatibly (W3: `code`, a typed
// seam/gap and message `BLOCKED:<seam>`; J00: `status`/`execution` NOT_RUN, a seam
// sanitized to /^[A-Z0-9_]+$/ and message `BLOCKED/NOT_RUN <seam>`). Both are kept:
// W3's keeps the name; J00's is `J00ConsoleCoreBlocked` (its .name is still
// 'ConsoleCoreBlocked') and default-off-census.spec.ts imports it as
// `ConsoleCoreBlocked`. StackIdentity was identical in W3 and J00 and is declared
// once, in the W3 section. Nothing else collides.
//
// ===========================================================================
// W3 (J03/J04 core)
// ===========================================================================
// W3 consumer-only adapter. Resolve composed sources in THIS checkout, never a
// sibling worktree or arbitrary environment module. No replacement HR-CJ client.
import { createHash, randomUUID } from 'node:crypto';
import { existsSync, readFileSync } from 'node:fs';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import type { APIRequestContext, BrowserContext, Page, PlaywrightWorkerOptions, TestInfo } from '@playwright/test';
import { test as walkTest, type Walk } from '../../lib/walk/fixtures';
import { gatewayProfile } from '../../lib/profile';
import { JOURNEY_PUBLICATION_ACCEPTED } from './publication-contract';
import type { ScopeActor, ScopeActors } from '../../lib/scope-actors';
import type { ConsoleOriginBinding } from './console-origin-matrix';

export const STAGE_A = Object.freeze({
  sourceHead: '09fc47c13a60ed17c801d2effee6d38e167db40d',
  exportManifest: '24b82e6e487e3c0ec3f1a7b056f12fd4c62c380f2017b6970f368d7f793798b8',
  baseRoot: 'e1bb3bf147a85d86cccdf2b2eff172c26421961cada3ccc7d56d3852f5d7c5bc',
});
// Export is source-only: dispatchAllowed/runtimeQualified/releaseAccepted=false.
// Exact file bytes inspected at gw/E2E-HR-CJ-1 e0133094d124ce773b219e61f50d5ab26af6a0da.
// Rebinding a source requires review, not a same-named export or environment override.
const pins = {
  lm708: 'bb56874d57de890b827000a0df48f01ff10a0abb139a73a701398d28693b19c0',
  'console-round-trip': 'ab3adcd3e0017480fadee5df73f85f9c340a6643b33847d519e329e318b8f80d',
  'host-assertion': '74d25ee3f2ee599496b6145e9b1370397d724a0e6329376412433a85e59371b5',
  upstream: '19c7ea9e61c5ce8f57fa0a72552d54f8a53023c333bb08a536ded29f8dac9dc2',
  'console-evidence': 'd35524d38474c19a2e04d75228bee8206a3b2ace8acf4d597cdfff2aea0ee097',
  'j04-state-probe': null, // Undelivered G5 reader: even a matching name is not accepted.
} as const;
const sources = {
  readLM708: ['lm708', 'readLM708'],
  bindLM708: ['lm708', 'bindLM708'],
  evaluateApplicability: ['lm708', 'evaluateApplicability'],
  verifyConsoleAdmission: ['console-round-trip', 'verifyConsoleAdmission'],
  HostAssertionClient: ['host-assertion', 'HostAssertionClient'],
  MockUpstream: ['upstream', 'MockUpstream'],
  verifyRunObserve: ['console-evidence', 'verifyRunObserve'],
  readJ04SiblingState: ['j04-state-probe', 'readJ04SiblingState'],
} as const;
// Conservative relative-import closure at the SAME inspected source head,
// including type-only edges. A matching entry file cannot bless divergent
// admission/network/environment helpers. All bytes are checked before import.
const closurePins: Record<string, string> = {
  ...Object.fromEntries(Object.entries(pins).filter(([, hash]) => hash !== null)
    .map(([name, hash]) => [`lib/${name}.ts`, hash!])),
  'lib/console-admission.ts': '10210e27d0bbd9ca490d836dcc1fcb91015d9dffe65efc7c9cc3dfc99db0f516',
  'lib/django-fixture.ts': '50b8e4d5e23dc29db5dda59fbdb817b86092625068ee6223bb00ceabc7e9f1e4',
  'lib/env.ts': '0c2037a797da791c3552df8e7c7b7aed410d9aa92e64a1fa91066029018dc734',
  'lib/scope-actors.ts': 'e7f0053935101fbf48ab81bed5d26df2a48fce5b059003cff86828f403697d82',
  'lib/api-client.ts': 'd8780b277d7bd242e277a7c15f62711b2fbf24af664423231af9cb934ec9f44e',
  'lib/auth.ts': '25c582ae52668a894c51f6d6d801d8c3ea232324ea5307ed7a876f92fec2c3fe',
  'lib/provisioning.ts': 'c74626105610e801104022a8e90a20ac01c7c1ddf9d730d1f5258b9586234b49',
  'lib/state-probe.ts': 'a722ad0735e58bd4cb9668ffec9eba9a09c10d7a0c1e3976f8367e4537ebecfd',
  'lib/public-ingress.ts': 'a0ea25db7263dc2a0a26187e35d1296068b9e6af6e7f4de14386f6eeace4fae3',
  'lib/public-network.ts': 'd69a635834d2d124d50b9e9c5e9349fcd5de0d4a65f4f977ee8fa264746c10fb',
  'scripts/fa-edge-ee-profile.mjs': 'fbc55729430c3fadcd9ae94c7f725850902e831bca5b8e0987b771da1971e7bc',
};
const relativeImports: Record<string, readonly string[]> = {
  'lib/lm708.ts': [], 'lib/console-evidence.ts': [], 'lib/django-fixture.ts': [],
  'lib/api-client.ts': [], 'lib/auth.ts': [], 'lib/public-network.ts': [], 'scripts/fa-edge-ee-profile.mjs': [],
  'lib/console-round-trip.ts': ['lib/console-admission.ts', 'lib/host-assertion.ts', 'lib/lm708.ts', 'lib/public-ingress.ts', 'lib/scope-actors.ts'],
  'lib/console-admission.ts': ['lib/django-fixture.ts', 'lib/env.ts', 'lib/host-assertion.ts', 'lib/console-round-trip.ts'],
  'lib/env.ts': ['scripts/fa-edge-ee-profile.mjs'], 'lib/host-assertion.ts': ['lib/scope-actors.ts'],
  'lib/scope-actors.ts': ['lib/api-client.ts', 'lib/auth.ts', 'lib/env.ts', 'lib/provisioning.ts', 'lib/state-probe.ts'],
  'lib/provisioning.ts': ['lib/api-client.ts', 'lib/env.ts'], 'lib/state-probe.ts': ['lib/api-client.ts'],
  'lib/public-ingress.ts': ['lib/scope-actors.ts', 'lib/lm708.ts'],
  'lib/upstream.ts': ['lib/env.ts', 'lib/host-assertion.ts', 'lib/public-network.ts'],
};
type Seam = keyof typeof sources;
type ProducerGap = 'STAGE_B_BINDING' | 'J03_FINITE_ROUTES_AND_GRAPH' |
  'J04_PUBLIC_MUTATIONS_AND_CLAIMS' | 'J04_EVENT_FENCE_AND_STREAM_CLOCK' |
  'J04_ACTIVE_SIBLING_AND_CHILD_ORIGINALS' | 'J04_PRINCIPAL_RESIDUAL_CONTRACT' |
  'J04_CONFIG_AND_FAILURE_EMITTER' | 'J04_FIRST_MINT_FENCE' | 'APPLICABILITY_EVIDENCE' |
  'BROWSER_QUALIFICATION' | 'BROWSER_EXECUTION' | 'BROWSER_OR_FIXTURE_SETUP' |
  'TENANT_FIXTURE' | 'WITHDRAWAL_NOT_CORE' | 'COMPLETED_NATIVE_RESULT';
export class ConsoleCoreBlocked extends Error {
  readonly code = 'BLOCKED';
  readonly seam: Seam | ProducerGap | 'PUBLICATION';
  constructor(seam: Seam | ProducerGap | 'PUBLICATION') {
    super(`BLOCKED:${seam}`); this.name = 'ConsoleCoreBlocked'; this.seam = seam;
  }
}
export class ConsoleCoreFailure extends Error {
  readonly assertion: string;
  constructor(assertion: string) {
    super(`FAIL:${assertion}`); this.name = 'ConsoleCoreFailure'; this.assertion = assertion;
  }
}
export function check(ok: unknown, assertion: string): asserts ok {
  if (ok !== true) throw new ConsoleCoreFailure(assertion);
}
export const sha = (value: string | Buffer) => createHash('sha256').update(value).digest('hex');
export async function resolveSeam<T>(name: Seam): Promise<T> {
  const [module, symbol] = sources[name];
  const expected = pins[module];
  if (!expected) throw new ConsoleCoreBlocked(name);
  const url = new URL(`../../lib/${module}.ts`, import.meta.url);
  const visited = new Set<string>();
  const verifyFile = async (path: string): Promise<void> => {
    if (visited.has(path)) return;
    visited.add(path);
    if (!closurePins[path] || !relativeImports[path]) throw new ConsoleCoreBlocked(name);
    let bytes: Buffer;
    try { bytes = await readFile(new URL(`../../${path}`, import.meta.url)); }
    catch { throw new ConsoleCoreBlocked(name); }
    if (sha(bytes) !== closurePins[path]) throw new ConsoleCoreBlocked(name);
    for (const dependency of relativeImports[path]) await verifyFile(dependency);
  };
  await verifyFile(`lib/${module}.ts`);
  let loaded: Record<string, unknown>;
  // Playwright's ESM loader handles the composed TypeScript (including parameter
  // properties). No __dirname, synchronous require, or Node strip-only fallback.
  try { loaded = await import(url.href); } catch { throw new ConsoleCoreBlocked(name); }
  if (typeof loaded[symbol] !== 'function') throw new ConsoleCoreBlocked(name);
  return loaded[symbol] as T;
}
/** CLOSED joins, not claims these producer APIs exist. Source handoffs must name
 * actual callables and review these consumer needs before replacing this gate. */
export function requireProducer<T>(gap: ProducerGap): T {
  throw new ConsoleCoreBlocked(gap);
}

export interface Manifest {
  digest: string; tuple?: Record<string, unknown>; source?: Record<string, unknown>;
  served?: string[]; admitted?: string[]; approvedParts?: string[];
}
export interface StackIdentity {
  tuple: Record<string, unknown>; source: Record<string, unknown>;
  approvedParts: string[]; lm708Digest: string;
}
export interface Binding {
  candidatePath: string; expectedDigest: string; stack: StackIdentity;
  tuples: Record<string, Record<string, unknown>>;
  browser: 'chromium' | 'firefox' | 'webkit'; phase: string;
  candidateHead: string; unionDigest: string;
  stageAExport: string; stageABase: string;
  stageBDerivationAccepted: boolean; qualifiedEngines: readonly string[];
  allContextValueFreeObserverAccepted: boolean; failureEmitterAccepted: boolean;
  actualEdgeAndEEBound: boolean; tenantOrgHandles: readonly [string, string];
  admissionInput: unknown;
}
export interface CoreContext {
  manifest: Manifest; binding: Binding; branch: 'capability' | 'absence';
}
export interface CoreRuntime {
  walk: Walk; browserName: string;
  capture: Pick<PlaywrightWorkerOptions, 'trace' | 'video' | 'screenshot'>;
}
type CaptureRuntime = Pick<CoreRuntime, 'capture'>;
const isCoreCase = (info: Pick<TestInfo, 'title'>) => /^JRN-E2E-004\.|^JRN-E2E-003\.L5:/.test(info.title);
function earlyCorePolicy(runtime: CaptureRuntime, info: TestInfo): void {
  if (!isCoreCase(info)) return;
  // Only option fixtures are resolved here. No walk/browser/scopeActors/request
  // dependency may be added: even provisioning handles passwords and sessions.
  checkCapturePolicy(runtime, info);
  if (/^JRN-E2E-004\./.test(info.title)) requireProducer('J04_CONFIG_AND_FAILURE_EMITTER');
  // Standalone-stock still reaches its unchanged API-only absence rehearsal.
  // This exemption is NOT an evaluated LM absence or permission to qualify CORE.
  if (!(gatewayProfile() === 'stock' && process.env.E2E_STACK === 'standalone'))
    requireProducer('STAGE_B_BINDING');
}
async function guardedCoreUse(info: TestInfo, policy: () => void, use: () => Promise<void>): Promise<void> {
  let setupFailure: unknown;
  try { policy(); await use(); }
  catch (error) { setupFailure = error; throw error; }
  finally {
    if (pendingReceipts.has(info)) await finalizeCoreReceipt(info, setupFailure !== undefined);
    else if (isCoreCase(info) && info.status !== 'skipped') {
      const blocked = !(setupFailure instanceof ConsoleCoreFailure);
      await info.attach('console-core-receipt', { contentType: 'application/json', body: JSON.stringify({
        schema: 'console-core-consumer/v2', outcome: blocked ? 'BLOCKED' : 'FAIL', execution: 'NOT_RUN',
        reason: setupFailure instanceof ConsoleCoreBlocked ? setupFailure.seam :
          setupFailure instanceof ConsoleCoreFailure ? setupFailure.assertion : 'BROWSER_OR_FIXTURE_SETUP',
        assertionCount: 0, capabilityPassed: false, releaseAccepted: false,
      }) });
    }
  }
}
// Automatic test-scoped fixture: policy runs before non-auto scopeActors/walk
// setup, and finalization runs after their disposal/boundary.assert(). Native
// runner/reporting completion is later still, so even clean finals are provisional.
export const test = walkTest.extend<{ coreGuard: void; coreRuntime: CoreRuntime }>({
  coreGuard: [async ({ trace, video, screenshot }, use, info) => {
    await guardedCoreUse(info, () => earlyCorePolicy({ capture: { trace, video, screenshot } }, info), use);
  }, { auto: true }],
  coreRuntime: async ({ coreGuard, walk, browserName, trace, video, screenshot }, use) => {
    void coreGuard;
    await use({ walk, browserName, capture: { trace, video, screenshot } });
  },
});
export function checkCapturePolicy(runtime: CaptureRuntime, info: TestInfo) {
  // These are the resolved test fixtures, including test.use overrides.
  check(runtime.capture.trace === 'off' && runtime.capture.video === 'off' && runtime.capture.screenshot === 'off' &&
    info.config.workers === 1 && info.project.retries === 0, 'CAPTURE_AND_SERIAL_POLICY');
}
const browsers = new WeakMap<CoreContext, { context: BrowserContext; page: Page; challenge: string;
  info: TestInfo; runDigest: string; contextDigest: string; engine: string; versionDigest: string }>();
export function coreBrowserContext(context: CoreContext): BrowserContext {
  const witness = browsers.get(context);
  if (!witness) throw new ConsoleCoreBlocked('BROWSER_EXECUTION');
  return witness.context;
}
export async function evaluateCore(binding: Binding, expression: string): Promise<CoreContext> {
  const read = await resolveSeam<(path: string, digest: string) => Manifest>('readLM708');
  const bind = await resolveSeam<(m: Manifest, s: StackIdentity) => void>('bindLM708');
  const evaluate = await resolveSeam<(e: string, m: Manifest, s: StackIdentity,
    tuples: Binding['tuples']) => 'capability' | 'absence'>('evaluateApplicability');
  const manifest = read(binding.candidatePath, binding.expectedDigest);
  if (!manifest.tuple || !manifest.source || !manifest.served || !manifest.admitted || !manifest.approvedParts)
    throw new ConsoleCoreBlocked('APPLICABILITY_EVIDENCE');
  bind(manifest, binding.stack);
  const branch = evaluate(expression, manifest, binding.stack, binding.tuples);
  check(branch === 'capability' || branch === 'absence', 'APPLICABILITY_BRANCH');
  return { manifest, binding, branch };
}
export async function qualifyCore(context: CoreContext, info: TestInfo, runtime: CoreRuntime): Promise<void> {
  if (!JOURNEY_PUBLICATION_ACCEPTED) throw new ConsoleCoreBlocked('PUBLICATION');
  check(context.branch === 'capability', 'CAPABILITY_BRANCH');
  const { binding } = context;
  if (!['chromium', 'firefox', 'webkit'].includes(binding.browser) || binding.browser !== runtime.browserName ||
      !['chromium', 'firefox', 'webkit'].every(b => binding.qualifiedEngines.includes(b)))
    throw new ConsoleCoreBlocked('BROWSER_QUALIFICATION');
  if (binding.tenantOrgHandles.length !== 2 || !binding.tenantOrgHandles.every(Boolean) ||
      binding.tenantOrgHandles[0] === binding.tenantOrgHandles[1]) throw new ConsoleCoreBlocked('TENANT_FIXTURE');
  check(/^[a-f0-9]{40}$/.test(binding.candidateHead) && /^[a-f0-9]{64}$/.test(binding.unionDigest) &&
    typeof binding.phase === 'string' && binding.phase.length > 0, 'CANDIDATE_BINDING');
  if (!binding.stageBDerivationAccepted || !binding.actualEdgeAndEEBound ||
      binding.stageAExport !== STAGE_A.exportManifest || binding.stageABase !== STAGE_A.baseRoot)
    throw new ConsoleCoreBlocked('STAGE_B_BINDING');
  if (!binding.allContextValueFreeObserverAccepted || !binding.failureEmitterAccepted)
    throw new ConsoleCoreBlocked('J04_CONFIG_AND_FAILURE_EMITTER');
  checkCapturePolicy(runtime, info);
  try {
    // Only the existing guarded walk machinery opens contexts. A configured
    // engine name is not execution evidence; a same-test engine must execute JS.
    const browserContext = await runtime.walk.anonymous('console-core-engine');
    const browser = browserContext.browser();
    if (!browser?.isConnected() || browser.browserType().name() !== binding.browser || !browser.version())
      throw new ConsoleCoreBlocked('BROWSER_EXECUTION');
    const page = await browserContext.newPage();
    const challenge = randomUUID();
    if (await page.evaluate(value => value, challenge) !== challenge) throw new ConsoleCoreBlocked('BROWSER_EXECUTION');
    browsers.set(context, { context: browserContext, page, challenge, info,
      runDigest: sha(JSON.stringify([info.testId, info.retry, info.workerIndex, randomUUID()])),
      contextDigest: sha(challenge), engine: browser.browserType().name(), versionDigest: sha(browser.version()) });
  } catch { throw new ConsoleCoreBlocked('BROWSER_EXECUTION'); }
}
export async function bindCore(binding: Binding, expression: string, info: TestInfo, runtime: CoreRuntime): Promise<CoreContext> {
  const context = await evaluateCore(binding, expression);
  if (context.branch === 'capability') await qualifyCore(context, info, runtime);
  return context;
}
export async function verifyAdmission(context: CoreContext): Promise<void> {
  if (context.branch !== 'capability' || !context.manifest.admitted?.includes('console'))
    throw new ConsoleCoreBlocked('WITHDRAWAL_NOT_CORE');
  const verify = await resolveSeam<(input: unknown) => Promise<{ passed: boolean; lm708Digest: string }>>('verifyConsoleAdmission');
  const proof = await verify(context.binding.admissionInput);
  check(proof.passed === true && proof.lm708Digest === context.manifest.digest, 'ADMISSION_AND_MANIFESTS');
  // Call BEFORE constructing retained A/B fixtures: this helper toggles admission.
}

// Body evidence is private until the outer guard has observed required fixture
// teardown. It still cannot claim a completed native test: reporters/other auto
// fixtures can fail later. A separately accepted native-result join is owed.
const children = new WeakMap<TestInfo, Record<string, unknown>[]>();
interface PendingReceipt { receipt: Record<string, unknown>; children: Record<string, unknown>[] }
const pendingReceipts = new WeakMap<TestInfo, PendingReceipt>();
async function finalizeCoreReceipt(info: TestInfo, guardFailed: boolean): Promise<void> {
  const pending = pendingReceipts.get(info);
  check(!!pending, 'CORE_RECEIPT_MISSING');
  pendingReceipts.delete(info);
  const fixturesPassed = !guardFailed && info.status === 'passed' && info.expectedStatus === 'passed' && info.errors.length === 0;
  const bodyPassed = pending.receipt.bodyOutcome === 'PASS';
  const outcome = bodyPassed ? (fixturesPassed ? 'BLOCKED' : 'FAIL') : pending.receipt.bodyOutcome;
  const reason = bodyPassed ? (fixturesPassed ? 'COMPLETED_NATIVE_RESULT' : 'FIXTURE_TEARDOWN_OR_NATIVE_FAILURE') : pending.receipt.reason;
  for (const witness of pending.children) await info.attach('j04-sibling-child', {
    contentType: 'application/json', body: JSON.stringify({ ...witness,
      bodyPassed, fixturesPassed, passed: false, nativeCompletionPending: true,
      assertionCount: bodyPassed && fixturesPassed ? pending.receipt.assertionCount : 0,
    }),
  });
  await info.attach('console-core-receipt', { contentType: 'application/json', body: JSON.stringify({
    ...pending.receipt, outcome, reason, fixturesPassed, nativeCompletionPending: true,
    capabilityPassed: false, releaseAccepted: false,
  }) });
}
export function stageCoreChild(info: TestInfo, witness: Record<string, unknown>): void {
  const pending = children.get(info);
  check(!!pending, 'CHILD_WITHOUT_CORE_BODY');
  pending.push(witness);
}
export async function coreReceipt(info: TestInfo, leg: string,
  body: (setContext: (c: CoreContext) => void) => Promise<number>): Promise<void> {
  const state: { context?: CoreContext } = {};
  let assertions = 0;
  let outcome: 'PASS' | 'FAIL' | 'BLOCKED' = 'FAIL';
  let reason: string | null = null, failure: Error | undefined;
  children.set(info, []);
  try {
    assertions = await body(c => { state.context = c; });
    check(Number.isSafeInteger(assertions) && assertions > 0 && info.errors.length === 0, 'NONVACUOUS_BODY');
    if (state.context?.branch === 'capability') {
      const w = browsers.get(state.context);
      if (!w || w.info !== info || !w.context.browser()?.isConnected() || w.page.isClosed() ||
          await w.page.evaluate(value => value, w.challenge) !== w.challenge)
        throw new ConsoleCoreBlocked('BROWSER_EXECUTION');
    }
    outcome = 'PASS';
  } catch (error) {
    if (error instanceof ConsoleCoreBlocked) { outcome = 'BLOCKED'; reason = error.seam; failure = error; }
    else {
      reason = error instanceof ConsoleCoreFailure ? error.assertion : 'UNEXPECTED';
      failure = new ConsoleCoreFailure(reason);
    }
  } finally {
    const context = state.context, digest = context?.manifest.digest;
    const w = context ? browsers.get(context) : undefined;
    const stagedChildren = (children.get(info) ?? []).map(witness => ({ ...structuredClone(witness),
      executed: outcome === 'PASS', sourceOnly: false, skipped: false,
      browserRunDigest: w?.runDigest ?? null, browserContextDigest: w?.contextDigest ?? null,
      nativeTestDigest: sha(JSON.stringify([info.testId, info.project.name, info.retry, info.workerIndex, info.repeatEachIndex])),
    }));
    children.delete(info);
    check(!pendingReceipts.has(info), 'DUPLICATE_CORE_RECEIPT');
    pendingReceipts.set(info, { children: stagedChildren, receipt: {
      schema: 'console-core-consumer/v2', leg, bodyOutcome: outcome, reason,
      nativeTestDigest: sha(JSON.stringify([info.testId, info.project.name, info.retry, info.workerIndex, info.repeatEachIndex])),
      execution: outcome === 'BLOCKED' ? 'NOT_RUN' : 'ATTEMPTED',
      lm708Digest: digest && /^sha256:[a-f0-9]{64}$/.test(digest) ? digest : null,
      stageAExport: STAGE_A.exportManifest,
      candidateHead: context && /^[a-f0-9]{40}$/.test(context.binding.candidateHead) ? context.binding.candidateHead : null,
      unionDigest: context && /^[a-f0-9]{64}$/.test(context.binding.unionDigest) ? context.binding.unionDigest : null,
      phaseDigest: context ? sha(context.binding.phase) : null,
      tupleDigest: context ? sha(JSON.stringify(context.binding.stack.tuple)) : null,
      sourceDigest: context ? sha(JSON.stringify(context.binding.stack.source)) : null,
      browser: w?.engine ?? null, browserRunDigest: w?.runDigest ?? null,
      browserContextDigest: w?.contextDigest ?? null, browserVersionDigest: w?.versionDigest ?? null,
      assertionCount: assertions, bodyCapabilityPassed: outcome === 'PASS' && assertions > 0 &&
        context?.branch === 'capability' && !!w,
    } });
  }
  if (failure) throw failure;
}

// Authored synthetic controls, not native Playwright/three-engine receipts. The
// callbacks exercise the same outer guard and deferred publisher as real tests.
export async function coreLifecycleControls(): Promise<void> {
  const fake = () => {
    const attachments: Record<string, any>[] = [];
    const info = { title: 'JRN-E2E-004.L1.baseline: synthetic', testId: 'synthetic', retry: 0,
      workerIndex: 0, repeatEachIndex: 0, config: { workers: 1 }, project: { name: 'synthetic', retries: 0 },
      status: 'passed', expectedStatus: 'passed', errors: [],
      attach: async (_name: string, options: { body: string }) => { attachments.push(JSON.parse(options.body)); },
    } as unknown as TestInfo;
    return { info, attachments };
  };
  for (const mode of ['trace', 'video', 'screenshot'] as const) {
    const { info, attachments } = fake();
    const capture: CoreRuntime['capture'] = { trace: 'off', video: 'off', screenshot: 'off' };
    if (mode === 'screenshot') capture.screenshot = 'on'; else capture[mode] = 'on';
    let provisions = 0, killed = false;
    try { await guardedCoreUse(info, () => earlyCorePolicy({ capture }, info), async () => { provisions++; }); }
    catch (e) { killed = e instanceof ConsoleCoreFailure && e.assertion === 'CAPTURE_AND_SERIAL_POLICY'; }
    check(killed && provisions === 0 && attachments.length === 1 && attachments[0].capabilityPassed === false,
      'CORE.CAPTURE_BEFORE_PROVISION_CONTROL');
  }
  {
    const { info } = fake(); let provisions = 0, blocked = false;
    try { await guardedCoreUse(info, () => earlyCorePolicy({ capture: { trace: 'off', video: 'off', screenshot: 'off' } }, info),
      async () => { provisions++; }); }
    catch (e) { blocked = e instanceof ConsoleCoreBlocked && e.seam === 'J04_CONFIG_AND_FAILURE_EMITTER'; }
    check(blocked && provisions === 0, 'CORE.CONFIG_BEFORE_PROVISION_CONTROL');
  }
  for (const failTeardown of [false, true, false]) {
    const { info, attachments } = fake();
    await guardedCoreUse(info, () => {}, async () => {
      await coreReceipt(info, 'synthetic', async () => { stageCoreChild(info, { synthetic: true }); return 1; });
      check(attachments.length === 0, 'CORE.BODY_IS_PROVISIONAL');
      // Same ordering as walk's use() return followed by boundary.assert().
      try { check(!failTeardown, 'SYNTHETIC_WALK_BOUNDARY'); }
      catch { info.status = 'failed'; info.errors.push({ message: 'SYNTHETIC_WALK_BOUNDARY' }); }
    });
    const [child, receipt] = attachments;
    check(attachments.length === 2 && child.bodyPassed === true && child.passed === false &&
      child.fixturesPassed === !failTeardown && receipt.capabilityPassed === false &&
      receipt.outcome === (failTeardown ? 'FAIL' : 'BLOCKED') && receipt.reason ===
        (failTeardown ? 'FIXTURE_TEARDOWN_OR_NATIVE_FAILURE' : 'COMPLETED_NATIVE_RESULT'), 'CORE.TEARDOWN_CONTROL');
  }
}

// ===========================================================================
// W1 (J16 core)
// ===========================================================================
// CJ-CONT W1, J16 core (L1-L3): the ONE adapter between the journey bodies and
// producer seams that are not composed into e2e/console-journeys-708 yet.
//   HR-CJ-SEAMS  gw/E2E-HR-CJ-1 @ e0133094d1: lm708, console-round-trip,
//                host-assertion, public-ingress, upstream (HR-CJ-02/03),
//                console-evidence, console-embed.
//   HR-JRN-01    publication-contract.ts (journeys-main-708, still false).
//   Stage B      the Console run adapter below. No producer has authored it;
//                its interface is a NORMALIZED CONSUMER CONTRACT in the style of
//                gw-hr-cj console-stop-fixture.ts:1-3, never a product wire schema.
// Every seam is resolved at runtime by explicit path. Missing or incompatible
// seams throw ConsoleBlocked with a value-free code. A blocked seam is never
// absence, never a skip without a receipt and never a PASS.

export type BlockCode =
  | 'PUBLICATION_GATE_CLOSED' | 'SEAM_MISSING' | 'SEAM_INCOMPATIBLE'
  | 'APPLICABILITY_EVIDENCE_MISSING' | 'APPLICABILITY_UNEVALUABLE' | 'STAGE_A_BINDING_MISSING'
  | 'STAGE_A_BINDING_INVALID' | 'STAGE_A_READINESS_MISSING' | 'STAGE_A_JOIN_UNACCEPTED'
  | 'TENANT_FIXTURE_MISSING' | 'D5_GRANT_MISSING' | 'D5_GRANT_UNBOUND' | 'FIXTURE_CONTRACT_MISSING'
  | 'BROWSER_ENGINE_UNQUALIFIED' | 'BROWSER_SELECTION_CONFLICT' | 'SELECTION_UNSAFE' | 'UPSTREAM_LEG_NOT_PASSED'
  | 'ADAPTER_REFUSED';

/** Typed BLOCKED outcome. `subject` is a logical seam/engine name, never a value. */
export class ConsoleBlocked extends Error {
  constructor(readonly code: BlockCode, readonly subject = '', readonly detail = '') {
    super(`BLOCKED ${subject ? `${code}:${subject}` : code}`);
    this.name = 'ConsoleBlocked';
  }
  get reason(): string { return this.subject ? `${this.code}:${this.subject}` : this.code; }
}

export const sha256 = (bytes: string | Buffer) => createHash('sha256').update(bytes).digest('hex');
/** Opaque, value-free handle for receipts: never the raw id. */
export const handle = (value: string | null | undefined) => (value ? `h:${sha256(value).slice(0, 16)}` : null);

// ---------------------------------------------------------------------------
// Seam registry. Paths are relative to this file. `source` cites the producer
// definition the body is written against (gw-hr-cj path:line at e0133094d1).
// ---------------------------------------------------------------------------
const SEAMS = {
  lm708: { file: '../../lib/lm708.ts', exports: ['readLM708', 'bindLM708', 'evaluateApplicability'],
    source: 'gw-hr-cj e2e/lib/lm708.ts:69-113' },
  admission: { file: '../../lib/console-round-trip.ts', exports: ['verifyConsoleAdmission'],
    source: 'gw-hr-cj e2e/lib/console-round-trip.ts:23-89' },
  hostAssertion: { file: '../../lib/host-assertion.ts', exports: ['HostAssertionClient'],
    source: 'gw-hr-cj e2e/lib/host-assertion.ts:42-104' },
  ingress: { file: '../../lib/public-ingress.ts', exports: ['compareRouteManifests'],
    source: 'gw-hr-cj e2e/lib/public-ingress.ts:34-55' },
  upstream: { file: '../../lib/upstream.ts', exports: ['MockUpstream'],
    methods: { MockUpstream: ['baseUrl', 'checkpoint', 'dispatchSince', 'streams'] },
    source: 'gw-hr-cj e2e/lib/upstream.ts:29-124' },
  evidence: { file: '../../lib/console-evidence.ts', exports: ['verifyRunObserve'],
    source: 'gw-hr-cj e2e/lib/console-evidence.ts:8-35' },
  embed: { file: '../../lib/console-embed.ts', exports: ['ConsoleEmbedCapture', 'ConsoleEmbedRefusal'],
    source: 'gw-hr-cj e2e/lib/console-embed.ts:50-375' },
  publication: { file: './publication-contract.ts', exports: ['JOURNEY_PUBLICATION_ACCEPTED', 'JOURNEY_PUBLICATION_GAP'],
    source: 'journeys-main-708 e2e/flows/journeys/publication-contract.ts:7-14' },
  // NOT AUTHORED by any producer at this head. Stage B / HR-CJ-SEAMS supplies it.
  runAdapter: { file: '../../lib/console-core-run-adapter.ts', exports: ['openConsoleCoreRunAdapter'],
    source: 'PROPOSED consumer contract: ConsoleCoreRunAdapter in this file' },
} as const satisfies Record<string, { file: string; exports: readonly string[];
  methods?: Readonly<Record<string, readonly string[]>>; source: string }>;
export type SeamName = keyof typeof SEAMS;
export const SEAM_SOURCES: Readonly<Record<SeamName, string>> = Object.fromEntries(
  Object.entries(SEAMS).map(([name, seam]) => [name, seam.source])) as Record<SeamName, string>;

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Module = Record<string, any>;
const loaded = new Map<SeamName, Promise<Module>>();

async function load(name: SeamName): Promise<Module> {
  const seam: { file: string; exports: readonly string[]; methods?: Readonly<Record<string, readonly string[]>> } = SEAMS[name];
  const url = new URL(seam.file, import.meta.url);
  if (!existsSync(fileURLToPath(url))) throw new ConsoleBlocked('SEAM_MISSING', name);
  let mod: Module;
  // Non-literal specifier: tsc does not resolve an uncomposed module at typecheck.
  try { mod = await import(url.href) as Module; } catch { throw new ConsoleBlocked('SEAM_INCOMPATIBLE', `${name}.load`); }
  for (const key of seam.exports) {
    if (!(key in mod) || mod[key] === undefined) throw new ConsoleBlocked('SEAM_INCOMPATIBLE', `${name}.${key}`);
  }
  for (const [cls, methods] of Object.entries(seam.methods ?? {})) {
    for (const method of methods) {
      if (typeof mod[cls]?.prototype?.[method] !== 'function') throw new ConsoleBlocked('SEAM_INCOMPATIBLE', `${name}.${cls}.${method}`);
    }
  }
  return mod;
}

/** Resolve a producer seam or throw ConsoleBlocked naming it. Memoized per worker.
 * W1's `resolveSeam` (renamed in the union; W3 owns that name above). */
export function resolveJ16Seam(name: SeamName): Promise<Module> {
  let pending = loaded.get(name);
  if (!pending) { pending = load(name); loaded.set(name, pending); }
  return pending;
}
/** How a body reaches its seams. Canonical cells always use resolveJ16Seam; only the
 * fixture-free orchestration controls inject a recording resolver. */
export type SeamResolver = typeof resolveJ16Seam;

// ---------------------------------------------------------------------------
// Gates, in the order a positive cell evaluates them. Each refuses BEFORE any
// provisioning fixture is set up (they run from beforeEach with browserName only).
// ---------------------------------------------------------------------------

/** HR-JRN-01, honoured exactly like default-off-census.spec.ts:25 and
 * two-tenant-isolation.spec.ts:15: refuse while JOURNEY_PUBLICATION_ACCEPTED is
 * not true. No environment override. Absent module = closed (fail-closed). */
export async function publicationGate(): Promise<void> {
  const contract = await resolveJ16Seam('publication');
  if (contract.JOURNEY_PUBLICATION_ACCEPTED !== true) {
    throw new ConsoleBlocked('PUBLICATION_GATE_CLOSED', 'HR-JRN-01', String(contract.JOURNEY_PUBLICATION_GAP ?? ''));
  }
}

export const REQUIRED_ENGINES = ['chromium', 'firefox', 'webkit'] as const;
/** Stage A INTERFACES.json E4 refuses WebKit before context creation; the
 * qualified same-runtime WebKit successor is gateway's wf_e92197a0-c1b. Change
 * this only in the change that composes that ACCEPTED successor. No env override. */
export const WEBKIT_RUNTIME_QUALIFIED: boolean = false;

/** C02 engine gate. `effective` is the browserName fixture; `requested` is
 * testInfo.project.use.browserName, which the CLI --browser flag sets (CONSUMER-core
 * argv). consoleOptions no longer names an engine, so the two agree unless something
 * else overrides the selection. `selector` is the retired E2E_CONSOLE_BROWSER: never a
 * selector, only a cross-check; when set it must name the effective engine. */
export function browserGate(effective: string, requested: unknown,
  selector: string | null = process.env.E2E_CONSOLE_BROWSER ?? null): void {
  if (typeof requested === 'string' && requested !== effective) {
    throw new ConsoleBlocked('BROWSER_SELECTION_CONFLICT', `${requested}->${effective}`);
  }
  if (selector !== null && selector !== effective) throw new ConsoleBlocked('BROWSER_SELECTION_CONFLICT', `${selector}->${effective}`);
  if (!(REQUIRED_ENGINES as readonly string[]).includes(effective)) throw new ConsoleBlocked('BROWSER_ENGINE_UNQUALIFIED', 'unknown');
  if (effective === 'webkit' && !WEBKIT_RUNTIME_QUALIFIED) throw new ConsoleBlocked('BROWSER_ENGINE_UNQUALIFIED', 'webkit');
}

/** CJ-OVERLAY Stage A export the bodies are written against
 * (tmp/c744-cj-overlay/d7-rebind/export-09fc47c13a60/EXPORT-MANIFEST.json; it
 * ships no README). It is READ_ONLY_CANDIDATE, dispatchAllowed=false and
 * runtimeQualified=false: a successor export requires rebinding these pins. */
export const STAGE_A_EXPORT = Object.freeze({
  manifestSha256: '24b82e6e487e3c0ec3f1a7b056f12fd4c62c380f2017b6970f368d7f793798b8',
  sourceHead: '09fc47c13a60ed17c801d2effee6d38e167db40d',
  payloadRootSha256: 'a28f861431864deb09169bd42b9e9ba8a4cdffc1108e6df15b6971e2248796b5',
  handoffSha256: 'c9ddaef558699685f74f9548c5164b8fa52bbf9cc0fbc91f7fe045e92b9ca7ed',
  // HANDOFF-GATEWAY.json route.shipmentRootSha256: the successor tier-1 root.
  tier1ShipmentRootSha256: 'e1bb3bf147a85d86cccdf2b2eff172c26421961cada3ccc7d56d3852f5d7c5bc',
});

/** sha256 of the export's consumer-extension members (files/deploy/e2e-overlay/
 * compose-kit/consumer-extension/<name>). extension.py:50-52 sets a binding's
 * extensionRoot to that directory, so its bytes must be exactly these: a stale or
 * successor kit refuses until this table is rebound with STAGE_A_EXPORT. */
export const STAGE_A_EXTENSION_PINS: Readonly<Record<string, string>> = Object.freeze({
  'extension.py': '7d04c6d614fda40823f137a793d9bdfca1e63266f9abcabade1de99b5f8df7ec',
  'INTERFACES.json': '86b4d5399c8a0b3470ef3c5edc3f437f7373d7497be315371489ba90dece12ea',
  'JOIN-DESCRIPTOR.json': '78c7038926fc9568ef5dc6ada988a612adc00581e69c92dd353ff75a9f770976',
  'JOIN-PLAN.json': '14d2c802eeed533a29c27d8b06f660d644cf7914208accd43e60a41e6c53e1d6',
  'JOIN-STATUS.json': 'd5a34d001000ef9c9d3311ccc440242f70559fcb3ee427f6de407d5ab4bb3a29',
});
/** Producer binding schema: export extension.py:50-68 native_binding and :128-138
 * reporter/observer slots; read by cj-overlay-744 consumer-extension/binding.cjs:31-63
 * @ 09fc47c13a (load). */
export const STAGE_A_BINDING_SCHEMA = 'cj-walk-consumer-binding/v1';
/** packets/J16/CONSUMER-core.json (sha256 61bdc9d19dc2912e3a093e8f7c94de5f2f7cc5335211cecacffb5deb8c71f1d0)
 * reporter and contextObserver: the kit binds exactly these per invocation. */
export const J16_CORE_HOOKS = Object.freeze({
  reporter: { module: 'e2e/lib/j16/reporter.cjs', outputPath: 'j16-inventory.json',
    options: { journey: 'J16', group: 'core', requiredLegs: ['L1', 'L2', 'L3'], inventory: 'e2e/fixtures/j16/obligations.json',
      requiredArms: ['positive', 'negative', 'restored'], rejectSkipped: true, rejectUnexpectedTests: true, rejectEmpty: true } },
  observer: { module: 'e2e/lib/j16/context-observer.cjs', export: 'beforeFirstPage',
    options: { journey: 'J16', group: 'core', appSideRequired: true, requireNoPages: true,
      requireBaseAllContextObservation: true } },
});
/** CONSUMER-BINDINGS.json hooks.observer.base_join: Stage A must bind the app-side
 * prewalk and base all-context readiness links into this binding. These field names
 * are PROPOSED, not accepted producer fields: no value of them can satisfy the join. */
export const STAGE_A_READINESS_FIELDS = ['appSideReceipt', 'baseObservationReceipt'] as const;
/** The CONSUMER-core invocation id of a cell, e.g. j16-core-firefox-a-positive. */
export const coreInvocationId = (browser: string, tenant: 'A' | 'B', arm: string) =>
  `j16-core-${browser}-${tenant.toLowerCase()}-${arm}`;

const THIS_FILE = fileURLToPath(import.meta.url);
const THIS_SOURCE_PATH = path.join('e2e', 'flows', 'journeys', 'console-core-seams.ts');
/** The FA_REF checkout this module runs from: the binding's sourceRoot must be it. */
export const CONSOLE_SOURCE_ROOT = path.resolve(path.dirname(THIS_FILE), '..', '..', '..');
const canonicalPath = (value: unknown): value is string =>
  typeof value === 'string' && path.isAbsolute(value) && path.normalize(value) === value && !/[\0\r\n]/.test(value);
const inside = (root: string, name: string) => {
  const relative = path.relative(root, name);
  return relative !== '' && relative !== '..' && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative);
};
const canonicalJson = (value: unknown): string => {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`;
  if (value && typeof value === 'object') {
    const record = value as Record<string, unknown>;
    return `{${Object.keys(record).sort().map(k => `${JSON.stringify(k)}:${canonicalJson(record[k])}`).join(',')}}`;
  }
  return JSON.stringify(value) ?? 'undefined';
};
const slot = (value: unknown): Record<string, unknown> | null =>
  value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null;

export interface StageABinding { invocationId: string; bindingDigest: string }
/** INTERFACES.json E3: the kit points CJ_CONSUMER_BINDING at a create-only JSON file
 * per invocation. Validated against the producer schema AND this exact cell: its
 * invocation id, this checkout as sourceRoot, a disjoint landing that holds the file,
 * the CONSUMER-core hooks, the pinned export bytes and the readiness links. The
 * readiness links have no accepted contract yet, so even a binding that passes every
 * other check ends BLOCKED STAGE_A_JOIN_UNACCEPTED: the change that composes the
 * accepted join validates them here. Nothing is taken from constants as evidence.
 * `pins` defaults to the export table; only the fixture-free controls pass another. */
export function stageABinding(cell: { invocationId: string },
  pins: Readonly<Record<string, string>> = STAGE_A_EXTENSION_PINS): StageABinding {
  const file = process.env.CJ_CONSUMER_BINDING;
  if (!file || !existsSync(file)) throw new ConsoleBlocked('STAGE_A_BINDING_MISSING', 'CJ_CONSUMER_BINDING');
  const invalid = (subject: string) => new ConsoleBlocked('STAGE_A_BINDING_INVALID', subject);
  let parsed: unknown;
  try { parsed = JSON.parse(readFileSync(file).toString()); } catch { parsed = null; }
  const b = slot(parsed);
  if (!b) throw invalid('json');
  if (b.schema !== STAGE_A_BINDING_SCHEMA) throw invalid('schema');
  if (b.invocationId !== cell.invocationId) throw invalid('invocation');
  const { sourceRoot, landingRoot, extensionRoot } = b;
  if (!canonicalPath(sourceRoot) || !canonicalPath(landingRoot) || !canonicalPath(extensionRoot) || !canonicalPath(file)) {
    throw invalid('roots');
  }
  if (path.relative(sourceRoot, THIS_FILE) !== THIS_SOURCE_PATH) throw invalid('source');
  if (sourceRoot === landingRoot || inside(sourceRoot, landingRoot) || inside(landingRoot, sourceRoot) ||
      !inside(landingRoot, file)) throw invalid('landing');
  const output = path.join(landingRoot, cell.invocationId);
  const reporter = slot(b.reporter), observer = slot(b.observer);
  if (!reporter || reporter.module !== path.join(sourceRoot, J16_CORE_HOOKS.reporter.module) ||
      canonicalJson(reporter.options) !== canonicalJson(J16_CORE_HOOKS.reporter.options) ||
      reporter.outputFile !== path.join(output, J16_CORE_HOOKS.reporter.outputPath)) throw invalid('reporter');
  if (!observer || observer.module !== path.join(sourceRoot, J16_CORE_HOOKS.observer.module) ||
      observer.export !== J16_CORE_HOOKS.observer.export ||
      canonicalJson(observer.options) !== canonicalJson(J16_CORE_HOOKS.observer.options) ||
      observer.outputDirectory !== path.join(output, 'contexts')) throw invalid('observer');
  const members = Object.entries(pins);
  if (!members.length) throw invalid('export-pin');
  for (const [name, pin] of members) {
    let digest: string | null = null;
    try { digest = sha256(readFileSync(path.join(extensionRoot, name))); } catch { digest = null; }
    if (digest !== pin) throw invalid('export-pin');
  }
  for (const field of STAGE_A_READINESS_FIELDS) {
    if (b[field] === undefined || b[field] === null) throw new ConsoleBlocked('STAGE_A_READINESS_MISSING', field);
  }
  throw new ConsoleBlocked('STAGE_A_JOIN_UNACCEPTED', 'readiness-contract',
    'appSideReceipt/baseObservationReceipt are proposed binding fields without an accepted producer contract');
}

/** Owner D5/D4 real-model grant. The handle is a grant
 * record path plus its pinned sha256; only the digest reaches receipts. The
 * adapter, not the test, routes the product through the approved relay. */
export function d5Grant(): { path: string; digest: string } {
  const path = process.env.E2E_J16_D5_GRANT, pinned = process.env.E2E_J16_D5_GRANT_SHA256;
  if (!path || !pinned) throw new ConsoleBlocked('D5_GRANT_MISSING');
  if (!/^[a-f0-9]{64}$/.test(pinned) || !existsSync(path) || sha256(readFileSync(path)) !== pinned) {
    throw new ConsoleBlocked('D5_GRANT_UNBOUND');
  }
  return { path, digest: `sha256:${pinned}` };
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export interface CoreApplicability { branch: 'capability' | 'absence'; lm708Digest: string; manifest: any; stack: any;
  tuples: Record<string, Record<string, unknown>> }

/** HR-CJ-01 conjunction on the exact LM-708 candidate and the runner's actual
 * stack identity. Unevaluable never becomes absence. Consumer input names:
 * E2E_LM708_PATH, E2E_LM708_DIGEST (sha256:<hex>), E2E_LM708_STACK (runner stack
 * identity JSON) and E2E_LM708_TUPLES (preregistered tuple identities JSON). */
export async function coreApplicability(expression: string, requiredParts: readonly string[] = []): Promise<CoreApplicability> {
  const path = process.env.E2E_LM708_PATH, digest = process.env.E2E_LM708_DIGEST;
  const stackPath = process.env.E2E_LM708_STACK, tuplesPath = process.env.E2E_LM708_TUPLES;
  if (!path || !digest || !stackPath || !tuplesPath) throw new ConsoleBlocked('APPLICABILITY_EVIDENCE_MISSING', 'LM-708');
  const lm = await resolveJ16Seam('lm708');
  try {
    const manifest = lm.readLM708(path, digest);
    const stack = JSON.parse(readFileSync(stackPath, 'utf8'));
    const tuples = JSON.parse(readFileSync(tuplesPath, 'utf8'));
    const branch = lm.evaluateApplicability(expression, manifest, stack, tuples, requiredParts);
    if (branch !== 'capability' && branch !== 'absence') throw new Error();
    return { branch, lm708Digest: manifest.digest, manifest, stack, tuples };
  } catch (error) {
    if (error instanceof ConsoleBlocked) throw error;
    throw new ConsoleBlocked('APPLICABILITY_UNEVALUABLE', 'LM-708');
  }
}

/** For receipts that must carry the LM-708 digest when one is bound: never throws. */
export async function lm708Binding(expression: string): Promise<{ digest: string | null; branch: string | null; reason: string | null }> {
  try {
    const bound = await coreApplicability(expression);
    return { digest: bound.lm708Digest, branch: bound.branch, reason: null };
  } catch (error) {
    return { digest: null, branch: null, reason: error instanceof ConsoleBlocked ? error.reason : 'APPLICABILITY_UNEVALUABLE:LM-708' };
  }
}

// ---------------------------------------------------------------------------
// Normalized J16 consumer contract for the Stage B product adapter. Ids are
// opaque; every reader issues a FRESH read (api and pg never share an object);
// UI methods drive the real Console/FA UI in the page they are given.
// ---------------------------------------------------------------------------
export interface CoreActor { organizationId: string; workspaceId: string; tokens: { access: string } }
export interface CoreTenant extends CoreActor { label: 'A' | 'B'; projectId: string; accountId: string }
export interface AgentRecord { agentId: string; organizationId: string; workspaceId: string; accountId: string }
export interface RunRecord { runId: string; agentId: string; organizationId: string; workspaceId: string;
  projectId: string; traceId: string; state: string }
export interface RunEvent { seq: number; runId: string; kind: 'content' | 'terminal' | 'other' }
/** Approved-relay receipt for the run; `origin` is the relay's own classification. */
export interface RelayReceipt { runId: string; grantDigest: string; origin: 'live' | 'scripted' }
export interface ObserveWitness { organizationId: string; workspaceId: string; projectId: string; runId: string;
  traceId: string; masked: boolean }
export interface ObserveReaders {
  api(): Promise<ObserveWitness>; pg(): Promise<ObserveWitness>; clickhouseFinal(): Promise<ObserveWitness>;
  foreign(): Promise<{ status: number; rows: unknown[] }>;
}
export type UpstreamState = 'held' | 'running' | 'cancelled' | 'completed' | 'unknown';
export interface UpstreamWitness {
  kind: 'rehearsal' | 'live';
  checkpoint(): Promise<unknown>;
  /** Throws unless exactly `expected` upstream dispatches happened since `checkpoint`. */
  since(checkpoint: unknown, expected: number): Promise<{ count: number }>;
  state(runId: string): Promise<UpstreamState>;
}
export type ProviderBinding =
  | { kind: 'rehearsal'; baseUrl: string }
  | { kind: 'live'; grantPath: string; grantDigest: string };
export interface AdmissionProofInputs {
  routeCopies: Record<string, string>; orm: unknown; hosts: { api: string; public: string };
  embed: { path: string; contentType: string; allowed: unknown; denied: unknown };
}
export interface ConsoleCoreRunAdapter {
  readonly kind: 'stage-b-product';
  /** Exact configured embed origin (VITE_OMEGA_CONSOLE_URL), loopback in the VM. */
  readonly consoleOrigin: string;
  /** Accepted run-state contract; the test never invents a state name. */
  readonly runningStates: readonly string[];
  readonly stoppedStates: readonly string[];
  /** Accepted outcomes of a repeated Stop on an already-stopped run. */
  readonly repeatStopStatuses: readonly number[];
  /** Synthetic marker that the accepted Observe masking rule must mask. */
  maskingSentinel(): string;
  /** Stage B private two-org x two-user seed; admitted, distinct ids. */
  tenants(): Promise<readonly [CoreTenant, CoreTenant]>;
  admissionProofInputs(): Promise<AdmissionProofInputs>;
  bindProvider(tenant: CoreTenant, provider: ProviderBinding): Promise<void>;
  liveUpstream(tenant: CoreTenant): UpstreamWitness;
  signIn(context: BrowserContext, tenant: CoreTenant): Promise<Page>;
  sessionScope(page: Page): Promise<{ organizationId: string; workspaceId: string } | null>;
  createAgent(page: Page, name: string): Promise<{ agentId: string }>;
  readAgent(actor: CoreActor, agentId: string, via: 'api' | 'pg'): Promise<{ status: number; agent: AgentRecord | null }>;
  startRun(page: Page, agentId: string, prompt: string): Promise<{ runId: string }>;
  startRunApi(actor: CoreActor, agentId: string, prompt: string): Promise<{ status: number; runId: string | null }>;
  readRun(actor: CoreActor, runId: string, via: 'api' | 'pg'): Promise<{ status: number; run: RunRecord | null }>;
  /** Opens the run's own Console view (Run Detail) in a freshly signed-in page. */
  openRun(page: Page, runId: string): Promise<void>;
  /** Run ids for `agentId` as stored in `scope`'s own organization/workspace. */
  runIds(scope: CoreActor, agentId: string): Promise<string[]>;
  /** Heartbeat-bounded no-work barrier for REFUSED starts, which have no run id to
   * settle: resolves only once every queue/outbox a start in these scopes could feed
   * has drained and been read back. Never a fixed sleep. */
  noWorkBarrier(scopes: readonly CoreActor[]): Promise<void>;
  runEvents(tenant: CoreTenant, runId: string): Promise<RunEvent[]>;
  /** Text stays in test memory; receipts carry only lengths/digests. */
  visibleOutput(page: Page): Promise<{ runId: string | null; text: string }>;
  relayReceipt(runId: string): Promise<RelayReceipt | null>;
  stopViaUi(page: Page): Promise<{ acknowledged: boolean }>;
  stopViaApi(actor: CoreActor, runId: string): Promise<{ status: number }>;
  /** Heartbeat-bounded persistence barrier, never a fixed sleep. */
  settled(runId: string): Promise<void>;
  debitIds(scope: CoreActor): Promise<string[]>;
  /** Clicks the run's own trace link in the Console and reads the FA Observe page. */
  openTrace(page: Page): Promise<{ url: string; destinationTraceId: string | null; renderedTraceId: string | null }>;
  /** Reads the FA Observe trace view rendered in `page` for that page's OWN session:
   * whether the accepted denial state is positively displayed, and how many trace
   * rows and content characters are rendered. Text stays in adapter memory. */
  observeTraceView(page: Page): Promise<TraceView>;
  observeReaders(identity: Omit<ObserveWitness, 'masked'>, owner: CoreTenant, foreign: CoreTenant): ObserveReaders;
  observeContent(owner: CoreTenant, traceId: string, page: Page): Promise<{ ui: string; api: string }>;
  /** The accepted Stage B composition's peer inventory for a release part, read
   * independently of the declarations it is compared with. */
  composedPeers(part: string): Promise<readonly string[]>;
  peerDeclarations(part: string): Promise<{ peer: string; declared: boolean }[]>;
  /** Accepted runtime observation of a flag that is not a release part: lm708.ts:83-87
   * deliberately does not infer one from the release-part declaration. */
  flagAdmission(flag: string, tenant: CoreTenant): Promise<{ admitted: boolean }>;
  dispose(): Promise<void>;
}
export interface TraceView { refused: boolean; rows: number; contentChars: number }

/** Opens the Stage B adapter; refuses doubles and incomplete tenant seeds. */
export async function openRunAdapter(): Promise<ConsoleCoreRunAdapter> {
  const mod = await resolveJ16Seam('runAdapter');
  let adapter: ConsoleCoreRunAdapter;
  try { adapter = await mod.openConsoleCoreRunAdapter(); } catch { throw new ConsoleBlocked('ADAPTER_REFUSED', 'runAdapter'); }
  if (!adapter || adapter.kind !== 'stage-b-product') throw new ConsoleBlocked('SEAM_INCOMPATIBLE', 'runAdapter.kind');
  return adapter;
}

export async function coreTenants(adapter: ConsoleCoreRunAdapter, selected: 'A' | 'B') {
  let pair: readonly CoreTenant[];
  try { pair = await adapter.tenants(); } catch { throw new ConsoleBlocked('TENANT_FIXTURE_MISSING', 'stage-b-seed'); }
  const owner = pair?.find(t => t.label === selected), foreign = pair?.find(t => t.label !== selected);
  if (pair?.length !== 2 || !owner || !foreign) throw new ConsoleBlocked('TENANT_FIXTURE_MISSING', 'stage-b-seed');
  return { owner, foreign };
}

/** HR-CJ-02/03 provider-free rehearsal stream for the OWNER's run. Producer:
 * gw-hr-cj e2e/stack/mock-llm/server.mjs:313-357 @ e0133094d1. A held stream has
 * written ONLY its role frame (:325); every content chunk is written by finish()
 * (:326-337), which release() calls and which ends the stream as completed
 * (:348-353). So a held stream renders no content (L1.04, L2.01 cannot pass) and a
 * released one completes before L2's running-state Stop (L2.02). The negative and
 * restored arms need content BEFORE the hold on a stream that stays open and
 * cancellable; no producer fixture offers that, so they are BLOCKED here, before
 * provisioning. The change composing such an accepted fixture replaces this gate;
 * nothing in it may be flipped to pass. */
export function rehearsalStreamGate(): never {
  throw new ConsoleBlocked('FIXTURE_CONTRACT_MISSING', 'upstream.content-before-hold');
}

/** HR-CJ-03 held stream, for the foreign tenant's running SIBLING only (L2.04 needs
 * it running, not streaming content). Never the owner's rehearsal stream. */
export async function heldUpstream(scenario: string,
  resolve: SeamResolver = resolveJ16Seam): Promise<{ baseUrl: string; witness: UpstreamWitness }> {
  const { MockUpstream } = await resolve('upstream');
  const upstream = new MockUpstream();
  return {
    baseUrl: upstream.baseUrl(scenario, { hold: true }),
    witness: {
      kind: 'rehearsal',
      checkpoint: () => upstream.checkpoint(scenario),
      since: (checkpoint: unknown, expected: number) => upstream.dispatchSince(checkpoint, expected),
      async state(): Promise<UpstreamState> {
        const streams = await upstream.streams(scenario) as { state: UpstreamState }[];
        return streams.length ? streams[streams.length - 1].state : 'unknown';
      },
    },
  };
}

// ===========================================================================
// J00 (phase-bound Console core consumer)
// ===========================================================================
// J00 consumer adapter only. No launcher, provisioning, LM reader, product API,
// JWT decoder, browser policy or replacement gateway fixture lives here.

export const CJ_STAGE_A = Object.freeze({
  sourceHead: '09fc47c13a60ed17c801d2effee6d38e167db40d',
  exportDigest: 'sha256:24b82e6e487e3c0ec3f1a7b056f12fd4c62c380f2017b6970f368d7f793798b8',
  // Source binding ONLY: this export explicitly excludes Stage B and does not
  // qualify WebKit, authorize execution, or lift publication-contract.ts.
});
export type Engine = 'chromium' | 'firefox' | 'webkit';
export type Phase = 'REHEARSAL' | 'WITHDRAWN' | 'TUESDAY_GA';
export type Tenant = 'A' | 'B' | 'F';
export type AuthMethod = 'session' | 'key';
export type Digest = `sha256:${string}`;
// J00's `ConsoleCoreBlocked` (renamed in the union; W3 owns that name above).
export class J00ConsoleCoreBlocked extends Error {
  readonly status = 'BLOCKED';
  readonly execution = 'NOT_RUN';
  readonly seam: string;
  constructor(seam: string) {
    // Only literal consumer-owned seam codes may reach an error/receipt.
    const safe = /^[A-Z0-9_]+$/.test(seam) ? seam : 'CONSOLE_CORE_SEAM';
    super(`BLOCKED/NOT_RUN ${safe}`);
    this.seam = safe;
    this.name = 'ConsoleCoreBlocked';
  }
}
export interface LM708 {
  digest: string; tuple?: Record<string, unknown>; source?: Record<string, unknown>;
  served?: string[]; admitted?: string[]; approvedParts?: string[];
  contracts?: Record<string, unknown>;
}
// StackIdentity: identical in W3 and J00; declared once in the W3 section above.
export interface HostObservation {
  status: number; contentType: string; url: string;
  headers: Readonly<Record<string, string>>; body: Buffer;
}
export interface HostExpectation {
  status: number; contentType: string;
  route: { label: string; matches(observation: HostObservation): boolean | Promise<boolean> };
}
export type HostAuth = { kind: 'session'; actor: ScopeActor } | {
  kind: 'key'; apiKey: string; secretKey: string; organizationId: string; workspaceId: string;
};
export interface HostClient {
  assert(host: string, path: string, expected: HostExpectation,
    options?: { method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'; data?: unknown }): Promise<HostObservation>;
  dispose(): Promise<void>;
}
// The pinned client deliberately hides callback errors. Preserve a consumer's
// typed missing-seam refusal without reclassifying ordinary contract failures.
export async function assertPreservingBlock(client: HostClient, host: string, path: string,
  expected: HostExpectation, options?: Parameters<HostClient['assert']>[3]): Promise<HostObservation> {
  let blocked: J00ConsoleCoreBlocked | undefined;
  try {
    const response = await client.assert(host, path, { ...expected, route: { ...expected.route,
      matches: async observation => {
        try { return await expected.route.matches(observation); }
        catch (error) {
          if (error instanceof J00ConsoleCoreBlocked) blocked = error;
          throw error;
        }
      },
    } }, options);
    if (blocked) throw blocked;
    return response;
  } catch (error) { throw blocked ?? error; }
}

// Explicit source gaps, NOT extensible runtime-supplied approval booleans.
// e0133094d1 lm708.ts:49-65 drops phase/effective rolloutStages, and :83-88
// explicitly excludes runtime flag resolution. Stage A INTERFACES excludes
// Stage B. Neither pin supplies an accepted phase/rollout resolver. Until a
// reviewed producer successor exists, BOTH legs must stop before applicability.
// Replacing this refusal requires joining resolved LM phase/effective product
// rollout flags to the exact LM/composition AND independently observed server
// configuration; absent/PENDING/mismatched fields must still block. Do not add
// guessed LM fields, a second LM parser, or a runtime-installed bypass here.
export function requireJ00PhaseRolloutBinding(): void {
  throw new J00ConsoleCoreBlocked('J00_SOURCE_BOUND_PHASE_ROLLOUT_CONTRACT');
}

// public-ingress.ts:39-55 proves only deployed byte parity. Neither pinned
// source issues the reviewed method/path inventory or semantic-family to
// approved-part mapping. Self-consistent observation arrays cannot supply it.
// A reviewed successor must independently pin those sets to the arm's LM,
// composition and manifest, compare method/path cases and enabled receipts
// exactly, and reject undeclared withdrawals. Semantic names are NOT part IDs.
export function requireJ00ReviewedRouteInventory(): void {
  throw new J00ConsoleCoreBlocked('J00_REVIEWED_ROUTE_PART_INVENTORY');
}

export type RouteCopies = Record<'edge' | 'ee' | 'console', string>;
export interface AdmissionInput {
  manifest: LM708; stack: StackIdentity; routeCopies: RouteCopies;
  orm: (script: string, stdin: string) => string | Promise<string>;
  actors: readonly [ScopeActor, ScopeActor]; hosts: { api: string; public: string };
  embed: { path: string; contentType: string; allowed: HostExpectation['route']; denied: HostExpectation['route'] };
}
export interface CoreSeams {
  readLM708(path: string, digest: string): LM708;
  bindLM708(manifest: LM708, stack: StackIdentity): void;
  evaluateApplicability(expression: string, manifest: LM708, stack: StackIdentity,
    tuples: Readonly<Record<string, Record<string, unknown>>>): 'capability' | 'absence';
  verifyConsoleAdmission(input: AdmissionInput): Promise<{ lm708Digest: string; phases: string[] }>;
  HostAssertionClient: { open(hosts: Record<string, string>, auth: HostAuth): Promise<HostClient> };
  publicIngressStatus(request: APIRequestContext, origin: string, path: string, actor?: ScopeActor): Promise<{ status: number }>;
  compareRouteManifests(copies: RouteCopies, manifest: LM708): { lm708Digest: string; digests: RouteCopies };
}

async function moduleAt(path: string, seam: string): Promise<Record<string, unknown>> {
  try { return await import(new URL(`${path}.ts`, import.meta.url).href) as Record<string, unknown>; }
  catch { throw new J00ConsoleCoreBlocked(seam); } // Never publish loader paths or raw errors.
}
function callable<T>(module: Record<string, unknown>, name: string, seam: string): T {
  if (typeof module[name] !== 'function') throw new J00ConsoleCoreBlocked(seam);
  return module[name] as T;
}
export async function loadConsoleCoreSeams(): Promise<CoreSeams> {
  // Exact source interfaces at gw-hr-cj e0133094d1:
  // e2e/lib/lm708.ts:69,75,89; console-round-trip.ts:11,23;
  // host-assertion.ts:42,48,69; public-ingress.ts:21,39.
  // Resolve only the eventually COMPOSED local lib, never another worktree,
  // an env-selected module, or an unpinned absolute export.
  const lm = await moduleAt('../../lib/lm708', 'HR_JRN_02_LM708');
  const ingress = await moduleAt('../../lib/public-ingress', 'HR_JRN_05_PUBLIC_INGRESS');
  return {
    readLM708: callable(lm, 'readLM708', 'HR_JRN_02_READ_LM708'),
    bindLM708: callable(lm, 'bindLM708', 'HR_JRN_02_BIND_LM708'),
    evaluateApplicability: callable(lm, 'evaluateApplicability', 'HR_CJ_01_APPLICABILITY'),
    // Do not make the unconditional public wall depend on an unused positive
    // admission seam. Resolve these only when that assertion actually needs them.
    verifyConsoleAdmission: async input => {
      const admission = await moduleAt('../../lib/console-round-trip', 'HR_CJ_01_ADMISSION');
      return callable<CoreSeams['verifyConsoleAdmission']>(admission, 'verifyConsoleAdmission', 'HR_CJ_01_ADMISSION')(input);
    },
    HostAssertionClient: { open: async (hosts, auth) => {
      const host = await moduleAt('../../lib/host-assertion', 'HR_CJ_01_HOST_ASSERTION');
      const client = callable<CoreSeams['HostAssertionClient']>(host, 'HostAssertionClient', 'HR_CJ_01_HOST_ASSERTION');
      return client.open(hosts, auth);
    } },
    publicIngressStatus: callable(ingress, 'publicIngressStatus', 'HR_JRN_05_PUBLIC_INGRESS'),
    compareRouteManifests: callable(ingress, 'compareRouteManifests', 'HR_JRN_05_MANIFEST_COPIES'),
  };
}

// The following is an OWED consumer input contract, NOT an assertion that a
// gateway export implements it. Stage A export INTERFACES.json:41-58 supplies
// only E5/E6 hooks and explicitly excludes Stage B. No source-bound J00 phase,
// personal-key issuer, signature verifier, lifecycle/fault or routing inventory
// adapter exists at that export or e0133094d1. A future reviewed composition
// installs this factory; until then requireJ00Runtime always throws BLOCKED.
// This explicit missing slot avoids inventing wire endpoints or passing JWT
// base64 decoding off as verification. Fixtures and credentials stay in memory.
export interface J00Identity {
  tenant: Tenant; actor: ScopeActor; role: string; admitted: boolean;
  personalKey: Extract<HostAuth, { kind: 'key' }>;
  keyOwnerId: string;
}
export interface VerifiedExchange {
  signatureVerified: boolean; audience: string; products: string[];
  organizationId: string; workspaceId: string; personId: string; role: string;
  authMethod: string; edgeStatus: number; edgeOrganizationId: string; edgePersonId: string;
}
export interface DenialObservation {
  control: 'anonymous' | 'non-admitted' | 'removed' | 'revoked' | 'deactivated' | 'downgraded';
  tenant: Tenant; auth: AuthMethod; status: number; expectedStatus: number;
  usableConsoleClaims: number; staleAuthorityUses: number; omittedClaimSignatureVerified: boolean;
}
export interface BoundReceipt {
  subject: string; kind: 'capability' | 'absence'; evidence: 'live';
  lm708Digest: string; compositionDigest: string; browser: Engine; phase: Phase;
  imageDigest: string; configDigest: string;
  digest: string; executed: number; skipped: number; failed: number;
}
export interface RoutingEvidence {
  // Observations only, NOT routing authority. These self-supplied lists cannot
  // establish completeness until requireJ00ReviewedRouteInventory is replaced
  // by an accepted source-bound producer contract and its exact-set joins.
  requiredHidden: string[];
  hidden: { id: string; path: string; method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
    expected: HostExpectation }[];
  enabledFamilies: string[]; familyReceipts: Record<string, BoundReceipt>;
  withdrawnBaseline?: BoundReceipt & { unchanged: boolean };
  phaseCases: { tenant: Tenant | 'anonymous'; family: string; declared: boolean;
    authorized: boolean; status: number; expectedStatus: number }[];
  // Reader must independently measure DNS AND ingress, even when HTTP is 404.
  publicNames: string[]; dns: { name: string; resolvesToEE: boolean }[];
  ingress: { name: string; routesToEE: boolean }[];
}
export interface J00Runtime {
  lmPath: string; lmDigest: string; stack: StackIdentity;
  tuples: Record<string, Record<string, unknown>>;
  binding: { phase: Phase; compositionDigest: string; imageDigest: string; configDigest: string;
    stageAExportDigest: string; stageBDerivationDigest: string; runtimeAcceptanceDigest: string;
    browser: Engine; qualifiedEngines: Engine[];
    // OWED Stage A/composition input for the dedicated Console-origin listener
    // (fix/cdn-runtime-config-744): exact listener origin, the deployed image's
    // non-empty root document and immutable-js asset file (the rulings
    // are fixed in the matrix), and the listener API cases' bearer values (a Future AGI browser
    // token plus one distinct pre-minted assertion per case, in memory only).
    // Also appListenerUrl: the exact http origin of the gateway's app listener
    // ITSELF (source 445-471 `base`; not the edge or frontend in front of it,
    // never the Console listener origin), where the browser token must exchange.
    // Absent/malformed => those J00.L3 Console-origin cells are BLOCKED value-free.
    // J00 never derives it from origins.console, origins.public/api, consoleUrl or
    // an env default.
    consoleOrigin?: ConsoleOriginBinding };
  origins: { api: string; public: string; console: string }; consoleUrl: string;
  // Must contain A/B owners and members, plus independently created fresh F.
  identities: J00Identity[];
  readServerAdmission(): Promise<{ phase: Phase; scope: 'all' | 'named' | 'none'; allowlist: string[];
    admittedOrganizations: string[] }>;
  admissionInput: Omit<AdmissionInput, 'manifest' | 'stack' | 'actors' | 'hosts'>;
  readRouteCopies(): Promise<RouteCopies>;
  // Must verify signature/JWKS/audience and CONSUME the assertion on the actual
  // edge. No synthetic token, decoded-only claim, or normalized passed boolean.
  verifyAndConsumeExchange(response: HostObservation, identity: J00Identity, auth: AuthMethod): Promise<VerifiedExchange>;
  // Attach observation BEFORE calling navigate; never reconstruct a handshake
  // or document response by re-fetching after page.goto has already completed.
  observeFrame(page: Page, navigate: () => Promise<void>): Promise<{ src: string; documentUrl: string; documentStatus: number;
    handshakeOrigin: string; handshakeCompleted: boolean }>;
  // Expected route identities/serializers are producer-owned; no guessed selectors.
  navigation(identity: J00Identity): Promise<{ route: string; kind: 'deep-link' | 'search' | 'palette';
    expectedAllowed: boolean; actualAllowed: boolean }[]>;
  denialControls(): Promise<{ observations: DenialObservation[]; j04L1: BoundReceipt }>;
  capabilityFaults(): Promise<{ tenant: Tenant; source: 'capabilities' | 'exchange';
    state: 'loading' | 'error'; priorPositive: boolean; admittedUI: boolean; usableClaims: number; restored: boolean }[]>;
  withSandboxState(state: 'disabled' | 'capacity-full', run: () => Promise<void>): Promise<{
    state: 'disabled' | 'capacity-full'; creationRefused: boolean; restored: boolean }>;
  otherProducts(): Promise<{ tenant: Tenant; product: string; declared: boolean;
    expectedAllowed: boolean; capabilityAllowed: boolean; backendAllowed: boolean; uiAllowed: boolean;
    deepLinkAllowed: boolean; searchAllowed: boolean; paletteAllowed: boolean }[]>;
  shellAbsence(): Promise<{ registeredOmegaRoutes: number; usableConsoleClaims: number; switchers: number }>;
  readRoutingEvidence(): Promise<RoutingEvidence>;
  dispose(): Promise<void>;
}
export type J00RuntimeFactory = (input: { scopes: ScopeActors; browser: Engine; testInfo: TestInfo }) => Promise<J00Runtime>;
let runtimeFactory: J00RuntimeFactory | undefined;
/** Composition-owned, in-memory E5 handoff. Not an env bypass or acceptance grant. */
export function installJ00RuntimeFactory(factory: J00RuntimeFactory): void {
  if (runtimeFactory || typeof factory !== 'function') throw new J00ConsoleCoreBlocked('J00_RUNTIME_FACTORY');
  runtimeFactory = factory;
}
export async function requireJ00Runtime(input: Parameters<J00RuntimeFactory>[0]): Promise<J00Runtime> {
  if (!runtimeFactory) throw new J00ConsoleCoreBlocked('J00_STAGE_B_LIVE_ADAPTER');
  return runtimeFactory(input);
}
