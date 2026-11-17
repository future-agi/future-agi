// J07: Local Studio embedded inside FA through omega-embed/1 (JOURNEYS-708.json J07.L1).
// Eleven integrated case IDs (PACKET.json legs[0].new_specs.test_ids), one real FA
// host plus one real built omega dev child per case, in Chromium, Firefox and
// (once the shared successor wf_e92197a0-c1b is qualified) WebKit. Assertion map:
//   J07-01 A01-A04   J07-02 A05-A08   J07-03 A09-A12,A29   J07-04 A13-A16
//   J07-05 A17-A20   J07-06 A21,A22   J07-06b A23          J07-07 A24-A27 (+A34 walk)
//   J07-08 A28       J07-09 A31-A34 (A30 adopts J00)       J07-10 A37
// A35/A36 are the Stage A kit/acceptance envelope, not assertions of this file.
//
// Runs only under playwright.j07.config.ts with a runner-supplied binding
// (J07_BINDING, see studio-embed.binding.cjs). Without one, against an omega dev
// child whose ancestry evidence for c103d224f0 (memory-only key) or 79164e25f3 (A27
// (b): keyless local link in a non-terminal listing) is missing, without G5a
// qualification of the installed failure-artifact producer, with an APP-SIDE
// descriptor of another build, an incomplete declared log set, a reporter outside the
// allowlist, a debug logger that could write protocol/network content, or any
// EFFECTIVE capture option (trace/screenshot/video, HAR, video, storage state), every
// case refuses with a constant "J07-BLOCKED <CODE>" before any login, child start
// or connect token. Today that is G5A_UNQUALIFIED by design.
// Under the ordinary playwright.config.ts the file is collected and skipped.
//
// Execution order matters (workers=1, not fully parallel): J07-08 first, J07-07
// last, because J07-07 audits the whole walk this worker ran. A worker restart
// after a failure resets that census, and J07-07 then fails "coverage incomplete"
// rather than passing on a partial walk. A case whose teardown cannot PROVE a context
// closed (close rejected or hung, no 'close' event, still listed) is never sealed: it
// fails CONTEXT_CLOSE_FAILED / CONTEXT_NOT_CLOSED, and J07-07 fails the walk on it.
//
// Value-free by construction: no expect() on strings, no token in titles, steps,
// annotations, argv or env; token-bearing links are followed by the page itself
// (followLink) so no Playwright error or call log can echo them; every failure is
// a constant assertion-ID message (studio-embed.fixture.ts). Every decision over
// observations is a pure function in studio-embed.oracles.cjs, runnable offline.
import fs from 'node:fs';
import path from 'node:path';
import type { Frame, Page, Route } from '@playwright/test';
import {
  addAudit,
  addBearer,
  bindingModule,
  emptyAudit,
  J07Case,
  J07Failure,
  marker,
  observer,
  oracles,
  poll,
  syntheticKey,
  test,
  type Census,
  type LogRole,
  type LoopbackProxy,
  type PageEvent,
  type StudioChild,
  type ThemeState,
  type ThemeWord,
} from './studio-embed.fixture';

test.skip(({ j07Walk }) => !j07Walk, 'J07 runs only under playwright.j07.config.ts with a Stage A binding');

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));
const monotonic = () => performance.now();
const enc = encodeURIComponent;
const pathOf = (url: string) => {
  try {
    return new URL(url).pathname;
  } catch {
    return '';
  }
};

const meta = (title: string, assertions: string[]) => ({
  tag: ['@journey', '@journey-J07'],
  annotation: [{
    type: 'journey-contract',
    description: JSON.stringify({
      journey: 'J07', leg: 'L1', case: title.slice(0, title.indexOf(' ')), assertions,
      packet: 'plan/JOURNEYS-708/packets/J07/PACKET.json', amendment: 'r2',
      contracts: ['AUTH-STREAM.json', 'DERIVATION.json', 'ARTIFACT-POLICY.json'],
    }),
  }],
});

// ---------------------------------------------------------------- shared observations

type ObserverApi = {
  keyMatches(token: string): boolean; keyCount(): number; keyMatchesAny(token: string): boolean; keyCountAny(): number;
};
const OBSERVER_KEY = 'j07.observer';

/** Keys the bound parent delivered into Studio-origin frame documents. */
function keyEvents(census: Census, studioOrigin: string): PageEvent[] {
  return census.events.filter((event) => event.kind === 'message' && event.frameKind === 'child'
    && event.origin === studioOrigin && event.keyShaped === true && event.fromParent === true);
}

/** `any`: every key delivered to the frame document, whoever sent it (not only the parent's). */
async function frameKeyMatches(walk: J07Case, frame: Frame, token: string, assertion: string, any = false): Promise<boolean> {
  return walk.guard(assertion, 'KEY_MATCH', () => frame.evaluate(([key, name, all]) => {
    const api = (window as unknown as Record<symbol, ObserverApi>)[Symbol.for(name)];
    return all ? api.keyMatchesAny(key) : api.keyMatches(key);
  }, [token, OBSERVER_KEY, any] as const));
}

async function frameKeyCount(walk: J07Case, frame: Frame, assertion: string, any = false): Promise<number> {
  return walk.guard(assertion, 'KEY_COUNT', () => frame.evaluate(([name, all]) => {
    const api = (window as unknown as Record<symbol, ObserverApi>)[Symbol.for(name)];
    return all ? api.keyCountAny() : api.keyCount();
  }, [OBSERVER_KEY, any] as const));
}

/**
 * A re-armable hold on the Studio frame document (A09/A12): while armed, matching
 * document requests wait until release(). Kept short: Studio's hello bound is
 * 4500 ms and a gate retry re-keys the iframe.
 */
function holdGate() {
  let armed = false;
  let held = 0;
  let open: () => void = () => undefined;
  let gate: Promise<void> = Promise.resolve();
  return {
    arm() {
      armed = true;
      gate = new Promise<void>((resolve) => { open = resolve; });
    },
    release() {
      armed = false;
      open();
    },
    held: () => held,
    async pass(route: Route) {
      if (armed) {
        held += 1;
        await gate;
      }
      await route.continue().catch(() => undefined);
    },
  };
}

async function frameByName(walk: J07Case, page: Page, name: string, assertion: string): Promise<Frame> {
  const find = () => page.frames().find((frame) => frame.name() === name);
  walk.check(assertion, await poll(() => Boolean(find()), 15_000), 'FIXTURE_FRAME', { origin_slot: 'none' });
  return find() as Frame;
}

async function frameByOrigin(walk: J07Case, page: Page, origin: string, assertion: string): Promise<Frame> {
  const find = () => page.frames().find((frame) => frame !== page.mainFrame() && frame.url().startsWith(`${origin}/`));
  walk.check(assertion, await poll(() => Boolean(find()), 15_000), 'FIXTURE_FRAME', { origin_slot: 'hostile' });
  return find() as Frame;
}

/** The rendered theme: Studio declares color-scheme on :root; FA paints the body. */
async function computedMode(target: Page | Frame, prefer: 'scheme' | 'luminance'): Promise<string> {
  return target.evaluate((preferred) => {
    const parse = (value: string): number[] | null => {
      const match = /rgba?\(([^)]+)\)/.exec(value);
      if (!match) return null;
      const parts = match[1].split(/[\s,/]+/).filter(Boolean).map(Number);
      if (parts.length >= 4 && parts[3] === 0) return null;
      return parts.slice(0, 3);
    };
    const bg = parse(getComputedStyle(document.body).backgroundColor)
      ?? parse(getComputedStyle(document.documentElement).backgroundColor);
    const lum = bg ? (0.2126 * bg[0] + 0.7152 * bg[1] + 0.0722 * bg[2]) / 255 : null;
    const fromLum = lum === null ? 'unknown' : lum < 0.5 ? 'dark' : 'light';
    const scheme = getComputedStyle(document.documentElement).colorScheme;
    const fromScheme = scheme === 'dark' ? 'dark' : scheme === 'light' ? 'light' : 'unknown';
    if (preferred === 'scheme') return fromScheme !== 'unknown' ? fromScheme : fromLum;
    return fromLum !== 'unknown' ? fromLum : fromScheme;
  }, prefer);
}

/** A15/A16 oracle: computed host AND frame styles, never the messages alone. */
async function expectModes(walk: J07Case, page: Page, frame: Frame, host: string, child: string, assertion: string, code: string): Promise<void> {
  const ok = await poll(async () => (await computedMode(page, 'luminance')) === host
    && (await computedMode(frame, 'scheme')) === child, 8_000, 150);
  walk.check(assertion, ok, code, { phase: 'theme' });
}

/** The bound OSS Studio theme key (packages/studio-ui/src/theme/theme.ts THEME_STORAGE_KEY). */
const STUDIO_THEME_KEY = 'omega-studio-theme';

/**
 * The frame's theme state, value-free: the toggle's explicit choice (data-theme-choice),
 * the persisted Studio choice read back from the frame's own localStorage, and the
 * rendered scheme, each mapped IN the page to light/dark/system/none/other.
 */
async function frameThemeState(walk: J07Case, frame: Frame, assertion: string): Promise<ThemeState> {
  const read = await walk.guard(assertion, 'FRAME_THEME_READ', () => frame.evaluate((key) => {
    const word = (value: string | null) => (value === 'light' || value === 'dark' || value === 'system' ? value
      : value === null ? 'none' : 'other');
    const choice = document.querySelector('[data-testid="theme-toggle"]')?.getAttribute('data-theme-choice') ?? null;
    let stored: string | null = 'unreadable';
    try { stored = window.localStorage.getItem(key); } catch { stored = 'unreadable'; }
    return { choice: word(choice), stored: word(stored) };
  }, STUDIO_THEME_KEY));
  const rendered = await walk.guard(assertion, 'FRAME_THEME_READ', () => computedMode(frame, 'scheme'));
  return { choice: read.choice as ThemeWord, stored: read.stored as ThemeWord,
    rendered: rendered === 'light' || rendered === 'dark' ? rendered : 'other' };
}

/**
 * The frame's OWN explicit theme command by real UI (oracles.chooseTheme): Studio's toggle
 * cycles its stored CHOICE system -> light -> dark (bound OSS theme.ts), and 'system' can
 * render the wanted color without being that choice, so the toggle is pressed (at most
 * three times) until the explicit choice AND the rendered theme are `want`; with
 * `requireStored`, also until the persisted Studio key holds exactly `want`.
 */
async function frameChoose(walk: J07Case, frame: Frame, want: 'light' | 'dark', assertion: string, code: string,
  requireStored = false): Promise<ThemeState> {
  const read = () => frameThemeState(walk, frame, assertion);
  const next = async (previous: ThemeState): Promise<ThemeState> => {
    let state = previous;
    await poll(async () => {
      state = await read();
      return state.choice !== previous.choice;
    }, 3_000, 100);
    if (state.choice === 'light' || state.choice === 'dark') {
      const explicit = state.choice;
      await poll(async () => {
        state = await read();
        return state.rendered === explicit && state.stored === explicit;
      }, 3_000, 100);
    }
    return state;
  };
  const result = await oracles.chooseTheme({
    read,
    click: () => walk.guard(assertion, 'FRAME_THEME_TOGGLE', () => frame.locator('[data-testid="theme-toggle"]').click()),
    next,
    want,
    requireStored,
  });
  walk.check(assertion, result.ok, code, { phase: 'theme', count: result.clicks });
  return result;
}

/**
 * A15 initial precedence over a conflicting PERSISTED Studio theme. The stored choice is
 * made through the embedded frame's own toggle (real UI, in the frame's own storage
 * partition; no storage-seeding init script) by its STORED explicit value: the toggle is
 * cycled until the persisted key holds the conflicting light/dark choice (read back
 * value-free) and a localStorage write by the Studio document is witnessed. A fresh
 * printed link then re-mounts the surface: the new Studio document starts from that
 * stored choice, and the host's initial theme must still win on host and frame styles.
 */
async function storedConflictRemount(walk: J07Case, page: Page, census: Census, studio: StudioChild, frame: Frame,
  host: 'light' | 'dark', code: string): Promise<Frame> {
  const opposite = host === 'light' ? 'dark' : 'light';
  const mark = census.events.length;
  await frameChoose(walk, frame, opposite, 'A15', 'STORED_THEME_NOT_CHOSEN', true);
  await expectModes(walk, page, frame, host, opposite, 'A15', 'STORED_THEME_NOT_RENDERED');
  const stored = await frameThemeState(walk, frame, 'A15');
  walk.check('A15', stored.choice === opposite && stored.stored === opposite, 'STORED_THEME_NOT_PERSISTED', { phase: 'theme' });
  const persisted = census.events.slice(mark).filter((event) => event.kind === 'op' && event.op === 'storage.setItem'
    && event.area === 'local' && event.frameKind === 'child' && event.origin === studio.origin).length;
  walk.check('A15', persisted > 0, 'STORED_THEME_WRITE_NOT_WITNESSED', { phase: 'theme', count: persisted });
  await walk.followLink(page, studio.link, 'auto', 'A15');
  await walk.waitStatus(page, 'connected', 60_000, 'A15', 'REMOUNT_NOT_CONNECTED');
  const fresh = await walk.freshBoundFrame(page, frame, studio.origin, 'A15');
  await walk.guard('A15', 'FRAME_READY', () => fresh.waitForSelector('[data-testid="entries-home"]', { timeout: 60_000 }));
  await expectModes(walk, page, fresh, host, host, 'A15', code);
  return fresh;
}

async function hostPath(walk: J07Case, page: Page): Promise<string> {
  return walk.guard('A14', 'HOST_PATH', () => page.evaluate(() => location.pathname + location.search));
}

/** A05-A07 on host and frame: raw/encoded token in no store, address, history or IndexedDB. */
async function storageClean(walk: J07Case, page: Page, frame: Frame, census: Census, studioOrigin: string, phase: string): Promise<void> {
  // Write coverage first: every Blob a write carried must have been read (natively) and
  // reported before the verdict; a read still pending afterwards counts as unreadable.
  const barrier = await walk.blobBarrier(census, [page, frame]);
  walk.check('A07', barrier.settled, 'BLOB_READ_NOT_SETTLED', { phase, count: barrier.pending });
  const host = await walk.scanStorage(page);
  const child = await walk.scanStorage(frame);
  walk.zero('A05', host.local + child.local, 'LOCALSTORAGE_TOKEN', { phase });
  walk.zero('A06', host.session + child.session, 'SESSIONSTORAGE_TOKEN', { phase });
  walk.zero('A06', host.historyState + host.href + child.historyState + child.href, 'HISTORY_TOKEN', { phase });
  walk.zero('A06', host.cookie + child.cookie + host.name + child.name, 'COOKIE_OR_NAME_TOKEN', { phase });
  walk.zero('A07', host.idb + child.idb, 'INDEXEDDB_TOKEN', { phase });
  // Structured-clone values the scan could not read (unopenable store, host object) are incomplete, not clean.
  walk.zero('A07', host.idbIncomplete + child.idbIncomplete, 'INDEXEDDB_SCAN_INCOMPLETE', { phase });
  // Never disable storage to pass: a refused store is a failure, not a clean result.
  walk.zero('A05', host.refused + child.refused, 'STORAGE_REFUSED', { phase });
  // A browser without IndexedDB enumeration still needs complete write-observer coverage.
  const docs = census.docs();
  const observed = docs.some((doc) => doc.frameKind === 'main' && doc.start !== null)
    && docs.some((doc) => doc.frameKind === 'child' && doc.start?.origin === studioOrigin);
  walk.check('A07', (host.idbEnumerated && child.idbEnumerated) || observed, 'IDB_UNOBSERVED', { phase, count: host.idbDatabases + child.idbDatabases });
  walk.zero('A07', J07Case.writeHits(docs), 'OBSERVED_WRITE_CARRIED_TOKEN', { phase });
  // Unreadable write payloads, failed Blob reads and Blob reads never settled: incomplete.
  walk.zero('A07', J07Case.writeUnsupported(docs), 'OBSERVED_WRITE_UNREADABLE', { phase });
}

// ---------------------------------------------------------------- J07-06 adopted helpers
// From OSS packages/studio-ui/e2e/gone-and-cut.spec.ts:71-159 (truth/look/off/
// agreeAndHold), narrowed to the real FA iframe (embed.spec.ts asPage precedent),
// with the deadline enforced on every branch and fixed codes instead of text.

const ACTION: Readonly<Record<string, string | null>> = {
  running: 'Cancel', waiting: null, paused: 'Continue', interrupted: 'Continue', succeeded: null, failed: null, cancelled: null,
};
interface Truth { run: string; thread: string; badge: number }
interface Look {
  header: string | null; action: string | null; row: string | null; mark: string | null; badge: number;
  decisionOpen: boolean; nodes: Record<string, string | null>; reconnecting: boolean; problem: boolean;
  gate: string | null; changes: boolean;
  /** studio-node elements on the canvas, those whose data-status is 'done', and those with no node id (counts). */
  nodeTotal: number; nodeDone: number; nodeUnidentified: number;
}

async function truth(walk: J07Case, studio: StudioChild, threadId: string, runId: string): Promise<Truth> {
  const run = await walk.studioApi<{ status?: string }>(studio.serverUrl, studio.token, 'GET', `/studio/v1/runs/${enc(runId)}`);
  const thread = await walk.studioApi<{ status?: string }>(studio.serverUrl, studio.token, 'GET', `/studio/v1/threads/${enc(threadId)}`);
  const pending = await walk.studioApi<{ interrupts?: { runId: string; kind: string }[] }>(studio.serverUrl, studio.token, 'GET', '/studio/v1/interrupts');
  const list = pending.json?.interrupts ?? [];
  const tool = list.some((item) => item.runId === runId && item.kind === 'tool');
  const status = run.json?.status ?? 'unknown';
  return { run: tool && status === 'running' ? 'waiting' : status, thread: thread.json?.status ?? 'unknown',
    badge: list.filter((item) => item.kind !== 'breakpoint').length };
}

function look(frame: Frame, threadId: string, runId: string): Promise<Look> {
  return frame.evaluate(([thread, run]) => {
    const row = document.querySelector(`[data-testid="thread-row"][data-thread-id="${CSS.escape(thread)}"]`);
    const badge = document.querySelector('[data-testid="waiting-badge"]')?.textContent?.trim() ?? '';
    return {
      header: document.querySelector('[data-testid="run-status"]')?.getAttribute('data-status') ?? null,
      action: document.querySelector('[data-testid="run-action"]')?.textContent?.trim() ?? null,
      row: row?.querySelector(`[data-testid="thread-run"][data-run-id="${CSS.escape(run)}"]`)?.getAttribute('data-status') ?? null,
      mark: row?.getAttribute('data-status') ?? null,
      badge: badge === '' ? 0 : Number(badge.split(' ')[0]),
      decisionOpen: document.querySelector('[data-testid="decision"] [data-testid="decision-approve"], [data-testid="decision"] [data-testid="decision-answer"]') !== null,
      nodes: Object.fromEntries([...document.querySelectorAll('[data-testid="studio-node"]')]
        .map((node) => [node.getAttribute('data-node-id') ?? '', node.getAttribute('data-status')])),
      // Element counts, so a duplicated node id cannot hide a second, not-done card.
      nodeTotal: document.querySelectorAll('[data-testid="studio-node"]').length,
      nodeDone: document.querySelectorAll('[data-testid="studio-node"][data-status="done"]').length,
      nodeUnidentified: [...document.querySelectorAll('[data-testid="studio-node"]')]
        .filter((node) => (node.getAttribute('data-node-id') ?? '') === '').length,
      reconnecting: document.querySelector('[data-testid="reconnecting"]') !== null,
      problem: document.querySelector('[data-testid="run-problem"]') !== null,
      gate: document.querySelector('[data-testid="gate"]')?.getAttribute('data-gate') ?? null,
      changes: document.querySelector('[data-testid="changes-reconnecting"], [data-testid="changes-problem"]') !== null,
    };
  }, [threadId, runId] as const);
}

/** What disagrees with the server, as fixed codes (empty: nothing). */
function off(seen: Look, now: Truth): string[] {
  const out: string[] = [];
  if (seen.header !== now.run) out.push('header');
  if (seen.row !== now.run) out.push('rail-row');
  if (seen.mark !== now.thread) out.push('thread-mark');
  if (seen.badge !== now.badge) out.push('badge');
  if (seen.action !== (ACTION[now.run] ?? null)) out.push('action');
  if (now.run === 'waiting' && !seen.decisionOpen) out.push('decision-missing');
  if (now.run !== 'waiting' && seen.decisionOpen) out.push('decision-stale');
  const under = Object.values(seen.nodes).some((node) => node === 'running' || node === 'waiting');
  if (!['running', 'waiting'].includes(now.run) && under) out.push('node-running');
  if (seen.reconnecting) out.push('reconnecting');
  if (seen.problem) out.push('problem');
  if (seen.gate !== null) out.push('gate');
  if (seen.changes) out.push('changes');
  return out;
}

/**
 * One A22 observation after direct truth reported the run succeeded: no surface disagrees
 * (off) AND, at EVERY observation (the initial agreeing one and every hold sample), the
 * run is succeeded per direct truth, the canvas is nonempty, every node element is 'done'
 * (static/null/failed/absent break agreement) and the node-id set equals the PRE-CUT canvas
 * set (oracles.canvasNodeKey): `pin` starts from that set, so a node that vanished
 * before the first agreeing observation still breaks agreement.
 */
function restoredAgreement(seen: Look, now: Truth, pin: { key: string | null }): boolean {
  const nodes = oracles.nodesDoneAgreement({ run: now.run, nodes: seen.nodes, total: seen.nodeTotal, done: seen.nodeDone,
    unidentified: seen.nodeUnidentified, pinned: pin.key });
  return off(seen, now).length === 0 && nodes.agree;
}

/**
 * A22 via oracles.agreeAndHold: the 6000 ms deadline is checked after every awaited read,
 * each read bounded by it; the 3000 ms hold is sampled at the 250 ms cadence, every hold
 * read bounded by the remaining gap budget (max sample gap 500 ms), and the samples must
 * span the whole window. Agreement includes every node done (restoredAgreement), at
 * every observation, not only after the hold.
 */
async function agreeAndHold(walk: J07Case, frame: Frame, studio: StudioChild, threadId: string, runId: string, ms: number,
  preCutKey: string) {
  const pin = { key: preCutKey as string | null };
  const result = await oracles.agreeAndHold({
    ms,
    observe: async () => {
      const seen = await look(frame, threadId, runId);
      if (seen.gate === 'unreachable') return { agree: false, gate: 'unreachable' };
      return { agree: restoredAgreement(seen, await truth(walk, studio, threadId, runId), pin), gate: seen.gate };
    },
    // Only the existing Try again action, bounded by the remaining budget.
    retry: (remaining) => frame.locator('[data-testid="gate-primary"]').click({ timeout: Math.max(1, remaining) }).catch(() => undefined),
  });
  if (!result.ok) {
    const code = result.code === 'AGREEMENT_NOT_HELD' ? 'AGREEMENT_NOT_HELD_3000MS'
      : result.code === 'HOLD_SAMPLE_GAP' ? 'HOLD_NOT_SAMPLED_EVERY_250MS' : 'NO_AGREEMENT_WITHIN_6000MS';
    walk.check('A22', false, code, { phase: 'restore', count: result.samples, elapsed_ms: result.maxGap });
  }
  walk.record({ assertion_id: 'A22', outcome: 'observed', phase: 'restore', count: result.samples, elapsed_ms: result.span });
  return { agreed: result.agreed as number, samples: result.samples, tryAgain: result.retries };
}

const frameRunStatus = (frame: Frame) => frame.evaluate(() =>
  document.querySelector('[data-testid="run-status"]')?.getAttribute('data-status') ?? null);

// ---------------------------------------------------------------- C02-auth-order in-run witness

/**
 * Before any real token exists: a deliberately early storage-writing init script
 * (the old authInitScript shape, unrelated value) on a SYNTHETIC connect fragment,
 * in the host document and in a child frame. The A04 oracle must count both, or
 * the observer is blind and nothing it reports later means anything.
 */
async function earlyWriteWitness(walk: J07Case): Promise<void> {
  const synthetic = marker();
  const hostile = await walk.hostileServer();
  const { context, census } = await walk.newContext('anonymous');
  await context.addInitScript(() => {
    try { window.localStorage.setItem('j07-early-write', 'unrelated'); } catch { /* storage refused */ }
  });
  const page = await context.newPage();
  await walk.guard('A04', 'C02_WITNESS_ROUTE', () => page.goto(
    `${walk.fa}/dashboard/studio#connect=${enc('http://127.0.0.1:9')}&t=${synthetic}`, { waitUntil: 'domcontentloaded' }));
  await walk.guard('A04', 'C02_WITNESS_ROUTE', () => page.goto(`${hostile.origin}/frame-child.html?marker=${synthetic}`, { waitUntil: 'load' }));
  await poll(() => census.docs().some((doc) => doc.frameKind === 'child' && doc.start?.connect === true), 10_000);
  const docs = census.docs();
  const host = J07Case.connectPhaseWrites(docs.filter((doc) => doc.frameKind === 'main'));
  const child = J07Case.connectPhaseWrites(docs.filter((doc) => doc.frameKind === 'child'));
  walk.check('A04', host > 0, 'C02_OBSERVER_BLIND_HOST', { control_id: 'C02-auth-order-selfcheck', phase: 'control', count: host });
  walk.check('A04', child > 0, 'C02_OBSERVER_BLIND_CHILD', { control_id: 'C02-auth-order-selfcheck', phase: 'control', count: child, origin_slot: 'hostile' });
  await context.close();
}

// ================================================================= cases

test('J07-08 protocol-fixture-byte-parity', meta('J07-08 protocol-fixture-byte-parity', ['A28']), async ({ walk }) => {
  const parity = walk.j07.parity;
  if (!parity) return walk.blocked(walk.j07.parityCode ?? 'PROTOCOL_PARITY');
  // Raw bytes on both sides at runtime, never parsed JSON or message names.
  walk.record({ assertion_id: 'A28', outcome: 'observed', phase: 'precondition', count: parity.faBytes, origin_slot: 'fa-app' });
  walk.record({ assertion_id: 'A28', outcome: 'observed', phase: 'precondition', count: parity.ossBytes, origin_slot: 'studio' });
  walk.check('A28', parity.faCanonical, 'FA_FIXTURE_NOT_CANONICAL', { origin_slot: 'fa-app' });
  walk.check('A28', parity.ossCanonical, 'OSS_FIXTURE_NOT_CANONICAL', { origin_slot: 'studio' });
  walk.check('A28', parity.equal, 'FIXTURE_BYTES_DIFFER');
  walk.check('A28', parity.controlDetected && parity.controlSemanticallyEqual, 'COMPARATOR_NOT_FALLIBLE', { control_id: 'C09-one-byte' });
});

test('J07-01 printed-link-and-first-script', meta('J07-01 printed-link-and-first-script', ['A01', 'A02', 'A03', 'A04']), async ({ walk }) => {
  test.setTimeout(300_000);
  walk.requireParity();
  await earlyWriteWitness(walk);

  // Real UI login first, then omega dev, then its printed link on the SAME page.
  const { page, census, studio, mode } = await walk.connected('orga-user1');
  walk.check('A03', mode === 'document', 'NOT_A_COLD_INTAKE', { phase: 'connect' });

  // A03: no executable script ran ahead of the intake while the fragment was live; the
  // intake's native replaceState is the first operation of the connect document; and a
  // witness taken before the first FOLLOWING script (never DOMContentLoaded alone) sees
  // no token and no extra history entry.
  const connectDocs = census.docs().filter((doc) => doc.frameKind === 'main' && doc.start?.connect === true);
  walk.check('A03', connectDocs.length === 1, 'CONNECT_DOCUMENT', { phase: 'connect', count: connectDocs.length });
  const verdict = J07Case.intakeVerdict(connectDocs[0]);
  walk.check('A03', verdict.intakeSeen, 'INTAKE_SCRIPT_NOT_SEEN', { phase: 'connect' });
  walk.zero('A03', verdict.preIntakeScripts, 'SCRIPT_BEFORE_INTAKE', { phase: 'connect' });
  walk.check('A03', verdict.firstOpScrub, 'INTAKE_NOT_FIRST_NATIVE_REPLACE', { phase: 'connect' });
  walk.check('A03', verdict.witnessed, 'NO_FOLLOWING_SCRIPT_WITNESS', { phase: 'connect' });
  walk.check('A03', verdict.witnessClean, 'FOLLOWING_SCRIPT_SAW_TOKEN', { phase: 'connect' });
  walk.check('A03', verdict.pass, 'FIRST_SCRIPT_ORDER', { phase: 'connect' });
  const href = await walk.hostHref(page);
  walk.check('A03', walk.hits(href) === 0 && !href.includes('connect='), 'ADDRESS_BAR_TOKEN', { phase: 'connect' });

  // A04: no storage/IDB/cookie write or navigation in ANY frame while connect/t was present,
  // and no context carries an init script besides the read-only observer.
  walk.zero('A04', J07Case.connectPhaseWrites(census.docs()), 'WRITE_DURING_INTAKE', { phase: 'connect' });
  walk.zero('A04', census.initScripts - 1, 'EXTRA_INIT_SCRIPT', { phase: 'connect' });

  // A02: the real authenticated Studio route framed the real Studio from that link.
  const source = await walk.attr(page, 'studio-connected', 'data-source');
  walk.check('A02', source === 'link', 'NOT_CONNECTED_FROM_LINK', { phase: 'embedded' });
  const frame = await walk.studioFrame(page, studio.origin);
  const frameUrl = frame.url();
  walk.check('A02', frameUrl.startsWith(`${studio.origin}/?embed=1`) && walk.hits(frameUrl) === 0, 'FRAME_SRC', { origin_slot: 'studio' });

  // A04 again: a second connect link arriving by same-tab fragment navigation.
  const mark = census.events.length;
  const sameTab = await walk.followLink(page, studio.link, 'fragment', 'A04');
  walk.check('A04', sameTab === 'fragment', 'NOT_A_FRAGMENT_NAVIGATION', { phase: 'connect' });
  await walk.waitStatus(page, 'connected', 60_000, 'A04', 'SAME_TAB_LINK_NOT_CONNECTED');
  const late = census.events.slice(mark).filter((event) => event.frameKind === 'main');
  const arrived = late.some((event) => event.kind === 'nav' && event.connect === true);
  const scrubbed = late.some((event) => event.kind === 'op' && event.op === 'replaceState' && event.before === true && event.after === false);
  walk.check('A04', arrived && scrubbed, 'SAME_TAB_INTAKE_NOT_OBSERVED', { phase: 'connect' });
  walk.zero('A04', J07Case.connectPhaseWrites(census.docs()), 'WRITE_DURING_SAME_TAB_INTAKE', { phase: 'connect' });
  const href2 = await walk.hostHref(page);
  walk.check('A04', walk.hits(href2) === 0 && !href2.includes('connect='), 'ADDRESS_BAR_TOKEN_AFTER_SAME_TAB', { phase: 'connect' });

  // A02: a run inside the frame, and omega dev's own backend names it.
  const live = await walk.studioFrame(page, studio.origin);
  const runId = await walk.runRouterInFrame(live);
  walk.check('A02', runId !== '', 'NO_RUN_ID', { run_alias: 'r1' });
  const runs = await walk.studioApi<{ runs?: { id: string; status: string; entryId: string }[] }>(studio.serverUrl, studio.token, 'GET', '/studio/v1/runs');
  const run = runs.json?.runs?.find((item) => item.id === runId);
  walk.check('A02', runs.status === 200 && run?.status === 'succeeded' && run?.entryId === 'router', 'BACKEND_RUN_IDENTITY',
    { run_alias: 'r1', http_status: runs.status, method_enum: 'GET', route_template_id: 'studio.v1.runs' });

  await walk.finishCase(walk.bearerFor(studio));
});

test('J07-02 storage-and-history', meta('J07-02 storage-and-history', ['A05', 'A06', 'A07', 'A08']), async ({ walk }) => {
  test.setTimeout(360_000);
  walk.requireParity();
  const { page, census, studio, landing } = await walk.connected('orga-user1');
  const frame = await walk.studioFrame(page, studio.origin, 'A05');
  await walk.guard('A05', 'FRAME_READY', () => frame.waitForSelector('[data-testid="entries-home"]', { timeout: 60_000 }));

  // A05/A06/A07 initially.
  await storageClean(walk, page, frame, census, studio.origin, 'embedded');

  // ... and after navigation inside the frame (the host mirrors the route).
  await walk.guard('A05', 'FRAME_ROUTE', () => frame.evaluate(() => { window.location.hash = '#/e/router'; }));
  const mirrored = await poll(async () => (await hostPath(walk, page)) === '/dashboard/studio/e/router', 15_000);
  walk.check('A05', mirrored, 'HOST_ROUTE_NOT_MIRRORED', { phase: 'navigation' });
  await storageClean(walk, page, frame, census, studio.origin, 'navigation');

  // A06: an embedded document reload loses the in-memory key and says needs-key.
  await walk.guard('A06', 'FRAME_RELOAD', () => frame.evaluate(() => { window.location.reload(); }));
  const needsKey = async () => (await walk.attr(page, 'omega-embed-gate', 'data-state')) === 'needs-key'
    || (await walk.attr(page, 'studio-connect-notice', 'data-kind')) === 'needs-key';
  walk.check('A06', await poll(needsKey, 30_000, 200), 'RELOADED_FRAME_KEPT_KEY', { phase: 'embedded' });
  // A saved connection is a bookmark (URL only): it cannot recover the key.
  if ((await page.getByTestId('studio-saved-connect').count()) > 0) {
    await walk.guard('A06', 'SAVED_CONNECT', () => page.getByTestId('studio-saved-connect').first().click());
    walk.check('A06', await poll(needsKey, 30_000, 200), 'SAVED_CONNECTION_RECOVERED_KEY', { phase: 'embedded' });
  }
  // Only a fresh genuine CLI link recovers.
  await walk.followLink(page, studio.link, 'auto', 'A06');
  await walk.waitStatus(page, 'connected', 60_000, 'A06', 'FRESH_LINK_DID_NOT_RECOVER');
  const recovered = await walk.studioFrame(page, studio.origin, 'A06');
  await walk.guard('A06', 'FRAME_READY', () => recovered.waitForSelector('[data-testid="entries-home"]', { timeout: 60_000 }));
  await storageClean(walk, page, recovered, census, studio.origin, 'embedded');

  // A06: standalone Studio persistence outside embedded mode is unchanged (its own context).
  const { context: solo, census: soloCensus } = await walk.newContext('anonymous');
  soloCensus.standalone = true;
  soloCensus.setStudioOrigins([studio.origin]);
  const soloPage = await solo.newPage();
  await walk.followLink(soloPage, `${studio.serverUrl}/#t=${studio.token}`, 'document', 'A06');
  await walk.guard('A06', 'STANDALONE_READY', () => soloPage.waitForSelector('[data-testid="entries-home"]', { timeout: 60_000 }));
  const kept = await walk.scanStorage(soloPage);
  walk.check('A06', kept.session > 0 && kept.local === 0, 'STANDALONE_PERSISTENCE_CHANGED', { phase: 'embedded', origin_slot: 'studio' });
  await walk.guard('A06', 'STANDALONE_RELOAD', () => soloPage.reload({ waitUntil: 'domcontentloaded' }));
  const stillIn = await poll(async () => (await soloPage.locator('[data-testid="entries-home"]').count()) > 0
    && (await soloPage.locator('[data-testid="gate"][data-gate="no-token"]').count()) === 0, 30_000, 200);
  walk.check('A06', stillIn, 'STANDALONE_RELOAD_LOST_KEY', { phase: 'embedded', origin_slot: 'studio' });
  await solo.close();

  // A08: back/forward over the seeded before (landing) and after pages never resurrects the fragment.
  const before = new URL(landing).pathname;
  const mark = census.events.length;
  let steps = 0;
  for (; steps < 10; steps += 1) {
    if ((await walk.guard('A08', 'HOST_PATH', () => page.evaluate(() => location.pathname))) === before) break;
    await walk.guard('A08', 'HISTORY_BACK', () => page.goBack({ waitUntil: 'commit', timeout: 15_000 }));
    await sleep(300);
    const href = await walk.hostHref(page);
    const state = await walk.guard('A08', 'HISTORY_STATE', () => page.evaluate(() => {
      try { return JSON.stringify(history.state) ?? ''; } catch { return ''; }
    }));
    walk.zero('A08', walk.hits(href) + walk.hits(state) + (href.includes('connect=') ? 1 : 0), 'HISTORY_RETAINS_TOKEN', { phase: 'navigation' });
  }
  walk.check('A08', steps > 0 && (await walk.guard('A08', 'HOST_PATH', () => page.evaluate(() => location.pathname))) === before,
    'BEFORE_PAGE_NOT_REACHED', { phase: 'navigation', count: steps });
  for (let i = 0; i < steps; i += 1) {
    await walk.guard('A08', 'HISTORY_FORWARD', () => page.goForward({ waitUntil: 'commit', timeout: 15_000 }));
    await sleep(300);
    const href = await walk.hostHref(page);
    const state = await walk.guard('A08', 'HISTORY_STATE', () => page.evaluate(() => {
      try { return JSON.stringify(history.state) ?? ''; } catch { return ''; }
    }));
    walk.zero('A08', walk.hits(href) + walk.hits(state) + (href.includes('connect=') ? 1 : 0), 'FORWARD_RESURRECTED_FRAGMENT', { phase: 'navigation' });
  }
  // After both traversal directions: the host page's retained state (history.state, storage)
  // and every write the census observed during the walk so far carry no key.
  const traversed = await walk.scanStorage(page);
  walk.zero('A08', traversed.historyState + traversed.href + traversed.local + traversed.session + traversed.cookie + traversed.name,
    'TRAVERSAL_RETAINS_TOKEN', { phase: 'navigation' });
  walk.zero('A08', J07Case.writeHits(census.docs()), 'TRAVERSAL_WRITE_CARRIED_TOKEN', { phase: 'navigation' });
  const resurrected = census.events.slice(mark).filter((event) => event.frameKind === 'main'
    && ((event.kind === 'nav' && event.connect === true) || (event.kind === 'doc-start' && event.connect === true))).length;
  walk.zero('A08', resurrected, 'TRAVERSAL_RESURRECTED_CONNECT', { phase: 'navigation' });

  await walk.finishCase(walk.bearerFor(studio));
});

test('J07-03 exact-window-origin-handshake', meta('J07-03 exact-window-origin-handshake', ['A09', 'A10', 'A11', 'A12', 'A29']), async ({ walk }) => {
  test.setTimeout(360_000);
  walk.requireParity();
  const gate = holdGate();
  let studioOrigin = '';
  const { page, census, studio } = await walk.connected('orga-user1', {
    waitReady: false,
    beforeLink: async ({ context, studio: child }) => {
      studioOrigin = child.origin;
      // A29: malformed/unknown-protocol hellos from the real frame, before Studio's own.
      await observer.armMalformedHello(context, child.origin);
      // A09: hold the real Studio document so the handshake cannot start until released.
      gate.arm();
      await context.route(
        (url) => url.origin === studioOrigin && url.pathname === '/' && url.searchParams.get('embed') === '1',
        (route) => gate.pass(route));
    },
  });
  // A10 sender witness precondition: the host's calls into the bound frame are observed.
  walk.check('A10', await walk.hostInstrumented(page), 'SENDER_WITNESS_UNAVAILABLE', { phase: 'precondition' });
  walk.check('A09', await poll(() => gate.held() >= 1, 30_000), 'FRAME_DOCUMENT_NOT_HELD', { phase: 'embedded', count: gate.held() });
  await sleep(1_500);
  walk.zero('A09', keyEvents(census, studio.origin).length, 'KEY_WHILE_HANDSHAKE_HELD', { phase: 'embedded' });
  walk.zero('A09', J07Case.posts(census.events).filter((event) => event.hasToken === true).length, 'KEY_SENT_WHILE_HANDSHAKE_HELD', { phase: 'embedded' });
  walk.check('A09', (await walk.attr(page, 'studio-status', 'data-state')) !== 'connected', 'READY_WITHOUT_HANDSHAKE', { phase: 'embedded' });
  gate.release();
  await walk.waitStatus(page, 'connected', 60_000, 'A09', 'NOT_CONNECTED_AFTER_RELEASE');
  const frame = await walk.studioFrame(page, studio.origin, 'A10');
  await sleep(1_000);

  const hellos = census.events.filter((event) => event.kind === 'message' && event.frameKind === 'main' && event.type === 'embed.hello');
  const accepted = hellos.filter((event) => event.protocolValid === true && event.fromBoundFrame === true && event.from === studio.origin);
  const malformed = hellos.filter((event) => event.protocolValid === false && event.from === studio.origin);
  const keys = keyEvents(census, studio.origin);
  const keyPosts = J07Case.posts(census.events).filter((event) => event.hasToken === true);
  walk.check('A09', accepted.length >= 1, 'NO_VALID_HELLO_FROM_BOUND_FRAME', { count: accepted.length });
  walk.check('A09', keys.length === 1, 'KEY_DELIVERY_COUNT', { count: keys.length });
  const firstHello = Math.min(...accepted.map((event) => event.ts));
  walk.check('A09', keys[0].ts > firstHello, 'KEY_BEFORE_VALID_HELLO', { elapsed_ms: Math.max(0, Math.round(keys[0].ts - firstHello)) });
  walk.check('A09', keyPosts.length === 1 && keyPosts[0].ts >= firstHello, 'KEY_POSTED_BEFORE_VALID_HELLO', { count: keyPosts.length });

  // A10: the hello came from the live iframe window on the exact Studio origin; the key
  // came from the parent on the exact FA origin, into the frame that is bound now; and
  // on the SENDER side the host's postMessage named exactly the Studio origin as its
  // targetOrigin (MessageEvent never shows that argument; '*' would arrive identically).
  const bound = await walk.boundFrame(page);
  walk.check('A10', keys[0].fromParent === true && keys[0].from === walk.fa, 'KEY_SENDER_NOT_EXACT_PARENT', { origin_slot: 'fa-app' });
  walk.check('A10', bound !== null && keys[0].frame === bound && !bound.isDetached(), 'KEY_NOT_IN_LIVE_FRAME', { origin_slot: 'studio' });
  const sender = oracles.senderVerdict(J07Case.posts(census.events), studio.origin);
  walk.check('A10', sender.keyPosts === 1, 'KEY_POST_NOT_WITNESSED', { count: sender.keyPosts });
  walk.zero('A10', sender.wildcard, 'WILDCARD_TARGET_ORIGIN');
  walk.zero('A10', sender.keyNotPinned, 'KEY_TARGET_ORIGIN_NOT_EXACT', { origin_slot: 'studio' });
  walk.zero('A10', sender.notPinned, 'TARGET_ORIGIN_NOT_EXACT', { origin_slot: 'studio' });
  walk.check('A10', keyPosts[0].toBound === true && keyPosts[0].redirected !== true, 'KEY_POST_NOT_TO_BOUND_FRAME');

  // A11: exactly the connect token, once (sender AND receiver); frame URL clean; only the
  // frame sends it, as a Bearer header to its own Studio origin; the host never calls Studio.
  walk.check('A11', await frameKeyMatches(walk, frame, studio.token, 'A11'), 'KEY_IS_NOT_THE_CONNECT_TOKEN');
  walk.check('A11', (await frameKeyCount(walk, frame, 'A11')) === 1, 'KEY_SENT_MORE_THAN_ONCE');
  walk.check('A11', (await frameKeyCount(walk, frame, 'A11', true)) === 1, 'FRAME_GOT_ANOTHER_KEY');
  walk.check('A11', keyPosts.length === 1, 'HOST_SENT_KEY_MORE_THAN_ONCE', { count: keyPosts.length });
  walk.check('A11', walk.hits(frame.url()) === 0, 'FRAME_URL_TOKEN', { origin_slot: 'studio' });
  const audit = await census.audit(walk.secrets(), walk.bearerFor(studio));
  walk.check('A11', audit.bearerOnStudioCalls > 0, 'NO_BEARER_STUDIO_CALL', { count: audit.bearerOnStudioCalls });
  walk.zero('A11', audit.studioCallsWithoutBearer, 'STUDIO_CALL_WITHOUT_BEARER');
  // A request without its ACTUAL headers is not in the bearer census at all: fail closed.
  walk.zero('A11', audit.headersIncomplete, 'HEADER_CENSUS_INCOMPLETE');
  walk.zero('A11', audit.bearerMisplaced + audit.otherHeaderHits + audit.bodyHits, 'TOKEN_OUTSIDE_FRAME_BEARER');
  walk.zero('A11', audit.mainToStudio, 'HOST_CALLED_STUDIO', { origin_slot: 'studio' });

  // A29: malformed hellos reached the host from the right frame and got nothing; the
  // exact omega-embed/1 hello from that frame did (the bridge is not globally broken).
  walk.check('A29', malformed.length >= 3, 'MALFORMED_HELLOS_NOT_DELIVERED', { count: malformed.length });
  walk.check('A29', keys.length === 1 && keys[0].ts > firstHello && keyPosts.length === 1, 'MALFORMED_HELLO_GOT_KEY');

  // A12 (1): a second hello from the same live frame cannot reuse the consumed token.
  const secondMark = census.events.length;
  await walk.guard('A12', 'SECOND_HELLO', () => frame.evaluate(() => {
    window.parent.postMessage({ type: 'embed.hello', protocol: 'omega-embed/1' }, '*');
  }));
  await sleep(1_500);
  walk.check('A12', keyEvents(census, studio.origin).length === 1, 'SECOND_HELLO_GOT_KEY', { count: keyEvents(census, studio.origin).length });
  walk.zero('A12', J07Case.posts(census.events.slice(secondMark)).filter((event) => event.hasToken === true).length, 'SECOND_HELLO_KEY_POSTED');

  // A12 (2): the iframe changes while an ACCEPTED hello's key is pending. The host's key is
  // synchronous (takeOmegaConnectToken), so the pending interval is the bridge's microtask
  // chain between capturing the frame window at the hello and its post-mint recheck
  // (bridge.js handshake). A real DOM replacement there cannot be observed (a removed
  // iframe's browsing context is discarded and a post to it vanishes), so the seam makes
  // the bound element resolve, for exactly that interval, to a REAL, loaded Studio-origin
  // window (the replacement): a bridge that posts to the current frame window after the
  // change delivers into the replacement; one that skips the recheck delivers into the
  // changed frame. Either is a received key here. The seam must witness the accepted
  // hello and the bridge's recheck read, or A12 fails as not established.
  await walk.guard('A12', 'REPLACEMENT_FRAME', () => page.evaluate((src) => {
    const element = document.createElement('iframe');
    element.name = 'j07-replacement';
    element.src = src;
    element.style.cssText = 'position:fixed;width:2px;height:2px;bottom:0;right:4px;border:0';
    document.body.appendChild(element);
  }, `${studio.origin}/?embed=1`));
  const replacement = await frameByName(walk, page, 'j07-replacement', 'A12');
  const replacementLoaded = await poll(() => replacement.url().startsWith(`${studio.origin}/`)
    && census.docs().some((doc) => doc.start?.frame === replacement && doc.start.origin === studio.origin), 15_000);
  walk.check('A12', replacementLoaded, 'REPLACEMENT_NOT_LOADED', { control_id: 'C04-pending-replacement', origin_slot: 'replacement' });
  const previous = await walk.boundFrame(page);
  gate.arm();
  const heldBefore = gate.held();
  const followed = await walk.followLink(page, studio.link, 'auto', 'A12');
  walk.check('A12', followed === 'fragment', 'NOT_A_FRAGMENT_NAVIGATION', { phase: 'connect' });
  // The next bound document must actually be held (the poll result is checked, not ignored).
  const heldNext = await poll(() => gate.held() > heldBefore, 10_000);
  walk.check('A12', heldNext, 'NEXT_FRAME_DOCUMENT_NOT_HELD', { phase: 'embedded', count: gate.held() - heldBefore });
  walk.check('A12', await walk.armPending(page, 'j07-replacement'), 'SEAM_NOT_ARMED', { control_id: 'C04-pending-replacement' });
  const seamMark = census.events.length;
  gate.release();
  const settled = await poll(async () => ['done', 'missed'].includes((await walk.seamState(page)).stage), 30_000, 50);
  // The changed bound frame, captured now: a later needs-key gate may unmount it.
  const changed = await walk.freshBoundFrame(page, previous, studio.origin, 'A12');
  await sleep(2_000);
  const seam = await walk.seamState(page);
  const delivered = census.events.slice(seamMark).filter((event) => event.kind === 'message' && event.frameKind === 'child'
    && event.keyShaped === true);
  const pending = oracles.pendingReplacementVerdict(seam, delivered.length);
  walk.check('A12', settled && pending.established, 'PENDING_REPLACEMENT_NOT_ESTABLISHED',
    { control_id: 'C04-pending-replacement', count: seam.redirectedReads });
  walk.zero('A12', pending.delivered, 'KEY_DELIVERED_AFTER_FRAME_CHANGED', { control_id: 'C04-pending-replacement' });
  walk.zero('A12', delivered.filter((event) => event.frame === replacement).length, 'KEY_IN_REPLACEMENT_WINDOW', { origin_slot: 'replacement' });
  walk.zero('A12', await frameKeyCount(walk, replacement, 'A12', true), 'KEY_IN_REPLACEMENT_DOCUMENT', { origin_slot: 'replacement' });
  walk.zero('A12', delivered.filter((event) => event.frame === changed).length, 'KEY_IN_CHANGED_FRAME', { origin_slot: 'studio' });
  walk.check('A12', (await walk.attr(page, 'studio-status', 'data-state')) !== 'connected', 'READY_FROM_DROPPED_KEY');
  await walk.guard('A12', 'REPLACEMENT_REMOVE', () => page.evaluate(() => {
    document.querySelector('iframe[name="j07-replacement"]')?.remove();
  }));
  // ... and only a fresh genuine link recovers, with exactly one key into the live frame.
  const recoverMark = census.events.length;
  await walk.followLink(page, studio.link, 'auto', 'A12');
  await walk.waitStatus(page, 'connected', 60_000, 'A12', 'FRESH_LINK_DID_NOT_RECOVER');
  const live = await walk.freshBoundFrame(page, changed, studio.origin, 'A12');
  walk.check('A12', (await frameKeyCount(walk, live, 'A12')) === 1, 'RECOVERED_KEY_COUNT');
  const recovered = J07Case.posts(census.events.slice(recoverMark)).filter((event) => event.hasToken === true);
  walk.check('A12', recovered.length === 1 && recovered[0].toBound === true && recovered[0].targetOrigin === studio.origin,
    'RECOVERED_KEY_POST', { count: recovered.length });

  // A10 forced origin transition AFTER an accepted hello: the live, keyed frame window is
  // navigated to another local origin, then the host is made to send (theme commands go
  // through the bridge's post to the bound window). Every forced send must name the
  // Studio origin, so the changed-origin document receives nothing. (A key send is only
  // ever made inside the hello's microtask chain, so a wildcard KEY destination is caught
  // by the sender witness above, not by delivery here.)
  const hostile = await walk.hostileServer();
  const transitionMark = census.events.length;
  await walk.guard('A10', 'FRAME_TO_HOSTILE', () => page.evaluate((src) => {
    const element = document.querySelector('iframe[data-testid="omega-embed-frame"]') as HTMLIFrameElement | null;
    if (element) element.src = src;
  }, `${hostile.origin}/hostile.html`));
  const hostileFrame = await frameByOrigin(walk, page, hostile.origin, 'A10');
  await sleep(1_500);
  const hostMode = await computedMode(page, 'luminance');
  await walk.setHostTheme(page, hostMode === 'dark' ? 'light' : 'dark');
  await walk.setHostTheme(page, hostMode === 'dark' ? 'dark' : 'light');
  await sleep(1_000);
  const forced = J07Case.posts(census.events.slice(transitionMark))
    .filter((event) => event.toBound === true && (event.type ?? '').startsWith('embed.'));
  walk.check('A10', forced.length >= 1, 'ORIGIN_TRANSITION_SEND_NOT_FORCED', { control_id: 'C04-origin-transition', count: forced.length });
  walk.zero('A10', forced.filter((event) => event.targetOrigin !== studio.origin).length, 'SEND_AFTER_TRANSITION_NOT_PINNED',
    { control_id: 'C04-origin-transition' });
  const seenHostile = await walk.guard('A10', 'HOSTILE_READ', () => hostileFrame.evaluate(() =>
    (window as unknown as { __j07Hostile: { received: number; keys: number } }).__j07Hostile));
  walk.zero('A10', seenHostile.received + seenHostile.keys, 'CHANGED_ORIGIN_RECEIVED_EMBED_MESSAGE', { origin_slot: 'hostile' });
  walk.zero('A10', await frameKeyCount(walk, hostileFrame, 'A10', true), 'CHANGED_ORIGIN_GOT_KEY', { origin_slot: 'hostile' });
  const hostileHellos = census.events.slice(transitionMark).filter((event) => event.kind === 'message' && event.frameKind === 'main'
    && event.type === 'embed.hello' && event.from === hostile.origin && event.fromBoundFrame === true).length;
  walk.check('A10', hostileHellos > 0, 'CHANGED_ORIGIN_HELLO_NOT_DELIVERED', { origin_slot: 'hostile', count: hostileHellos });
  walk.zero('A10', J07Case.posts(census.events.slice(transitionMark)).filter((event) => event.hasToken === true).length,
    'CHANGED_ORIGIN_HELLO_GOT_KEY_POST', { origin_slot: 'hostile' });
  // The whole case, sender side: never a wildcard, every bound send pinned.
  const whole = oracles.senderVerdict(J07Case.posts(census.events), studio.origin);
  walk.check('A10', whole.pass, 'SENDER_TARGET_ORIGIN', { count: whole.embed });

  await walk.finishCase(walk.bearerFor(studio));
});

test('J07-04 route-and-theme-precedence', meta('J07-04 route-and-theme-precedence', ['A13', 'A14', 'A15', 'A16']), async ({ walk }) => {
  test.setTimeout(360_000);
  walk.requireParity();
  // System preference dark (conflicting); the host's explicit theme is light.
  const { page, census, studio } = await walk.connected('orga-user2', { colorScheme: 'dark', hostTheme: 'light' });
  let frame = await walk.studioFrame(page, studio.origin, 'A13');
  await walk.guard('A13', 'FRAME_READY', () => frame.waitForSelector('[data-testid="entries-home"]', { timeout: 60_000 }));

  // A15 / A16 row 1: the host's initial light overrides the frame's system dark.
  await expectModes(walk, page, frame, 'light', 'light', 'A15', 'INITIAL_HOST_THEME_LOST');
  // A15: ... and a conflicting PERSISTED Studio theme (dark) at the next initial mount.
  frame = await storedConflictRemount(walk, page, census, studio, frame, 'light', 'INITIAL_HOST_THEME_LOST_TO_STORED');
  walk.record({ assertion_id: 'A16', outcome: 'pass', phase: 'theme', comparison_boolean: true });

  // A13: a real run inside the iframe; the host route tracks the exact child path; backend names it.
  const runId = await walk.runRouterInFrame(frame, 'A13');
  const runRoute = new RegExp(`^/dashboard/studio/t/[^/?]+\\?run=${runId.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}$`);
  const tracking = await poll(async () => runRoute.test(await hostPath(walk, page)), 15_000);
  walk.check('A13', runId !== '' && tracking, 'HOST_ROUTE_NOT_TRACKING', { run_alias: 'r1', phase: 'navigation' });
  const run = await walk.studioApi<{ id?: string; status?: string }>(studio.serverUrl, studio.token, 'GET', `/studio/v1/runs/${enc(runId)}`);
  walk.check('A13', run.status === 200 && run.json?.id === runId && run.json?.status === 'succeeded', 'BACKEND_RUN_IDENTITY',
    { run_alias: 'r1', http_status: run.status, method_enum: 'GET', route_template_id: 'studio.v1.runs.id' });
  const quiet = census.events.length;
  await sleep(2_000);
  const echoes = census.events.slice(quiet).filter((event) => event.kind === 'message'
    && (event.type === 'embed.route' || event.type === 'embed.navigate')).length;
  walk.zero('A13', echoes, 'ROUTE_ECHO_LOOP', { phase: 'navigation' });

  // A14: host back/forward drive the real frame; addresses stay token-free.
  const frameRoute = () => new URL(frame.url()).hash;
  const onRun = frameRoute();
  await walk.guard('A14', 'HISTORY_BACK', () => page.goBack({ waitUntil: 'commit', timeout: 15_000 }));
  const left = await poll(async () => frameRoute() !== onRun && !runRoute.test(await hostPath(walk, page)), 15_000);
  walk.check('A14', left, 'BACK_DID_NOT_DRIVE_FRAME', { phase: 'navigation' });
  await walk.guard('A14', 'HISTORY_FORWARD', () => page.goForward({ waitUntil: 'commit', timeout: 15_000 }));
  const back = await poll(async () => frameRoute() === onRun && runRoute.test(await hostPath(walk, page)), 15_000);
  walk.check('A14', back, 'FORWARD_DID_NOT_DRIVE_FRAME', { phase: 'navigation' });
  walk.zero('A14', walk.hits(await walk.hostHref(page)) + walk.hits(frame.url()), 'ADDRESS_TOKEN', { phase: 'navigation' });

  // A16: ordered last-explicit-command table, system dark.
  await frameChoose(walk, frame, 'dark', 'A16', 'FRAME_TOGGLE_FAILED'); // the frame's own command: dark
  await expectModes(walk, page, frame, 'light', 'dark', 'A16', 'FRAME_TOGGLE_CHANGED_HOST_OR_FAILED');
  // A host rerender without a theme change is not a command.
  await walk.guard('A16', 'HOST_RERENDER', async () => {
    await page.goBack({ waitUntil: 'commit', timeout: 15_000 });
    await page.goForward({ waitUntil: 'commit', timeout: 15_000 });
  });
  await poll(async () => runRoute.test(await hostPath(walk, page)), 15_000);
  await expectModes(walk, page, frame, 'light', 'dark', 'A16', 'RERENDER_TREATED_AS_COMMAND');
  await walk.setHostTheme(page, 'dark');
  await expectModes(walk, page, frame, 'dark', 'dark', 'A16', 'CHANGED_HOST_DARK_LOST');
  await walk.setHostTheme(page, 'light');
  await expectModes(walk, page, frame, 'light', 'light', 'A16', 'CHANGED_HOST_LIGHT_LOST');

  // Inverse / conflicting preference: system light.
  await walk.guard('A16', 'SYSTEM_PREFERENCE', () => page.emulateMedia({ colorScheme: 'light' }));
  await walk.setHostTheme(page, 'dark');
  await expectModes(walk, page, frame, 'dark', 'dark', 'A16', 'INVERSE_HOST_DARK_LOST');
  await frameChoose(walk, frame, 'light', 'A16', 'INVERSE_FRAME_TOGGLE_FAILED'); // the frame's own command: light
  await expectModes(walk, page, frame, 'dark', 'light', 'A16', 'INVERSE_FRAME_TOGGLE_CHANGED_HOST_OR_FAILED');
  await walk.setHostTheme(page, 'light');
  await expectModes(walk, page, frame, 'light', 'light', 'A16', 'INVERSE_HOST_LIGHT_LOST');
  await walk.setHostTheme(page, 'dark');
  await expectModes(walk, page, frame, 'dark', 'dark', 'A16', 'INVERSE_HOST_DARK_AGAIN_LOST');
  // A15 inverse initial precedence: system light AND a persisted light Studio choice; host dark wins at mount.
  frame = await storedConflictRemount(walk, page, census, studio, frame, 'dark', 'INVERSE_INITIAL_HOST_THEME_LOST_TO_STORED');

  await walk.finishCase(walk.bearerFor(studio));
});

test('J07-05 wrong-origin-and-wrong-window', meta('J07-05 wrong-origin-and-wrong-window', ['A17', 'A18', 'A19', 'A20']), async ({ walk }) => {
  test.setTimeout(360_000);
  walk.requireParity();
  const hostile = await walk.hostileServer();
  let holdingOn = true;
  let held = 0;
  let release: () => void = () => undefined;
  const holding = new Promise<void>((resolve) => { release = resolve; });
  let studioOrigin = '';
  const { context, page, census, studio } = await walk.connected('orga-user1', {
    waitReady: false,
    beforeLink: async ({ context: ctx, studio: child }) => {
      studioOrigin = child.origin;
      await ctx.route(
        (url) => holdingOn && url.origin === studioOrigin && url.pathname === '/' && url.searchParams.get('embed') === '1',
        async (route) => {
          held += 1;
          await holding;
          await route.continue().catch(() => undefined);
        });
    },
  });
  walk.check('A17', await poll(() => held >= 1, 30_000), 'FRAME_DOCUMENT_NOT_HELD', { phase: 'hostile' });
  const original = await walk.guard('A17', 'FRAME_SRC', () => page.evaluate(() =>
    document.querySelector('iframe[data-testid="omega-embed-frame"]')?.getAttribute('src') ?? ''));
  walk.check('A17', original.startsWith(`${studio.origin}/`) && walk.hits(original) === 0, 'FRAME_SRC', { origin_slot: 'studio' });

  // A17: the SAME frame window navigates to another local origin, which says hello.
  const setSrc = (src: string) => page.evaluate((value) => {
    const element = document.querySelector('iframe[data-testid="omega-embed-frame"]') as HTMLIFrameElement | null;
    if (element) element.src = value;
  }, src);
  await walk.guard('A17', 'FRAME_TO_HOSTILE', () => setSrc(`${hostile.origin}/hostile.html`));
  holdingOn = false;
  release();
  const hostileFrame = await frameByOrigin(walk, page, hostile.origin, 'A17');
  await sleep(1_500);
  const seen = await walk.guard('A17', 'HOSTILE_READ', () => hostileFrame.evaluate(() =>
    (window as unknown as { __j07Hostile: { received: number; keys: number } }).__j07Hostile));
  walk.zero('A17', seen.received + seen.keys, 'WRONG_ORIGIN_GOT_EMBED_MESSAGE', { origin_slot: 'hostile' });
  const wrongOriginHellos = census.events.filter((event) => event.kind === 'message' && event.frameKind === 'main'
    && event.type === 'embed.hello' && event.from === hostile.origin && event.fromBoundFrame === true).length;
  walk.check('A17', wrongOriginHellos > 0, 'WRONG_ORIGIN_HELLO_NOT_DELIVERED', { count: wrongOriginHellos, origin_slot: 'hostile' });
  walk.check('A17', (await walk.attr(page, 'studio-status', 'data-state')) !== 'connected', 'READY_FROM_WRONG_ORIGIN', { origin_slot: 'hostile' });
  // Restoring the legitimate origin in the same window works: the key was never spent.
  await walk.guard('A17', 'FRAME_RESTORE', () => setSrc(original));
  await walk.waitStatus(page, 'connected', 60_000, 'A17', 'RESTORED_ORIGIN_NOT_CONNECTED');
  walk.check('A17', keyEvents(census, studio.origin).length === 1, 'RESTORED_KEY_COUNT');

  // A18: a DIFFERENT real sibling window at the correct Studio origin says hello.
  const beforeSibling = census.events.length;
  await walk.guard('A18', 'SIBLING_FRAME', () => page.evaluate((src) => {
    const element = document.createElement('iframe');
    element.name = 'j07-sibling';
    element.src = src;
    element.style.cssText = 'position:fixed;width:2px;height:2px;bottom:0;right:0;border:0';
    document.body.appendChild(element);
  }, `${studio.origin}/?embed=1`));
  const sibling = await frameByName(walk, page, 'j07-sibling', 'A18');
  await sleep(3_000);
  const siblingHellos = census.events.slice(beforeSibling).filter((event) => event.kind === 'message' && event.frameKind === 'main'
    && event.type === 'embed.hello' && event.from === studio.origin && event.fromBoundFrame === false).length;
  walk.check('A18', siblingHellos > 0, 'SIBLING_HELLO_NOT_DELIVERED', { count: siblingHellos, origin_slot: 'sibling' });
  walk.zero('A18', await frameKeyCount(walk, sibling, 'A18'), 'SIBLING_GOT_KEY', { origin_slot: 'sibling' });
  const hrefBefore = await walk.hostHref(page);
  await walk.guard('A18', 'SIBLING_ROUTE', () => sibling.evaluate(() => { window.location.hash = '#/e/router'; }));
  await sleep(1_500);
  walk.check('A18', (await walk.hostHref(page)) === hrefBefore, 'SIBLING_HAS_ROUTE_AUTHORITY', { origin_slot: 'sibling' });
  walk.check('A18', (await walk.attr(page, 'studio-status', 'data-state')) === 'connected', 'SIBLING_DISTURBED_BOUND_FRAME');

  // A19, source and origin separated. On the child side three senders are refused:
  //   (1) wrong origin, not the parent (hostile sibling);
  //   (2) listed FA origin, not the parent (same-origin sibling): the parent-SOURCE check alone;
  //   (3) the ACTUAL parent window on an unlisted origin: the parent-ORIGIN check alone (below,
  //       its own context).
  // Each arm first proves its messages (and the synthetic key) were DELIVERED to the Studio
  // document, whoever sent them; refusal is then judged on the product: no request ever
  // carries the synthetic key, the frame stays connected on its real key, and its theme and
  // route do not move. Only the actual configured parent provides key, route or theme.
  const frame = await walk.studioFrame(page, studio.origin, 'A19');
  await walk.guard('A19', 'SENDER_FRAMES', () => page.evaluate((src) => {
    const style = 'position:fixed;width:2px;height:2px;bottom:0;left:0;border:0';
    const hostileElement = document.createElement('iframe');
    hostileElement.name = 'j07-hostile';
    hostileElement.src = src;
    hostileElement.style.cssText = style;
    document.body.appendChild(hostileElement);
    const sameOrigin = document.createElement('iframe');
    sameOrigin.name = 'j07-same-origin';
    sameOrigin.style.cssText = style;
    document.body.appendChild(sameOrigin);
  }, `${hostile.origin}/blank.html`));
  const wrongOriginSender = await frameByName(walk, page, 'j07-hostile', 'A19');
  const sameOriginSender = await frameByName(walk, page, 'j07-same-origin', 'A19');
  await poll(() => wrongOriginSender.url().startsWith(hostile.origin), 10_000);
  // The bound frame's index, read with the observer's native accessor (contentWindow is instrumented).
  const index = await walk.boundIndex(page, 'A19');
  walk.check('A19', index >= 0, 'BOUND_FRAME_INDEX');
  const themeBefore = await computedMode(frame, 'scheme');
  const routeBefore = new URL(frame.url()).hash;
  const flip = themeBefore === 'dark' ? 'light' : 'dark';
  const send = (sender: Frame, key: string) => sender.evaluate(([i, target, token, mode]) => {
    const child = window.parent.frames[i as number];
    child.postMessage({ type: 'embed.theme', mode }, target as string);
    child.postMessage({ type: 'embed.navigate', path: '/e/router' }, target as string);
    child.postMessage({ type: 'embed.key', token }, target as string);
  }, [index, studio.origin, key, flip] as const);
  const wrongOriginKey = syntheticKey();
  const sameOriginKey = syntheticKey();
  const beforeSend = census.events.length;
  await walk.guard('A19', 'WRONG_ORIGIN_SENDER', () => send(wrongOriginSender, wrongOriginKey));
  await walk.guard('A19', 'SAME_ORIGIN_SENDER', () => send(sameOriginSender, sameOriginKey));
  await sleep(1_500);
  const delivered = census.events.slice(beforeSend).filter((event) => event.kind === 'message' && event.frameKind === 'child'
    && event.origin === studio.origin && event.fromParent === false);
  walk.check('A19', delivered.length >= 6, 'NON_PARENT_MESSAGES_NOT_DELIVERED', { count: delivered.length });
  walk.check('A19', delivered.some((event) => event.from === hostile.origin) && delivered.some((event) => event.from === walk.fa),
    'NON_PARENT_SENDERS_NOT_BOTH_DELIVERED', { count: delivered.length });
  // Delivered: the observer in the Studio document saw both synthetic keys arrive (non-parent).
  walk.check('A19', await frameKeyMatches(walk, frame, wrongOriginKey, 'A19', true), 'WRONG_ORIGIN_KEY_NOT_DELIVERED', { origin_slot: 'hostile' });
  walk.check('A19', await frameKeyMatches(walk, frame, sameOriginKey, 'A19', true), 'SAME_ORIGIN_KEY_NOT_DELIVERED', { origin_slot: 'same-origin-sibling' });
  walk.check('A19', (await computedMode(frame, 'scheme')) === themeBefore, 'NON_PARENT_THEME_APPLIED', { origin_slot: 'same-origin-sibling' });
  walk.check('A19', new URL(frame.url()).hash === routeBefore, 'NON_PARENT_ROUTE_APPLIED', { origin_slot: 'same-origin-sibling' });
  // Refused by the product: neither synthetic key is ever used, the real one still is.
  walk.zero('A19', await census.headerUses(wrongOriginKey), 'WRONG_ORIGIN_NON_PARENT_KEY_USED', { origin_slot: 'hostile' });
  walk.zero('A19', await census.headerUses(sameOriginKey), 'SAME_ORIGIN_NON_PARENT_KEY_USED', { origin_slot: 'same-origin-sibling' });
  walk.check('A19', (await walk.attr(page, 'studio-status', 'data-state')) === 'connected', 'NON_PARENT_DISTURBED_FRAME');
  // ... while the actual parent still drives the theme.
  await walk.setHostTheme(page, flip);
  walk.check('A19', await poll(async () => (await computedMode(frame, 'scheme')) === flip, 8_000, 150), 'PARENT_THEME_NOT_APPLIED');

  // A19 (3): the ACTUAL parent window, on an unlisted origin. A local page frames real Studio;
  // in this arm's own context only, Studio's framing denial is removed from its document
  // response (a recorded fixture seam, oracles.withoutFramingDenial) so the child runs and
  // its parent-origin check is the only thing left to refuse. The parent then sends key,
  // theme and route to its own child, with '*' and with the exact Studio origin.
  {
    const { context: parentContext, census: parentCensus } = await walk.newContext('anonymous');
    parentCensus.setStudioOrigins([studio.origin]);
    let seamed = 0;
    let removed = 0;
    await walk.guard('A19', 'PARENT_ARM_SEAM', () => parentContext.route(
      (url) => url.origin === studio.origin && url.pathname === '/' && url.searchParams.get('embed') === '1',
      async (route) => {
        try {
          const response = await route.fetch();
          const stripped = oracles.withoutFramingDenial(response.headers());
          seamed += 1;
          removed += stripped.removed;
          await route.fulfill({ response, headers: stripped.headers });
        } catch {
          await route.abort().catch(() => undefined);
        }
      }));
    const parentPage = await parentContext.newPage();
    await walk.guard('A19', 'PARENT_PAGE', () => parentPage.goto(`${hostile.origin}/parent.html?src=${enc(`${studio.origin}/?embed=1`)}`,
      { waitUntil: 'load' }));
    const child = await frameByName(walk, parentPage, 'j07-wrong-origin-parent-child', 'A19');
    const established = await poll(() => seamed >= 1 && child.url().startsWith(`${studio.origin}/`)
      && parentCensus.docs().some((doc) => doc.start?.frame === child && doc.start.origin === studio.origin), 20_000);
    walk.check('A19', established && removed >= 1, 'PARENT_ARM_NOT_ESTABLISHED',
      { control_id: 'C06-parent-origin', origin_slot: 'hostile-parent', count: removed });
    await sleep(1_500);
    const childTheme = await walk.guard('A19', 'PARENT_ARM_THEME', () => computedMode(child, 'scheme'));
    const childRoute = new URL(child.url()).hash;
    const parentKey = syntheticKey();
    const parentMark = parentCensus.events.length;
    for (const target of ['*', studio.origin]) {
      await walk.guard('A19', 'PARENT_ARM_SEND', () => parentPage.evaluate(([key, mode, where]) =>
        (window as unknown as { __j07Parent: { send(k: string, m: string, p: string, t: string): boolean } }).__j07Parent
          .send(key, mode, '/e/router', where), [parentKey, childTheme === 'dark' ? 'light' : 'dark', target] as const));
    }
    await sleep(2_000);
    const fromParent = parentCensus.events.slice(parentMark).filter((event) => event.kind === 'message' && event.frameKind === 'child'
      && event.origin === studio.origin && event.fromParent === true && event.from === hostile.origin).length;
    walk.check('A19', fromParent >= 6, 'PARENT_MESSAGES_NOT_DELIVERED', { control_id: 'C06-parent-origin', origin_slot: 'hostile-parent', count: fromParent });
    walk.check('A19', await frameKeyMatches(walk, child, parentKey, 'A19', true), 'PARENT_KEY_NOT_DELIVERED', { origin_slot: 'hostile-parent' });
    walk.zero('A19', await parentCensus.headerUses(parentKey), 'WRONG_ORIGIN_PARENT_KEY_USED', { origin_slot: 'hostile-parent' });
    walk.zero('A19', await child.locator('[data-testid="entries-home"]').count(), 'WRONG_ORIGIN_PARENT_UNLOCKED_STUDIO', { origin_slot: 'hostile-parent' });
    walk.check('A19', (await computedMode(child, 'scheme')) === childTheme, 'WRONG_ORIGIN_PARENT_THEME_APPLIED', { origin_slot: 'hostile-parent' });
    walk.check('A19', new URL(child.url()).hash === childRoute, 'WRONG_ORIGIN_PARENT_ROUTE_APPLIED', { origin_slot: 'hostile-parent' });
    await parentContext.close();
  }

  // A20: frame-ancestors denies an unlisted local host; real Studio answers with CSP.
  const head = await walk.guard('A20', 'STUDIO_HEAD', () => fetch(`${studio.serverUrl}/?embed=1`));
  const csp = head.headers.get('content-security-policy') ?? '';
  const ancestors = /frame-ancestors([^;]*)/.exec(csp)?.[1] ?? '';
  walk.check('A20', ancestors.includes(walk.fa) && !ancestors.includes(hostile.origin) && !ancestors.includes('*'),
    'FRAME_ANCESTORS_NOT_EXACT', { origin_slot: 'studio', http_status: head.status });
  const top = await context.newPage();
  await walk.guard('A20', 'UNLISTED_HOST', () => top.goto(`${hostile.origin}/frame-studio.html?src=${enc(`${studio.origin}/?embed=1`)}`, { waitUntil: 'load' }));
  await sleep(3_000);
  const unlisted = await walk.guard('A20', 'UNLISTED_READ', () => top.evaluate(() =>
    (window as unknown as { __j07Hostile: { received: number } }).__j07Hostile.received));
  walk.zero('A20', unlisted, 'UNLISTED_HOST_GOT_HELLO', { origin_slot: 'hostile' });
  const onTop = (frameOf: () => Frame) => {
    try { return frameOf().page() === top; } catch { return false; }
  };
  const rendered = census.events.filter((event) => event.kind === 'doc-start' && event.origin === studio.origin && onTop(() => event.frame)).length;
  walk.zero('A20', rendered, 'STUDIO_RENDERED_UNDER_UNLISTED_HOST', { origin_slot: 'hostile' });
  const apiOk = census.requests.filter((request) => request.response?.status === 200
    && pathOf(request.url).startsWith('/studio/v1/') && onTop(() => request.request.frame())).length;
  walk.zero('A20', apiOk, 'STUDIO_API_SUCCESS_UNDER_UNLISTED_HOST', { origin_slot: 'hostile' });
  // The named FA origin still has a working frame.
  walk.check('A20', (await walk.attr(page, 'studio-status', 'data-state')) === 'connected', 'NAMED_HOST_FRAME_BROKEN', { origin_slot: 'fa-app' });

  await walk.finishCase(walk.bearerFor(studio));
});

test('J07-06 stream-drop-and-restore', meta('J07-06 stream-drop-and-restore', ['A21', 'A22', 'A06']), async ({ walk }) => {
  // AUTH-STREAM.json stream_case: case work budget 120000 ms, plus login and child start.
  test.setTimeout(420_000);
  walk.requireParity();
  let proxy: LoopbackProxy | null = null;
  let threadId = '';
  let runId = '';
  let runCreates = 0;
  let continues = 0;
  const { page, census, studio, frameOrigin } = await walk.connected('orga-user2', {
    // Recorded fault topology: ONLY connect=<server> becomes connect=<proxy> in the real
    // printed link, in memory; host origin, token and route are unchanged. Not CLI output.
    transform: async (child) => {
      proxy = await walk.loopbackProxy(child.port);
      const from = `#connect=${child.serverUrl}&`;
      walk.check('A21', child.link.split(from).length === 2, 'TOPOLOGY_TRANSFORM', { phase: 'precondition', origin_slot: 'studio-proxy' });
      return { link: child.link.replace(from, `#connect=${proxy.origin}&`), origin: proxy.origin };
    },
    // One thread, one run held before step1 (slow-steps), paused per direct truth.
    beforeLink: async ({ studio: child }) => {
      const thread = await walk.studioApi<{ id?: string }>(child.serverUrl, child.token, 'POST', '/studio/v1/threads', { entryId: 'slow-steps' });
      walk.check('A21', thread.status < 300 && typeof thread.json?.id === 'string', 'THREAD_CREATE',
        { http_status: thread.status, method_enum: 'POST', route_template_id: 'studio.v1.threads' });
      threadId = thread.json?.id as string;
      const run = await walk.studioApi<{ id?: string }>(child.serverUrl, child.token, 'POST', `/studio/v1/threads/${enc(threadId)}/runs`,
        { input: {}, breakpoints: { before: ['step1'] } }, true);
      runCreates += 1;
      walk.check('A21', run.status < 300 && typeof run.json?.id === 'string', 'RUN_CREATE',
        { http_status: run.status, method_enum: 'POST', route_template_id: 'studio.v1.threads.runs', run_alias: 'r1' });
      runId = run.json?.id as string;
      const paused = await poll(async () => (await walk.studioApi<{ status?: string }>(child.serverUrl, child.token, 'GET', `/studio/v1/runs/${enc(runId)}`)).json?.status === 'paused', 30_000, 25);
      walk.check('A21', paused, 'RUN_NOT_PAUSED', { phase: 'stream', run_alias: 'r1' });
    },
  });
  const cutProxy = proxy as LoopbackProxy | null;
  if (!cutProxy) throw new J07Failure('A21', 'NO_PROXY');
  const getRun = async () => (await walk.studioApi<{ status?: string; lastCheckpointId?: string }>(studio.serverUrl, studio.token, 'GET', `/studio/v1/runs/${enc(runId)}`)).json ?? {};

  // The real FA-to-proxy frame shows that thread/run; the rail is opened by UI after the scrub.
  const frame = await walk.studioFrame(page, frameOrigin, 'A21');
  await walk.guard('A21', 'FRAME_TO_RUN', async () => {
    await frame.waitForSelector('[data-testid="entries-home"]', { timeout: 60_000 });
    await frame.evaluate((hash) => { window.location.hash = hash; }, `#/t/${enc(threadId)}?run=${enc(runId)}`);
    await frame.waitForSelector('.react-flow__node', { timeout: 30_000 });
    if ((await frame.locator('[data-testid="threads-rail"]').getAttribute('data-open')) === 'false') {
      await frame.locator('[data-testid="rail-toggle"]').click();
    }
    await frame.waitForSelector('[data-testid="threads-rail"][data-open="true"]', { timeout: 10_000 });
  });
  // Paused frame and direct run/thread/lastCheckpointId identity first; no run SSE is
  // required while paused (useRun opens it only for running/queued).
  const pausedUi = await poll(async () => (await frameRunStatus(frame)) === 'paused', 30_000, 50);
  walk.check('A21', pausedUi, 'FRAME_NOT_PAUSED', { phase: 'stream', run_alias: 'r1' });
  const paused = await getRun();
  walk.check('A21', paused.status === 'paused' && typeof paused.lastCheckpointId === 'string', 'NO_CHECKPOINT_IDENTITY', { phase: 'stream', run_alias: 'r1' });

  // Exactly one asynchronous continue with checkpointId=lastCheckpointId.
  const continueWall = Date.now();
  const resumed = await walk.studioApi(studio.serverUrl, studio.token, 'POST', `/studio/v1/runs/${enc(runId)}/continue`,
    { checkpointId: paused.lastCheckpointId }, true);
  continues += 1;
  walk.check('A21', resumed.status >= 200 && resumed.status < 300, 'CONTINUE_REFUSED',
    { http_status: resumed.status, method_enum: 'POST', route_template_id: 'studio.v1.runs.continue', run_alias: 'r1' });
  walk.check('A21', (await getRun()).status !== 'succeeded', 'CONTINUE_NOT_ASYNCHRONOUS', { phase: 'stream', run_alias: 'r1' });

  // Within 10000 ms: running UI AND backend AND the frame's exact run stream 200 text/event-stream.
  const streamPath = `/studio/v1/runs/${enc(runId)}/stream`;
  const openStream = () => census.requests.find((request) => request.frameKind === 'child' && request.method === 'GET'
    && request.url.startsWith(`${frameOrigin}/`) && pathOf(request.url) === streamPath && request.ts >= continueWall
    && request.response?.status === 200 && request.response.contentType.includes('text/event-stream') && request.ended === null);
  const began = monotonic();
  const active = await poll(async () => (await frameRunStatus(frame)) === 'running'
    && (await getRun()).status === 'running' && openStream() !== undefined, 10_000, 50);
  walk.check('A21', active, 'NO_ACTIVE_RUN_STREAM_BEFORE_CUT', { phase: 'stream', elapsed_ms: Math.round(monotonic() - began), route_template_id: 'studio.v1.runs.stream' });
  const stream = openStream();
  if (!stream) throw new J07Failure('A21', 'NO_ACTIVE_RUN_STREAM_BEFORE_CUT');
  walk.record({ assertion_id: 'A21', outcome: 'observed', phase: 'stream', request_ordinal: stream.ordinal, http_status: 200, route_template_id: 'studio.v1.runs.stream', origin_slot: 'studio-proxy' });

  // The canvas node-id set while the run is live (static/running/done cards): A22 later
  // requires exactly this set, every node done.
  const preCut = await walk.guard('A22', 'FRAME_LOOK', () => look(frame, threadId, runId));
  const preCutNodes = oracles.canvasNodeKey({ nodes: preCut.nodes, total: preCut.nodeTotal, unidentified: preCut.nodeUnidentified });
  walk.check('A22', preCutNodes.ok, 'PRE_CUT_CANVAS_INCOMPLETE', { phase: 'stream', count: preCut.nodeTotal });

  // Still active per direct truth; the cut snapshot of the run's state; then refuse and cut
  // in the same awaited turn.
  const cutSnapshot = await walk.studioApi<{ status?: string; updatedAt?: string; lastCheckpointId?: string }>(
    studio.serverUrl, studio.token, 'GET', `/studio/v1/runs/${enc(runId)}`);
  walk.check('A21', cutSnapshot.json?.status === 'running', 'RUN_ENDED_BEFORE_CUT', { phase: 'cut', run_alias: 'r1' });
  cutProxy.refuse(true);
  const destroyed = cutProxy.cut();
  const cutAt = monotonic();
  const cutWall = Date.now();
  let falseDone = 0;
  while (monotonic() - cutAt < 1_000) {
    const header = await frameRunStatus(frame).catch(() => null);
    if (header === 'succeeded' && (await getRun()).status !== 'succeeded') falseDone += 1;
    await sleep(100);
  }
  // The disconnect witness is judged WHILE forwarding is still refused: the proxy reset a
  // live pair and the exact witnessed run stream failed inside the refusal interval. A
  // no-op cut, a natural completion or a closure seen only after restore fails here.
  const witness = oracles.cutWitness({ ended: stream.ended, cutWall, evaluatedAt: Date.now(), destroyed });
  const refusedDuringCut = cutProxy.refused();
  cutProxy.refuse(false);
  const restoreWall = Date.now();
  walk.record({ assertion_id: 'A21', outcome: 'observed', phase: 'cut', elapsed_ms: Math.round(monotonic() - cutAt), count: refusedDuringCut });
  walk.zero('A21', falseDone, 'FALSE_COMPLETED_DURING_CUT', { phase: 'cut' });
  walk.check('A21', witness.destroyed > 0, 'CUT_RESET_NO_CONNECTION', { phase: 'cut', count: witness.destroyed, control_id: 'C07-noop-cut' });
  walk.check('A21', witness.closedDuringRefusal && witness.failed, 'RUN_STREAM_NOT_DISCONNECTED_WHILE_REFUSED',
    { phase: 'cut', request_ordinal: stream.ordinal, control_id: 'C07-disconnect' });
  walk.check('A21', witness.pass, 'CUT_WITNESS', { phase: 'cut' });

  // Restore: the SAME run succeeds per direct truth; within 6000 ms every surface agrees,
  // every node done, then holds 3000 ms; a NEW post-restore response for this run carries
  // a state strictly later than the cut snapshot.
  const succeeded = await poll(async () => (await getRun()).status === 'succeeded', 30_000, 25);
  walk.check('A22', succeeded, 'RUN_NOT_SUCCEEDED_AFTER_RESTORE', { phase: 'restore', run_alias: 'r1' });
  const result = await agreeAndHold(walk, frame, studio, threadId, runId, 6_000, preCutNodes.key);
  walk.check('A22', result.agreed <= 6_000, 'NO_AGREEMENT_WITHIN_6000MS', { phase: 'restore', elapsed_ms: result.agreed, count: result.samples });
  const final = await walk.guard('A22', 'FRAME_LOOK', () => look(frame, threadId, runId));
  // The final observation meets the same identity + cardinality + done oracle, pinned to the pre-cut set.
  const finalNodes = oracles.nodesDoneAgreement({ run: 'succeeded', nodes: final.nodes, total: final.nodeTotal, done: final.nodeDone,
    unidentified: final.nodeUnidentified, pinned: preCutNodes.key });
  walk.check('A22', finalNodes.agree, 'NODE_NOT_DONE', { phase: 'restore', count: final.nodeTotal });
  const restoreTargets = new Set([
    `/studio/v1/runs/${enc(runId)}`, `/studio/v1/runs/${enc(runId)}/stream`,
    `/studio/v1/threads/${enc(threadId)}`, `/studio/v1/threads/${enc(threadId)}/stream`,
  ]);
  const postRestore = () => census.requests.filter((request) => request.frameKind === 'child' && request.url.startsWith(`${frameOrigin}/`)
    && request.ts >= restoreWall && request.response?.status === 200 && request.ended?.kind === 'finished'
    && restoreTargets.has(pathOf(request.url)));
  await poll(() => postRestore().length > 0, 10_000, 100);
  const candidates: { kind: 'json' | 'sse'; text: string }[] = [];
  for (const request of postRestore()) {
    const text = await request.request.response()
      .then((response) => (response ? response.body() : null))
      .then((body) => (body ? body.toString('utf8') : null), () => null)
      .catch(() => null);
    if (text === null) continue;
    candidates.push({ kind: request.response?.contentType.includes('text/event-stream') ? 'sse' : 'json', text });
  }
  const newer = oracles.newerThanCut(cutSnapshot.json ?? {}, candidates, runId);
  walk.check('A22', newer.candidates > 0, 'NO_POST_RESTORE_RECONCILIATION', { phase: 'restore', count: postRestore().length });
  walk.check('A22', newer.pass, 'POST_RESTORE_STATE_NOT_NEWER_THAN_CUT', { phase: 'restore', count: newer.states });

  // Exactly one run and one continue; the frame repeated no run action; no reload.
  const runs = await walk.studioApi<{ runs?: { id: string; entryId?: string }[] }>(studio.serverUrl, studio.token, 'GET', '/studio/v1/runs');
  const slow = (runs.json?.runs ?? []).filter((item) => item.entryId === 'slow-steps');
  walk.check('A22', slow.length === 1 && slow[0].id === runId && runCreates === 1 && continues === 1, 'RUN_OR_CONTINUE_DUPLICATED', { count: slow.length });
  const repeated = census.requests.filter((request) => request.method === 'POST' && request.frameKind === 'child'
    && (/\/studio\/v1\/threads\/[^/]+\/runs$/.test(pathOf(request.url)) || /\/continue$/.test(pathOf(request.url)))).length;
  walk.zero('A22', repeated, 'FRAME_REPEATED_RUN_ACTION');
  const reloaded = census.docs().filter((doc) => doc.frameKind === 'child' && doc.start?.origin === frameOrigin
    && (doc.start?.ts ?? 0) >= continueWall).length;
  walk.zero('A22', reloaded, 'FRAME_RELOADED');

  // A06: transport-only interruption without reload kept the same in-memory auth and run.
  walk.check('A06', await frameKeyMatches(walk, frame, studio.token, 'A06'), 'TRANSPORT_CUT_LOST_KEY', { phase: 'restore' });
  walk.check('A06', (await walk.attr(page, 'studio-status', 'data-state')) === 'connected', 'TRANSPORT_CUT_LOST_AUTH', { phase: 'restore' });
  // A05 after stream restoration.
  await storageClean(walk, page, frame, census, frameOrigin, 'restore');

  await cutProxy.close();
  await walk.finishCase(addBearer(walk.bearerFor(studio), cutProxy.origin, studio.token));
});

test('J07-06b server-restart-key-rotation', meta('J07-06b server-restart-key-rotation', ['A23']), async ({ walk }) => {
  test.setTimeout(360_000);
  walk.requireParity();
  const { page, census, studio } = await walk.connected('orga-user1');
  const frame = await walk.studioFrame(page, studio.origin, 'A23');
  await walk.guard('A23', 'FRAME_READY', () => frame.waitForSelector('[data-testid="entries-home"]', { timeout: 60_000 }));

  // A ledgered stop shows unreachable within 6000 ms.
  const stopAt = monotonic();
  await studio.stop();
  const unreachable = await poll(async () => (await frame.locator('[data-testid="gate"][data-gate="unreachable"]').count()) > 0, 6_000, 100);
  walk.check('A23', unreachable, 'STOP_NOT_SHOWN_WITHIN_6000MS', { phase: 'restart', elapsed_ms: Math.round(monotonic() - stopAt) });

  // Restart the same project/port with a new key: the old key is denied.
  const mark = census.events.length;
  const restarted = await walk.startStudio('restart', { newKey: true, project: studio.project });
  census.setStudioOrigins([restarted.origin]);
  walk.check('A23', restarted.token !== studio.token, 'KEY_NOT_ROTATED', { phase: 'restart' });
  walk.check('A23', restarted.origin === studio.origin, 'NOT_SAME_PROJECT_PORT', { phase: 'restart', origin_slot: 'studio' });
  const oldKey = await walk.studioApi(restarted.serverUrl, studio.token, 'GET', '/studio/v1/runs');
  walk.check('A23', oldKey.status === 401 || oldKey.status === 403, 'OLD_KEY_ACCEPTED', { phase: 'restart', http_status: oldKey.status });
  const newKey = await walk.studioApi(restarted.serverUrl, restarted.token, 'GET', '/studio/v1/runs');
  walk.check('A23', newKey.status === 200, 'NEW_KEY_REFUSED', { phase: 'restart', http_status: newKey.status });
  // Retaining the old key never restores ready (no sessionStorage recovery while embedded).
  await sleep(5_000);
  const readyWithOldKey = census.events.slice(mark).filter((event) => event.kind === 'message' && event.frameKind === 'main'
    && event.type === 'embed.gate' && event.state === 'ready').length;
  walk.zero('A23', readyWithOldKey, 'OLD_KEY_RESTORED_READY', { phase: 'restart' });

  // Only the fresh actual printed link (in memory) restores ready within 15000 ms.
  const followAt = monotonic();
  await walk.followLink(page, restarted.link, 'auto', 'A23');
  const ready = await poll(async () => (await walk.attr(page, 'studio-status', 'data-state')) === 'connected',
    Math.max(0, 15_000 - (monotonic() - followAt)), 100);
  walk.check('A23', ready, 'FRESH_LINK_NOT_READY_WITHIN_15000MS', { phase: 'restart', elapsed_ms: Math.round(monotonic() - followAt) });
  walk.zero('A23', J07Case.connectPhaseWrites(census.docs()), 'WRITE_DURING_RESTART_INTAKE', { phase: 'restart' });
  const href = await walk.hostHref(page);
  walk.zero('A23', walk.hits(href) + (href.includes('connect=') ? 1 : 0), 'RESTART_LINK_IN_ADDRESS_BAR', { phase: 'restart' });
  // Every leak surface, across both ephemeral keys.
  const host = await walk.scanStorage(page);
  const child = await walk.scanStorage(await walk.studioFrame(page, restarted.origin, 'A23'));
  walk.zero('A23', host.local + host.session + host.idb + host.historyState + host.cookie + host.name
    + child.local + child.session + child.idb + child.historyState + child.cookie + child.name, 'KEY_IN_STORAGE', { phase: 'restart' });
  walk.zero('A23', host.idbIncomplete + child.idbIncomplete + host.refused + child.refused, 'STORAGE_SCAN_INCOMPLETE', { phase: 'restart' });
  const actions = census.requests.filter((request) => request.method === 'POST' && request.frameKind === 'child'
    && /\/studio\/v1\/threads\/[^/]+\/runs$/.test(pathOf(request.url))).length;
  walk.zero('A23', actions, 'RUN_ACTION_DUPLICATED', { phase: 'restart' });

  await walk.finishCase(walk.bearerFor(studio, restarted));
});

test('J07-09 admission-and-inert-config', meta('J07-09 admission-and-inert-config', ['A31', 'A32', 'A33', 'A34']), async ({ walk }) => {
  test.setTimeout(420_000);
  walk.requireParity();
  // A30: on T-DARK J07 is not run; J00's absence receipt is adopted there, never a J07 PASS.
  walk.record({ assertion_id: 'A30', outcome: 'not_covered', phase: 'precondition' });

  // A32/A33: the bound effective build/runtime descriptor (CJ-OVERLAY APP-SIDE) of THIS FA
  // build was read and judged at worker admission, before any page or token: its sourceRef
  // equals the bound faRef, every required telemetry key is declared and inert, every URL is
  // loopback. A mismatched or incomplete descriptor never reaches this case (J07-BLOCKED).
  const described = walk.j07.resolved.appSide;
  walk.check('A32', described.sourceRefMatches, 'DESCRIPTOR_NOT_THIS_BUILD', { phase: 'config' });
  walk.zero('A32', described.missingRequired, 'DESCRIPTOR_INCOMPLETE', { phase: 'config' });
  walk.zero('A32', described.telemetryLive, 'TELEMETRY_CONFIGURED', { phase: 'config' });
  walk.zero('A33', described.nonlocalUrls, 'NONLOCAL_ENDPOINT_CONFIGURED', { phase: 'config' });

  // A31: org B (denied) gets no route and no frame, even with a genuine printed link.
  const { context: bContext, census: bCensus } = await walk.newContext('orgb-user1');
  const bPage = await bContext.newPage();
  const { landing: bLanding } = await walk.login(bPage, walk.seed.B.users[0], 'orgb-user1');
  await walk.assertAdmission(bPage, walk.seed.B, false, 'orgb-user1');
  await walk.gotoTokenFree(bPage, bLanding.startsWith(`${walk.fa}/dashboard/studio`) ? `${walk.fa}/dashboard/home` : bLanding);
  const studio = await walk.startStudio('admission');
  bCensus.setStudioOrigins([studio.origin]);
  await walk.followLink(bPage, studio.link, 'auto', 'A31');
  const denied = await poll(async () => (await bPage.evaluate(() => location.pathname)) === '/404', 30_000, 200);
  walk.check('A31', denied, 'DENIED_ORG_REACHED_STUDIO_ROUTE', { role_alias: 'orgb-user1' });
  walk.zero('A31', await bPage.locator('iframe[data-testid="omega-embed-frame"]').count(), 'DENIED_ORG_GOT_FRAME', { role_alias: 'orgb-user1' });
  await sleep(2_000);
  const toStudio = bCensus.requests.filter((request) => observer.originOf(request.url) === studio.origin).length;
  walk.zero('A31', toStudio, 'DENIED_ORG_REACHED_STUDIO', { role_alias: 'orgb-user1', origin_slot: 'studio' });
  const bStore = await walk.scanStorage(bPage);
  walk.zero('A31', bStore.local + bStore.session + bStore.idb + bStore.historyState + bStore.href, 'DENIED_ORG_KEPT_TOKEN', { role_alias: 'orgb-user1' });

  // The second org B user is denied too; the second org A user is admitted.
  const { context: b2Context } = await walk.newContext('orgb-user2');
  const b2Page = await b2Context.newPage();
  await walk.login(b2Page, walk.seed.B.users[1], 'orgb-user2');
  await walk.assertAdmission(b2Page, walk.seed.B, false, 'orgb-user2');
  const { context: aContext, census: aCensus } = await walk.newContext('orga-user2');
  const aPage = await aContext.newPage();
  await walk.login(aPage, walk.seed.A.users[1], 'orga-user2');
  await walk.assertAdmission(aPage, walk.seed.A, true, 'orga-user2');

  // A32/A33 on the served page: runtime config local and inert; no telemetry SDK started.
  const served = await walk.guard('A32', 'RUNTIME_CONFIG', () => aPage.evaluate(() => {
    const config = (window as unknown as { __FUTURE_AGI_CONFIG__?: Record<string, unknown> }).__FUTURE_AGI_CONFIG__ ?? {};
    const values: Record<string, string> = {};
    for (const [key, value] of Object.entries(config)) {
      if (['string', 'number', 'boolean'].includes(typeof value)) values[key] = String(value);
    }
    const w = window as unknown as Record<string, { __loaded?: boolean } | undefined>;
    const scripts = [...document.querySelectorAll('script[src]')].map((element) => (element as HTMLScriptElement).src);
    return {
      values,
      posthog: w.posthog?.__loaded === true,
      mixpanel: w.mixpanel?.__loaded === true,
      newrelic: typeof w.NREUM !== 'undefined' || typeof w.newrelic !== 'undefined',
      scripts,
    };
  }));
  const runtime = bindingModule.inertness(served.values, false);
  walk.zero('A32', runtime.telemetryLive, 'RUNTIME_TELEMETRY_CONFIGURED', { phase: 'config' });
  walk.zero('A32', Number(served.posthog) + Number(served.mixpanel) + Number(served.newrelic), 'TELEMETRY_SDK_STARTED', { phase: 'config' });
  walk.zero('A33', runtime.nonlocalUrls + served.scripts.filter((src) => !observer.isLocalUrl(src)).length, 'NONLOCAL_RUNTIME_URL', { phase: 'config' });

  // A34: the observer is not blind (a local telemetry stub and a dedicated worker are seen).
  const hostile = await walk.hostileServer();
  const witness = await aContext.newPage();
  await walk.guard('A34', 'LOCAL_STUB', () => witness.goto(`${hostile.origin}/marker.html?j07marker=${marker()}`, { waitUntil: 'load' }));
  walk.check('A34', await poll(() => aCensus.requests.some((request) => request.url.startsWith(`${hostile.origin}/telemetry-stub`)), 10_000),
    'OBSERVER_BLIND_TO_LOCAL_STUB', { control_id: 'C11-local-stub', origin_slot: 'hostile' });
  walk.check('A34', await poll(() => aCensus.workers > 0, 10_000), 'OBSERVER_BLIND_TO_WORKER', { control_id: 'C11-local-stub' });

  // A33/A34: every page, frame, worker and socket of every context stayed local (finishCase
  // fails any case with a nonlocal attempt or a service worker; J07-07 re-checks the walk).
  const total = await walk.finishCase(walk.bearerFor(studio));
  walk.record({ assertion_id: 'A34', outcome: 'observed', phase: 'audit', count: total.pages + total.popups + total.workers + total.websockets });
});

test('J07-10 token-safe-artifact-policy', meta('J07-10 token-safe-artifact-policy', ['A37']), async ({ walk }, testInfo) => {
  // Worker admission already refused, before any login or token, anything below; this case
  // re-derives each fact from the resolved runtime and records it.
  walk.zero('A37', walk.j07.policyViolations.length, 'CAPTURE_POLICY', { phase: 'config' });
  const selfTest = walk.j07.policySelfTest;
  walk.check('A37', selfTest.pass, 'POLICY_VALIDATOR_NOT_FALLIBLE', { control_id: 'C13-config-trace', count: selfTest.refused });
  const reporters = testInfo.config.reporter.map(([name]) => name);
  // Reporter ALLOWLIST by exact module identity (real path, plus sha256 for runner-accepted ones).
  walk.zero('A37', bindingModule.reporterViolations(reporters, walk.j07.resolved.accepted).length, 'REPORTER_NOT_ALLOWLISTED', { phase: 'config' });
  walk.check('A37', reporters.length === walk.j07.reporters.length && reporters.every((name, i) => name === walk.j07.reporters[i]),
    'REPORTERS_CHANGED_AFTER_ADMISSION', { phase: 'config' });
  const resolved = bindingModule.capturePolicyViolations({
    use: testInfo.project.use, workers: testInfo.config.workers, retries: testInfo.project.retries, reporters, env: process.env,
    accepted: walk.j07.resolved.accepted, effective: walk.j07.effective.values,
  });
  walk.zero('A37', resolved.length, 'RESOLVED_CAPTURE_POLICY', { phase: 'config' });
  // The EFFECTIVE values, not project.use: trace/screenshot/video, launch and connect options
  // exactly as the fixture pool resolved them for this worker, and this test's merged
  // context options (what every browser.newContext here inherits).
  walk.zero('A37', bindingModule.effectiveWorkerViolations(walk.j07.effective.values).length, 'EFFECTIVE_WORKER_CAPTURE', { phase: 'config' });
  walk.zero('A37', walk.contextAdmission.violations.length
    + bindingModule.effectiveContextViolations(walk.contextAdmission.values).length, 'EFFECTIVE_CONTEXT_CAPTURE', { phase: 'config' });
  walk.zero('A37', bindingModule.debugLoggingViolations(process.env).length, 'DEBUG_LOGGING_ENABLED', { phase: 'config' });
  walk.check('A37', process.env.PLAYWRIGHT_NO_COPY_PROMPT === '1', 'ARIA_SNAPSHOT_PRODUCER_ON', { phase: 'config' });
  // G5a: the installed failure-artifact producer (Playwright package version plus the exact
  // bytes of its artifact/error-context files) carries the runner's qualification receipt for
  // this engine on success, assertion failure, goto timeout, setup failure, test timeout and
  // worker crash. Admission refused G5A_UNQUALIFIED otherwise, before any token existed.
  const g5a = walk.j07.resolved.g5a;
  walk.check('A37', g5a.qualified && g5a.engines.includes(walk.browserName) && g5a.files >= 2, 'G5A_UNQUALIFIED',
    { control_id: 'C13-failure-paths', phase: 'config' });

  // Verification, not admission: nothing produced so far is a capture artifact or an
  // error-context file. The reporter repeats this inventory after the LAST case ends.
  const inventory = artifactInventory(testInfo.project.outputDir);
  walk.zero('A37', inventory.capture, 'CAPTURE_FILE_PRODUCED', { phase: 'audit' });
  walk.zero('A37', inventory.errorContext, 'ERROR_CONTEXT_PRODUCED', { phase: 'audit' });
  walk.record({ assertion_id: 'A37', outcome: 'observed', phase: 'audit', count: inventory.files });
  // The G-Q3 bootstrap-emitter controls stay a separate mandatory product-emitter gate.
  walk.record({ assertion_id: 'A37', control_id: 'C13-bootstrap-emitter', outcome: 'not_covered', phase: 'control' });
});

// Last: the whole walk this worker ran, plus this case's own complete walk.
test('J07-07 request-referer-and-server-log-audit', meta('J07-07 request-referer-and-server-log-audit', ['A24', 'A25', 'A26', 'A27', 'A34']), async ({ walk }) => {
  test.setTimeout(360_000);
  walk.requireParity();
  // The complete declared log set (every role, admitted before the walk started).
  const logs = walk.j07.resolved.logs;
  const hostile = await walk.hostileServer();
  const { context, page, census, studio } = await walk.connected('orga-user1');
  const frame = await walk.studioFrame(page, studio.origin, 'A24');
  walk.check('A24', (await walk.runRouterInFrame(frame, 'A24')) !== '', 'NO_RUN', { run_alias: 'r1' });
  const policy = await walk.guard('A25', 'FRAME_ATTRIBUTE', () => page.getByTestId('omega-embed-frame').getAttribute('referrerpolicy'));
  walk.check('A25', policy === 'no-referrer', 'FRAME_REFERRER_POLICY', { origin_slot: 'studio' });

  // C08 in-run scanner fallibility, with a synthetic non-secret marker: the URL/query and
  // Referer scanners must find it, and the FA (and bound Studio) logs must cover it.
  const m = marker();
  const markerPage = await context.newPage();
  await walk.guard('A24', 'C08_MARKER', () => markerPage.goto(`${hostile.origin}/marker.html?j07marker=${m}`, { waitUntil: 'load' }));
  await poll(() => census.requests.some((request) => request.url.startsWith(`${hostile.origin}/telemetry-stub`)), 10_000);
  const scanner = await census.audit([m]);
  walk.check('A24', scanner.urlHits > 0, 'C08_URL_SCANNER_BLIND', { control_id: 'C08-marker-url' });
  walk.check('A25', scanner.refererHits > 0, 'C08_REFERER_SCANNER_BLIND', { control_id: 'C08-marker-referer' });
  // The same marker closes the walk window in every declared FA log role.
  await walk.guard('A26', 'C08_MARKER', () => fetch(`${walk.binding.origins.faApi}/accounts/user-info/?j07marker=${m}`).then((response) => response.arrayBuffer()));
  await walk.guard('A26', 'C08_MARKER', () => fetch(`${walk.fa}/auth/jwt/login?j07marker=${m}`).then((response) => response.arrayBuffer()));
  await walk.guard('A26', 'C08_MARKER', () => markerPage.goto(`${walk.fa}/auth/jwt/login?j07marker=${m}`, { waitUntil: 'domcontentloaded' }));

  // Stops every child (each sends its own end marker first) and audits this case's
  // outputs; the census verdict that counts is the sealed one taken below.
  await walk.finishCase(walk.bearerFor(studio));

  // A26: the FA roles over the bounded walk window. Every declared file of every FA role
  // (frontend, access, API) must be nonempty, contain the walk-start marker (sent at
  // admission) AND this end marker, and hold zero raw/encoded token occurrences. A role
  // the binding omitted never got this far (J07-BLOCKED LOG_ROLE_UNDECLARED_*).
  const read = (file: string) => {
    try { return fs.readFileSync(file, 'utf8'); } catch { return ''; }
  };
  const start = walk.j07.aggregate.walkStart;
  const sent = walk.j07.aggregate.walkStartSent;
  walk.check('A26', sent.faApp && sent.faApi, 'WALK_START_MARKER_NOT_SENT', { control_id: 'C08-walk-window' });
  for (const role of bindingModule.LOG_ROLES) {
    if (role === 'studio-access') continue;
    const control = `C08-marker-${role}`;
    const files = logs[role as LogRole];
    walk.check('A26', files.length > 0, 'LOG_ROLE_EMPTY', { control_id: control, count: files.length });
    const ended = await poll(() => files.every((file) => read(file).includes(m)), 20_000, 500);
    const texts = files.map(read);
    walk.zero('A26', texts.filter((text) => text.length === 0).length, 'LOG_FILE_EMPTY', { control_id: control });
    walk.zero('A26', texts.filter((text) => !text.includes(start)).length, 'LOG_WINDOW_START_UNCOVERED', { control_id: control });
    walk.check('A26', ended && texts.every((text) => text.includes(m)), 'LOG_WINDOW_END_UNCOVERED', { control_id: control });
    walk.zero('A26', texts.reduce((sum, text) => sum + walk.hits(text), 0), 'TOKEN_IN_LOG', { control_id: control });
  }

  // A27: Studio access logs PER (DECLARED FILE, CHILD). Every child of the walk got its own
  // start marker when it started serving and its own end marker just before it was
  // stopped; some declared studio-access file must hold that child's start (none: its log
  // was never declared, so the audit cannot be complete: BLOCKED). EVERY declared file
  // holds both of a child's markers (start before end) or neither: a file with only the
  // start is a truncated sink, one with only the end lost its head, and a complete
  // aggregate file never excuses either. Every declared file must hold some child's
  // complete window, and no file may hold a token.
  const childLogs = walk.j07.aggregate.childLogs;
  const studioFiles = logs['studio-access'];
  walk.check('A27', childLogs.length >= 1 && childLogs.length === walk.j07.aggregate.children, 'STUDIO_CHILD_UNTRACKED',
    { control_id: 'C08-studio-children', count: childLogs.length });
  const coverage = () => oracles.studioLogCoverage(childLogs, studioFiles.map(read));
  await poll(() => coverage().pass, 20_000, 500);
  const studioCoverage = coverage();
  walk.record({ assertion_id: 'A27', control_id: 'C08-studio-children', outcome: 'observed', phase: 'audit', count: studioCoverage.children });
  if (studioCoverage.undeclared > 0) walk.blocked('STUDIO_CHILD_LOG_UNDECLARED');
  walk.zero('A27', studioCoverage.startUnsent, 'STUDIO_CHILD_START_MARKER_NOT_SENT', { control_id: 'C08-studio-children' });
  walk.zero('A27', studioCoverage.endUnsent, 'STUDIO_CHILD_END_MARKER_NOT_SENT', { control_id: 'C08-studio-children' });
  walk.zero('A27', studioCoverage.emptyFiles, 'LOG_FILE_EMPTY', { control_id: 'C08-marker-studio-access' });
  walk.zero('A27', studioCoverage.foreignFiles, 'LOG_FILE_NOT_THIS_WALK', { control_id: 'C08-marker-studio-access' });
  walk.zero('A27', studioCoverage.unmarked, 'STUDIO_CHILD_MARKERS_INVALID', { control_id: 'C08-studio-children' });
  walk.zero('A27', studioCoverage.endUncovered, 'LOG_WINDOW_END_UNCOVERED_CHILD', { control_id: 'C08-marker-studio-access' });
  walk.zero('A27', studioCoverage.startUncovered, 'LOG_WINDOW_START_UNCOVERED_CHILD', { control_id: 'C08-marker-studio-access' });
  walk.zero('A27', studioCoverage.orderInverted, 'LOG_WINDOW_ORDER_CHILD', { control_id: 'C08-marker-studio-access' });
  walk.check('A27', studioCoverage.pass, 'STUDIO_LOG_COVERAGE', { control_id: 'C08-marker-studio-access', count: studioCoverage.files });
  walk.zero('A27', studioFiles.map(read).reduce((sum, text) => sum + walk.hits(text), 0), 'TOKEN_IN_LOG',
    { control_id: 'C08-marker-studio-access' });

  // A27 ((b), OSS 79164e25f3): every child's COMPLETE stdout/stderr
  // including bootstrap. Stdout is a pipe and pages may frame Studio, so the plain local
  // link is the literal "Open http://127.0.0.1:<port>/#t=<key>", the key occurs EXACTLY
  // ONCE on stdout (inside the one "Open in Future AGI" line, the printed link) and ZERO
  // times on stderr; .omega/studio.key is the one other channel (owner-only, same key).
  const children = walk.j07.aggregate.childAudits;
  const sum = (pick: (child: (typeof children)[number]) => number) => children.reduce((total, child) => total + pick(child), 0);
  walk.check('A27', children.length >= 1 && children.length === walk.j07.aggregate.children
    && children.every((child) => child.bootstrapComplete && child.stdoutBytes > 0), 'CLI_LOG_INCOMPLETE', { count: children.length });
  walk.check('A27', children.every((child) => child.plainOpenLiteral === 1), 'LOCAL_LINK_NOT_KEYLESS', { count: sum((child) => child.plainOpenLiteral) });
  walk.zero('A27', sum((child) => child.plainOpenKeyed), 'LOCAL_LINK_CARRIES_KEY');
  walk.check('A27', children.every((child) => child.allowedLine === 1 && child.faLines === 1), 'PRINTED_LINK_NOT_THE_KEY_LINE');
  walk.check('A27', children.every((child) => child.stdoutKeyHits === 1), 'KEY_NOT_EXACTLY_ONCE_ON_STDOUT', { count: sum((child) => child.stdoutKeyHits) });
  walk.zero('A27', sum((child) => child.elsewhere + child.otherTokenHits), 'TOKEN_OUTSIDE_PRINTED_LINK');
  walk.zero('A27', sum((child) => child.stderrHits), 'TOKEN_ON_STDERR');
  walk.check('A27', children.every((child) => child.pass), 'CHILD_OUTPUT');
  walk.check('A27', children.every((child) => child.keyFileOwnerOnly && child.keyFileMatches), 'KEY_FILE_CHANNEL');

  // SEAL: close every context of this case (each earlier case sealed at its own teardown),
  // so a request, worker or socket during the log polls above is in the final census.
  // The whole-walk A24/A25/A34 verdict is computed ONLY from these sealed audits.
  // A context whose closure teardown could not prove (close rejected or timed out, no
  // 'close' event, still listed) was never sealed: it fails its own case and the walk.
  await walk.close();
  const required = ['J07-01', 'J07-02', 'J07-03', 'J07-04', 'J07-05', 'J07-06', 'J07-06b', 'J07-09', 'J07-07'];
  const { finished, sealed, closeFailures, unsealedCensuses } = walk.j07.aggregate;
  walk.zero('A34', closeFailures, 'CONTEXT_CLOSE_FAILED_IN_WALK', { phase: 'audit' });
  walk.zero('A34', unsealedCensuses.length, 'CENSUS_UNSEALED_IN_WALK', { phase: 'audit' });
  walk.zero('A24', required.filter((id) => !finished.has(id) || !sealed.has(id)).length, 'WALK_COVERAGE_INCOMPLETE', { phase: 'audit' });
  const all = [...sealed.values()].reduce((total, audit) => addAudit(total, audit), emptyAudit());
  walk.record({ assertion_id: 'A24', outcome: 'observed', phase: 'audit', count: all.requests });
  walk.zero('A24', all.urlHits + all.websocketHits, 'TOKEN_IN_REQUEST_URL', { phase: 'audit' });
  walk.zero('A24', all.bodyHits + all.otherHeaderHits + all.bearerMisplaced, 'TOKEN_ELSEWHERE_IN_REQUEST', { phase: 'audit' });
  walk.zero('A25', all.refererHits, 'TOKEN_IN_REFERER', { phase: 'audit' });
  // A request whose ACTUAL headers were not obtained (allHeaders rejected or timed out) has
  // an INCOMPLETE Referer and header-token census: it fails, never counted as absence.
  walk.zero('A24', all.headersIncomplete, 'HEADER_CENSUS_INCOMPLETE', { phase: 'audit' });
  walk.zero('A25', all.refererUnavailable, 'REFERER_UNACCOUNTED', { phase: 'audit' });
  walk.record({ assertion_id: 'A25', outcome: 'observed', phase: 'audit', count: all.refererAbsent });
  // A34 across the WHOLE walk, sealed: any nonlocal attempt in any case, even blocked/aborted
  // or made while J07-07 polled its logs, fails; so does anything observed after a seal.
  // An unsealed census (its context not proven closed) stays live and still counts.
  const unsealedNonlocal = unsealedCensuses.reduce((total, census) => total + census.nonlocalAttempts, 0);
  walk.zero('A34', all.nonlocal + unsealedNonlocal, 'NONLOCAL_ATTEMPT_IN_WALK', { phase: 'audit' });
  walk.zero('A34', all.serviceWorkers, 'SERVICE_WORKER_IN_WALK', { phase: 'audit' });
  const lateNow = walk.j07.aggregate.sealedCensuses.reduce((total, census) => total + census.lateEvents, 0);
  walk.zero('A34', Math.max(all.lateEvents, lateNow), 'OBSERVED_AFTER_SEAL_IN_WALK', { phase: 'audit' });
});

// ---------------------------------------------------------------- pure helpers

/** Value-free inventory of an output directory: capture artifacts and error-context files. */
function artifactInventory(dir: string): { files: number; capture: number; errorContext: number } {
  const out = { files: 0, capture: 0, errorContext: 0 };
  const walkDir = (current: string) => {
    let entries: fs.Dirent[] = [];
    try {
      entries = fs.readdirSync(current, { withFileTypes: true });
    } catch {
      return;
    }
    for (const entry of entries) {
      const full = path.join(current, entry.name);
      if (entry.isDirectory()) walkDir(full);
      else {
        out.files += 1;
        if (entry.name === 'error-context.md') out.errorContext += 1;
        if (/\.(zip|png|jpe?g|webm|har|trace|network)$/i.test(entry.name)) out.capture += 1;
      }
    }
  };
  walkDir(dir);
  return out;
}

