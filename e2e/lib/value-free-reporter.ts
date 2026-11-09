// HR-JRN-01: the report half of value-free mode (lib/value-free.ts). Listed FIRST,
// it rewrites in place what every later reporter (list, line, json, html, blob,
// walks/vm/counts-reporter.cjs) then reads: suite and test titles, tags and
// locations; error messages, stacks, values and snippets; step titles, locations
// and errors; captured stdout and stderr; annotations; and attachments. Text
// bodies are scrubbed, text files on disk (Playwright's own error-context.md, an
// attachment's copy) are rewritten, and a binary attachment's file is deleted and
// replaced in the report by a note. Colours are removed before anything is
// matched. It knows every value a worker registered (the run's shared registry),
// so a worker's org name is tokenized here exactly as in that worker's evidence.
// It does that when a test ends and again when the run ends, once every worker has
// stopped and so every value of the run is registered.
//
// Round 4: what leaves the machine is ONE publish directory, which this reporter
// assembles in onExit, once every reporter's onEnd has written and every worker has
// stopped (lib/value-free-publish.ts): reports re-serialized, attachments copied by
// content under harness names, the runner's merged output as runner.log, all
// sanitized, scanned for every registered value and sealed. Per-test stdout and
// stderr in every report become a pointer to runner.log. A run with a value refused
// at registration, an unsafe or unresolvable reporter, an attachment that is a link,
// a hard link or a special file, a write into the publish directory before assembly
// or any scan hit is REFUSED: it fails, its publish directory holds only
// REFUSED.json, and its held output is replaced by one line saying why. At the
// runner's exit the seal and the output about to be printed are checked once more.
// It still scrubs the files the run leaves on the machine (lib/value-free-scrub.ts:
// contents in onEnd, names after the assembly, deleted on a refusal); those are
// never pulled. Round 5: attachment bodies and text files are sanitized through every
// layer (lib/value-free-codec.ts), and one nested past the bound or in an encoding the
// codec does not decode is withheld and refuses the run; so is the output printed at the
// runner's exit, which is sanitized the same way; a refusal that cannot be recorded
// cleanly publishes nothing.
// Listed without E2E_VALUE_FREE=1 it refuses the run, as the explicit option does
// (one switch, one salt). Only when it is first does it tell the workers it runs
// (E2E_VALUE_FREE_REPORTER), so the ScopeActors fixture refuses a value-free run
// whose report would carry values. It counts as printing to stdio, so Playwright
// never puts its default line or dot reporter ahead of it; a value-free run with
// only file reporters prints only its own progress counts.
import { lstatSync, readFileSync, realpathSync, unlinkSync, writeFileSync, type Stats } from 'node:fs';
import path from 'node:path';
import type { FullConfig, FullResult, Reporter, Suite, TestCase, TestError, TestResult, TestStep } from '@playwright/test/reporter';
import { admitPublication, heldOutput, isInside, isTextContent, onRunnerRelease, printNow, refusalMessage, sharedValueFree,
  valueFreeFromEnv, VALUE_FREE_REPORTER, VALUE_FREE_REPORTER_ENV, withdrawAdmission, type Refusal, type ValueFree } from './value-free';
import { problemReason } from './value-free-builder';
import { sanitizeText } from './value-free-codec';
import { RUNNER_LOG } from './value-free-record.mjs';
import { assemblePublication, clearPublishDir, confirmAtExit, planPublication, publishDirFor, refusalLine, scanPublication,
  STDIO_POINTER, type Assembly, type Plan, type Reason } from './value-free-publish';
import { existingParent, guardTargets, outputTargets, renameTarget, scrubTree, withhold, type ScrubTargets } from './value-free-scrub';

type Attachment = { name: string; contentType: string; path?: string; body?: Buffer };
type Located = { location?: { file: string } };
/** Every test under a suite, from its own lists (suite.allTests() without relying on the method). */
const testsUnder = (suite: Suite): TestCase[] => [...(suite.tests ?? []), ...(suite.suites ?? []).flatMap(testsUnder)];
const real = (file: string): string => { try { return realpathSync(file); } catch { return path.resolve(file); } };
const overlaps = (a: string, b: string): boolean => a === b || isInside(a, b) || isInside(b, a);
const inWorker = (): boolean => process.env.TEST_WORKER_INDEX !== undefined;
/** A value refused at registration, as a refusal-record reason (length, kind and file:line only). */
const refusalReason = (r: Refusal): Reason =>
  ({ reason: r.reason === 'token-syntax' ? 'token-syntax' : 'short-value', kind: r.what, length: r.refused, at: r.at });
/** Why an attachment's file cannot be read or rewritten safely: a link (never followed), a hard link, a special file. */
const unsafeKind = (st: Stats): string | null =>
  st.isSymbolicLink() ? 'symlink' : !st.isFile() ? 'special file' : st.nlink > 1 ? 'hardlink' : null;
/** What the runner prints when even its refusal line would quote a registered value. */
const WITHHELD_LINE = 'value-free: REFUSED. Its reasons are withheld too (their text overlaps a registered value), and so ' +
  'is its output.\n';

export default class ValueFreeReporter implements Reporter {
  private valueFree?: ValueFree;
  private root?: Suite;
  private rootDir = process.cwd();
  private outputDirs: string[] = [];
  private targets: ScrubTargets = { dirs: [], files: [] };
  /** Each target's deepest existing directory when the run began: the run made the ones below it. */
  private readonly roots = new Map<string, string>();
  private readonly seen = new WeakSet<object>();
  private total = 0;
  private finishedTests = 0;
  private refusalsShown = 0;
  private plan?: Plan;
  private assembly?: Assembly;
  /** What scrubbing the report in place found that refuses the run: an attachment file that is a link, a hard link or a
   * special file (never read), a body or file nested past the codec's bound or in an encoding it does not decode. */
  private readonly reportReasons: Reason[] = [];
  /** Whether onEnd already failed the run (onExit fails it only for what assembly finds). */
  private failed = false;

  constructor(private readonly options: { configDir?: string; _commandHash?: string } = {}) {
    if (!valueFreeFromEnv()) {
      throw new Error('lib/value-free-reporter.ts is listed but E2E_VALUE_FREE=1 is not set: value-free mode is one ' +
        'switch, set it for the run (the reporter alone is refused, as the explicit option is)');
    }
  }

  printsToStdio(): boolean { return true; }

  onBegin(config: FullConfig, suite: Suite): void {
    this.valueFree = sharedValueFree();
    this.root = suite;
    this.rootDir = config.rootDir;
    // Before any worker is forked; the CLI --reporter= list, when given, is this list. Structural S3: nothing is admitted
    // until this reporter, first, has a publication plan that refuses nothing (below).
    withdrawAdmission();
    const first = config.reporter[0]?.[0] ?? '';
    const isFirst = real(path.resolve(this.options.configDir ?? process.cwd(), first)) === real(VALUE_FREE_REPORTER);
    if (isFirst) process.env[VALUE_FREE_REPORTER_ENV] = '1';
    const configDir = this.options.configDir ?? (config.configFile ? path.dirname(config.configFile) : process.cwd());
    const ctx = { configDir, commandHash: this.options._commandHash, shard: config.shard };
    const sourceDirs = [configDir, config.rootDir, ...config.projects.map((p) => p.testDir)];
    // The publication: every reporter resolved now, an unsafe or unresolvable one refuses the run (never a warning).
    const plan = planPublication(config, ctx, publishDirFor(config), sourceDirs);
    clearPublishDir(plan);
    this.plan = plan;
    onRunnerRelease((merged) => this.release(merged));
    // The files that stay on the machine (scrubbed in place, never pulled), the publish directory never among them.
    const guarded = guardTargets(outputTargets(config, ctx), sourceDirs);
    this.targets = { dirs: guarded.dirs.filter((d) => !overlaps(d, plan.publishDir)),
      files: guarded.files.filter((f) => !overlaps(f, plan.publishDir)) };
    for (const target of [...this.targets.dirs, ...this.targets.files]) this.roots.set(target, existingParent(target));
    this.outputDirs = config.projects.map((p) => path.resolve(p.outputDir)).filter((d) => this.targets.dirs.includes(d));
    this.suite(suite);
    this.total = testsUnder(suite).length;
    printNow(`value-free: ${this.total} tests. The runner's output is held and printed, scrubbed, when the run ends.\n`);
    process.on('exit', () => this.finish());
    // Structural S3: admission is decided here, synchronously, before any worker is forked. A refused plan throws now (its
    // refusal record is still assembled at the runner's exit, by the hooks set above), never only at onEnd: Playwright
    // records the throw as a reporter error and fails the run, and since nothing was admitted, every provisioning entry
    // point refuses (lib/value-free.ts requirePublicationAdmission): nothing is signed up or sent.
    if (plan.reasons.length) {
      printNow(refusalLine(plan.reasons));
      throw new Error(refusalLine(plan.reasons));
    }
    if (isFirst) admitPublication();
  }

  onStdOut(_chunk: string | Buffer, _test?: TestCase, result?: TestResult): void { this.stdio(result?.stdout, true); }
  onStdErr(_chunk: string | Buffer, _test?: TestCase, result?: TestResult): void { this.stdio(result?.stderr, true); }
  onStepBegin(_test: TestCase, _result: TestResult, step: TestStep): void { if (this.valueFree) this.step(step); }
  onStepEnd(_test: TestCase, result: TestResult, step: TestStep): void {
    if (!this.valueFree) return;
    this.step(step);
    this.attachments(result, true);
  }

  onTestEnd(test: TestCase, result: TestResult): void {
    if (!this.valueFree) return;
    this.test(test);
    this.result(test, result);
    printNow(`value-free: ${++this.finishedTests}/${this.total} finished\n`);
    const refusals = this.valueFree.refusals();
    if (refusals.length > this.refusalsShown) {
      this.refusalsShown = refusals.length;
      printNow(refusalMessage(refusals));
    }
  }

  onError(error: TestError): void { this.error(error); }

  /** Every worker has stopped, so every value of the run is registered: everything a later reporter serializes is
   * scrubbed once more, then file contents (html and blob still copy attachments by path after this). */
  async onEnd(_result?: FullResult): Promise<{ status?: FullResult['status'] } | undefined> {
    const vf = this.valueFree;
    if (!vf || !this.root) return undefined;
    this.suite(this.root);
    for (const test of testsUnder(this.root)) for (const result of test.results ?? []) this.result(test, result);
    for (const dir of this.outputDirs) scrubTree(dir, vf, false);
    this.failed = vf.refusals().length > 0 || (this.plan?.reasons.length ?? 0) > 0 || this.reportReasons.length > 0;
    return this.failed ? { status: 'failed' } : undefined;
  }

  /** Every reporter's onEnd has written: the publish directory is assembled (or refused), then the files that stay on
   * the machine are scrubbed. A refusal only assembly finds fails the run here (a reporter error fails a passed run). */
  async onExit(): Promise<void> {
    const assembly = this.assemble();
    this.finish();
    if (assembly?.refused && !this.failed) {
      this.failed = true;
      throw new Error(refusalLine(assembly.reasons, assembly.nothing));
    }
  }

  /** Once: the publication from what every reporter wrote, every registration and the runner's output so far. */
  private assemble(extra: Reason[] = []): Assembly | undefined {
    const vf = this.valueFree;
    if (!vf || !this.plan) return undefined;
    this.assembly ??= assemblePublication({ plan: this.plan, vf, runnerLog: heldOutput(),
      reasons: [...vf.refusals().map(refusalReason), ...this.reportReasons, ...extra] });
    return this.assembly;
  }

  /** The runner's exit (lib/value-free.ts): the seal is still intact and the output about to be printed, sanitized through
   * every layer and read as the runner log (R2: lines of text, never a document), is clean, or the run is refused now; then
   * that output, or the one refusal line, is what the runner prints. */
  private release(merged: string): string {
    const vf = this.valueFree!;
    const output = sanitizeText(merged, vf, RUNNER_LOG);
    let assembly = this.assemble([{ reason: 'not-assembled', why: 'the run ended before its outputs were assembled' }])!;
    assembly = this.assembly = confirmAtExit(this.plan!, assembly, vf, output);
    if (!assembly.refused && typeof output === 'string') return output;
    if (!inWorker()) process.exitCode = 1;
    const refusals = vf.refusals();
    const others = assembly.reasons.filter((r) => r.reason !== 'short-value' && r.reason !== 'token-syntax');
    const line = vf.text(refusals.length && !assembly.nothing ? `${refusalMessage(refusals)}${others.length ? refusalLine(others) : ''}`
      : refusalLine(assembly.reasons, assembly.nothing));
    return scanPublication(null, vf.values(), [{ area: 'runner-output', text: line }]).length ? WITHHELD_LINE : line;
  }

  private finish(): void {
    const vf = this.valueFree;
    if (!vf) return;
    if (vf.refusals().length || this.assembly?.refused) withhold(this.targets, this.roots, vf);
    else {
      const targets = [...this.targets.dirs, ...this.targets.files];
      for (const target of targets) scrubTree(target, vf, true);
      for (const target of targets) renameTarget(target, this.roots.get(target) ?? path.dirname(target), vf);
    }
    // Round 7: the run's registry is its creator's to remove: the runner's own (lib/value-free.ts, at its exit), or the
    // consumer's that gave it (E2E_VALUE_FREE_REGISTRY: the walk job, whose final boundary check reads it), never this.
  }

  private suite(suite: Suite): void {
    const vf = this.valueFree!;
    suite.title = vf.text(suite.title);
    this.location(suite as Located);
    for (const test of suite.tests) {
      this.test(test);
      this.location(test);
      for (const annotation of test.annotations) this.annotation(annotation);
    }
    for (const child of suite.suites) this.suite(child);
  }

  /** A test's title, tags and the titles of the suites around it. */
  private test(test: TestCase): void {
    const vf = this.valueFree!;
    test.title = vf.text(test.title);
    const tags = (test as unknown as { _tags?: string[] })._tags;
    if (Array.isArray(tags)) tags.forEach((tag, i) => { tags[i] = vf.text(tag); });
    for (let parent: Suite | undefined = test.parent; parent; parent = parent.parent) parent.title = vf.text(parent.title);
  }

  private result(test: TestCase, result: TestResult): void {
    for (const error of [...result.errors, result.error]) this.error(error);
    for (const step of result.steps) this.step(step);
    // A test's output, chunk by chunk, can split a value across chunks or across the two streams: every report says
    // where it is instead (runner.log, the merged stream sanitized as one text).
    for (const chunks of [result.stdout, result.stderr]) if (chunks?.length) chunks.splice(0, chunks.length, STDIO_POINTER);
    for (const annotation of [...test.annotations, ...result.annotations]) this.annotation(annotation);
    this.attachments(result, false);
  }

  /** The part under the root directory only, so reporters still relate it to rootDir; the absolute rest of a path
   * is scrubbed in the files they write. */
  private location(item: Located | undefined): void {
    if (!item?.location || typeof item.location.file !== 'string') return;
    const rel = path.relative(this.rootDir, item.location.file);
    const clean = this.valueFree!.text(rel);
    if (clean === rel) return;
    try { item.location.file = path.resolve(this.rootDir, clean); } catch { /* frozen: a source path */ }
  }

  private annotation(annotation: { type: string; description?: string } & Located): void {
    annotation.type = this.valueFree!.text(annotation.type);
    if (annotation.description) annotation.description = this.valueFree!.text(annotation.description);
    this.location(annotation);
  }

  private error(error: TestError | undefined): void {
    if (!error || !this.valueFree) return;
    for (const field of ['message', 'stack', 'value', 'snippet'] as const) {
      const value = error[field];
      if (typeof value === 'string') error[field] = this.valueFree.text(value);
    }
    this.location(error as Located);
    this.error(error.cause);
  }

  private step(step: TestStep): void {
    step.title = this.valueFree!.text(step.title);
    this.location(step);
    this.error(step.error);
    for (const child of step.steps) this.step(child);
  }

  private stdio(chunks: (string | Buffer)[] | undefined, lastOnly: boolean): void {
    if (!chunks || !this.valueFree) return;
    for (let i = lastOnly ? chunks.length - 1 : 0; i >= 0 && i < chunks.length; i++) {
      const chunk = chunks[i];
      chunks[i] = typeof chunk === 'string' ? this.valueFree.text(chunk) : Buffer.from(this.valueFree.text(chunk.toString('utf8')));
    }
  }

  // In place: steps refer to these same objects. Idempotent, so it runs at every step end (new attachments only),
  // test end and run end (all of them), each time under everything registered so far.
  private attachments(result: TestResult, newOnly: boolean): void {
    const vf = this.valueFree!;
    for (const attachment of result.attachments as Attachment[]) {
      if (newOnly && this.seen.has(attachment)) continue;
      this.seen.add(attachment);
      attachment.name = vf.text(attachment.name);
      if (attachment.path === undefined) {
        if (attachment.body === undefined) continue;
        if (isTextContent(attachment.contentType)) {
          const clean = sanitizeText(attachment.body.toString('utf8'), vf);
          if (typeof clean === 'string') { attachment.body = Buffer.from(clean); continue; }
          this.reportReasons.push(problemReason(clean, 'attachments'));
        }
      } else {
        const file = path.resolve(attachment.path);
        let st: Stats | undefined;
        try { st = lstatSync(file); } catch { /* gone: withheld below */ }
        const unsafe = st && unsafeKind(st);
        // A link is never followed (rewriting through it would write its target), nor a hard link or a special file read.
        if (unsafe) this.reportReasons.push({ reason: 'unsafe-source', source: 'attachment', why: unsafe });
        const owned = !unsafe && st !== undefined && this.outputDirs.some((dir) => isInside(file, dir));
        if (owned && isTextContent(attachment.contentType)) {
          // In place, where html and blob copy it from; the worker wrote it before attaching.
          try {
            const before = readFileSync(file, 'utf8');
            const after = sanitizeText(before, vf);
            if (typeof after === 'string') {
              if (after !== before) writeFileSync(file, after);
              continue;
            }
            this.reportReasons.push(problemReason(after, 'attachments'));
          } catch { /* unreadable: withheld below */ }
        }
        if (owned) try { unlinkSync(file); } catch { /* already gone */ }
      }
      attachment.body = Buffer.from(`withheld in value-free mode: ${attachment.contentType}`);
      attachment.contentType = 'text/plain';
      delete attachment.path;
    }
  }
}
