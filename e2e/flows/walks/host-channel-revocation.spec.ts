// JRN-E2E-004 positive Console CORE bodies. Canonical JOURNEYS-708.json:
// L1 45s, L2 healthy 35s / signal-loss 15min FROM KEY ISSUANCE, L5 always.
// J04/PACKET.json L1-01..11, L2-01..09, L5-01..06 are the assertion inventory.
// Missing accepted public mutation/clock/claim/projection callables are producer
// gates, NOT permission to invent routes, SQL writes, claims or fake timers.
import type { BrowserContext, TestInfo } from '@playwright/test';
import { flowAnnotation } from '../../lib/flow-meta';
import { JOURNEY_PUBLICATION_ACCEPTED, JOURNEY_PUBLICATION_GAP } from '../journeys/publication-contract';
import { test, bindCore, evaluateCore, check, checkCapturePolicy, coreBrowserContext, ConsoleCoreBlocked, ConsoleCoreFailure,
  coreReceipt, coreLifecycleControls, stageCoreChild, requireProducer, resolveSeam, sha, verifyAdmission,
  type Binding, type CoreContext, type CoreRuntime } from '../journeys/console-core-seams';
import { beforeRevocation, observeDuringRevocation, afterRevocation, finishRevocation, assertProtectedStateUnchanged,
  collectDuringSamples, siblingActivityControls, type ActionWindow, type ChildOriginal, type SiblingPort } from './j04-sibling';

// All credential-bearing arms, including failure/negative/restored, suppress
// captures BEFORE fixture use. This does not replace accepted reporter/observer
// sentinel controls. Publication refusal is the same definition-time gate as J03.
test.use({ scopeRoster: { orgB: [3] }, trace: 'off', video: 'off', screenshot: 'off' });
test.skip(!JOURNEY_PUBLICATION_ACCEPTED, JOURNEY_PUBLICATION_GAP);
const FLOW = 'JRN-E2E-004';
const ASSERTION_MAX_MS = 45_000;
const SIGNAL_UP_MAX_MS = 35_000;
const STREAM_MAX_MS = 900_000;
// NEW native-cap case only: 15min + bounded 3min setup/restoration/cleanup.
// Individually reviewed G5 budget required; never widen historical timeouts.
const NATIVE_CAP_CASE_MS = 1_080_000;
type Credential = 'browser-session' | 'personal-key';
type Mutation = 'removal' | 'downgrade' | 'deactivation';
const credentials: readonly Credential[] = ['browser-session', 'personal-key'];
interface Subject { person: string; organization: string; workspace: string; role: 'admin' | 'member' }
interface Claim {
  signatureVerified: boolean; person: string; organization: string; workspace: string;
  role: string; products: string[]; authMethod: string; issuedAt: number; expiresAt: number;
}
interface Mint {
  status: number; claim: Claim | null; assertionHandle: string | null;
  newlyIssued: number; requestStartedAt: number; // authoritative same clock domain
}
interface Consume {
  status: number; accepted: boolean; executions: number; at: number;
  refusalReason: 'none' | 'expired' | 'replay' | 'revoked' | 'other';
}
interface Commit { at: number; committed: boolean; subject: Subject; removalEventDigest: string | null }
interface OperationWindow { startedAt: number; completedAt: number }
interface MintFixture {
  binding: Binding; subject: Subject; sibling: SiblingPort;
  samePersonPersonalKey: boolean; browserSessionWarm: boolean;
  principalResidualContractAccepted: boolean;
  mint(auth: Credential, count: 1 | 5): Promise<Mint[]>;
  mintViewer(auth: Credential): Promise<Mint>;
  consume(assertionHandle: string): Promise<Consume>;
  // Consumer requirement behind the CLOSED producer join, not a delivered API:
  // real public mutation holds AFTER commit at a backend barrier across fresh B
  // progress + sample, before native completion. Never use post-expiry recovery.
  mutate(action: Mutation, sample: () => Promise<void>): Promise<Commit & OperationWindow>;
  waitUntilAfter(expiry: number): Promise<void>; // native clock, no time travel
  dispose(): Promise<void>;
}
interface StreamStart {
  subject: Subject; keyHandle: string; keyDigest: string; streamDigest: string; issuedAt: number; runHandle: string;
  serverOpen: boolean; admittedConsole: boolean; progress: { at: number; sequence: number }[];
}
interface StreamEnd {
  endedAt: number; serverEnded: boolean; cause: 'revoked' | 'key-age-cap' | 'client-abort' | 'crash' | 'other';
  postCloseApplicationEvents: number;
}
interface Delivery {
  protocol: string; subject: Subject; removalCommitAt: number; attemptedAt: number; acknowledgedAt: number | null;
  recheckedAt: number | null;
  eventDigest: string; recheckEventDigest: string; streamDigest: string; keyDigest: string;
}
interface Reconnect extends OperationWindow {
  status: number; opened: boolean; replayPayloads: number; applicationFrames: number;
}
interface Fence { acknowledgedAt: number; scopedOnlyToA: boolean; siblingDeliveryHealthy: boolean }
interface StreamFixture {
  binding: Binding; sibling: SiblingPort;
  // Fresh A subject per arm. For fault arm age A key BEFORE starting B, so B's
  // own issuance cap is strictly later than A deadline, not simultaneous expiry.
  start(): Promise<StreamStart>;
  remove(sample: () => Promise<void>): Promise<Commit & OperationWindow>;
  delivery(): Promise<Delivery>;
  awaitServerEnd(deadline: number, sampleSibling: () => Promise<void>): Promise<StreamEnd>;
  // The accepted producer must expose a real in-flight checkpoint and native
  // completion clock. Sampling AFTER a finished operation cannot qualify it.
  // remove/reconnect/mintRemoved/restoreDelivery must hold at a backend barrier
  // until B progresses and sample completes, including millisecond denials.
  // Poll callbacks likewise wait for fresh B progress; no invented minimum hold.
  reconnect(auth: Credential, mode: 'last-event-id' | 'fresh', sample: () => Promise<void>): Promise<Reconnect>;
  mintRemoved(auth: Credential, sample: () => Promise<void>): Promise<Mint & OperationWindow>;
  disableADelivery(): Promise<Fence>;
  deliveryCountsSince(fence: Fence): Promise<{ attemptedA: number; deliveredA: number; deliveredB: number }>;
  healthyNoRemovalControl(): Promise<{ serverOpen: boolean; newProgressEvents: number; crashed: boolean }>;
  restoreDelivery(sample: () => Promise<void>): Promise<OperationWindow>;
  // MUST be a newly authorized membership, not resurrection of removed A.
  freshRestorationFixture(): Promise<StreamFixture>;
  dispose(): Promise<void>;
}
interface SiblingJoin {
  binding: Binding;
  // Exact resolved LM + discovered L1-L4 arm inventory, independently pinned;
  // includes positive, fault, denial and restoration arms, including H8/selfhost
  // when selected. Core-only receipts must NOT shrink this inventory.
  inventoryAccepted: boolean;
  inventory: readonly { leg: ChildOriginal['leg']; armDigest: string; originalDigest: string }[];
  children(): Promise<readonly { bytes: string; expectedDigest: string }[]>;
}

// These are consumer ports, NOT exports that exist in gw-hr-cj. The central
// adapter's requireProducer remains CLOSED pending the exact source handoffs.
// Existing seams are resolved only from the composed checkout, not imported
// from another worktree or invoked through a home-grown client/runner.
async function requireJ04Policy(info: TestInfo, runtime: CoreRuntime) {
  localOracleControls(); await siblingActivityControls(); await coreLifecycleControls(); // Synthetic only.
  // Verifier requires e2e/playwright.j04.config.ts, but that path is OUTSIDE
  // W3's current write allowlist. Do not author it here or silently use walk
  // multi-project defaults. Producer must supply project-less single --browser
  // derivation, reject --project, resolved settings, and sentinel-failure proof.
  // The auto coreGuard already runs both checks BEFORE credential setup. These
  // are defense-in-depth, not the first policy admission point.
  checkCapturePolicy(runtime, info);
  requireProducer('J04_CONFIG_AND_FAILURE_EMITTER');
}
const subjectEqual = (a: Subject, b: Subject) => a.person === b.person &&
  a.organization === b.organization && a.workspace === b.workspace;
const finiteClock = (n: number) => Number.isFinite(n) && n > 0;
const digest = (s: unknown): s is string => typeof s === 'string' && /^[a-f0-9]{64}$/.test(s);
function bindSibling(port: SiblingPort, binding: Binding, subject: Subject, leg: 'L1' | 'L2') {
  const s = port.scope;
  check(s.lmDigest === binding.expectedDigest.replace(/^sha256:/, '') && s.candidateHead === binding.candidateHead &&
    s.unionDigest === binding.unionDigest && s.phase === binding.phase && s.browser === binding.browser && s.leg === leg,
  'L5-06.CHILD_BINDING');
  check(s.orgId !== subject.organization && !s.userIds.includes(subject.person), 'L5-05.DISTINCT_B');
}
function minted(m: Mint, subject: Subject, auth: Credential, role: string): boolean {
  const c = m.claim;
  return m.status === 200 && m.newlyIssued === 1 && !!m.assertionHandle && !!c &&
    c.signatureVerified === true && c.person === subject.person && c.organization === subject.organization &&
    c.workspace === subject.workspace && c.role === role && c.products.includes('console') &&
    (auth !== 'personal-key' || c.authMethod === 'api_key') && finiteClock(c.issuedAt) &&
    finiteClock(c.expiresAt) && c.expiresAt > c.issuedAt && c.expiresAt - c.issuedAt <= ASSERTION_MAX_MS;
}
function deniedMint(m: Mint): boolean {
  return [401, 403].includes(m.status) && m.claim === null && m.assertionHandle === null && m.newlyIssued === 0;
}
function deniedConsume(c: Consume): boolean { return [401, 403].includes(c.status) && !c.accepted && c.executions === 0; }
function expiredRefused(c: Consume, signedExpiry: number): boolean {
  return finiteClock(c.at) && c.at > signedExpiry && deniedConsume(c) && c.refusalReason === 'expired';
}
function principalAggregate(consumes: readonly Consume[]): boolean {
  return consumes.length === 10 && consumes.filter(r => r.accepted).length <= 1;
}
function deliveryCorrelated(d: Delivery, commit: Commit, start: StreamStart): boolean {
  return digest(commit.removalEventDigest) && d.eventDigest === commit.removalEventDigest &&
    d.recheckEventDigest === commit.removalEventDigest && d.streamDigest === start.streamDigest && d.keyDigest === start.keyDigest &&
    d.protocol === 'omega-host-events/v1' && subjectEqual(d.subject, start.subject) && d.removalCommitAt === commit.at &&
    finiteClock(d.attemptedAt) && d.attemptedAt >= commit.at && d.acknowledgedAt !== null &&
    finiteClock(d.acknowledgedAt) && d.acknowledgedAt >= d.attemptedAt && d.acknowledgedAt <= commit.at + SIGNAL_UP_MAX_MS &&
    d.recheckedAt !== null && finiteClock(d.recheckedAt) && d.recheckedAt >= d.attemptedAt &&
    d.recheckedAt <= commit.at + SIGNAL_UP_MAX_MS;
}
function live(s: StreamStart): boolean {
  return s.admittedConsole && s.serverOpen && !!s.keyHandle && digest(s.keyDigest) && digest(s.streamDigest) && !!s.runHandle && finiteClock(s.issuedAt) &&
    s.progress.length >= 2 && s.progress.every((p, i) => finiteClock(p.at) && p.at >= s.issuedAt &&
      Number.isSafeInteger(p.sequence) && (i === 0 || p.sequence > s.progress[i - 1].sequence && p.at >= s.progress[i - 1].at));
}
function streamClosed(end: StreamEnd, commit: Commit, start: StreamStart, signal: 'up' | 'down'): boolean {
  const deadline = signal === 'up' ? commit.at + SIGNAL_UP_MAX_MS : start.issuedAt + STREAM_MAX_MS;
  return end.serverEnded && (signal === 'up' ? end.cause === 'revoked' : ['revoked', 'key-age-cap'].includes(end.cause)) &&
    finiteClock(end.endedAt) && end.endedAt >= commit.at && end.endedAt <= deadline && end.postCloseApplicationEvents === 0;
}
function replayDenied(r: Reconnect): boolean {
  return [401, 403].includes(r.status) && !r.opened && r.replayPayloads === 0 && r.applicationFrames === 0;
}

// Synthetic in-memory fixtures prove the actual scalar oracles can go red.
// These do not substitute for prepared-image source mutations and restoration.
function localOracleControls() {
  const subject: Subject = { person: 'synthetic-person', organization: 'synthetic-org', workspace: 'synthetic-workspace', role: 'member' };
  const claim: Claim = { signatureVerified: true, person: subject.person, organization: subject.organization,
    workspace: subject.workspace, role: 'member', products: ['console'], authMethod: 'api_key', issuedAt: 1_000_000, expiresAt: 1_045_000 };
  const mint: Mint = { status: 200, claim, assertionHandle: 'synthetic-handle', newlyIssued: 1, requestStartedAt: 1_000_000 };
  const start: StreamStart = { subject, keyHandle: 'synthetic-key-handle', keyDigest: sha('key'), streamDigest: sha('stream'), issuedAt: 1_000_000, runHandle: 'synthetic-run',
    serverOpen: true, admittedConsole: true, progress: [{ at: 1_001_000, sequence: 1 }, { at: 1_002_000, sequence: 2 }] };
  const commit: Commit = { subject, committed: true, at: 1_010_000, removalEventDigest: sha('removal-event') };
  const end: StreamEnd = { endedAt: 1_045_000, serverEnded: true, cause: 'revoked', postCloseApplicationEvents: 0 };
  const pair = (id: string, green: () => boolean, red: () => boolean) => {
    check(green(), id);
    let killed = false;
    try { check(red(), id); } catch (e) { killed = e instanceof ConsoleCoreFailure && e.assertion === id; }
    check(killed, 'J04.CONTROL_MUST_KILL'); check(green(), id);
  };
  for (const changed of [{ organization: 'wrong-org' }, { person: 'wrong-person' }, { workspace: 'wrong-workspace' },
    { role: 'admin' }, { products: ['studio_tab'] }, { signatureVerified: false }, { authMethod: 'session' }, { expiresAt: 1_045_001 }])
    pair('L1-02-03.BASELINE', () => minted(mint, subject, 'personal-key', 'member'),
      () => minted({ ...mint, claim: { ...claim, ...changed } }, subject, 'personal-key', 'member'));
  pair('L1.IMMEDIATE_REFUSAL', () => deniedMint({ ...mint, status: 403, claim: null, assertionHandle: null, newlyIssued: 0 }), () => deniedMint(mint));
  pair('L2-01.LIVE_PROGRESS', () => live(start), () => live({ ...start, progress: start.progress.slice(0, 1) }));
  pair('L2-03.SERVER_CLOSE_35000', () => streamClosed(end, commit, start, 'up'),
    () => streamClosed({ ...end, endedAt: commit.at + 35_001 }, commit, start, 'up'));
  pair('L2-03.SERVER_CLOSE_35000', () => streamClosed(end, commit, start, 'up'),
    () => streamClosed({ ...end, cause: 'client-abort' }, commit, start, 'up'));
  pair('L2-07.ISSUANCE_CAP_900000', () => streamClosed({ ...end, cause: 'key-age-cap', endedAt: start.issuedAt + STREAM_MAX_MS }, commit, start, 'down'),
    () => streamClosed({ ...end, cause: 'key-age-cap', endedAt: commit.at + STREAM_MAX_MS }, commit, start, 'down'));
  const expired: Consume = { status: 401, accepted: false, executions: 0, at: 1_045_001, refusalReason: 'expired' };
  const residualBatch = (accepted: number) => Array.from({ length: 10 }, (_, i): Consume => ({
    status: i < accepted ? 200 : 403, accepted: i < accepted, executions: i < accepted ? 1 : 0,
    at: commit.at + 1, refusalReason: i < accepted ? 'none' : 'revoked',
  }));
  pair('L1-10.PRINCIPAL_AGGREGATE', () => principalAggregate(residualBatch(1)), () => principalAggregate(residualBatch(2)));
  check(principalAggregate(residualBatch(0)), 'L1-10.ZERO_RESIDUAL_ALLOWED');
  pair('L1-11.SIGNED_EXPIRY', () => expiredRefused(expired, claim.expiresAt),
    () => expiredRefused({ ...expired, status: 200, accepted: true, executions: 1, refusalReason: 'none' }, claim.expiresAt));
  pair('L1-11.SIGNED_EXPIRY', () => expiredRefused(expired, claim.expiresAt),
    () => expiredRefused({ ...expired, refusalReason: 'revoked' }, claim.expiresAt));
  const delivery: Delivery = { protocol: 'omega-host-events/v1', subject, removalCommitAt: commit.at,
    attemptedAt: commit.at, acknowledgedAt: commit.at + 1, recheckedAt: commit.at + 2,
    eventDigest: commit.removalEventDigest!, recheckEventDigest: commit.removalEventDigest!,
    streamDigest: start.streamDigest, keyDigest: start.keyDigest };
  for (const change of [{ eventDigest: sha('other-event-same-principal') },
    { recheckEventDigest: sha('periodic-recheck') }, { streamDigest: sha('other-stream') }])
    pair('L2-02.TAGGED_ACK_AND_RECHECK', () => deliveryCorrelated(delivery, commit, start),
      () => deliveryCorrelated({ ...delivery, ...change }, commit, start));
  const refused: Reconnect = { status: 403, opened: false, replayPayloads: 0, applicationFrames: 0,
    startedAt: 1_050_000, completedAt: 1_050_010 };
  for (const changed of [{ status: 200 }, { opened: true }, { replayPayloads: 1 }, { applicationFrames: 1 }])
    pair('L2-04-05.RECONNECT', () => replayDenied(refused), () => replayDenied({ ...refused, ...changed }));
  const selected: OwnSelection = { l1: 'capability', l2: 'capability' };
  const inventory = positiveOwnArms.map(arm => ({ leg: arm.slice(0, 2), armDigest: sha(arm) }));
  assertOwnArms(inventory, selected);
  let killed = false;
  try { assertOwnArms(inventory.filter(c => c.armDigest !== sha('L2.signals-down')), selected); }
  catch (e) { killed = e instanceof ConsoleCoreFailure && e.assertion === 'L5-01.OWN_ARM_INVENTORY'; }
  check(killed, 'L5-01.INVENTORY_CONTROL_MUST_KILL'); assertOwnArms(inventory, selected);
  // Legitimate non-Console compositions need L2 absence, not impossible Console
  // positives. L1 can be studio_tab-positive, exchange-absent or served-vacuous.
  for (const l1 of ['capability', 'absence', 'vacuous'] as const) {
    const absent: OwnSelection = { l1, l2: 'absence' };
    const originals = mandatoryOwnArms(absent).map(arm => ({ leg: arm.slice(0, 2), armDigest: sha(arm) }));
    assertOwnArms(originals, absent);
    let missingKilled = false;
    try { assertOwnArms(originals.filter(c => c.armDigest !== sha('L2.absent')), absent); }
    catch (e) { missingKilled = e instanceof ConsoleCoreFailure && e.assertion === 'L5-01.OWN_ARM_INVENTORY'; }
    check(missingKilled, 'L5-01.ABSENCE_ORIGINAL_REQUIRED'); assertOwnArms(originals, absent);
  }
}

// Every mutation/fault/restoration owns an active B witness, even on an A
// assertion failure. No relogin, key rotation or replacement B run is allowed.
async function withSibling(info: TestInfo, port: SiblingPort,
  action: (sample: () => Promise<void>) => Promise<ActionWindow>, armId = port.scope.armId): Promise<void> {
  const before = await beforeRevocation(port);
  const incomplete = async () => {
    // Failure paths still get a FINAL authoritative read, not a cached during
    // sample. They never manufacture clocks or qualify a sibling child.
    const after = await afterRevocation(port, before);
    await info.attach('j04-sibling-incomplete', { contentType: 'application/json', body: JSON.stringify({
      lmDigest: before.scope.lmDigest, baselineDigest: sha(before.bytes), finalDigest: sha(after.bytes),
      protectedEqual: before.bytes === after.bytes, outcome: 'FAIL', concurrentActivityQualified: false,
    }) });
  };
  try {
    const { during, window } = await collectDuringSamples(() => observeDuringRevocation(port, before), action);
    const receipt = await finishRevocation(port, before, during, window);
    stageCoreChild(info, { ...receipt, armDigest: sha(armId), samples: during.length });
  } catch (error) {
    try { await incomplete(); }
    catch {
      // Neither a failed final read nor its marker can replace the killed oracle.
      try { await info.attach('j04-sibling-incomplete', { contentType: 'application/json',
        body: JSON.stringify({ outcome: 'FAIL', reason: 'INCOMPLETE_READ_FAILED', concurrentActivityQualified: false }) }); }
      catch { /* Preserve the original action/temporal error, never raw reader data. */ }
    }
    throw error;
  }
}

/** L1-01..11. Three independent subjects, never remove then downgrade an already
 * removed user. Fresh same-scope baseline must succeed BEFORE each change. */
async function mintMutation(fixture: MintFixture, action: Mutation, info: TestInfo): Promise<number> {
  if (!fixture.principalResidualContractAccepted) throw new ConsoleCoreBlocked('J04_PRINCIPAL_RESIDUAL_CONTRACT');
  // CARRIED P2: canonical 'immediately' has no accepted numeric scheduling
  // allowance in the inspected packet. Do not invent one, or qualify a delayed
  // first mint on requestStartedAt >= commit alone. G5 must deliver a reviewed
  // commit-to-first-request fence/bound and its late-mint red control.
  requireProducer('J04_FIRST_MINT_FENCE');
  check(fixture.samePersonPersonalKey && fixture.browserSessionWarm, 'L1-01.OWNED_WARM_CREDENTIALS');
  check(fixture.subject.role === (action === 'downgrade' ? 'admin' : 'member'), 'L1-01.INDEPENDENT_SUBJECT');
  bindSibling(fixture.sibling, fixture.binding, fixture.subject, 'L1');
  let assertions = 2;
  await withSibling(info, fixture.sibling, async sample => {
    const residual: Mint[] = [], expiring: Mint[] = [];
    for (const auth of credentials) {
      const baseline = await fixture.mint(auth, 1);
      check(baseline.length === 1 && minted(baseline[0], fixture.subject, auth, fixture.subject.role), 'L1-02-03.BASELINE');
      const consumed = await fixture.consume(baseline[0].assertionHandle!);
      check(consumed.accepted && consumed.status >= 200 && consumed.status < 300 && consumed.executions === 1 &&
        consumed.at >= baseline[0].claim!.issuedAt && consumed.at < baseline[0].claim!.expiresAt, 'L1-10.PRECHANGE_CONSUME');
      assertions += 2;
      await info.attach('j04-l1-baseline', { contentType: 'application/json', body: JSON.stringify({
        lm708Digest: fixture.binding.expectedDigest, action, credential: auth, status: baseline[0].status,
        claimBound: minted(baseline[0], fixture.subject, auth, fixture.subject.role),
        issuedAt: baseline[0].claim!.issuedAt, expiresAt: baseline[0].claim!.expiresAt,
        consumedAt: consumed.at, executions: consumed.executions,
      }) });
      // Strict residual interpretation includes pre-downgrade Admin assertions.
      const held = await fixture.mint(auth, 5), expiry = await fixture.mint(auth, 1);
      check(held.length === 5 && expiry.length === 1 && [...held, ...expiry].every(m =>
        minted(m, fixture.subject, auth, fixture.subject.role)), 'L1-10.MIXED_COUNT5');
      residual.push(...held); expiring.push(...expiry); assertions++;
    }
    const handles = [...residual, ...expiring].map(m => m.assertionHandle);
    check(new Set(handles).size === handles.length, 'L1-10.DISTINCT_ASSERTIONS');
    const commit = await fixture.mutate(action, sample);
    check(commit.committed && finiteClock(commit.at) && subjectEqual(commit.subject, fixture.subject) &&
      finiteClock(commit.startedAt) && finiteClock(commit.completedAt) &&
      commit.startedAt <= commit.at && commit.at <= commit.completedAt, 'L1.PUBLIC_COMMIT'); assertions++;
    // First attempt follows authoritative commit, with no poll-until-denied or
    // grace period. Repeat twice more to exercise prewarmed cache paths.
    for (let repetition = 0; repetition < 3; repetition++) for (const auth of credentials) {
      const next = await fixture.mint(auth, 1);
      check(next.length === 1 && next[0].requestStartedAt >= commit.at, 'L1.FIRST_NEXT_MINT');
      check(action === 'downgrade' ? minted(next[0], fixture.subject, auth, 'member') : deniedMint(next[0]),
        action === 'downgrade' ? 'L1-06-07.NEXT_ROLE' : action === 'removal' ? 'L1-04-05.IMMEDIATE_REFUSAL' : 'L1-08-09.IMMEDIATE_REFUSAL');
      await info.attach('j04-l1-post-commit', { contentType: 'application/json', body: JSON.stringify({
        lm708Digest: fixture.binding.expectedDigest, action, credential: auth, repetition,
        commitAt: commit.at, requestStartedAt: next[0].requestStartedAt,
        postCommitDelayMs: next[0].requestStartedAt - commit.at, status: next[0].status,
        newlyIssued: next[0].newlyIssued, nextRoleIsMember: next[0].claim?.role === 'member',
      }) });
      assertions++;
    }
    for (const auth of credentials) {
      const viewer = await fixture.mintViewer(auth);
      check(viewer.status === 403 && deniedMint(viewer), 'L1-06.VIEWER_REFUSAL'); assertions++;
    }
    let completedAt = commit.at;
    {
      // Aggregate PRINCIPAL/org/workspace upper bound, not just per-JWT replay.
      // The intentionally concurrent consumes discriminate two distinct old
      // assertions accepted once each. Zero successful residual consumes is OK.
      check(residual.length === 10 && expiring.length === 2 && residual.every(m =>
        m.claim!.issuedAt <= commit.at && commit.at < m.claim!.expiresAt), 'L1-10.LIVE_RESIDUAL_BATCH');
      const first = await Promise.all(residual.map(m => fixture.consume(m.assertionHandle!)));
      check(principalAggregate(first), 'L1-10.PRINCIPAL_AGGREGATE'); assertions++;
      for (const [i, r] of first.entries()) {
        const c = residual[i].claim!;
        check(finiteClock(r.at) && r.at >= commit.at && r.at < c.expiresAt &&
          r.at - c.issuedAt < ASSERTION_MAX_MS && (r.accepted ? r.status >= 200 && r.status < 300 &&
          r.executions === 1 : deniedConsume(r)), 'L1-10.ORIGINAL_EXPIRY');
        completedAt = Math.max(completedAt, r.at);
      }
      const second = await Promise.all(residual.map(m => fixture.consume(m.assertionHandle!)));
      check(second.every(r => deniedConsume(r) && finiteClock(r.at) && r.at >= commit.at), 'L1-10.NO_REPLAY'); assertions++;
      completedAt = Math.max(completedAt, ...second.map(r => r.at));
      await info.attach('j04-l1-principal-residual', { contentType: 'application/json', body: JSON.stringify({
        lm708Digest: fixture.binding.expectedDigest, action, commitAt: commit.at, batchPerCredential: 5,
        acceptedTotal: first.filter(r => r.accepted).length, replayAccepted: second.filter(r => r.accepted).length,
        observations: first.map((r, i) => ({ consumedAt: r.at, status: r.status, executions: r.executions,
          issuedAt: residual[i].claim!.issuedAt, expiresAt: residual[i].claim!.expiresAt })),
      }) });
      // Additional safety check on old authority; NOT the independent L1-11
      // expiration oracle (revocation/budget can explain these refusals).
      await fixture.waitUntilAfter(Math.max(...expiring.map(m => m.claim!.expiresAt)));
      await sample();
      for (const held of expiring) {
        const expired = await fixture.consume(held.assertionHandle!);
        check(expired.at > held.claim!.expiresAt && deniedConsume(expired), 'L1-10.OLD_AUTHORITY_AFTER_EXPIRY');
        completedAt = Math.max(completedAt, expired.at); assertions++;
        await info.attach('j04-l1-expiry', { contentType: 'application/json', body: JSON.stringify({
          lm708Digest: fixture.binding.expectedDigest, issuedAt: held.claim!.issuedAt,
          expiresAt: held.claim!.expiresAt, consumedAt: expired.at, refused: deniedConsume(expired),
        }) });
      }
    }
    return { startedAt: commit.startedAt, completedAt,
      checkpointWindow: { startedAt: commit.at, completedAt: commit.completedAt } };
  });
  return assertions + 6; // The six L5 assertion families executed by withSibling.
}

/** Independent active-principal scenarios. No removal, deactivation, downgrade
 * or residual batch is executed here, so none can mask an expiry/replay mutant. */
async function activeMintPass(fixture: MintFixture, mode: 'baseline' | 'expiry', info: TestInfo): Promise<number> {
  check(fixture.samePersonPersonalKey && fixture.browserSessionWarm && fixture.subject.role === 'member', 'L1-01.ACTIVE_SUBJECT');
  bindSibling(fixture.sibling, fixture.binding, fixture.subject, 'L1');
  let assertions = 2;
  await withSibling(info, fixture.sibling, async sample => {
    const used = new Set<string>(), held: { mint: Mint; auth: Credential }[] = [];
    let startedAt = Infinity, completedAt = 0;
    const fresh = async (auth: Credential) => {
      const ms = await fixture.mint(auth, 1);
      check(ms.length === 1 && minted(ms[0], fixture.subject, auth, 'member') &&
        finiteClock(ms[0].requestStartedAt) && !used.has(ms[0].assertionHandle!), 'L1-02-03.BASELINE');
      used.add(ms[0].assertionHandle!); assertions++;
      startedAt = Math.min(startedAt, ms[0].requestStartedAt);
      return ms[0];
    };
    const usable = async (m: Mint) => {
      const r = await fixture.consume(m.assertionHandle!);
      check(r.accepted && r.status >= 200 && r.status < 300 && r.executions === 1 &&
        r.at >= m.claim!.issuedAt && r.at < m.claim!.expiresAt, 'L1.ACTIVE_USABLE');
      completedAt = Math.max(completedAt, r.at); assertions++;
    };
    for (const auth of credentials) {
      const baseline = await fresh(auth);
      await usable(baseline);
      if (mode === 'expiry') held.push({ mint: await fresh(auth), auth });
      await sample();
    }
    if (mode === 'expiry') {
      check(held.length === 2, 'L1-11.UNUSED_HELD_ASSERTIONS');
      await fixture.waitUntilAfter(Math.max(...held.map(h => h.mint.claim!.expiresAt)));
      await sample();
      for (const { mint, auth } of held) {
        // Fresh same-principal authorization still works AFTER the old exp. No
        // removed principal, exhausted residual allowance or replay can explain
        // the unused held token's refusal. Both credential classes are covered.
        await usable(await fresh(auth));
        const expired = await fixture.consume(mint.assertionHandle!);
        check(expiredRefused(expired, mint.claim!.expiresAt), 'L1-11.SIGNED_EXPIRY'); assertions++;
        completedAt = Math.max(completedAt, expired.at);
        await info.attach('j04-l1-independent-expiry', { contentType: 'application/json', body: JSON.stringify({
          lm708Digest: fixture.binding.expectedDigest, credential: auth,
          issuedAt: mint.claim!.issuedAt, expiresAt: mint.claim!.expiresAt, consumedAt: expired.at,
          refusalReason: expired.refusalReason, executions: expired.executions,
        }) });
      }
    } else {
      // Completion follows the during sample, and comes from an actual backend
      // operation rather than Date.now or the browser's clock.
      await usable(await fresh('browser-session'));
    }
    return { startedAt, completedAt };
  });
  return assertions + 6;
}

/** L2-01..09. Real EE stream and host-event channel, never a standalone mock
 * fetch counted as an EE run. No real-model grant is needed for these legs. */
async function reconnectPass(fixture: StreamFixture, info: TestInfo, armId: string, notBefore: number) {
  let completedAt = notBefore;
  for (const auth of credentials) for (const mode of ['last-event-id', 'fresh'] as const) {
    await withSibling(info, fixture.sibling, async sample => {
      const r = await fixture.reconnect(auth, mode, sample);
      check(replayDenied(r), mode === 'last-event-id' ? 'L2-04.REPLAY_REFUSED' : 'L2-05.RECONNECT_REFUSED');
      check(finiteClock(r.startedAt) && finiteClock(r.completedAt) && r.startedAt >= completedAt &&
        r.completedAt >= r.startedAt, 'L2.DENIAL_OPERATION_CLOCK');
      completedAt = r.completedAt;
      return r;
    }, `${armId}.${mode}.${auth}`);
  }
  return { assertions: 4 * 8, completedAt };
}
async function streamPass(fixture: StreamFixture, signal: 'up' | 'down', info: TestInfo,
  armId = fixture.sibling.scope.armId): Promise<{ assertions: number; subject: Subject; completedAt: number }> {
  const start = await fixture.start();
  check(live(start), 'L2-01.LIVE_PROGRESS');
  bindSibling(fixture.sibling, fixture.binding, start.subject, 'L2');
  let assertions = 1, endedAt = 0;
  await withSibling(info, fixture.sibling, async sample => {
    let fence: Fence | undefined;
    if (signal === 'down') {
      fence = await fixture.disableADelivery();
      check(finiteClock(fence.acknowledgedAt) && fence.scopedOnlyToA && fence.siblingDeliveryHealthy, 'L2-06.A_ONLY_FENCE'); assertions++;
    }
    const commit = await fixture.remove(sample);
    check(commit.committed && subjectEqual(commit.subject, start.subject) && finiteClock(commit.at) &&
      commit.at >= start.progress[start.progress.length - 1].at && finiteClock(commit.startedAt) &&
      finiteClock(commit.completedAt) && commit.startedAt <= commit.at && commit.at <= commit.completedAt, 'L2.PUBLIC_REMOVAL');
    if (signal === 'down') check(commit.at > start.issuedAt && commit.at < start.issuedAt + STREAM_MAX_MS &&
      fence!.acknowledgedAt <= commit.startedAt && fence!.acknowledgedAt < commit.at, 'L2-07.KEY_AGE_ORIGIN');
    if (signal === 'up') {
      const d = await fixture.delivery();
      check(deliveryCorrelated(d, commit, start), 'L2-02.TAGGED_ACK_AND_RECHECK'); assertions++;
      await info.attach('j04-l2-delivery', { contentType: 'application/json', body: JSON.stringify({
        lm708Digest: fixture.binding.expectedDigest, signal, taggedScopeMatches: subjectEqual(d.subject, start.subject),
        commitAt: commit.at, attemptedAt: d.attemptedAt, acknowledgedAt: d.acknowledgedAt, recheckedAt: d.recheckedAt,
        eventDigest: d.eventDigest, recheckEventDigest: d.recheckEventDigest, streamDigest: d.streamDigest, keyDigest: d.keyDigest,
      }) });
    }
    const end = await fixture.awaitServerEnd(signal === 'up' ? commit.at + SIGNAL_UP_MAX_MS : start.issuedAt + STREAM_MAX_MS, sample);
    check(streamClosed(end, commit, start, signal), signal === 'up' ? 'L2-03.SERVER_CLOSE_35000' : 'L2-07.ISSUANCE_CAP_900000'); assertions++;
    await info.attach('j04-l2-native-close', { contentType: 'application/json', body: JSON.stringify({
      lm708Digest: fixture.binding.expectedDigest, signal, issuedAt: start.issuedAt, commitAt: commit.at,
      keyDigest: start.keyDigest, streamDigest: start.streamDigest,
      keyAgeAtRemovalMs: commit.at - start.issuedAt, endedAt: end.endedAt, cause: end.cause,
      fromIssuanceMs: end.endedAt - start.issuedAt, fromRemovalMs: end.endedAt - commit.at,
      postCloseApplicationEvents: end.postCloseApplicationEvents,
    }) });
    if (signal === 'down') {
      const c = await fixture.deliveryCountsSince(fence!);
      check(c.attemptedA > 0 && c.deliveredA === 0 && c.deliveredB > 0, 'L2-06.ATTEMPTED_NOT_DELIVERED'); assertions++;
      await info.attach('j04-l2-fence', { contentType: 'application/json', body: JSON.stringify({
        lm708Digest: fixture.binding.expectedDigest, acknowledgedAt: fence!.acknowledgedAt,
        attemptedA: c.attemptedA, deliveredA: c.deliveredA, deliveredB: c.deliveredB,
      }) });
      const healthy = await fixture.healthyNoRemovalControl();
      check(healthy.serverOpen && healthy.newProgressEvents > 0 && !healthy.crashed, 'L2-07.NO_REMOVAL_CONTROL'); assertions++;
    }
    endedAt = end.endedAt;
    return { startedAt: signal === 'down' ? fence!.acknowledgedAt : commit.startedAt,
      completedAt: Math.max(end.endedAt, commit.completedAt),
      checkpointWindow: { startedAt: commit.at, completedAt: commit.completedAt } };
  }, armId);
  // Denial operations happen after closure and have their OWN retained-B
  // witnesses. The closure window cannot silently stand in for these windows.
  const denial = await reconnectPass(fixture, info, armId, endedAt);
  return { assertions: assertions + 6 + denial.assertions, subject: start.subject, completedAt: denial.completedAt };
}

const positiveOwnArms = [
  ...['baseline', 'once', 'expiry', 'removal', 'downgrade', 'deactivation'].map(a => `L1.${a}`),
  'L2.signals-up', 'L2.signals-down', 'L2.restore', 'L2.signals-down.delivery-restoration',
  ...['L2.signals-up', 'L2.signals-down', 'L2.restore', 'L2.signals-down.post-restore'].flatMap(a =>
    credentials.flatMap(auth => ['last-event-id', 'fresh'].map(mode => `${a}.${mode}.${auth}`))),
  ...credentials.map(auth => `L2.signals-down.post-restore.mint.${auth}`),
];
type OwnSelection = { l1: 'capability' | 'absence' | 'vacuous'; l2: 'capability' | 'absence' };
function mandatoryOwnArms(selection: OwnSelection): string[] {
  return [
    ...(selection.l1 === 'capability' ? positiveOwnArms.filter(a => a.startsWith('L1.')) :
      [selection.l1 === 'absence' ? 'L1.absent' : 'L1.vacuous']),
    ...(selection.l2 === 'capability' ? positiveOwnArms.filter(a => a.startsWith('L2.')) : ['L2.absent']),
  ];
}
function assertOwnArms(inventory: readonly { leg: string; armDigest: string }[], selection: OwnSelection): void {
  check(mandatoryOwnArms(selection).every(arm => inventory.some(c => c.leg === arm.slice(0, 2) && c.armDigest === sha(arm))), 'L5-01.OWN_ARM_INVENTORY');
}
async function ownSelection(context: CoreContext): Promise<OwnSelection> {
  // Never let the child/inventory producer choose its own weaker applicability.
  // Both predicates are re-evaluated by the pinned LM reader/binder, not by UI
  // visibility, child labels or an inferred absence from missing originals.
  const l1 = await evaluateCore(context.binding, 'served:omega-session-exchange');
  const l2 = await evaluateCore(context.binding, 'served:omega-edge & served:ee-daemon & admitted:console');
  check([l1, l2].every(c => c.manifest.digest === context.manifest.digest), 'L5-01.PREDICATE_BINDING');
  return { l1: l1.branch === 'absence' ? 'absence' : l1.manifest.admitted!.length > 0 ? 'capability' : 'vacuous', l2: l2.branch };
}
export async function assertSiblingJoin(join: SiblingJoin, context: CoreContext, children: readonly ChildOriginal[]): Promise<number> {
  if (!join.inventoryAccepted) throw new ConsoleCoreBlocked('J04_ACTIVE_SIBLING_AND_CHILD_ORIGINALS');
  check(context.branch === 'capability', 'L5.ALWAYS_APPLICABLE');
  const selection = await ownSelection(context);
  // Missing absence/vacuity originals still fail. Their exact receipt contracts
  // remain owed; removing Console does not fabricate a served B activity proof.
  assertOwnArms(join.inventory, selection); assertOwnArms(children, selection);
  const key = (c: { leg: string; armDigest: string }) => `${c.leg}/${c.armDigest}`;
  check(join.inventory.length > 0 && new Set(join.inventory.map(key)).size === join.inventory.length &&
    children.length === join.inventory.length && new Set(children.map(key)).size === children.length, 'L5-01.COMPLETE_INVENTORY');
  // Inventory producer must include every applicable L1-L4 (and denial arms for
  // absent surfaces). Do not close unconditional L5 with only L1/L2 receipts.
  check(['L1', 'L2', 'L3', 'L4'].every(leg => join.inventory.some(c => c.leg === leg)), 'L5-01.ALL_CHILD_LEGS');
  for (const expected of join.inventory) {
    const c = children.find(child => key(child) === key(expected));
    check(!!c && c.schema === 'j04-sibling-consumer/v3' && c.executed === true && c.skipped === false && c.sourceOnly === false &&
      c.passed === false && c.bodyPassed === true && c.fixturesPassed === true && c.nativeCompletionPending === true && digest(c.nativeTestDigest) &&
      Number.isSafeInteger(c.samples) && c.samples > 0 && digest(c.browserRunDigest) && digest(c.browserContextDigest) &&
      Number.isSafeInteger(c.assertionCount) && c.assertionCount > 0 &&
      /^[a-f0-9]{64}$/.test(c.originalDigest) && c.originalDigest === expected.originalDigest, 'L5-06.EXECUTED_ORIGINAL');
    check(c.lmDigest === context.manifest.digest.replace(/^sha256:/, '') && c.unionDigest === context.binding.unionDigest &&
      c.candidateHead === context.binding.candidateHead && c.browser === context.binding.browser &&
      c.phaseDigest === sha(context.binding.phase), 'L5-06.EXACT_BINDING');
    check(c.protectedEqual === true && c.retainedSession === true && c.retainedPersonalKey === true && c.concurrentRun === true &&
      /^[a-f0-9]{64}$/.test(c.beforeDigest) && c.beforeDigest === c.duringDigest && c.beforeDigest === c.afterDigest,
    'L5-05.BASELINE_FINAL');
    check([c.beforeAt, c.duringAt, c.afterAt, c.actionStartedAt, c.actionCompletedAt].every(finiteClock) &&
      c.beforeAt <= c.actionStartedAt && c.duringAt >= c.actionStartedAt && c.duringAt <= c.actionCompletedAt &&
      c.afterAt >= c.actionCompletedAt, 'L5-04.CONCURRENT_RECEIPT');
    check([c.checkpointStartedAt, c.checkpointCompletedAt].every(finiteClock) &&
      c.checkpointStartedAt >= c.actionStartedAt && c.checkpointCompletedAt >= c.checkpointStartedAt &&
      c.checkpointCompletedAt <= c.actionCompletedAt && c.duringAt >= c.checkpointStartedAt &&
      c.duringAt <= c.checkpointCompletedAt && Array.isArray(c.observations) && c.observations.length === c.samples &&
      c.observations[0].at === c.duringAt && c.observations[0].activityAt >= c.checkpointStartedAt &&
      c.observations[0].activityAt <= c.checkpointCompletedAt && c.observations.every((o, i) =>
        o.protectedDigest === c.beforeDigest && digest(o.activityDigest) && finiteClock(o.at) && finiteClock(o.activityAt) &&
        o.activityAt <= o.at && o.activityAt >= c.actionStartedAt && o.at <= c.actionCompletedAt &&
        (i === 0 || o.at >= c.observations[i - 1].at && o.activityAt >= c.observations[i - 1].activityAt)), 'L5-04.ALL_CHECKPOINTS');
  }
  // A body/fixture boolean is NOT a completed native result. No accepted reporter
  // or result-reader handoff exists in this checkout. Keep the join BLOCKED until
  // it can independently bind each original's digest + nativeTestDigest + browser
  // run to a completed passed native test (including boundary/teardown errors).
  // Do not invent a reporter API, trust an adapter's passed=true, or finalize from
  // inside the still-running owning test. J03 capability receipts share this gate.
  return requireProducer<number>('COMPLETED_NATIVE_RESULT');
}

const detail = (suffix: string, goal: string) => ({
  tag: ['@flow', '@journey', '@journey-J04'],
  annotation: [flowAnnotation({ id: `${FLOW}.${suffix}`, area: 'journeys', userGoal: goal,
    steps: ['Resolve exact LM/Stage B and browser binding', 'Execute real dual-tenant observations and unchanged oracles'],
    backendChecks: ['Native bound; public authority; retained B before/during/after', 'No credential-bearing capture; no absent/core substitution'] }),
  { type: 'journey-flow-id', description: FLOW },
  { type: 'execution-gate', description: 'BLOCKED_NOT_RUN until HR01, accepted project-less J04 config, producer adapters and three-engine Stage B qualification' }],
});
// Factory contracts below are consumer requirements behind CLOSED joins, not
// newly invented producer exports. Admission proof precedes retained B creation.
type MintFactory = (context: CoreContext, browser: BrowserContext, arm: string) => Promise<MintFixture>;
type StreamFactory = (context: CoreContext, browser: BrowserContext, arm: string) => Promise<StreamFixture>;
async function prepareCore(info: TestInfo, runtime: CoreRuntime, expression: string,
  setContext: (c: CoreContext) => void): Promise<CoreContext> {
  await requireJ04Policy(info, runtime);
  const binding = requireProducer<Binding>('STAGE_B_BINDING');
  const context = await bindCore(binding, expression, info, runtime); setContext(context);
  return context;
}
for (const action of ['baseline', 'once', 'expiry', 'removal', 'downgrade', 'deactivation'] as const) {
  test(`${FLOW}.L1.${action}: dual-credential authority and sibling`, detail(`L1.${action}`, 'Fresh claims follow committed FA authority'), async ({ coreRuntime }, info) => {
    test.setTimeout(120_000);
    await coreReceipt(info, `J04.L1.${action}`, async setContext => {
      const context = await prepareCore(info, coreRuntime, 'served:omega-session-exchange', setContext);
      await resolveSeam('HostAssertionClient'); await resolveSeam('readJ04SiblingState');
      await verifyAdmission(context);
      const create = requireProducer<MintFactory>('J04_PUBLIC_MUTATIONS_AND_CLAIMS');
      const fixture = await create(context, coreBrowserContext(context), `L1.${action}`);
      try {
        check(fixture.binding === context.binding && fixture.sibling.scope.armId === `L1.${action}`, 'L1.FIXTURE_BINDING');
        // Exact packet L1-10 registration owns the mixed-credential count=5
        // aggregate/replay/original-expiry sequence on its OWN revoked subject.
        // It is not an active-principal same-token replay renamed "once".
        if (action === 'baseline' || action === 'expiry') return await activeMintPass(fixture, action, info);
        return await mintMutation(fixture, action === 'once' ? 'removal' : action, info);
      } finally { await fixture.dispose(); }
    });
  });
}
test(`${FLOW}.L2.signals-up: real stream close and replay refusal`, detail('L2.signals-up', 'Removal cuts live Console without harming B'), async ({ coreRuntime }, info) => {
  test.setTimeout(120_000);
  await coreReceipt(info, 'J04.L2', async setContext => {
    const context = await prepareCore(info, coreRuntime, 'served:omega-edge & served:ee-daemon & admitted:console', setContext);
    await resolveSeam('HostAssertionClient'); await resolveSeam('MockUpstream'); await resolveSeam('readJ04SiblingState');
    await verifyAdmission(context);
    const create = requireProducer<StreamFactory>('J04_EVENT_FENCE_AND_STREAM_CLOCK');
    const fixture = await create(context, coreBrowserContext(context), 'L2.signals-up');
    try {
      check(fixture.binding === context.binding && fixture.sibling.scope.armId === 'L2.signals-up', 'L2.FIXTURE_BINDING');
      return (await streamPass(fixture, 'up', info)).assertions;
    } finally { await fixture.dispose(); }
  });
});
test(`${FLOW}.L2.signals-down: native issuance cap and real restoration`, detail('L2.signals-down', 'A-only delivery loss cannot extend key lifetime'), async ({ coreRuntime }, info) => {
  test.setTimeout(NATIVE_CAP_CASE_MS);
  await coreReceipt(info, 'J04.L2', async setContext => {
    const context = await prepareCore(info, coreRuntime, 'served:omega-edge & served:ee-daemon & admitted:console', setContext);
    await resolveSeam('HostAssertionClient'); await resolveSeam('MockUpstream'); await resolveSeam('readJ04SiblingState');
    await verifyAdmission(context);
    const create = requireProducer<StreamFactory>('J04_EVENT_FENCE_AND_STREAM_CLOCK');
    const fixture = await create(context, coreBrowserContext(context), 'L2.signals-down');
    let deliveryRestored = false;
    try {
      check(fixture.binding === context.binding && fixture.sibling.scope.armId === 'L2.signals-down', 'L2.FIXTURE_BINDING');
      const fault = await streamPass(fixture, 'down', info);
      let restoredAt = 0;
      await withSibling(info, fixture.sibling, async sample => {
        const operation = await fixture.restoreDelivery(sample);
        check(operation.startedAt >= fault.completedAt, 'L2-09.RESTORE_CLOCK');
        restoredAt = operation.completedAt;
        return operation;
      }, 'L2.signals-down.delivery-restoration');
      deliveryRestored = true;
      const oldActor = await reconnectPass(fixture, info, 'L2.signals-down.post-restore', restoredAt);
      for (const auth of credentials) await withSibling(info, fixture.sibling, async sample => {
        const mint = await fixture.mintRemoved(auth, sample);
        check(deniedMint(mint) && mint.requestStartedAt >= oldActor.completedAt &&
          mint.startedAt >= oldActor.completedAt, 'L2-09.ORIGINAL_ACTOR_STILL_REVOKED');
        return mint;
      }, `L2.signals-down.post-restore.mint.${auth}`);
      const retained = await beforeRevocation(fixture.sibling);
      const restored = await fixture.freshRestorationFixture();
      try {
        // Only A is fresh. Same B port, identities and complete protected state;
        // no new session/key/run may hide damage caused by restoreDelivery.
        check(restored.binding === context.binding && restored.sibling === fixture.sibling, 'L2-09.RESTORATION_BINDING');
        assertProtectedStateUnchanged(retained, await afterRevocation(restored.sibling, retained));
        const result = await streamPass(restored, 'up', info, 'L2.restore');
        check(result.subject.person !== fault.subject.person && result.subject.organization === fault.subject.organization,
          'L2-09.FRESH_AUTHORIZED_SUBJECT');
        return fault.assertions + result.assertions + oldActor.assertions + 24;
      } finally { await restored.dispose(); }
    } finally {
      // Failure-only cleanup cannot produce a passing child; no unobserved
      // second restore operation runs on the successful path.
      try { if (!deliveryRestored) await fixture.restoreDelivery(async () => {}); }
      finally { await fixture.dispose(); }
    }
  });
});
for (const facet of ['inventory', 'session', 'key', 'run', 'state', 'join'] as const) {
  test(`${FLOW}.L5.${facet}: unconditional all-child sibling witness`, detail(`L5.${facet}`, 'B remains usable and byte-identical during every L1-L4 arm'), async ({ coreRuntime }, info) => {
    test.setTimeout(120_000);
    await coreReceipt(info, `J04.L5.${facet}`, async setContext => {
      const context = await prepareCore(info, coreRuntime, 'always', setContext);
      check(context.branch === 'capability', 'L5.ALWAYS_APPLICABLE');
      await resolveSeam('readJ04SiblingState');
      const join = requireProducer<SiblingJoin>('J04_ACTIVE_SIBLING_AND_CHILD_ORIGINALS');
      check(join.binding === context.binding, 'L5.JOIN_BINDING');
      // Each exact packet facet consumes original executed children; none can
      // pass on a weaker standalone projection or source-only inventory.
      const originals = await join.children();
      const children = originals.map(original => {
        check(digest(original.expectedDigest) && sha(original.bytes) === original.expectedDigest, 'L5-06.ORIGINAL_BYTES');
        let child: Omit<ChildOriginal, 'originalDigest'>;
        try { child = JSON.parse(original.bytes); } catch { check(false, 'L5-06.ORIGINAL_JSON'); }
        return { ...child, originalDigest: original.expectedDigest };
      });
      return assertSiblingJoin(join, context, children);
    });
  });
}

// Red-first fixture recipes. Change ONLY the named observation/source in an
// isolated local fixture; unchanged oracle must fail its indicated assertion,
// then pass after real source/fixture restoration. These declarations are NOT
// runtime red receipts. Missing source controls remain execution blockers.
export const J04_RED_CONTROLS = Object.freeze({
  'L1-01': 'Replace member key with org/system key or wrong owner; OWNED_WARM_CREDENTIALS fails.',
  'L1-02-03': 'All-denied baseline, wrong signature/person/org/workspace/product, or wrong api_key auth method fails BASELINE.',
  'L1-04-05': 'Bypass membership check in warm session/key mint; IMMEDIATE_REFUSAL fails on first post-commit mint.',
  'L1-06-07': 'Keep admin role on next assertion; NEXT_ROLE fails. Viewer denial alone cannot satisfy it.',
  'L1-08-09': 'Bypass user-active check; IMMEDIATE_REFUSAL fails with newly issued assertion.',
  'L1-10': 'Permit TWO distinct residual assertions once each; PRINCIPAL_AGGREGATE fails even if per-token replay works. Replay same token kills NO_REPLAY.',
  'L1-11': 'Signed exp-iat=45001ms fails BASELINE; independently held token accepted after exp fails SIGNED_EXPIRY.',
  'L2-01': 'No actual EE run/one progress event/closed baseline fails LIVE_PROGRESS.',
  'L2-02': 'Suppress exact tagged ack while recheck still runs, or ack wrong org; TAGGED_ACK_AND_RECHECK fails.',
  'L2-03': 'Server end at commit+35001ms or client-abort-only fails SERVER_CLOSE_35000.',
  'L2-04-05': 'Accept Last-Event-ID replay or fresh reconnect for either auth class; refusal oracle fails.',
  'L2-06': 'Fence B too or deliver any A event after fence; A_ONLY_FENCE/ATTEMPTED_NOT_DELIVERED fails.',
  'L2-07': 'End at removal+900000 rather than issuedAt+900000 with aged key; ISSUANCE_CAP_900000 fails. Crash fails NO_REMOVAL_CONTROL.',
  'L2-09': 'Restore config only with no ack/progress, or resurrect removed actor; restored up-arm/fresh-subject fails.',
  'L5-01-06': 'Drop any applicable L3/L4/fault child, duplicate arm, use skipped/source-only/cross-LM/browser original; join fails.',
  'L5-02-05': 'Replace B session/key/run, stop progress, or mutate any protected group; retained identity/liveness/full-byte oracle fails.',
  capture: 'Synthetic sentinel-shaped responses + deliberate failure must produce no sentinel in original outputs and no trace/video/screenshot/HAR/storageState. Re-enable EACH mode: resolved-policy must refuse before fixtures.',
});

// Withdrawal implementation is deliberately NOT claimed here. L1 absence still
// owes every A/B/fresh-org x role x credential exact-404 cell and removal while
// consumer stopped; served-but-vacuous must join executed J00.L2. L2 absence owes
// an executed same-binding J03.L5 absence+claim-omission original. This positive
// file blocks those arms rather than inventing a second weaker absence check.
// CARRIED inventory work: L1.absent/vacuous and L2.absent still need accepted
// original-receipt joins. L2.baseline/replay/reconnect/restore still need their
// packet-discoverable registrations and reviewed retained-fixture dependency
// handoff; folding them into signals-up/down is NOT an accepted crosswalk. Do
// not advertise exact packet inventory acceptance until these are authored.
