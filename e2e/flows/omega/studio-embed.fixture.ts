// J07 thin consumer fixture: Local Studio embedded inside FA through omega-embed/1.
//
// Built from the @playwright/test base exactly as flows/auth/login.spec.ts is,
// NOT from lib/fixtures (authInitScript) or ScopeActors.openContext: no context
// here ever gets an authentication, workspace, theme or rail storage init script
// (AUTH-STREAM.json authentication.forbidden_inheritance). Every context is a
// fresh non-persistent context from the worker's browser, with exactly one
// read-only observer installed before its first page (the shared runner's E4
// hook, or this fixture on a declared direct run; never both, never neither);
// no storageState is saved or loaded.
//
// Admission happens once per worker, before any login, child start or token:
// the binding (OSS ancestry for c103d224f0 AND 79164e25f3, build receipt, APP-SIDE
// sourceRef === faRef and inert, the complete declared log set, G5a producer
// qualification), the resolved runtime (WebKit, reporter ALLOWLIST, DEBUG/NODE_DEBUG
// read with the loggers' own semantics, configured capture policy, and the EFFECTIVE
// trace/screenshot/video/launch/connect values the fixture pool resolved for this
// worker), then the seed and the protocol byte pair. Each test then admits the
// EFFECTIVE merged context options (Playwright's _combinedContextOptions, which it
// copies into every browser.newContext, manual ones included) before its case object
// exists. Any refusal is a constant "J07-BLOCKED <CODE>".
//
// Identities come from the runner's seed manifest (org A admitted to studio_tab,
// org B denied, two synthetic users each, per engine). provisionActor is not
// used: it auto-creates a personal API key, which T05 forbids.
//
// The Studio child is the BUILT omega dev from the bound OSS artifact, started
// through the runner's guarded role seam (binding.studio.wrapper) with
//   omega dev --no-open --embed-origin <FA app origin>
// and FI_APP_URL, FI_API_KEY, FI_SECRET_KEY unset. Its stdout is a pipe (not a
// TTY), so (OSS 79164e25f3, A27 (b)) its listing prints the local link as the
// literal "Open http://127.0.0.1:<port>/#t=<key>"; the printed
// "Open in Future AGI: <origin>/dashboard/studio#connect=<server>&t=<key>" line is
// the one stdout occurrence of the key, read once into memory, and the only key
// channel this walk uses. Each child gets its own non-secret start marker when it
// starts and end marker just before it is stopped, so the A27 access-log audit can
// prove every child's own log was declared and covers that child's whole life.
// J07-06b adds --new-key (and the bound --port) to restart the same project with
// a rotated key; each deviation from the bare command is recorded as a count.
//
// Value-free emission (ARTIFACT-POLICY.json): failures are J07Failure /
// J07Blocked with constant "J07 <assertion> <CODE>" messages and no cause; every
// browser action that could carry a token-bearing URL runs inside guard(), which
// drops the original error. Records are annotations validated against the
// reporter's allowlist before they are pushed. Nothing here attaches, logs,
// screenshots or traces.
import {
  test as base,
  type Browser,
  type BrowserContext,
  type BrowserContextOptions,
  type Frame,
  type LaunchOptions,
  type Page,
  type Request,
  type TestInfo,
} from '@playwright/test';
import { spawn, type ChildProcessByStdio } from 'node:child_process';
import crypto from 'node:crypto';
import fs from 'node:fs';
import http from 'node:http';
import { createRequire } from 'node:module';
import net from 'node:net';
import path from 'node:path';
import type { Readable } from 'node:stream';
import { fileURLToPath } from 'node:url';
import pg from 'pg';
import { E2E } from '../../lib/env';
import { chromiumStrictArgs, FIREFOX_STRICT_PREFS, strictBrowser } from '../../lib/walk/browsers';

const load = createRequire(import.meta.url);
const HERE = path.dirname(fileURLToPath(import.meta.url));
/** The FA repository root (e2e/flows/omega -> repo). */
export const FA_ROOT = path.resolve(HERE, '..', '..', '..');

// ---------------------------------------------------------------- module types

export interface J07Binding {
  schema: string;
  faRef: string;
  origins: { faApp: string; faApi: string };
  oss: { childSha: string; artifactRoot: string; buildReceipt: string };
  studio: { node: string; workRoot: string; wrapper: string[]; port?: number };
  seed: { manifest: string };
  observer: { install: 'e4' | 'direct' };
  appSide: { descriptor: string };
  logs: { roles: Record<LogRole, string[]> };
  g5a: { receipt: string; receiptSha256: string };
  reporters?: { accepted?: { module: string; sha256: string }[] };
}
export type LogRole = 'fa-frontend' | 'fa-access' | 'fa-api' | 'studio-access';
export interface Inertness { telemetryLive: number; nonlocalUrls: number; missingRequired: number }
interface Resolved {
  cli: string; projectTemplate: string; projectModules: string | null; webkitQualified: boolean;
  ancestry: { required: number; proven: number };
  observerInstall: 'e4' | 'direct';
  appSide: Inertness & { sourceRefMatches: boolean };
  logs: Record<LogRole, string[]>;
  g5a: { qualified: boolean; version: string; engines: string[]; files: number };
  accepted: { real: string; sha256: string }[];
}
export interface SeedUser { email: string; password: string }
export interface SeedOrg { id: string; users: SeedUser[] }
export interface Seed { A: SeedOrg; B: SeedOrg }
export interface Parity {
  faBytes: number; ossBytes: number; faCanonical: boolean; ossCanonical: boolean; equal: boolean;
  controlDetected: boolean; controlSemanticallyEqual: boolean; pass: boolean;
}
/** Worker-scoped values the fixture pool resolved for THIS worker (config, project, CLI and test.use merged). */
export interface EffectiveWorker {
  trace: unknown; screenshot: unknown; video: unknown; launchOptions: unknown; connectOptions: unknown;
}
/** Test-scoped context options every browser.newContext of this test is created with. */
export interface EffectiveContext {
  combinedContextOptions: BrowserContextOptions; contextOptions: BrowserContextOptions; storageState: unknown;
}
interface RuntimeInput {
  browserName: string; use: unknown; workers: number; retries: number; reporters: string[]; env: NodeJS.ProcessEnv;
  effective: EffectiveWorker;
}
interface BindingModule {
  loadBinding(file: string | undefined): { binding: J07Binding; resolved: Resolved };
  admitRuntime(resolved: Resolved, runtime: RuntimeInput): string | null;
  admitContext(effective: EffectiveContext): string | null;
  debugLoggingViolations(env: NodeJS.ProcessEnv): string[];
  effectiveWorkerViolations(effective: EffectiveWorker): string[];
  effectiveContextViolations(effective: EffectiveContext): string[];
  loadSeed(binding: J07Binding, browserName: string): Seed;
  protocolParity(binding: J07Binding, faRoot: string): Parity;
  capturePolicyViolations(input: Omit<RuntimeInput, 'browserName' | 'effective'>
    & { accepted?: Resolved['accepted']; effective?: EffectiveWorker }): string[];
  capturePolicySelfTest(): { safeClean: boolean; variants: number; refused: number; pass: boolean };
  reporterViolations(names: string[], accepted?: Resolved['accepted']): string[];
  inertness(value: unknown, requireKeys: boolean): Inertness;
  LOG_ROLES: readonly LogRole[];
  BindingRefusal: new (code: string) => Error & { code: string };
}
export interface CensusRequest {
  ordinal: number; request: Request; url: string; method: string; resourceType: string; isNavigation: boolean;
  frameKind: 'main' | 'child' | 'worker' | 'unknown'; frameUrl: string; ts: number;
  headers: Record<string, string> | null;
  /** True when the ACTUAL headers (allHeaders) were not obtained: `headers` is then at most the provisional set. */
  headersUnavailable: boolean;
  response: { status: number; contentType: string; ts: number } | null;
  ended: { kind: 'finished' | 'failed'; ts: number } | null;
}
export interface PageEvent {
  kind: string; doc: string; seq: number; ts: number; frameKind: string; frame: Frame;
  op?: string; area?: string; event?: string; type?: string; from?: string; at?: string; origin?: string; state?: string;
  stage?: string; targetOrigin?: string;
  before?: boolean; after?: boolean; argConnect?: boolean; connect?: boolean; embed?: boolean; top?: boolean;
  fromParent?: boolean; fromBoundFrame?: boolean; protocolValid?: boolean; keyShaped?: boolean;
  intake?: boolean; pre?: boolean; toBound?: boolean; toReplacement?: boolean; redirected?: boolean; hasToken?: boolean;
  hits?: number; unsupported?: number; historyDelta?: number; history?: number; keyOrdinal?: number; keyOrdinalAny?: number;
  redirectedReads?: number; posts?: number; keyPosts?: number; blob?: number;
}
export interface Doc { doc: string; frameKind: string; start: PageEvent | null; events: PageEvent[] }
export interface Audit {
  requests: number; urlHits: number; fragmentNavigations: number; refererPresent: number; refererAbsent: number;
  refererUnavailable: number; headersIncomplete: number; refererHits: number; bearerOnStudioCalls: number; bearerMisplaced: number;
  studioCallsWithoutBearer: number; otherHeaderHits: number; bodyHits: number; mainToStudio: number; nonlocal: number;
  websockets: number; websocketHits: number; workers: number; serviceWorkers: number; pages: number; popups: number;
  droppedEvents: number; lateEvents: number;
}
export interface Census {
  label: string; via: string; requests: CensusRequest[]; events: PageEvent[]; websockets: { url: string }[];
  pages: number; popups: number; workers: number; serviceWorkers: number; nonlocalAttempts: number;
  initScripts: number; standalone?: boolean; sealed: boolean; lateEvents: number;
  setStudioOrigins(origins: string[]): void; docs(): Doc[]; settle(): Promise<void>; seal(): void;
  headerUses(token: string): Promise<number>;
  audit(secrets: string[], bearer?: Map<string, string[]>): Promise<Audit>;
}
interface ObserverModule {
  acquireCensus(context: BrowserContext, options: { label: string; install: 'e4' | 'direct' }): Promise<Census>;
  armMalformedHello(context: BrowserContext, studioOrigin: string): Promise<void>;
  countHits(text: string, variants: string[]): number;
  tokenVariants(secrets: Iterable<string>): string[];
  isLocalUrl(url: string): boolean;
  originOf(url: string): string;
  ObserverRefusal: new (code: string) => Error & { code: string };
}
export interface IdbScan {
  enumerated: boolean; databases: number; stores: number; records: number; texts: string[]; unsupported: number; unreadable: number;
}
export interface SeamState { stage: string; helloSeen: boolean; pendingSeen: boolean; redirectedReads: number; posts: number; keyPosts: number }
export type ThemeWord = 'light' | 'dark' | 'system' | 'none' | 'other';
export interface ThemeState { choice: ThemeWord; stored: ThemeWord; rendered: ThemeWord }
export interface ChildLog { caseId: string; start: string; end: string; startSent: boolean; endSent: boolean }
/** How one context.close() settled at teardown (a hang past CONTEXT_CLOSE_MS is 'timeout'). */
export type CloseOutcome = 'resolved' | 'rejected' | 'timeout';
export interface ChildOutput {
  allowedLine: number; faLines: number; elsewhere: number; stdoutKeyHits: number; stderrHits: number; otherTokenHits: number;
  plainOpenLiteral: number; plainOpenKeyed: number; pass: boolean;
}
export interface OraclesModule {
  poll(fn: () => Promise<boolean> | boolean, ms: number, every?: number): Promise<boolean>;
  withDeadline<T>(promise: Promise<T>, ms: number): Promise<T | symbol>;
  TIMED_OUT: symbol;
  agreeAndHold(input: {
    observe: () => Promise<{ agree: boolean; gate: string | null }>; retry: (remaining: number) => Promise<unknown>;
    ms: number; holdMs?: number; sampleMs?: number;
    maxGapMs?: number;
  }): Promise<{ ok: boolean; code: string | null; agreed: number | null; samples: number; retries: number; maxGap: number; span: number }>;
  connectPhaseWrites(docs: Doc[]): number;
  writeHits(docs: Doc[]): number;
  writeUnsupported(docs: Doc[]): number;
  pendingBlobReads(docs: Doc[]): number;
  blobBarrierSettled(inPage: { doc: string; started: number; settled: number } | null, docs: Doc[]): boolean;
  chooseTheme(input: {
    read: () => Promise<ThemeState>; click: () => Promise<void>; next: (previous: ThemeState) => Promise<ThemeState>;
    want: 'light' | 'dark'; requireStored?: boolean; maxClicks?: number;
  }): Promise<{ ok: boolean; clicks: number } & ThemeState>;
  studioLogCoverage(children: ChildLog[], texts: string[]): {
    children: number; files: number; startUnsent: number; endUnsent: number; undeclared: number; unmarked: number;
    endUncovered: number; startUncovered: number; orderInverted: number; foreignFiles: number; emptyFiles: number;
    blocked: boolean; pass: boolean;
  };
  closeVerdict(input: { outcome: CloseOutcome; closeEvent: boolean; listed: boolean }): { closed: boolean; code: string | null };
  canvasNodeKey(input: { nodes: Record<string, string | null>; total: number; unidentified?: number }): { ok: boolean; codes: string[]; key: string };
  nodesDoneAgreement(input: {
    run: string; nodes: Record<string, string | null>; total: number; done: number; unidentified?: number; pinned?: string | null;
  }): { codes: string[]; key: string; agree: boolean };
  childOutputAudit(input: { stdout: string; stderr: string; link: string; serverUrl: string; own: string[]; others: string[] }): ChildOutput;
  intakeVerdict(doc: Doc | undefined): {
    intakeSeen: boolean; preIntakeScripts: number; firstOpScrub: boolean; witnessed: boolean; witnessClean: boolean; pass: boolean;
  };
  senderVerdict(posts: PageEvent[], studioOrigin: string): {
    embed: number; toBound: number; keyPosts: number; wildcard: number; notPinned: number; keyNotPinned: number; pass: boolean;
  };
  pendingReplacementVerdict(seam: SeamState, receivedKeys: number): { established: boolean; delivered: number; pass: boolean };
  cutWitness(input: { ended: CensusRequest['ended']; cutWall: number; evaluatedAt: number; destroyed: number }): {
    destroyed: number; closedDuringRefusal: boolean; failed: boolean; pass: boolean;
  };
  newerThanCut(cut: { status?: string; updatedAt?: string }, candidates: { kind: 'json' | 'sse'; text: string }[], runId: string): {
    candidates: number; states: number; newer: number; pass: boolean;
  };
  withoutFramingDenial(headers: Record<string, string>): { headers: Record<string, string>; removed: number };
  structuredTexts: (value: unknown) => unknown;
  idbScanInPage: (walk: unknown) => Promise<IdbScan>;
}
interface ReporterModule {
  recordViolations(record: unknown): string[];
  RECORD_TYPE: string; BLOCKED_TYPE: string; SCHEMA: string; CASES: readonly string[];
}

export const bindingModule = load('./studio-embed.binding.cjs') as BindingModule;
export const observer = load('./studio-embed.observer.cjs') as ObserverModule;
export const oracles = load('./studio-embed.oracles.cjs') as OraclesModule;
const reporter = load('./studio-embed.reporter.cjs') as ReporterModule;

// ---------------------------------------------------------------- value-free failures

/** A failed J07 assertion: constant message, no cause, no value. */
export class J07Failure extends Error {
  constructor(readonly assertion: string, readonly code: string) {
    super(`J07 ${assertion} ${code}`);
    this.name = 'J07Failure';
  }
}
/** A refused cell: the walk cannot run here (unbound, unqualified, unproven). */
export class J07Blocked extends Error {
  constructor(readonly code: string) {
    super(`J07-BLOCKED ${code}`);
    this.name = 'J07Blocked';
  }
}

export type RoleAlias = 'orga-user1' | 'orga-user2' | 'orgb-user1' | 'orgb-user2' | 'anonymous';
export type OriginSlot = 'fa-app' | 'fa-api' | 'studio' | 'studio-proxy' | 'hostile' | 'hostile-parent' | 'sibling'
  | 'same-origin-sibling' | 'replacement' | 'none';
export interface J07Record {
  assertion_id?: string; control_id?: string; context_ordinal?: number; request_ordinal?: number; phase?: string;
  outcome: 'pass' | 'fail' | 'blocked' | 'not_covered' | 'observed'; count?: number; elapsed_ms?: number;
  role_alias?: RoleAlias; run_alias?: string; http_status?: number; method_enum?: string; route_template_id?: string;
  origin_slot?: OriginSlot; comparison_boolean?: boolean;
}

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));
const monotonic = () => performance.now();
/** Teardown bound on one context.close(); past it the close counts as failed (CONTEXT_CLOSE_FAILED). */
const CONTEXT_CLOSE_MS = 30_000;
/** Bounded wait for a context's own 'close' event after its close() resolved. */
const CLOSE_EVENT_MS = 5_000;

/**
 * Polls `fn` until it returns true within `ms` (oracles.poll): a true observed only
 * after the deadline is a miss, and each read is bounded by the remaining budget.
 */
export const poll = (fn: () => Promise<boolean> | boolean, ms: number, every = 100): Promise<boolean> =>
  oracles.poll(fn, ms, every);

const STUDIO_DEV_HEADER = 'Omega Studio · running on this machine';
const FA_LINK_PREFIX = 'Open in Future AGI: ';
const LINK_RE = /^(https?:\/\/[^/#?\s]+)\/dashboard\/studio#connect=(http:\/\/127\.0\.0\.1:(\d{2,5}))&t=([A-Za-z0-9_-]{43})$/;
const DROPPED_ENV = /^(OTEL_|OMEGA_TRACE|FI_|J07_|E2E_|PW|PLAYWRIGHT_)|_API_KEY$|_TOKEN$|_SECRET|^GOOGLE_|^VERTEX|^ANTHROPIC|^OPENAI|^GEMINI|^AWS_|^AZURE_/;
const FORBIDDEN_CHILD_ENV = ['FI_APP_URL', 'FI_API_KEY', 'FI_SECRET_KEY'] as const;

// ---------------------------------------------------------------- Studio child

export interface StudioChild {
  serverUrl: string; origin: string; port: number; token: string; link: string;
  project: string; caseDir: string; extraFlags: number;
  /** .omega/studio.key facts read when the link was printed (a later --new-key rewrites the file). */
  keyFile: { ownerOnly: boolean; matches: boolean };
  stdout(): string; stderr(): string; alive(): boolean;
  stop(): Promise<void>;
}

/** Value-free A27 facts about one child's complete stdout/stderr and key file. */
export interface ChildAudit extends ChildOutput {
  caseId: string; bootstrapComplete: boolean; keyFileOwnerOnly: boolean; keyFileMatches: boolean; stdoutBytes: number;
}

// ---------------------------------------------------------------- worker state

export interface Aggregate {
  /** Cases that reached finishCase (their workload ran to its end). */
  finished: Set<string>;
  /** Per case, the audit of every census taken AFTER all of the case's contexts were closed (sealed). */
  sealed: Map<string, Audit>;
  /** Every sealed census of the walk, kept live: anything it observes after its seal stays countable. */
  sealedCensuses: Census[];
  /**
   * Contexts whose closure teardown could not PROVE (close rejected or timed out, or no
   * 'close' event / still listed by the browser): never sealed, never accepted, counted
   * here and kept tracked (their censuses stay live in `unsealedCensuses`). Any fails the walk.
   */
  closeFailures: number;
  unsealedCensuses: Census[];
  secrets: Set<string>;
  childAudits: ChildAudit[];
  children: number;
  /** Per started Studio child: its own non-secret start and end markers and whether each reached it. */
  childLogs: ChildLog[];
  /** The walk-window start marker (non-secret), sent to the FA app and API at admission. */
  walkStart: string;
  walkStartSent: { faApp: boolean; faApi: boolean };
}

export interface J07Worker {
  binding: J07Binding;
  resolved: Resolved;
  seed: Seed;
  parity: Parity | null;
  parityCode: string | null;
  policyViolations: string[];
  /** The effective worker-scoped capture values admission judged (and their violations: none, or it refused). */
  effective: { values: EffectiveWorker; violations: string[] };
  policySelfTest: { safeClean: boolean; variants: number; refused: number; pass: boolean };
  reporters: string[];
  browserName: 'chromium' | 'firefox' | 'webkit';
  invocation: string;
  aggregate: Aggregate;
}

export type J07Options = { j07Walk: boolean };

function blockedFrom(error: unknown): J07Blocked {
  if (error instanceof J07Blocked) return error;
  const code = (error as { code?: unknown } | null)?.code;
  if ((error instanceof bindingModule.BindingRefusal || error instanceof observer.ObserverRefusal) && typeof code === 'string'
    && /^[A-Z0-9_]{1,64}$/.test(code)) return new J07Blocked(code);
  return new J07Blocked('BINDING_INTERNAL');
}

// ---------------------------------------------------------------- the case object

export class J07Case {
  readonly caseId: string;
  /** Every context this case opened; kept (never spliced) so a context teardown cannot prove closed stays tracked. */
  private readonly contexts: BrowserContext[] = [];
  /** Contexts whose own 'close' event was witnessed (Playwright emits it only once the context is really gone). */
  private readonly closedContexts = new Set<BrowserContext>();
  private readonly censuses: Census[] = [];
  private readonly censusContext = new Map<Census, BrowserContext>();
  private readonly children: StudioChild[] = [];
  private readonly outputAudited = new Set<StudioChild>();
  private readonly cleanups: (() => Promise<void>)[] = [];
  private pool: pg.Pool | null = null;
  /** Bearer tokens issued per origin for this case's Studio calls (children, proxies). */
  private readonly bearer = new Map<string, string[]>();
  private closing: Promise<Audit> | null = null;

  constructor(
    readonly j07: J07Worker,
    private readonly browser: Browser,
    readonly testInfo: TestInfo,
    /** The effective context options this test's contexts get, as admitted before the case existed. */
    readonly contextAdmission: { values: EffectiveContext; violations: string[] },
  ) {
    const match = /^(J07-\d{2}b?) /.exec(testInfo.title);
    if (!match) throw new J07Blocked('CASE_TITLE');
    this.caseId = match[1];
  }

  get binding(): J07Binding {
    return this.j07.binding;
  }
  get seed(): Seed {
    return this.j07.seed;
  }
  get fa(): string {
    return this.j07.binding.origins.faApp;
  }
  get browserName(): string {
    return this.j07.browserName;
  }

  // ------------------------------------------------------------ records

  record(entry: J07Record): void {
    const full = {
      schema: reporter.SCHEMA,
      case_id: this.caseId,
      browser: this.j07.browserName,
      invocation_id: this.j07.invocation,
      ...entry,
    };
    if (reporter.recordViolations(full).length > 0) throw new J07Failure('A37', 'RECORD_SHAPE');
    this.testInfo.annotations.push({ type: reporter.RECORD_TYPE, description: JSON.stringify(full) });
  }

  /** Records the outcome and throws a constant failure when `ok` is false. */
  check(assertion: string, ok: boolean, code: string, extra: Omit<J07Record, 'outcome' | 'assertion_id'> = {}): void {
    this.record({ assertion_id: assertion, outcome: ok ? 'pass' : 'fail', comparison_boolean: ok, ...extra });
    if (!ok) throw new J07Failure(assertion, code);
  }

  /** A zero-occurrence oracle: records the count, fails above zero. */
  zero(assertion: string, count: number, code: string, extra: Omit<J07Record, 'outcome' | 'assertion_id' | 'count'> = {}): void {
    this.record({ assertion_id: assertion, outcome: count === 0 ? 'pass' : 'fail', count, ...extra });
    if (count !== 0) throw new J07Failure(assertion, code);
  }

  /** Runs a browser/API action; any error becomes a constant failure without its message. */
  async guard<T>(assertion: string, code: string, fn: () => Promise<T>): Promise<T> {
    try {
      return await fn();
    } catch (error) {
      if (error instanceof J07Failure || error instanceof J07Blocked) throw error;
      throw new J07Failure(assertion, code);
    }
  }

  blocked(code: string): never {
    this.testInfo.annotations.push({ type: reporter.BLOCKED_TYPE, description: code });
    this.record({ outcome: 'blocked', phase: 'blocked' });
    throw new J07Blocked(code);
  }

  requireParity(): void {
    if (!this.j07.parity) this.blocked(this.j07.parityCode ?? 'PROTOCOL_PARITY');
    if (!this.j07.parity.pass) this.blocked('PROTOCOL_PARITY');
  }

  // ------------------------------------------------------------ contexts

  /**
   * A fresh base context with exactly one observer installed before its first page:
   * the census the shared runner's E4 hook installed inside newContext (binding
   * observer.install 'e4'), or one this fixture installs on a declared direct run.
   * Never a second install, never a context without one.
   */
  async newContext(role: RoleAlias, colorScheme: 'light' | 'dark' = 'light'): Promise<{ context: BrowserContext; census: Census }> {
    if (this.closing) throw new J07Failure('A34', 'CONTEXT_AFTER_SEAL');
    // Every capture key is named explicitly: Playwright's runBeforeCreateBrowserContext copies
    // a merged context option into a manual context only when the key is absent, so these
    // can never inherit a HAR, video or storage state (admission refused one already).
    const context = await this.browser.newContext({
      serviceWorkers: 'block',
      colorScheme,
      viewport: { width: 1280, height: 800 },
      recordHar: undefined,
      recordVideo: undefined,
      storageState: undefined,
      proxy: undefined,
    });
    // The closure witness is armed before anything else can happen to the context.
    context.once('close', () => { this.closedContexts.add(context); });
    this.contexts.push(context);
    let census: Census;
    try {
      census = await observer.acquireCensus(context, { label: role, install: this.j07.resolved.observerInstall });
    } catch (error) {
      if (error instanceof observer.ObserverRefusal) this.blocked(blockedFrom(error).code);
      throw new J07Failure('A34', 'OBSERVER_INSTALL');
    }
    this.censuses.push(census);
    this.censusContext.set(census, context);
    this.record({ assertion_id: 'A04', outcome: 'observed', phase: 'precondition', role_alias: role,
      context_ordinal: this.contexts.length, count: census.initScripts });
    this.record({ assertion_id: 'A34', outcome: 'observed', phase: 'precondition', role_alias: role,
      context_ordinal: this.contexts.length, comparison_boolean: census.via === 'e4' });
    return { context, census };
  }

  /** Every token this worker has issued (memory only). */
  secrets(): string[] {
    return [...this.j07.aggregate.secrets];
  }

  variants(extra: string[] = []): string[] {
    return observer.tokenVariants([...this.secrets(), ...extra]);
  }

  hits(text: string, extra: string[] = []): number {
    return observer.countHits(text, this.variants(extra));
  }

  // ------------------------------------------------------------ FA login (AUTH-E2E-001 sequence)

  private pgPool(): pg.Pool {
    this.pool ??= new pg.Pool({ connectionString: E2E.pgUrl, max: 1 });
    return this.pool;
  }

  private async activeTokenRows(email: string): Promise<number> {
    const result = await this.pgPool().query<{ n: number }>(
      `SELECT count(*)::int AS n FROM accounts_auth_token t JOIN accounts_user u ON t.user_id = u.id
       WHERE u.email = $1 AND t.is_active`,
      [email],
    );
    return Number(result.rows[0]?.n ?? 0);
  }

  /**
   * Actual UI login on the token-free /auth/jwt/login route, before any Studio
   * token exists, with the active accounts_auth_token row-count witness. The
   * same page then stays in use; nothing is re-seeded.
   */
  async login(page: Page, user: SeedUser, role: RoleAlias): Promise<{ landing: string }> {
    await this.guard('A04', 'LOGIN_ROUTE', () => page.goto(`${this.fa}/auth/jwt/login`, { waitUntil: 'domcontentloaded' }));
    const fragmentFree = await this.guard('A04', 'LOGIN_ROUTE', () => page.evaluate(() => location.hash === ''));
    this.check('A04', fragmentFree, 'LOGIN_ROUTE_FRAGMENT', { phase: 'login', role_alias: role });
    const before = await this.guard('A04', 'LOGIN_PROBE', () => this.activeTokenRows(user.email));
    const tokenResponse = page.waitForResponse(
      (response) => new URL(response.url()).pathname.endsWith('/accounts/token/') && response.request().method() === 'POST',
      { timeout: 30_000 },
    );
    tokenResponse.catch(() => undefined);
    await this.guard('A04', 'LOGIN_FORM', async () => {
      await page.getByLabel(/email/i).fill(user.email);
      await page.getByLabel(/password/i).fill(user.password);
      await page.getByRole('button', { name: 'Continue' }).click();
    });
    const pair = await this.guard('A04', 'LOGIN_TOKEN', async () => {
      const response = await tokenResponse;
      const body = (await response.json()) as { access?: unknown; refresh?: unknown };
      const nonEmpty = (value: unknown) => typeof value === 'string' && value.length > 0;
      return { status: response.status(), ok: nonEmpty(body.access) && nonEmpty(body.refresh) };
    });
    this.check('A04', pair.status === 200 && pair.ok, 'LOGIN_TOKEN_PAIR', { phase: 'login', role_alias: role, http_status: pair.status });
    await this.guard('A04', 'LOGIN_LANDING', () =>
      page.waitForFunction(() => !location.pathname.startsWith('/auth/'), null, { timeout: 60_000 }));
    const grew = await poll(async () => (await this.activeTokenRows(user.email)) > before, 15_000, 250);
    this.check('A04', grew, 'LOGIN_ACTIVE_TOKEN_ROW', { phase: 'login', role_alias: role });
    const landing = await this.guard('A04', 'LOGIN_LANDING', () =>
      page.evaluate(() => location.origin + location.pathname + location.search));
    return { landing };
  }

  /**
   * Organization readback and studio_tab admission on the fragment-free Studio
   * route: the app's own /accounts/user-info/ and /api/capabilities/ answers, and
   * the rendered route (omega-tab-studio, or the standard 404).
   */
  async assertAdmission(page: Page, org: SeedOrg, admitted: boolean, role: RoleAlias, assertion = 'A31'): Promise<void> {
    let orgId: string | null = null;
    let allowed: boolean | null = null;
    const onResponse = async (response: import('@playwright/test').Response) => {
      try {
        const pathname = new URL(response.url()).pathname;
        if (response.status() !== 200) return;
        if (pathname.endsWith('/accounts/user-info/') && orgId === null) {
          const body = (await response.json()) as { organization?: { id?: unknown } };
          if (typeof body.organization?.id === 'string') orgId = body.organization.id;
        } else if (/\/capabilities\/?$/.test(pathname) && allowed === null) {
          const body = (await response.json()) as { features?: Record<string, { allowed?: unknown }> };
          allowed = body.features?.omega_studio_tab?.allowed === true;
        }
      } catch {
        /* unreadable answer: stays null and fails below */
      }
    };
    page.on('response', onResponse);
    try {
      await this.guard(assertion, 'ADMISSION_ROUTE', () => page.goto(`${this.fa}/dashboard/studio`, { waitUntil: 'domcontentloaded' }));
      const answered = await poll(() => orgId !== null && allowed !== null, 30_000, 100);
      this.check(assertion, answered, 'ADMISSION_READBACK', { phase: 'precondition', role_alias: role });
      this.check(assertion, orgId === org.id, 'ORG_READBACK', { phase: 'precondition', role_alias: role });
      this.check(assertion, allowed === admitted, 'CAPABILITY_ANSWER', { phase: 'precondition', role_alias: role });
      const rendered = admitted
        ? await poll(async () => (await page.getByTestId('omega-tab-studio').count()) === 1, 30_000, 200)
        : await poll(async () => (await page.evaluate(() => location.pathname)) === '/404'
            && (await page.getByTestId('omega-tab-studio').count()) === 0, 30_000, 200);
      this.check(assertion, rendered, admitted ? 'ADMITTED_ROUTE_ABSENT' : 'DENIED_ROUTE_RENDERED',
        { phase: 'precondition', role_alias: role });
    } finally {
      page.off('response', onResponse);
    }
  }

  async gotoTokenFree(page: Page, url: string, assertion = 'A04'): Promise<void> {
    this.check(assertion, !url.includes('#') && this.hits(url) === 0, 'TOKEN_FREE_ROUTE', { phase: 'precondition' });
    await this.guard(assertion, 'TOKEN_FREE_ROUTE', () => page.goto(url, { waitUntil: 'domcontentloaded' }));
  }

  /** Sets the FA header theme by real UI (the ThemeModeToggle cycles light, dark, system). */
  async setHostTheme(page: Page, mode: 'light' | 'dark'): Promise<void> {
    const want = mode === 'light' ? 'Light mode' : 'Dark mode';
    await this.guard('A16', 'HOST_THEME_TOGGLE', async () => {
      const toggle = page.getByRole('button', { name: /^(Light|Dark|System) mode$/ });
      for (let i = 0; i < 4; i += 1) {
        if ((await toggle.getAttribute('aria-label', { timeout: 15_000 })) === want) return;
        await toggle.click();
        await sleep(150);
      }
      if ((await toggle.getAttribute('aria-label')) !== want) throw new Error('theme');
    });
  }

  // ------------------------------------------------------------ Studio child (guarded role seam)

  private childEnv(caseDir: string): NodeJS.ProcessEnv {
    const env: NodeJS.ProcessEnv = Object.fromEntries(
      Object.entries(process.env).filter(([key]) => !DROPPED_ENV.test(key)),
    );
    for (const key of FORBIDDEN_CHILD_ENV) delete env[key];
    for (const name of ['home', 'xdg-config', 'xdg-data', 'xdg-cache', 'xdg-state']) {
      fs.mkdirSync(path.join(caseDir, name), { recursive: true });
    }
    return {
      ...env,
      HOME: path.join(caseDir, 'home'),
      XDG_CONFIG_HOME: path.join(caseDir, 'xdg-config'),
      XDG_DATA_HOME: path.join(caseDir, 'xdg-data'),
      XDG_CACHE_HOME: path.join(caseDir, 'xdg-cache'),
      XDG_STATE_HOME: path.join(caseDir, 'xdg-state'),
      GIT_CONFIG_GLOBAL: path.join(caseDir, 'gitconfig'),
      OMEGA_NO_UPDATE_CHECK: '1',
      OMEGA_FI_TRACING: 'off',
    };
  }

  /**
   * Starts omega dev through the runner's guarded role seam and reads the one
   * printed Future AGI link into memory (A01). `project` reuses a case's project
   * (J07-06b restart); `newKey` adds --new-key.
   */
  async startStudio(slug: string, options: { newKey?: boolean; project?: string } = {}): Promise<StudioChild> {
    const { binding, resolved } = this.j07;
    let caseDir: string;
    let project: string;
    if (options.project) {
      project = options.project;
      caseDir = path.dirname(project);
    } else {
      fs.mkdirSync(binding.studio.workRoot, { recursive: true });
      caseDir = fs.realpathSync(fs.mkdtempSync(path.join(binding.studio.workRoot,
        `${this.caseId.toLowerCase()}-${this.browserName}-${slug}-`)));
      project = path.join(caseDir, 'project');
      fs.cpSync(resolved.projectTemplate, project, { recursive: true });
      if (resolved.projectModules) fs.symlinkSync(resolved.projectModules, path.join(project, 'node_modules'), 'dir');
    }
    const env = this.childEnv(caseDir);
    const fiUnset = FORBIDDEN_CHILD_ENV.every((key) => !(key in env));
    this.check('A01', fiUnset, 'CHILD_ENV_FI_SET', { phase: 'precondition' });
    const extra = [
      ...(binding.studio.port !== undefined ? ['--port', String(binding.studio.port)] : []),
      ...(options.newKey ? ['--new-key'] : []),
    ];
    const [launcher, ...prefix] = binding.studio.wrapper;
    const argv = [...prefix, binding.studio.node, resolved.cli, 'dev', '--no-open', '--embed-origin', this.fa, ...extra];
    let out = '';
    let err = '';
    const child: ChildProcessByStdio<null, Readable, Readable> = spawn(launcher, argv, {
      cwd: project,
      env,
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    const exited = new Promise<void>((resolve) => child.once('close', () => resolve()));
    child.stdout.setEncoding('utf8');
    child.stderr.setEncoding('utf8');
    child.stdout.on('data', (chunk: string) => { out += chunk; });
    child.stderr.on('data', (chunk: string) => { err += chunk; });
    let spawnFailed = false;
    child.once('error', () => { spawnFailed = true; });
    const alive = () => !spawnFailed && child.exitCode === null && child.signalCode === null;
    // This child's own walk-window markers (non-secret): start once it serves, end just
    // before it is stopped, so its declared access log must cover its whole life (A27).
    const logRecord: ChildLog = { caseId: this.caseId, start: marker(), end: marker(), startSent: false, endSent: false };
    let serverForMarker: string | null = null;
    const stop = async () => {
      if (alive() && serverForMarker !== null && !logRecord.endSent) {
        logRecord.endSent = await sendMarker(`${serverForMarker}/studio/v1/info?j07walk=${logRecord.end}`);
      }
      if (alive()) {
        child.kill('SIGTERM');
        const timer = setTimeout(() => { if (alive()) child.kill('SIGKILL'); }, 10_000);
        await exited;
        clearTimeout(timer);
      } else {
        await Promise.race([exited, sleep(1_000)]);
      }
    };
    this.cleanups.push(stop);
    const ready = await poll(() => !alive() || out.split('\n').some((line) => line.startsWith('Runs are kept in ')), 90_000, 100);
    this.check('A01', ready && alive(), 'STUDIO_DID_NOT_START', { phase: 'precondition' });
    // The one printed link, consumed in memory; never compared with expect().
    const lines = out.split('\n');
    const linkLines = lines.filter((line) => line.startsWith(FA_LINK_PREFIX));
    this.check('A01', linkLines.length === 1, 'PRINTED_LINK_COUNT', { phase: 'precondition', count: linkLines.length });
    const link = linkLines[0].slice(FA_LINK_PREFIX.length).trim();
    const match = LINK_RE.exec(link);
    this.check('A01', match !== null && match[1] === this.fa, 'PRINTED_LINK_SHAPE', { phase: 'precondition', origin_slot: 'fa-app' });
    const [, , serverUrl, portText, token] = match as RegExpExecArray;
    this.j07.aggregate.secrets.add(token);
    // .omega/studio.key is the one other allowed key channel (A27): owner-only, same key.
    const keyFile = { ownerOnly: false, matches: false };
    try {
      const file = path.join(project, '.omega', 'studio.key');
      const stat = fs.statSync(file);
      keyFile.ownerOnly = stat.isFile() && (stat.mode & 0o077) === 0;
      keyFile.matches = fs.readFileSync(file, 'utf8').trim() === token;
    } catch {
      /* absent or unreadable: both stay false and A27 fails */
    }
    this.j07.aggregate.children += 1;
    // Walk-window coverage (A27): this child's access log carries ITS OWN start marker.
    this.j07.aggregate.childLogs.push(logRecord);
    serverForMarker = serverUrl;
    logRecord.startSent = await sendMarker(`${serverUrl}/studio/v1/info?j07walk=${logRecord.start}`);
    const studio: StudioChild = {
      serverUrl,
      origin: serverUrl,
      port: Number(portText),
      token,
      link,
      project,
      caseDir,
      extraFlags: (binding.studio.port !== undefined ? 1 : 0) + (options.newKey ? 1 : 0),
      keyFile,
      stdout: () => out,
      stderr: () => err,
      alive,
      stop,
    };
    this.children.push(studio);
    // Deviations from the bare contract command (--port, --new-key), as a count.
    this.record({ assertion_id: 'A01', outcome: 'observed', phase: 'precondition', count: studio.extraFlags });
    return studio;
  }

  /**
   * A27 facts for one child, computed in memory over its COMPLETE output (call after stop),
   * per A27 (b) at OSS 79164e25f3 (oracles.childOutputAudit): the plain
   * local link is the literal "Open <server>/#t=<key>", the key occurs exactly once on
   * stdout (the printed Future AGI link) and never on stderr.
   */
  auditChild(child: StudioChild): ChildAudit {
    const own = observer.tokenVariants([child.token]);
    const others = observer.tokenVariants(this.secrets().filter((secret) => secret !== child.token));
    const output = oracles.childOutputAudit({
      stdout: child.stdout(), stderr: child.stderr(), link: child.link, serverUrl: child.serverUrl, own, others,
    });
    const lines = child.stdout().split('\n');
    return {
      caseId: this.caseId,
      ...output,
      bootstrapComplete: lines.includes(STUDIO_DEV_HEADER) && lines.some((line) => line.startsWith('Runs are kept in ')),
      keyFileOwnerOnly: child.keyFile.ownerOnly,
      keyFileMatches: child.keyFile.matches,
      stdoutBytes: Buffer.byteLength(child.stdout()),
    };
  }

  // ------------------------------------------------------------ following the link

  /**
   * Follows a token-bearing link on this page without handing the URL to a
   * Playwright API whose error or call log could echo it: the page itself
   * assigns the location. 'document' expects a new document (cold intake);
   * 'fragment' a same-document fragment navigation (open-page intake).
   */
  async followLink(page: Page, link: string, kind: 'document' | 'fragment' | 'auto', assertion = 'A01'): Promise<'document' | 'fragment'> {
    return this.guard(assertion, 'FOLLOW_LINK', async () => {
      let mode = kind;
      if (mode === 'auto') {
        const here = await page.evaluate(() => location.pathname + location.search);
        mode = here === '/dashboard/studio' ? 'fragment' : 'document';
      }
      const loaded = mode === 'document' ? page.waitForEvent('domcontentloaded', { timeout: 60_000 }) : null;
      loaded?.catch(() => undefined);
      await page.evaluate((target) => { setTimeout(() => window.location.assign(target), 0); }, link);
      if (loaded) await loaded;
      await page.waitForFunction(() => !/(?:^#|&)connect=/.test(location.hash), null, { timeout: 15_000 });
      return mode;
    });
  }

  async hostHref(page: Page): Promise<string> {
    return this.guard('A08', 'HOST_HREF', () => page.evaluate(() => location.href));
  }

  async waitStatus(page: Page, state: string, ms: number, assertion: string, code: string): Promise<number> {
    const began = monotonic();
    const ok = await poll(async () => (await page.getByTestId('studio-status').getAttribute('data-state', { timeout: 1_000 })) === state, ms, 100);
    const elapsed = Math.round(monotonic() - began);
    this.check(assertion, ok, code, { elapsed_ms: elapsed });
    return elapsed;
  }

  async studioFrame(page: Page, origin: string, assertion = 'A02'): Promise<Frame> {
    const find = () => page.frames().find((frame) => frame !== page.mainFrame() && frame.url().startsWith(`${origin}/`)
      && frame.parentFrame() === page.mainFrame() && !frame.name().startsWith('j07-') && !frame.isDetached());
    const found = await poll(() => Boolean(find()), 30_000, 100);
    this.check(assertion, found, 'STUDIO_FRAME', { origin_slot: 'studio' });
    return find() as Frame;
  }

  /** The iframe element the host bound (data-testid omega-embed-frame) as a Playwright Frame. */
  async boundFrame(page: Page): Promise<Frame | null> {
    const handle = await page.$('iframe[data-testid="omega-embed-frame"]');
    return handle ? handle.contentFrame() : null;
  }

  /** The bound frame once it is a NEW frame (a re-mounted surface) on `origin`. */
  async freshBoundFrame(page: Page, previous: Frame | null, origin: string, assertion: string): Promise<Frame> {
    let current: Frame | null = null;
    const found = await poll(async () => {
      current = await this.boundFrame(page);
      return current !== null && current !== previous && !current.isDetached() && current.url().startsWith(`${origin}/`);
    }, 30_000, 100);
    this.check(assertion, found, 'BOUND_FRAME_NOT_REMOUNTED', { origin_slot: 'studio' });
    return current as unknown as Frame;
  }

  // ------------------------------------------------------------ host instrumentation (observer API, top document)

  private async hostApi<T>(page: Page, assertion: string, code: string, method: string, arg: string | null = null): Promise<T> {
    return this.guard(assertion, code, () => page.evaluate(([name, call, value]) => {
      const api = (window as unknown as Record<symbol, Record<string, (input?: string) => unknown>>)[Symbol.for(name)];
      return (value === null ? api[call]() : api[call](value)) as never;
    }, [OBSERVER_KEY, method, arg] as const)) as Promise<T>;
  }

  hostInstrumented(page: Page, assertion = 'A10'): Promise<boolean> {
    return this.hostApi<boolean>(page, assertion, 'HOST_INSTRUMENTATION', 'hostInstrumented');
  }

  boundIndex(page: Page, assertion = 'A19'): Promise<number> {
    return this.hostApi<number>(page, assertion, 'BOUND_FRAME_INDEX', 'boundIndex');
  }

  armPending(page: Page, replacementName: string): Promise<boolean> {
    return this.hostApi<boolean>(page, 'A12', 'SEAM_ARM', 'armPending', replacementName);
  }

  seamState(page: Page): Promise<SeamState> {
    return this.hostApi<SeamState>(page, 'A12', 'SEAM_STATE', 'seamState');
  }

  /** The host's postMessage calls into frames (sender witness), in arrival order. */
  static posts(events: PageEvent[]): PageEvent[] {
    return events.filter((event) => event.kind === 'post' && event.frameKind === 'main');
  }

  // ------------------------------------------------------------ Studio backend truth (direct, never through the page)

  async studioApi<T = unknown>(server: string, token: string, method: string, route: string, body?: unknown, respondAsync = false): Promise<{ status: number; json: T | null }> {
    return this.guard('A02', 'STUDIO_API', async () => {
      const response = await fetch(server + route, {
        method,
        headers: {
          authorization: `Bearer ${token}`,
          ...(body === undefined ? {} : { 'content-type': 'application/json' }),
          ...(respondAsync ? { prefer: 'respond-async' } : {}),
        },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
      const text = await response.text();
      let json: T | null = null;
      try {
        json = text ? (JSON.parse(text) as T) : null;
      } catch {
        json = null;
      }
      return { status: response.status, json };
    });
  }

  /** Opens the fixture's `router` entry inside the frame and runs it (studio-checks.cjs runRouterInFrame). */
  async runRouterInFrame(frame: Frame, assertion = 'A02'): Promise<string> {
    return this.guard(assertion, 'FRAME_RUN', async () => {
      await frame.waitForSelector('[data-testid="entries-home"]', { timeout: 60_000 });
      await frame.locator('[data-testid="entry-row"][data-entry-id="router"]').click();
      await frame.waitForSelector('.react-flow__node', { timeout: 30_000 });
      const editor = frame.locator('[data-testid="input-editor"]');
      await editor.waitFor();
      if ((await editor.getAttribute('data-mode')) !== 'raw') await frame.locator('[data-testid="input-mode"]').click();
      await frame.locator('[data-testid="run-input"]').fill(JSON.stringify({ question: 'Which lane?' }));
      await frame.locator('[data-testid="run-submit"]').click();
      await frame.waitForFunction(
        () => document.querySelector('[data-testid="run-status"]')?.getAttribute('data-status') === 'succeeded',
        null, { timeout: 60_000 });
      const hash = new URL(frame.url()).hash;
      return new URLSearchParams(hash.split('?')[1] || '').get('run') || '';
    });
  }

  // ------------------------------------------------------------ storage scans (A05-A07)

  /**
   * Raw storage of one document, scanned in Node memory; only counts leave.
   * IndexedDB and history.state are walked as structured-clone values (Map, Set,
   * ArrayBuffer, typed arrays, Blob, nested objects), never through JSON; what
   * cannot be read is counted as incomplete coverage.
   */
  async scanStorage(target: Page | Frame, extraSecrets: string[] = []): Promise<{
    local: number; session: number; cookie: number; name: number; historyState: number; href: number;
    idb: number; idbEnumerated: boolean; idbDatabases: number; idbIncomplete: number; refused: number;
  }> {
    const expression = `(${STORAGE_SCAN})(${oracles.structuredTexts.toString()}, ${oracles.idbScanInPage.toString()})`;
    const dump = await this.guard('A05', 'STORAGE_SCAN', () => target.evaluate(expression) as Promise<StorageDump>);
    const count = (texts: string[]) => texts.reduce((sum, text) => sum + this.hits(text, extraSecrets), 0);
    return {
      local: count(dump.local),
      session: count(dump.session),
      cookie: this.hits(dump.cookie, extraSecrets),
      name: this.hits(dump.name, extraSecrets),
      historyState: count(dump.historyState),
      href: this.hits(dump.href, extraSecrets),
      idb: count(dump.idb.texts),
      idbEnumerated: dump.idb.enumerated,
      idbDatabases: dump.idb.databases,
      idbIncomplete: dump.idb.unsupported + dump.idb.unreadable + dump.historyUnsupported,
      refused: dump.refused,
    };
  }

  /**
   * A07 write-coverage barrier, before a storage verdict: waits (bounded) until every Blob
   * read the observer started in each live target document has settled in that document
   * AND its settle report reached this census. Whatever is still pending afterwards (a
   * hung read, a discarded document) stays counted by oracles.writeUnsupported.
   */
  async blobBarrier(census: Census, targets: (Page | Frame)[], ms = 5_000): Promise<{ settled: boolean; pending: number }> {
    const until = monotonic() + ms;
    let settled = true;
    for (const target of targets) {
      const ok = await poll(async () => {
        const state = await target.evaluate((name) => {
          const api = (window as unknown as Record<symbol, { blobReads?: () => { doc: string; started: number; settled: number } }>)[Symbol.for(name)];
          return api && typeof api.blobReads === 'function' ? api.blobReads() : null;
        }, OBSERVER_KEY).catch(() => null);
        return oracles.blobBarrierSettled(state, census.docs());
      }, Math.max(0, until - monotonic()), 50);
      settled &&= ok;
    }
    return { settled, pending: oracles.pendingBlobReads(census.docs()) };
  }

  // ------------------------------------------------------------ observer analyses (A03/A04)

  /** Writes or navigations a document made while a connect fragment was in its address bar. */
  static connectPhaseWrites(docs: Doc[]): number {
    return oracles.connectPhaseWrites(docs);
  }

  /** Token occurrences any observed write/history operation carried (structured, memory-only counts). */
  static writeHits(docs: Doc[]): number {
    return oracles.writeHits(docs);
  }

  /** IndexedDB write payloads the observer could not read (incomplete A07 coverage). */
  static writeUnsupported(docs: Doc[]): number {
    return oracles.writeUnsupported(docs);
  }

  /** A03 for the connect document (oracles.intakeVerdict). */
  static intakeVerdict(doc: Doc | undefined) {
    return oracles.intakeVerdict(doc);
  }

  // ------------------------------------------------------------ local fixtures (hostile origin, proxy)

  /** A loopback hostile/sibling page server on its own port (never an external page). */
  async hostileServer(): Promise<{ origin: string }> {
    const server = http.createServer((request, response) => {
      const url = new URL(request.url ?? '/', 'http://127.0.0.1');
      const page = HOSTILE_PAGES[url.pathname];
      if (page === undefined) {
        response.writeHead(url.pathname === '/telemetry-stub' ? 204 : 404, { 'cache-control': 'no-store' });
        response.end();
        return;
      }
      response.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' });
      response.end(page);
    });
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', () => resolve()));
    const port = (server.address() as net.AddressInfo).port;
    this.cleanups.push(() => new Promise<void>((resolve) => {
      server.closeAllConnections();
      server.close(() => resolve());
    }));
    return { origin: `http://127.0.0.1:${port}` };
  }

  /**
   * Adopted from OSS packages/studio-ui/e2e/harness.ts:147-179 (loopbackProxy):
   * a worker-owned loopback TCP proxy to omega dev that a test can cut. It
   * forwards bytes only; it never answers for Studio.
   */
  async loopbackProxy(upstreamPort: number): Promise<LoopbackProxy> {
    const pairs = new Set<readonly [net.Socket, net.Socket]>();
    let refusing = false;
    let accepted = 0;
    let refused = 0;
    const server = net.createServer((client) => {
      if (refusing) {
        refused += 1;
        client.resetAndDestroy();
        return;
      }
      accepted += 1;
      const upstream = net.connect(upstreamPort, '127.0.0.1');
      const pair = [client, upstream] as const;
      pairs.add(pair);
      client.pipe(upstream);
      upstream.pipe(client);
      const end = () => {
        pairs.delete(pair);
        client.destroy();
        upstream.destroy();
      };
      for (const socket of pair) {
        socket.on('error', end);
        socket.on('close', end);
      }
    });
    // Resets every live pair, both sides; returns how many it reset (0: a no-op cut).
    const cut = () => {
      let destroyed = 0;
      for (const [client, upstream] of [...pairs]) {
        if (!client.destroyed) destroyed += 1;
        client.resetAndDestroy();
        upstream.destroy();
      }
      pairs.clear();
      return destroyed;
    };
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', () => resolve()));
    const port = (server.address() as net.AddressInfo).port;
    const close = () => new Promise<void>((resolve) => {
      cut();
      server.close(() => resolve());
    });
    this.cleanups.push(close);
    return {
      port,
      origin: `http://127.0.0.1:${port}`,
      cut,
      refuse: (on: boolean) => { refusing = on; },
      accepted: () => accepted,
      refused: () => refused,
      close,
    };
  }

  // ------------------------------------------------------------ the shared connect sequence

  /**
   * Fresh context, actual UI login, organization/admission readback on a
   * token-free route, back to the token-free landing page, start omega dev, and
   * follow its printed link as a cold document load on the SAME page.
   * `transform` lets J07-06 rewrite only connect=<server> to the proxy origin
   * (recorded fault topology, never claimed to be CLI output).
   */
  async connected(role: RoleAlias, options: {
    colorScheme?: 'light' | 'dark';
    beforeLink?: (ctx: { context: BrowserContext; page: Page; census: Census; studio: StudioChild }) => Promise<void>;
    transform?: (studio: StudioChild) => Promise<{ link: string; origin: string }>;
    hostTheme?: 'light' | 'dark';
    waitReady?: boolean;
  } = {}): Promise<{
    context: BrowserContext; page: Page; census: Census; studio: StudioChild; link: string; frameOrigin: string;
    landing: string; mode: 'document' | 'fragment';
  }> {
    const user = this.userFor(role);
    const org = role.startsWith('orga') ? this.seed.A : this.seed.B;
    const { context, census } = await this.newContext(role, options.colorScheme ?? 'light');
    const page = await context.newPage();
    const { landing } = await this.login(page, user, role);
    await this.assertAdmission(page, org, true, role, 'A02');
    const back = landing.startsWith(`${this.fa}/dashboard/studio`) ? `${this.fa}/dashboard/home` : landing;
    await this.gotoTokenFree(page, back);
    // Let a post-login landing redirect settle so the link is followed from a stable token-free route.
    let last = '';
    await poll(async () => {
      const now = await page.evaluate(() => location.pathname + location.search);
      const stable = now === last;
      last = now;
      return stable;
    }, 10_000, 750);
    // The settled token-free route is the seeded "before" page for history traversal.
    const settled = await this.guard('A04', 'LANDING', () => page.evaluate(() => location.origin + location.pathname + location.search));
    this.check('A04', this.hits(settled) === 0 && !settled.includes('#'), 'LANDING_NOT_TOKEN_FREE', { phase: 'precondition' });
    if (options.hostTheme) await this.setHostTheme(page, options.hostTheme);
    const studio = await this.startStudio('direct');
    let link = studio.link;
    let frameOrigin = studio.origin;
    if (options.transform) ({ link, origin: frameOrigin } = await options.transform(studio));
    census.setStudioOrigins([studio.origin, frameOrigin]);
    await options.beforeLink?.({ context, page, census, studio });
    const mode = await this.followLink(page, link, 'auto');
    if (options.waitReady !== false) await this.waitStatus(page, 'connected', 60_000, 'A02', 'NOT_CONNECTED');
    return { context, page, census, studio, link, frameOrigin, landing: settled, mode };
  }

  userFor(role: RoleAlias): SeedUser {
    switch (role) {
      case 'orga-user1': return this.seed.A.users[0];
      case 'orga-user2': return this.seed.A.users[1];
      case 'orgb-user1': return this.seed.B.users[0];
      case 'orgb-user2': return this.seed.B.users[1];
      default: throw new J07Blocked('ROLE');
    }
  }

  // ------------------------------------------------------------ end of case

  /** Audits each stopped child's complete output once (finishCase, or teardown for a case that failed earlier). */
  private auditChildren(): void {
    for (const child of this.children) {
      if (this.outputAudited.has(child) || child.alive()) continue;
      this.outputAudited.add(child);
      const facts = this.auditChild(child);
      this.j07.aggregate.childAudits.push(facts);
      this.record({ assertion_id: 'A27', outcome: 'observed', phase: 'audit', count: facts.stdoutKeyHits + facts.stderrHits });
    }
  }

  /**
   * The end of a case's workload: stops every child (awaited; each sends its end marker
   * first), audits every child's complete output, value-free, into the worker aggregate,
   * and takes an early LIVE census snapshot that can fail the case sooner. Per-case values
   * are recorded as observations; J07-07 owns the A24-A27 verdict, over SEALED audits.
   */
  async finishCase(bearer: Map<string, string[]> = new Map()): Promise<Audit> {
    for (const child of this.children) await child.stop();
    this.auditChildren();
    for (const child of this.children) addBearer(bearer, child.origin, child.token);
    for (const [origin, tokens] of bearer) for (const token of tokens) addBearer(this.bearer, origin, token);
    // An early, LIVE snapshot (contexts still open): it can fail this case sooner, but the
    // case and walk verdicts that count are the SEALED audits close() takes after teardown.
    const total = emptyAudit();
    for (const census of this.censuses) addAudit(total, await census.audit(this.secrets(), this.bearer));
    this.j07.aggregate.finished.add(this.caseId);
    this.record({ assertion_id: 'A24', outcome: 'observed', phase: 'audit', count: total.urlHits + total.websocketHits });
    this.record({ assertion_id: 'A25', outcome: 'observed', phase: 'audit', count: total.refererHits });
    // A34 in EVERY case: a nonlocal attempt (even blocked/aborted) fails the case it happened in.
    this.zero('A34', total.nonlocal, 'NONLOCAL_ATTEMPT', { phase: 'audit' });
    this.zero('A34', total.serviceWorkers, 'SERVICE_WORKER_REGISTERED', { phase: 'audit' });
    return total;
  }

  bearerFor(...children: StudioChild[]): Map<string, string[]> {
    const map = new Map<string, string[]>();
    for (const child of children) addBearer(map, child.origin, child.token);
    return map;
  }

  /** A data-testid attribute, or null when the element is not there (never throws). */
  async attr(page: Page, testId: string, name: string): Promise<string | null> {
    return page.getByTestId(testId).first().getAttribute(name, { timeout: 500 }).catch(() => null);
  }

  /**
   * Teardown and SEAL, once (idempotent; J07-07 calls it in-body, the fixture again):
   * closes every context, stops every child (end markers first) and runs every cleanup,
   * THEN seals each census whose context is PROVEN closed and audits it. Nothing can be
   * observed after its context is closed, so this sealed audit is final: it is what the
   * whole-walk A24/A25/A34 verdict sums, and this case fails on any sealed nonlocal
   * attempt, service worker or event that arrived after the seal.
   *
   * Closure is proven, never inferred (oracles.closeVerdict): context.close() must
   * resolve within CONTEXT_CLOSE_MS, the context's own 'close' event must have been
   * witnessed and the browser must no longer list it. Playwright 1.62.1 marks a context
   * 'closing' (isClosed() true) before its pre-close hooks and the channel close run, so a
   * rejected close may leave it live and every later close() a no-op; it is never
   * retried as if it could succeed. A context that is not proven closed is never sealed
   * or accepted: the remaining teardown still runs, its census stays live and tracked in
   * the worker aggregate (closeFailures, unsealedCensuses), the case is NOT entered into
   * aggregate.sealed (so the walk's coverage is incomplete), and the case fails A34
   * CONTEXT_CLOSE_FAILED (close rejected or timed out) or CONTEXT_NOT_CLOSED.
   */
  close(): Promise<Audit> {
    this.closing ??= this.closeOnce();
    return this.closing;
  }

  /** How context.close() settled, bounded (a hang is a failure, never a pass). */
  private static async settleClose(context: BrowserContext): Promise<CloseOutcome> {
    const pending = Promise.resolve().then(() => context.close());
    pending.catch(() => undefined); // a rejection after the deadline must not surface unhandled
    try {
      const value = await oracles.withDeadline(pending, CONTEXT_CLOSE_MS);
      return value === oracles.TIMED_OUT ? 'timeout' : 'resolved';
    } catch {
      return 'rejected';
    }
  }

  /** The closure verdict for one context: its close outcome plus both independent witnesses. */
  private async proveClosed(context: BrowserContext, outcome: CloseOutcome): Promise<{ closed: boolean; code: string | null }> {
    // A close that resolved has normally emitted 'close' already; one that returned at
    // once because the context was already 'closing' may never: wait, bounded.
    if (outcome === 'resolved' && !this.closedContexts.has(context))
      await poll(() => this.closedContexts.has(context), CLOSE_EVENT_MS, 50);
    let listed = true;
    try {
      listed = this.browser.contexts().includes(context);
    } catch {
      listed = true;
    }
    return oracles.closeVerdict({ outcome, closeEvent: this.closedContexts.has(context), listed });
  }

  private async closeOnce(): Promise<Audit> {
    const verdicts = new Map<BrowserContext, { closed: boolean; code: string | null }>();
    for (const context of [...this.contexts]) verdicts.set(context, await this.proveClosed(context, await J07Case.settleClose(context)));
    for (const cleanup of this.cleanups.splice(0).reverse()) await cleanup().catch(() => undefined);
    await this.pool?.end().catch(() => undefined);
    this.pool = null;
    this.auditChildren();
    for (const child of this.children) addBearer(this.bearer, child.origin, child.token);
    const failed = [...verdicts.values()].filter((verdict) => !verdict.closed);
    const accepted: Census[] = [];
    const unsealed: Census[] = [];
    for (const census of this.censuses) {
      const context = this.censusContext.get(census);
      if (context !== undefined && verdicts.get(context)?.closed === true) accepted.push(census);
      else unsealed.push(census);
    }
    for (const census of accepted) census.seal();
    this.j07.aggregate.sealedCensuses.push(...accepted);
    if (failed.length > 0 || unsealed.length > 0) {
      const aggregate = this.j07.aggregate;
      aggregate.closeFailures += Math.max(failed.length, unsealed.length);
      aggregate.unsealedCensuses.push(...unsealed);
      this.record({ assertion_id: 'A34', outcome: 'fail', phase: 'audit', count: Math.max(failed.length, unsealed.length),
        comparison_boolean: false });
      throw new J07Failure('A34', failed.some((verdict) => verdict.code === 'CONTEXT_CLOSE_FAILED') ? 'CONTEXT_CLOSE_FAILED' : 'CONTEXT_NOT_CLOSED');
    }
    const total = emptyAudit();
    for (const census of accepted) addAudit(total, await census.audit(this.secrets(), this.bearer));
    this.j07.aggregate.sealed.set(this.caseId, total);
    this.zero('A34', total.nonlocal, 'NONLOCAL_ATTEMPT_SEALED', { phase: 'audit' });
    this.zero('A34', total.serviceWorkers, 'SERVICE_WORKER_SEALED', { phase: 'audit' });
    this.zero('A34', total.lateEvents, 'OBSERVED_AFTER_SEAL', { phase: 'audit' });
    return total;
  }
}

export interface LoopbackProxy {
  port: number; origin: string; cut(): number; refuse(on: boolean): void; accepted(): number; refused(): number; close(): Promise<void>;
}

export function addBearer(map: Map<string, string[]>, origin: string, token: string): Map<string, string[]> {
  const list = map.get(origin) ?? [];
  if (!list.includes(token)) list.push(token);
  map.set(origin, list);
  return map;
}

export function emptyAudit(): Audit {
  return {
    requests: 0, urlHits: 0, fragmentNavigations: 0, refererPresent: 0, refererAbsent: 0, refererUnavailable: 0,
    headersIncomplete: 0, refererHits: 0, bearerOnStudioCalls: 0, bearerMisplaced: 0, studioCallsWithoutBearer: 0, otherHeaderHits: 0,
    bodyHits: 0, mainToStudio: 0, nonlocal: 0, websockets: 0, websocketHits: 0, workers: 0, serviceWorkers: 0,
    pages: 0, popups: 0, droppedEvents: 0, lateEvents: 0,
  };
}

export function addAudit(into: Audit, from: Audit): Audit {
  for (const key of Object.keys(into) as (keyof Audit)[]) into[key] += from[key] ?? 0;
  return into;
}

/** Sends one non-secret marker request; true when it was answered (any status). */
export const sendMarker = (url: string): Promise<boolean> =>
  fetch(url).then(async (response) => { await response.arrayBuffer(); return true; }, () => false);

// ---------------------------------------------------------------- in-page storage scan

const OBSERVER_KEY = 'j07.observer';

interface StorageDump {
  href: string; historyState: string[]; historyUnsupported: number; local: string[]; session: string[];
  cookie: string; name: string; idb: IdbScan; refused: number;
}

// Evaluated as an expression with oracles.structuredTexts and oracles.idbScanInPage
// passed by source text (no eval inside the page, so a strict page CSP is irrelevant).
const STORAGE_SCAN = `async function (walk, idbScan) {
  var entries = function (store) {
    var out = [];
    for (var i = 0; i < store.length; i += 1) {
      var key = store.key(i);
      if (key !== null) out.push(key, store.getItem(key) || '');
    }
    return out;
  };
  var refused = 0;
  var local = [];
  var session = [];
  try { local = entries(window.localStorage); } catch (e) { refused += 1; }
  try { session = entries(window.sessionStorage); } catch (e) { refused += 1; }
  var state = walk(history.state);
  var idb = await idbScan(walk);
  return { href: location.href, historyState: state.texts, historyUnsupported: state.unsupported + state.blobs.length,
    local: local, session: session, cookie: document.cookie, name: window.name, idb: idb, refused: refused };
}`;

// ---------------------------------------------------------------- local pages

const HOSTILE_PAGES: Record<string, string> = {
  // A17: a document on another local origin inside the SAME frame window says hello.
  '/hostile.html': `<!doctype html><meta charset="utf-8"><title>hostile</title><script>
    window.__j07Hostile = { received: 0, keys: 0 };
    addEventListener('message', function (e) {
      var d = e.data;
      if (d && typeof d === 'object' && typeof d.type === 'string' && d.type.indexOf('embed.') === 0) {
        window.__j07Hostile.received += 1;
        if (d.type === 'embed.key') window.__j07Hostile.keys += 1;
      }
    });
    var n = 0;
    var hello = function () { try { parent.postMessage({ type: 'embed.hello', protocol: 'omega-embed/1' }, '*'); } catch (e) {} };
    hello();
    var t = setInterval(function () { if (++n > 12) { clearInterval(t); return; } hello(); }, 250);
  </script>`,
  // A19 sender frame: scripts are run in it by the spec (frame.evaluate).
  '/blank.html': '<!doctype html><meta charset="utf-8"><title>blank</title>',
  // A20: an unlisted local host frames real Studio; frame-ancestors must deny it.
  '/frame-studio.html': `<!doctype html><meta charset="utf-8"><title>frame studio</title><body><script>
    window.__j07Hostile = { received: 0, keys: 0 };
    addEventListener('message', function (e) {
      var d = e.data;
      if (d && typeof d === 'object' && typeof d.type === 'string' && d.type.indexOf('embed.') === 0) window.__j07Hostile.received += 1;
    });
    var f = document.createElement('iframe');
    f.src = new URLSearchParams(location.search).get('src') || 'about:blank';
    document.body.appendChild(f);
  </script>`,
  // C02-auth-order child-frame witness: a subframe whose own URL carries a synthetic connect fragment.
  '/frame-child.html': `<!doctype html><meta charset="utf-8"><title>frame child</title><body><script>
    var f = document.createElement('iframe');
    f.src = '/blank.html#connect=http%3A%2F%2F127.0.0.1%3A9&t=' + encodeURIComponent(new URLSearchParams(location.search).get('marker') || 'x');
    document.body.appendChild(f);
  </script>`,
  // A19 wrong-origin PARENT arm: this unlisted local page IS the Studio child's parent window.
  // The spec calls send() to post key, theme and route to its own child.
  '/parent.html': `<!doctype html><meta charset="utf-8"><title>parent</title><body><script>
    var f = document.createElement('iframe');
    f.name = 'j07-wrong-origin-parent-child';
    f.src = new URLSearchParams(location.search).get('src') || 'about:blank';
    document.body.appendChild(f);
    window.__j07Parent = {
      send: function (key, mode, path, target) {
        var w = f.contentWindow;
        w.postMessage({ type: 'embed.key', token: key }, target);
        w.postMessage({ type: 'embed.theme', mode: mode }, target);
        w.postMessage({ type: 'embed.navigate', path: path }, target);
        return true;
      }
    };
  </script>`,
  // A34: a dedicated worker the observer must see; C08: a same-origin request carrying this page's Referer.
  '/marker.html': `<!doctype html><meta charset="utf-8"><title>marker</title><script>
    try { new Worker(URL.createObjectURL(new Blob(['postMessage(1)'], { type: 'text/javascript' }))); } catch (e) {}
    fetch('/telemetry-stub?j07=stub').catch(function () {});
  </script>`,
};

// ---------------------------------------------------------------- fixtures

type WorkerFixtures = { j07: J07Worker };
// `_combinedContextOptions` is Playwright 1.62.1's own test-scoped fixture (lib/index.js):
// every option fixture merged over contextOptions, the object _setupArtifacts copies into
// EVERY browser.newContext of the test. Declared here only so `walk` can read it.
type TestFixtures = { walk: J07Case; _combinedContextOptions: BrowserContextOptions };

export const test = base.extend<TestFixtures, J07Options & WorkerFixtures>({
  j07Walk: [false, { option: true, scope: 'worker' }],

  // The accepted walk browser policy (lib/walk/browsers, H11) per engine; the J07
  // config defines no projects because Stage A selects the engine with --browser.
  launchOptions: [async ({ browserName }, use) => {
    const options: LaunchOptions = {};
    if (strictBrowser()) {
      if (browserName === 'chromium') options.args = chromiumStrictArgs();
      if (browserName === 'firefox') options.firefoxUserPrefs = { ...FIREFOX_STRICT_PREFS };
    }
    await use(options);
  }, { scope: 'worker' }],

  // Refusal before any login, child start or connect token (PRE-TOKEN admission):
  // binding and OSS c103d224f0 + 79164e25f3 ancestry evidence, the E4 observer path, the
  // app-side descriptor of THIS FA build (sourceRef === faRef, inert, local), the complete
  // declared log set, G5a qualification of the installed failure-artifact producer,
  // WebKit qualification, the reporter ALLOWLIST, debug logging read with the loggers'
  // own parsers, the configured capture policy, and the EFFECTIVE worker-scoped values:
  // trace, screenshot and video exactly as the fixture pool resolved them for this
  // worker (a test.use or CLI override included; project.use alone does not show those),
  // the launch options and any remote connection.
  j07: [async ({ j07Walk, browserName, trace, screenshot, video, launchOptions, connectOptions }, use, workerInfo) => {
    if (!j07Walk) throw new J07Blocked('NOT_J07_CONFIG');
    let loaded: { binding: J07Binding; resolved: Resolved };
    try {
      loaded = bindingModule.loadBinding(process.env.J07_BINDING);
    } catch (error) {
      throw blockedFrom(error);
    }
    const reporters = workerInfo.config.reporter.map(([name]) => name);
    const effective: EffectiveWorker = { trace, screenshot, video, launchOptions, connectOptions };
    const runtime: RuntimeInput = {
      browserName,
      use: workerInfo.project.use,
      workers: workerInfo.config.workers,
      retries: workerInfo.project.retries,
      reporters,
      env: process.env,
      effective,
    };
    const refusal = bindingModule.admitRuntime(loaded.resolved, runtime);
    if (refusal !== null) throw new J07Blocked(refusal);
    const policyViolations = bindingModule.capturePolicyViolations({ ...runtime, accepted: loaded.resolved.accepted });
    if (policyViolations.length > 0) throw new J07Blocked('CAPTURE_POLICY');
    const effectiveViolations = bindingModule.effectiveWorkerViolations(effective);
    if (effectiveViolations.length > 0) throw new J07Blocked('CAPTURE_EFFECTIVE');
    const policySelfTest = bindingModule.capturePolicySelfTest();
    if (!policySelfTest.pass) throw new J07Blocked('CAPTURE_POLICY_VALIDATOR');
    let seed: Seed;
    try {
      seed = bindingModule.loadSeed(loaded.binding, browserName);
    } catch (error) {
      throw blockedFrom(error);
    }
    let parity: Parity | null = null;
    let parityCode: string | null = null;
    try {
      parity = bindingModule.protocolParity(loaded.binding, FA_ROOT);
    } catch (error) {
      parityCode = blockedFrom(error).code;
    }
    const invocation = /^[a-z0-9][a-z0-9.-]{0,47}$/.test(process.env.J07_INVOCATION_ID ?? '')
      ? (process.env.J07_INVOCATION_ID as string) : 'unbound';
    // The walk window opens here, before any case workload: a non-secret marker in
    // the FA frontend/access and API logs (Studio children get it when they start).
    const walkStart = marker();
    const walkStartSent = {
      faApp: await sendMarker(`${loaded.binding.origins.faApp}/auth/jwt/login?j07walk=${walkStart}`),
      faApi: await sendMarker(`${loaded.binding.origins.faApi}/accounts/user-info/?j07walk=${walkStart}`),
    };
    await use({
      binding: loaded.binding,
      resolved: loaded.resolved,
      seed,
      parity,
      parityCode,
      policyViolations,
      effective: { values: effective, violations: effectiveViolations },
      policySelfTest,
      reporters,
      browserName: browserName as J07Worker['browserName'],
      invocation,
      aggregate: {
        finished: new Set(), sealed: new Map(), sealedCensuses: [], closeFailures: 0, unsealedCensuses: [], secrets: new Set(),
        childAudits: [], children: 0, childLogs: [],
        walkStart, walkStartSent,
      },
    });
  }, { scope: 'worker' }],

  // Test admission before the case object exists (so before any login, child start or
  // token): the EFFECTIVE context options of this test, i.e. Playwright's merged
  // _combinedContextOptions (copied into every manual browser.newContext too), the public
  // contextOptions and storageState. Any HAR, video, storage state or proxy refuses.
  walk: async ({ j07, browser, _combinedContextOptions, contextOptions, storageState }, use, testInfo) => {
    const values: EffectiveContext = { combinedContextOptions: _combinedContextOptions, contextOptions, storageState };
    const refusal = bindingModule.admitContext(values);
    if (refusal !== null) throw new J07Blocked(refusal);
    const walk = new J07Case(j07, browser, testInfo, { values, violations: bindingModule.effectiveContextViolations(values) });
    try {
      await use(walk);
    } finally {
      await walk.close();
    }
  },
});

/** Value-free synthetic, token-shaped bytes that were never issued (control senders only). */
export const syntheticKey = () => crypto.randomBytes(32).toString('base64url');
/** A lowercase, non-secret marker for scanner and log-coverage controls. */
export const marker = () => `j07m${crypto.randomBytes(8).toString('hex')}`;
