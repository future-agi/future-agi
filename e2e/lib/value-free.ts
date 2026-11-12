// HR-JRN-01: value-free publication mode for ScopeActors evidence, walk receipts,
// attachments, log lines and the Playwright report.
//
// ONE switch: E2E_VALUE_FREE=1 for the run. test.use({ scopeValueFree: true }) and
// new ScopeActors(prefix, roster, { valueFree: true }) only confirm it; either one
// without the switch is refused, and so is turning it off under the switch. Every
// email, UUID, org key (uuid4().hex), JWT and ScopeActors prefix, and every value
// a worker registers (workspace and org names, displayed key prefixes), is then
// published as `vf-<run>-<kind>-<16 hex>`: HMAC-SHA256 under the run salt, after a
// five-letter segment the salt also picks, so no fixed text precedes the hash. The
// runner creates that salt (or takes E2E_VALUE_FREE_SALT) when it loads this module,
// before it forks a worker, so one value gets one token in every worker, and a
// different one (and a different segment) in the next run. Nothing writes the salt
// anywhere.
//
// Registrations are shared through a 0700 directory the runner creates under the
// system temp dir (E2E_VALUE_FREE_REGISTRY): each process appends what it registers
// to its own 0600 file BEFORE it sends anything that could quote the value, and
// every process reads the others' files before it scrubs. So the runner's report
// sanitizer knows a worker's org name by the time that worker's attachment, log
// line or failure arrives. The directory never lands in an output. Whoever
// creates it removes it: the runner, when the run ends; or a consumer that gives the run
// its own (E2E_VALUE_FREE_REGISTRY, the walk job), which reads it at the publication
// boundary's final check (lib/value-free-boundary.mjs) and then removes it. Off (the
// default), nothing here runs and every output is byte-for-byte what it was before.
//
// A value shorter than VALUE_FREE_MIN_LENGTH cannot be substituted safely (it would
// match inside other words, and inside tokens), and neither can one that overlaps the
// token syntax (it contains this run's `vf-<run>` or a token's shape, or it is part of
// a token's fixed text, alone or followed by hex): substituting it would write it back.
// Registering either REFUSES the run: register() records the refusal (its length and
// kind, never the value) in the registry and throws, and lib/value-free-reporter.ts
// then fails the run and publishes only a refusal record.
//
// The runner HOLDS everything it prints (its stdout and stderr) and prints it once,
// when the run ends, as ONE stream on stdout: both streams in the order they were
// written, sanitized as one text under every registration of the run (a value split
// across writes, or across the two streams, is whole again before it is matched).
// The line reporter prints a test's title before that test's worker can register a
// value in it. lib/value-free-reporter.ts decides at that point what is printed: the
// merged text, or the refusal line.
//
// A failing expect sends its message to the runner before any fixture teardown,
// so worker code cannot clean it: a value-free run lists lib/value-free-reporter.ts
// FIRST. playwright.config.ts does that when E2E_VALUE_FREE=1; a CLI --reporter=
// replaces the config's list, so name it first there too. That reporter also
// scrubs every output directory when the run ends (lib/value-free-scrub.ts). The
// ScopeActors fixture refuses a value-free test without it, or with trace,
// screenshot or video on: pixels and trace network bodies cannot be hashed.
// What leaves the machine is only the publish directory that reporter assembles
// (lib/value-free-publish.ts); every other file the run writes stays where it is.
import type { ChildProcess } from 'node:child_process';
import { createHmac, randomBytes } from 'node:crypto';
import diagnosticsChannel from 'node:diagnostics_channel';
import { appendFileSync, chmodSync, lstatSync, mkdtempSync, readdirSync, readFileSync, rmSync, unlinkSync, writeFileSync,
  writeSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import type { PlaywrightTestConfig, ReporterDescription, TestInfo } from '@playwright/test';
import { kindOf, RUN, SHAPES, stripAnsi, VALUE_FREE_MIN_LENGTH } from './value-free-reading.mjs';
import { REGISTRATION_KINDS } from './value-free-vocabulary.mjs';

// The shortest registrable value, the value shapes, kindOf and colour stripping live in
// lib/value-free-reading.mjs (plain Node), which the publication boundary's final check reads too.
export { kindOf, stripAnsi, VALUE_FREE_MIN_LENGTH };

export const VALUE_FREE_ENV = 'E2E_VALUE_FREE';
export const VALUE_FREE_SALT_ENV = 'E2E_VALUE_FREE_SALT';
export const VALUE_FREE_REGISTRY_ENV = 'E2E_VALUE_FREE_REGISTRY';
/** Set in the runner by lib/value-free-reporter.ts, only when it is the first reporter. A hint, never an admission
 * (structural S3): set from outside, it proves nothing. */
export const VALUE_FREE_REPORTER_ENV = 'E2E_VALUE_FREE_REPORTER';
/** The environment name of the admission proof workers inherited earlier (structural S3). Now nothing sets it any more and
 * nothing reads it as an admission (a worker asks its own runner, below); withdrawAdmission() drops one set from outside. */
export const VALUE_FREE_ADMISSION_ENV = 'E2E_VALUE_FREE_ADMISSION';
export const VALUE_FREE_REPORTER = fileURLToPath(new URL('./value-free-reporter.ts', import.meta.url));

export const valueFreeFromEnv = (): boolean => process.env[VALUE_FREE_ENV] === '1';
const inWorker = (): boolean => process.env.TEST_WORKER_INDEX !== undefined;

/** The one switch: an explicit option may confirm E2E_VALUE_FREE, never stand in for it or overrule it. */
export function valueFreeRequested(option?: boolean): boolean {
  const on = valueFreeFromEnv();
  if (option === true && !on) {
    throw new Error('value-free mode is one switch: set E2E_VALUE_FREE=1 for the run. The explicit option alone ' +
      'is refused, because only the runner can give every worker and the report one salt');
  }
  if (option === false && on) throw new Error('E2E_VALUE_FREE=1 is set for this run: a test cannot opt out of value-free mode');
  return on;
}

// Whole shapes first, registered values next, longest first, the fixture prefix last: SHAPES and RUN
// (lib/value-free-reading.mjs). A token matches none of them, so scrubbing is idempotent.
const KINDS = ['email', 'id', 'key', 'jwt', 'run', 'value'] as const;
// The run segment's letters: none is a hex digit, and none can start a key shape the walk receipt guard refuses
// (sk-, pk-, rk-, gsk-, ghp-, gho-, xoxb-, fagi-, fi_), so a token is never mistaken for one.
const SEGMENT_LETTERS = 'ghjkmnqtvwyz';
/** Any token of any run: `vf-<5 letters>-<kind>-<16 hex>` (at most 31 characters, lower case only). */
export const TOKEN_SHAPE = /\bvf-[a-z]{5}-(?:email|id|key|jwt|run|value)-[0-9a-f]{16}\b/;
const TOKEN_TEXT = /vf-[a-z]{5}-(?:email|id|key|jwt|run|value)-/;
const TEXT_CONTENT = /^(text\/|application\/(json|xml|x-ndjson|[\w.+-]*\+json)\b)/;
export const isTextContent = (contentType: string): boolean => TEXT_CONTENT.test(contentType);

/** What the product shows of an org key (model_hub/models/api_key.py mask_key), and its 8-character prefix. */
export function displayedKeys(...keys: (string | null | undefined)[]): string[] {
  return keys.filter((k): k is string => typeof k === 'string' && k.length > 8)
    .flatMap((k) => [k.slice(0, 8), `${k.slice(0, 4)}${'*'.repeat(Math.min(k.length - 8, 10))}${k.slice(-4)}`]);
}

/** A value that cannot be hidden: its length, its kind and where it was registered, never the value. `reason` is
 * set when it overlaps the token syntax; unset, it was too short. `what` is one of the fixed registration kinds
 * (lib/value-free-vocabulary.mjs), never a label the caller chose: any other label is `value`. */
export interface Refusal { refused: number; what: string; at: string; reason?: 'token-syntax' }
const describeRefusal = (r: Refusal): string => `a ${r.what} of ${r.refused} character${r.refused === 1 ? '' : 's'}` +
  (r.reason === 'token-syntax' ? ' that overlaps the token syntax' : '');
const CANNOT = `no value shorter than ${VALUE_FREE_MIN_LENGTH} characters can be substituted safely, nor one that overlaps ` +
  'the token syntax';
/** Thrown by register(): the run is refused (lib/value-free-reporter.ts publishes only a refusal record). */
export class ValueFreeRefusal extends Error {
  constructor(readonly refusals: Refusal[]) {
    super(`value-free refuses this run: ${refusals.map(describeRefusal).join(', ')} had to be hidden, and ${CANNOT}. ` +
      'The run publishes only its refusal record; give it a longer, random name (ScopeActors names its own that way)');
  }
}
/** The first caller outside this file, as file:line: where a refused value came from, with no path. */
function callSite(): string {
  const here = path.basename(fileURLToPath(import.meta.url));
  for (const line of (new Error().stack ?? '').split('\n').slice(1)) {
    const m = /([^/\\()\s]+\.[cm]?[jt]sx?):(\d+):\d+\)?\s*$/.exec(line);
    if (m && m[1] !== here) return `${m[1]}:${m[2]}`;
  }
  return 'unknown';
}

export class ValueFree {
  private readonly raw = new Set<string>();
  private readonly refused: Refusal[] = [];
  private longestFirst: string[] | null = null;
  private readonly offsets = new Map<string, number>();
  /** This run's token segment: `vf-<segment>-` starts every token it writes. */
  readonly segment: string;

  /** `registryDir`: the run's shared registry (E2E_VALUE_FREE_REGISTRY); without it, registrations stay in this object. */
  constructor(private readonly salt: string, readonly registryDir?: string) {
    if (!salt) throw new Error('value-free mode needs the run salt, which the runner creates when E2E_VALUE_FREE=1');
    this.segment = [...createHmac('sha256', salt).update('value-free token segment').digest().subarray(0, 5)]
      .map((b) => SEGMENT_LETTERS[b % SEGMENT_LETTERS.length]).join('');
  }

  sameRun(salt: string | undefined, registryDir: string | undefined): boolean {
    return salt === this.salt && registryDir === this.registryDir;
  }

  token(raw: string): string {
    return `vf-${this.segment}-${kindOf(raw)}-${createHmac('sha256', this.salt).update(raw).digest('hex').slice(0, 16)}`;
  }

  /** Whether substituting `raw` could write it back: it contains a token's syntax (this run's `vf-<segment>`, or any
   * token's shape), or it is part of a token's fixed text `vf-<segment>-<kind>-`, alone or followed by hex. A value of
   * hex digits only is not refused: the hash part is random per value, and the publication scan is the oracle there. */
  overlapsTokens(raw: string): boolean {
    const head = `vf-${this.segment}`;
    if (raw.includes(head) || TOKEN_TEXT.test(raw)) return true;
    for (const kind of KINDS) {
      const fixed = `${head}-${kind}-`;
      if (fixed.includes(raw)) return true;
      for (let i = 1; i < fixed.length; i++) {
        const tail = fixed.slice(i);
        if (raw.length > tail.length && raw.startsWith(tail) && /^[0-9a-f]{1,16}$/.test(raw.slice(tail.length))) return true;
      }
    }
    return false;
  }

  /** Every value registered in the run so far, by any process (the publication scan's oracle). */
  values(): string[] {
    this.sync();
    return [...this.raw];
  }

  /** Values no pattern recognises (a custom prefix, a name, a displayed key prefix), shared with the run. */
  register(...values: (string | null | undefined)[]): this { return this.registerAs('value', ...values); }

  /** register(), naming what the values are for a refusal. One shorter than VALUE_FREE_MIN_LENGTH, or one that overlaps
   * the token syntax, refuses the run: the others are registered first, then the refusal (length and kind only), then
   * it throws ValueFreeRefusal. */
  registerAs(label: string, ...values: (string | null | undefined)[]): this {
    // A label is diagnostic text that reaches the refusal record and the refusal line: only a fixed kind does.
    const what = REGISTRATION_KINDS.includes(label) ? label : 'value';
    const given = [...new Set(values.filter((v): v is string => typeof v === 'string' && v.length > 0 && !this.raw.has(v)))];
    const unsafe = (v: string) => v.length < VALUE_FREE_MIN_LENGTH || this.overlapsTokens(v);
    const added = given.filter((v) => !unsafe(v));
    const refused = given.filter(unsafe).map((v): Refusal => (v.length < VALUE_FREE_MIN_LENGTH
      ? { refused: v.length, what, at: callSite() } : { refused: v.length, what, at: callSite(), reason: 'token-syntax' }));
    const lines = [...added, ...refused].map((v) => `${JSON.stringify(v)}\n`).join('');
    // Synchronously, before this process sends anything that could quote them. A refusal comes back through sync().
    if (!this.registryDir) this.refused.push(...refused);
    else if (lines) appendFileSync(path.join(this.registryDir, `${process.pid}.jsonl`), lines, { mode: 0o600 });
    this.add(added);
    if (refused.length) throw new ValueFreeRefusal(refused);
    return this;
  }

  /** Every refusal of the run so far, by any process. */
  refusals(): Refusal[] {
    this.sync();
    return [...this.refused];
  }

  text(input: string): string {
    this.sync();
    return this.substitute(input, this.longestFirst ??= [...this.raw].sort((a, b) => b.length - a.length));
  }

  /** text() under everything registered so far, without re-reading the registry on every call: one pass over a whole
   * report (lib/value-free-codec.ts) calls it once per string. */
  frozen(): (input: string) => string {
    this.sync();
    const values = this.longestFirst ??= [...this.raw].sort((a, b) => b.length - a.length);
    return (input) => this.substitute(input, values);
  }

  private substitute(input: string, longestFirst: string[]): string {
    let out = stripAnsi(input);
    for (const [, re] of SHAPES) out = out.replace(re, (match) => this.token(match));
    for (const raw of longestFirst) {
      // Also its JSON-escaped form, for json() and report files.
      for (const form of new Set([raw, JSON.stringify(raw).slice(1, -1)])) {
        if (out.includes(form)) out = out.split(form).join(this.token(raw));
      }
    }
    return out.replace(RUN[1], (match) => this.token(match));
  }

  /** A JSON-able value with every string and object key scrubbed. */
  json<T>(value: T): T {
    return value === undefined ? value : JSON.parse(this.text(JSON.stringify(value)));
  }

  private add(values: string[]): void {
    for (const value of values) this.raw.add(value);
    if (values.length) this.longestFirst = null;
  }

  /** What every other process of the run registered since the last call (complete lines only). */
  private sync(): void {
    if (!this.registryDir) return;
    let files: string[];
    try { files = readdirSync(this.registryDir).filter((f) => f.endsWith('.jsonl')); } catch { return; } // removed at run end
    for (const file of files) {
      let bytes: Buffer;
      try { bytes = readFileSync(path.join(this.registryDir, file)); } catch { continue; }
      const from = this.offsets.get(file) ?? 0;
      const end = bytes.lastIndexOf(0x0a) + 1;
      if (end <= from) continue;
      this.offsets.set(file, end);
      const entries = bytes.subarray(from, end).toString('utf8').split('\n').filter(Boolean)
        .map((line) => JSON.parse(line) as string | Refusal);
      this.add(entries.filter((e): e is string => typeof e === 'string'));
      this.refused.push(...entries.filter((e): e is Refusal => typeof e === 'object'));
    }
  }
}

// On globalThis: one instance per process even if two module instances load this file.
const SHARED = '__futureagiE2eValueFree';
/** This run's instance in this process (every ScopeActors of a worker, and the reporter), under the run salt. */
export function sharedValueFree(): ValueFree {
  const salt = process.env[VALUE_FREE_SALT_ENV];
  const registryDir = process.env[VALUE_FREE_REGISTRY_ENV];
  if (!valueFreeFromEnv() || !salt || !registryDir) {
    throw new Error('value-free mode needs E2E_VALUE_FREE=1 in the runner, which then creates the run salt and ' +
      'registry before it starts a worker (playwright.config.ts or lib/value-free-reporter.ts loads lib/value-free.ts there)');
  }
  const holder = globalThis as unknown as Record<typeof SHARED, ValueFree | undefined>;
  const current = holder[SHARED];
  if (current?.sameRun(salt, registryDir)) return current;
  return (holder[SHARED] = new ValueFree(salt, registryDir));
}

/** The refused run's one line of output: lengths, kinds and file:line, never a value or a path. */
export function refusalMessage(refusals: Refusal[]): string {
  const seen = [...new Set(refusals.map((r) => `${describeRefusal(r)} (${r.at})`))];
  return `value-free: REFUSED. ${seen.join('; ')} had to be hidden, and ${CANNOT}. This run publishes only its ` +
    'refusal record (REFUSED.json in its publish directory) and its output is withheld. Give the value a longer, random name.\n';
}

const bound = new WeakMap<TestInfo, ValueFree>();
/** Marks a test value-free (the ScopeActors fixture does this) for the receipt and attachEvidence. */
export function bindValueFree(testInfo: TestInfo, valueFree: ValueFree): void { bound.set(testInfo, valueFree); }
export const valueFreeFor = (testInfo: TestInfo): ValueFree | undefined => bound.get(testInfo);

const TEXT_EXTENSIONS: Record<string, string> = { '.json': 'application/json', '.jsonl': 'application/x-ndjson',
  '.txt': 'text/plain', '.log': 'text/plain', '.md': 'text/markdown', '.csv': 'text/csv', '.html': 'text/html',
  '.xml': 'application/xml', '.yaml': 'text/yaml', '.yml': 'text/yaml' };
export const isInside = (file: string, dir: string): boolean => {
  const rel = path.relative(dir, file);
  return rel !== '' && !rel.startsWith('..') && !path.isAbsolute(rel);
};

/** testInfo.attach, value-free when the test is: text scrubbed (a source file too, in place), anything else withheld. */
export async function attachEvidence(testInfo: TestInfo, name: string,
  options: { body?: string | Buffer; contentType?: string; path?: string }): Promise<void> {
  const valueFree = valueFreeFor(testInfo);
  if (!valueFree) return testInfo.attach(name, options);
  const source = options.path === undefined ? undefined : path.resolve(options.path);
  const contentType = options.contentType ?? (source !== undefined ? TEXT_EXTENSIONS[path.extname(source).toLowerCase()] : undefined)
    ?? (typeof options.body === 'string' ? 'text/plain' : 'application/octet-stream');
  if (source !== undefined && !isInside(source, testInfo.project.outputDir)) {
    // A file elsewhere would stay on disk with its values; under the output directory the run scrubs it.
    throw new Error(valueFree.text(`value-free attachEvidence takes a file under the test output directory, not ${source}`));
  }
  if (source !== undefined) {
    // Never through a link (rewriting it would rewrite its target), nor a hard link or a special file.
    const st = lstatSync(source);
    if (st.isSymbolicLink() || !st.isFile() || st.nlink > 1) {
      throw new Error('value-free attachEvidence takes a regular file, not a link, a hard link or a special file');
    }
  }
  if (!isTextContent(contentType)) {
    if (source !== undefined) unlinkSync(source);
    return testInfo.attach(valueFree.text(name), { contentType: 'text/plain', body: `withheld in value-free mode: ${contentType}` });
  }
  const body = valueFree.text(source !== undefined ? readFileSync(source, 'utf8') : (options.body ?? '').toString());
  if (source !== undefined) writeFileSync(source, body);
  return testInfo.attach(valueFree.text(name), { contentType, body });
}

type Write = (chunk: unknown, ...rest: unknown[]) => boolean;
type Stream = NodeJS.WriteStream;
/** The runner's held output, both streams in the order written; null in a worker, before scrubLogs and after the release. */
let held: Buffer[] | null = null;
const direct = new Map<Stream, Write>();
let logsScrubbed = false;
/** console.* and every other stdout/stderr write of this process go out scrubbed, colours removed first. A worker
 * writes at once (to the runner); the runner holds every write until its exit. */
export function scrubLogs(): void {
  if (logsScrubbed) return;
  const valueFree = sharedValueFree();
  logsScrubbed = true;
  if (!inWorker()) held = [];
  for (const stream of [process.stdout, process.stderr]) {
    const write = stream.write.bind(stream) as Write;
    direct.set(stream, write);
    stream.write = ((chunk: unknown, ...rest: unknown[]) => {
      if (held && (typeof chunk === 'string' || chunk instanceof Uint8Array)) {
        const first = rest[0];
        const encoding: BufferEncoding = typeof first === 'string' && Buffer.isEncoding(first) ? first : 'utf8';
        held.push(typeof chunk === 'string' ? Buffer.from(chunk, encoding) : Buffer.from(chunk));
        // Playwright awaits some write callbacks (its exit flush): a held write still completes.
        const done = rest.find((r): r is () => void => typeof r === 'function');
        if (done) process.nextTick(done);
        return true;
      }
      return write(typeof chunk === 'string' ? valueFree.text(chunk)
        : chunk instanceof Uint8Array ? Buffer.from(valueFree.text(Buffer.from(chunk).toString('utf8'))) : chunk, ...rest);
    }) as typeof stream.write;
  }
}

/** Everything the runner has held so far, stdout and stderr in the order written, sanitized as ONE text: a value split
 * across writes, or across the two streams, is whole again before anything is matched. The publish directory's
 * runner.log, and (at exit) the runner's only output. */
export function heldOutput(): string {
  return sharedValueFree().text(Buffer.concat(held ?? []).toString('utf8'));
}

type Release = (merged: string) => string;
let release: Release | null = null;
/** lib/value-free-reporter.ts decides, at the runner's exit, what the held output becomes: the merged text or the
 * refusal line. Without it, a run with a refusal prints the refusal line and any other run the merged text. */
export function onRunnerRelease(decide: Release): void { release = decide; }

/** A line printed at once, while the runner holds its output: only counts and fixed words, never a value or a path. */
export function printNow(line: string): void {
  const text = sharedValueFree().text(line);
  const write = direct.get(process.stdout);
  if (write) write(text); else process.stdout.write(text);
}

/** The runner's exit hook: the held output, decided by the reporter, written synchronously to stdout only (so stdout,
 * stderr and the two together all read as the one merged stream). */
function releaseAtExit(): void {
  if (!held) return;
  let text: string;
  try {
    const merged = heldOutput();
    const refusals = sharedValueFree().refusals();
    text = release ? release(merged) : refusals.length ? refusalMessage(refusals) : merged;
  } catch {
    text = 'value-free: REFUSED. The held output could not be released safely and is withheld.\n';
    process.exitCode = 1;
  }
  held = null;
  const bytes = Buffer.from(text);
  for (let at = 0; at < bytes.length;) {
    try { at += writeSync(1, bytes, at); } catch (e) { if ((e as NodeJS.ErrnoException).code !== 'EAGAIN') break; }
  }
}

// Structural S3 (HR-JRN-01): publication admission is MANDATORY before anything is provisioned. The runner's
// lib/value-free-reporter.ts decides it in onBegin, before any worker is forked: FIRST, with a publication plan that refuses
// nothing, it calls admitPublication(); a refused plan throws there and admits nothing. The grant is in-process state of the
// process that made it (the runner), never anything in the environment.
// S3: a worker is admitted only by its own runner, asked over the IPC channel the runner
// forked it with: the one input of a worker no caller can set from outside (worker mode itself is chosen by the environment,
// TEST_WORKER_INDEX, and so was every input of the proof workers inherited before R2: the salt, the registry, the parent's
// pid). The runner answers each child that asks with its own grant at that moment, or with nothing (answerWorkers, installed
// by startRun before any worker exists); the worker accepts only its own runner's grant for its run's salt and registry. A
// process with no such channel, or whose parent answers nothing within ADMISSION_REPLY_MS, is not admitted, whatever its
// environment holds. Every provisioning entry point (provisionActor, the API client, ScopeActors.provision, the ScopeActors
// fixture through awaitPublicationAdmission and assertValueFreeRun) refuses without admission, before it sends anything.
const ADMITTED = Symbol.for('e2e.value-free.publication-admitted');
const ANSWERING = Symbol.for('e2e.value-free.answering-workers');
const ASKED = Symbol.for('e2e.value-free.worker-admission');
type WorkerAdmission = { pending: Map<string, Promise<boolean>>; settled: Map<string, boolean> };
type Granted = { [ADMITTED]?: string; [ANSWERING]?: true; [ASKED]?: WorkerAdmission };
/** The admission query a worker sends its runner over the fork's IPC channel, and the runner's answer (its grant, or null). */
const ADMISSION_QUERY = 'e2e-value-free-admission-query';
const ADMISSION_ANSWER = 'e2e-value-free-admission-answer';
/** How long a worker waits for its runner's answer; a runner answers at once, so only a parent that never does waits it out. */
export const ADMISSION_REPLY_MS = 10_000;
/** The grant of the runner `pid` for this run's salt and registry: null without them. */
function admissionProof(pid: number): string | null {
  const salt = process.env[VALUE_FREE_SALT_ENV];
  const registry = process.env[VALUE_FREE_REGISTRY_ENV];
  if (!salt || !registry) return null;
  return createHmac('sha256', salt).update(`value-free publication admitted\0${pid}\0${registry}`).digest('hex');
}
const granted = globalThis as Granted;
/** This process's own grant, when it made one for this run's salt and registry. */
function ownGrant(): string | null {
  const own = admissionProof(process.pid);
  return own !== null && granted[ADMITTED] === own ? own : null;
}
/** lib/value-free-reporter.ts only: FIRST, with a publication plan that refuses nothing, before any worker exists. */
export function admitPublication(): void {
  const proof = admissionProof(process.pid);
  if (!proof) throw new Error('value-free: no run salt and registry to admit the publication under');
  granted[ADMITTED] = proof;
}
/** No admission in this process (nor for any worker that asks it from now on); an admission proof set from outside dropped. */
export function withdrawAdmission(): void {
  delete granted[ADMITTED];
  delete process.env[VALUE_FREE_ADMISSION_ENV];
}

/** The runner's half (startRun): every child it forks from now on that asks is answered over that child's own IPC channel
 * with this process's grant at that moment, or null. Once per process, however many module instances load this file. */
function answerWorkers(): void {
  if (granted[ANSWERING]) return;
  granted[ANSWERING] = true;
  diagnosticsChannel.subscribe('child_process', (message) => {
    const child = (message as { process?: ChildProcess } | undefined)?.process;
    if (!child || typeof child.on !== 'function') return;
    child.on('message', (query: unknown) => {
      const q = query as { method?: unknown; params?: { id?: unknown } } | null;
      if (q?.method !== ADMISSION_QUERY || typeof q.params?.id !== 'string' || !child.connected) return;
      try {
        child.send({ method: ADMISSION_ANSWER, params: { id: q.params.id, grant: ownGrant() } }, () => undefined);
      } catch { /* the channel closed: the worker refuses on its own */ }
    });
  });
}

/** A worker's question to its own runner, over the IPC channel the runner forked it with: whether the runner's grant is
 * `expected` (its grant for this run's salt and registry). False without such a channel, on any error, or after
 * ADMISSION_REPLY_MS without an answer. */
function askRunner(expected: string): Promise<boolean> {
  return new Promise((resolve) => {
    if (typeof process.send !== 'function' || !process.connected) { resolve(false); return; }
    const id = randomBytes(16).toString('hex');
    let timer: NodeJS.Timeout | undefined;
    const settle = (admitted: boolean) => {
      if (timer) clearTimeout(timer);
      process.off('message', onAnswer);
      resolve(admitted);
    };
    const onAnswer = (answer: unknown) => {
      const a = answer as { method?: unknown; params?: { id?: unknown; grant?: unknown } } | null;
      if (a?.method === ADMISSION_ANSWER && a.params?.id === id) settle(a.params.grant === expected);
    };
    process.on('message', onAnswer);
    timer = setTimeout(() => settle(false), ADMISSION_REPLY_MS);
    try { process.send({ method: ADMISSION_QUERY, params: { id } }, (error: Error | null) => { if (error) settle(false); }); } catch {
      settle(false);
    }
  });
}
/** This process's worker admissions: per run (the grant expected of its runner), asked once, then settled. */
const workerAdmission = (): WorkerAdmission => (granted[ASKED] ??= { pending: new Map(), settled: new Map() });

/** Whether this run's publication is admitted, as far as is known now: by this very process (the runner's own grant, for
 * this salt and registry), or, in a worker, by its own runner, once it answered (awaitPublicationAdmission). */
export function publicationAdmitted(): boolean {
  if (ownGrant() !== null) return true;
  if (!inWorker()) return false;
  const expected = admissionProof(process.ppid);
  return expected !== null && workerAdmission().settled.get(expected) === true;
}
/** Whether this run's publication is admitted: in a worker, its own runner is asked first (once per run), over its IPC
 * channel. */
export async function awaitPublicationAdmission(): Promise<boolean> {
  if (publicationAdmitted()) return true;
  if (!inWorker()) return false;
  const expected = admissionProof(process.ppid);
  if (expected === null) return false;
  const state = workerAdmission();
  let pending = state.pending.get(expected);
  if (!pending) {
    pending = askRunner(expected).then((admitted) => { state.settled.set(expected, admitted); return admitted; });
    state.pending.set(expected, pending);
  }
  return pending;
}
/** Every provisioning entry point, first: in value-free mode nothing is sent before the publication is admitted. */
export async function requirePublicationAdmission(entry: string): Promise<void> {
  if (valueFreeFromEnv() && !(await awaitPublicationAdmission())) {
    throw new Error(`value-free: the publication is not admitted, so ${entry} sends nothing. lib/value-free-reporter.ts, ` +
      'FIRST and with a publication plan that refuses nothing, admits a run before any worker, and a worker only through its ' +
      'own runner; a marker or a proof set from outside does not');
  }
}

/** The ScopeActors fixture's refusal of a value-free test whose report or pixels would carry values. A worker awaits
 * awaitPublicationAdmission() first (lib/scope-actors.ts), so its runner's answer is known here. */
export function assertValueFreeRun(modes: Record<'trace' | 'screenshot' | 'video', unknown>): void {
  if (process.env[VALUE_FREE_REPORTER_ENV] !== '1') {
    throw new Error('value-free mode needs lib/value-free-reporter.ts as the FIRST reporter (playwright.config.ts ' +
      'adds it when E2E_VALUE_FREE=1; a CLI --reporter= list must name it first)');
  }
  // Structural S3: the marker is a hint; only the admission the reporter itself granted lets the fixture provision.
  if (!publicationAdmitted()) {
    throw new Error('value-free mode: the publication is not admitted (lib/value-free-reporter.ts admits a run in onBegin, ' +
      'FIRST and with a plan that refuses nothing; a reporter marker set from outside is no admission)');
  }
  const on = Object.entries(modes).filter(([, mode]) =>
    (typeof mode === 'string' ? mode : (mode as { mode?: string } | undefined)?.mode) !== 'off').map(([name]) => name);
  if (on.length) throw new Error(`value-free mode needs ${on.join(', ')} off: pixels and trace network bodies cannot be hashed`);
}

/** The config hook: the value-free reporter first when E2E_VALUE_FREE=1, else the same list, untouched. */
export function withValueFreeReporter(reporters: ReporterDescription[]): ReporterDescription[] {
  return valueFreeFromEnv() ? [[VALUE_FREE_REPORTER], ...reporters] : reporters;
}

/** The config's `use`: trace, screenshot and video off when E2E_VALUE_FREE=1 (the ScopeActors fixture refuses a
 * value-free test with any of them on: pixels and trace network bodies cannot be hashed), else the same object. */
export function withValueFreeUse(use: PlaywrightTestConfig['use']): PlaywrightTestConfig['use'] {
  return valueFreeFromEnv() ? { ...use, trace: 'off', screenshot: 'off', video: 'off' } : use;
}

/** The runner's half of the switch: one salt and one registry for the run, before any worker exists. */
function startRun(): void {
  // Structural S3: a reporter marker or admission proof this runner did not make is dropped: only its own reporter sets them.
  delete process.env[VALUE_FREE_REPORTER_ENV];
  withdrawAdmission();
  // S3: before any worker exists, so every worker it forks can ask it (and only it) whether the run is admitted.
  answerWorkers();
  process.env[VALUE_FREE_SALT_ENV] ||= randomBytes(32).toString('hex');
  let created: string | undefined;
  const given = process.env[VALUE_FREE_REGISTRY_ENV];
  if (!given) {
    created = mkdtempSync(path.join(os.tmpdir(), 'e2e-vf-'));
    chmodSync(created, 0o700);
    process.env[VALUE_FREE_REGISTRY_ENV] = created;
  } else {
    // A registry the consumer gave (the walk job's, one per run) is adopted, recorded by this runner's own file
    // there (empty until it registers), so the consumer's final boundary check refuses a registry no run used instead of
    // scanning against nothing. It stays for that check; the consumer removes it.
    try { appendFileSync(path.join(given, `${process.pid}.jsonl`), '', { mode: 0o600 }); } catch { /* the check refuses it */ }
  }
  // At exit, first of the exit hooks (this module loads before any reporter): the held output, as the reporter decides,
  // while the registry still exists; then the registry.
  process.on('exit', () => {
    try { releaseAtExit(); } finally { if (created) rmSync(created, { recursive: true, force: true }); }
  });
}

// The runner loads this module (its config, its reporter or its spec collection)
// before it forks the workers, which inherit this environment.
if (valueFreeFromEnv()) {
  if (!inWorker()) startRun();
  scrubLogs();
}
