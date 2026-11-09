import { spawnSync } from 'node:child_process';
import { createHmac, randomBytes } from 'node:crypto';
import { existsSync, lstatSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { test, expect, type APIRequestContext } from '@playwright/test';
import type { FullConfig, Suite } from '@playwright/test/reporter';
import { ApiClient } from '../lib/api-client';
import { provisionActor } from '../lib/provisioning';
import { assertValueFreeRun, ValueFree, VALUE_FREE_ENV, VALUE_FREE_REGISTRY_ENV, VALUE_FREE_REPORTER, VALUE_FREE_REPORTER_ENV,
  VALUE_FREE_SALT_ENV } from '../lib/value-free';
import { finalCheck, STATUS } from '../lib/value-free-boundary.mjs';
import { Builder, type Reason } from '../lib/value-free-builder';
import { assemblePublication, confirmAtExit, planPublication, scanPublication } from '../lib/value-free-publish';
import ValueFreeReporter from '../lib/value-free-reporter';
import { isProtectedPath, writeManifest } from '../lib/value-free-seal.mjs';
import { writeZip } from '../lib/value-free-zip';
import { privatePath, removePrivate } from './value-free/private-dir';
import { killChildren, resultOf, runChild } from './value-free/scan';

// Fixtures live in a private directory per test, outside the evidence tree (value-free/private-dir.ts).
test.afterEach(async ({}, testInfo) => removePrivate(testInfo));

// HR-JRN-01, the narrowed structural review (STRUCTURAL-REVIEW.json, S1-S3): the three publication-boundary properties,
// offline. No stack, no browser, no network: every request goes to a stub that records it. Every value is generated here.
//   S1 path disjointness: a pull is classified by where it canonically lies, every link below the pull root resolved, and
//      the private side (the registries) is disjoint from what crosses, on the path the check ACCEPTS too.
//   S2 closed record schema: a published report record holds only its closed vocabulary of fields and kinds; an unknown
//      field, an unknown kind or an arbitrary nested object refuses (the builder, the publication scan, the final check),
//      while an attachment payload stays a payload (sanitized and scanned, never schema'd).
//   S3 refuse before provisioning: a refused publication plan throws in the reporter's onBegin, before any worker, and no
//      provisioning entry point sends anything without the admission the reporter itself grants; a marker set from
//      outside is no admission.
const E2E_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
/** R2 (S3): the child run whose worker is admitted, or not, through its own runner (value-free/admission.config.ts). */
const ADMISSION_CONFIG = fileURLToPath(new URL('./value-free/admission.config.ts', import.meta.url));
const ADMISSION_TITLE = 'admission: a worker provisions only when its own runner admitted the publication';
/** The admission proof's environment name (lib/value-free.ts): set from outside, it proves nothing. */
const ADMISSION_ENV = 'E2E_VALUE_FREE_ADMISSION';
const hex = (bytes: number) => randomBytes(bytes).toString('hex');
const OFF = { trace: 'off', screenshot: 'off', video: 'off' } as const;
/** A refused pull withholds what it reaches the way the rig reads it, with bash's globstar (lib/value-free-reach.mjs): with
 * such a bash (the walk VM's, CI's) the refusal is the entry's; without one (macOS's bash 3.2) the withholding still moves
 * what it bounds, and the check reports that it failed. Either way the verdict refuses and nothing crosses. */
const REFUSED_ENTRY = spawnSync('bash', ['--noprofile', '--norc', '-c', 'shopt -s globstar'], { stdio: 'ignore' }).status === 0
  ? STATUS.entry : STATUS.failed;

function setEnv(vars: Record<string, string | undefined>): Record<string, string | undefined> {
  const previous = Object.fromEntries(Object.keys(vars).map((k) => [k, process.env[k]]));
  for (const [k, v] of Object.entries(vars)) if (v === undefined) delete process.env[k]; else process.env[k] = v;
  return previous;
}
/** The environment a value-free runner gives its workers (the switch, a fresh salt and registry), and nothing admitted. */
async function inRun<T>(body: () => Promise<T> | T): Promise<T> {
  const dir = mkdtempSync(path.join(os.tmpdir(), 'e2e-vf-structural-'));
  const previous = setEnv({ [VALUE_FREE_ENV]: '1', [VALUE_FREE_SALT_ENV]: hex(32), [VALUE_FREE_REGISTRY_ENV]: dir,
    [VALUE_FREE_REPORTER_ENV]: undefined, [ADMISSION_ENV]: undefined });
  try { return await body(); } finally { setEnv(previous); rmSync(dir, { recursive: true, force: true }); }
}
/** A request context that sends nothing: it records each call and answers 599. */
function stubRequest(calls: string[]): APIRequestContext {
  const record = (method: string) => async (url: string) => {
    calls.push(`${method} ${new URL(url).pathname}`);
    return { status: () => 599, text: async () => '{}' };
  };
  return { get: record('get'), post: record('post'), patch: record('patch'), delete: record('delete') } as unknown as APIRequestContext;
}
const reporterConfig = (out: string, reporters: unknown[]) => ({ reporter: reporters, rootDir: E2E_DIR,
  configFile: path.join(E2E_DIR, 'playwright.config.ts'), projects: [{ outputDir: path.join(out, 'test-results'), testDir: E2E_DIR }] }) as
  unknown as FullConfig;
const emptySuite = () => ({ title: '', tests: [], suites: [] }) as unknown as Suite;
const exists = (p: string): boolean => { try { lstatSync(p); return true; } catch { return false; } };
/** A registries root as a value-free run leaves it: one adopted run registry, holding `values`. */
function registries(at: string, values: string[]): string {
  mkdirSync(path.join(at, 'run-1'), { recursive: true });
  writeFileSync(path.join(at, 'run-1', '1.jsonl'), values.map((v) => `${JSON.stringify(v)}\n`).join(''));
  return at;
}
const reasonsOf = (rs: Reason[] | readonly object[]) => (rs as { reason: string }[]).map((r) => r.reason);

// ---------------------------------------------------------------------------------------------------------- S1
test.describe('value-free offline: structural S1, a pull is classified where it canonically lies', () => {
  let base: string;
  test.beforeEach(() => { base = mkdtempSync(path.join(os.tmpdir(), 'e2e-vf-s1-')); });
  test.afterEach(() => { rmSync(base, { recursive: true, force: true }); });

  test('value-free offline: structural S1, a pull through an in-root directory link to a directory outside the root is refused', () => {
    const root = path.join(base, 'root');
    const outside = path.join(base, 'outside');
    mkdirSync(path.join(outside, 'sub'), { recursive: true });
    writeFileSync(path.join(outside, 'sub', 'clean.txt'), 'clean\n');
    mkdirSync(root);
    symlinkSync(outside, path.join(root, 'link'));
    const verdict = finalCheck({ root, registries: registries(path.join(base, 'registries'), [hex(8)]), pulls: ['link/sub/**'] });
    expect(verdict.out.toString(), 'the accepted exact-pull path admitted a canonically outside directory').toBe(REFUSED_ENTRY);
    expect(verdict.code).toBe(1);
    expect(exists(path.join(root, 'link')), 'the link the rig would follow is withheld').toBe(false);
    expect(readFileSync(path.join(outside, 'sub', 'clean.txt'), 'utf8'), 'what it points to never moves').toBe('clean\n');
  });

  test('value-free offline: structural S1, a lexically clean exact path whose ancestor is a link is refused', () => {
    const root = path.join(base, 'root');
    const outside = path.join(base, 'elsewhere');
    mkdirSync(outside, { recursive: true });
    writeFileSync(path.join(outside, 'f.txt'), 'clean\n');
    mkdirSync(path.join(root, 'a'), { recursive: true });
    symlinkSync(outside, path.join(root, 'a', 'door'));
    const verdict = finalCheck({ root, registries: registries(path.join(base, 'registries'), [hex(8)]), pulls: ['a/door/f.txt'] });
    expect(verdict.out.toString()).toBe(REFUSED_ENTRY);
    expect(verdict.code).toBe(1);
    expect(exists(path.join(root, 'a', 'door'))).toBe(false);
    expect(readFileSync(path.join(outside, 'f.txt'), 'utf8')).toBe('clean\n');
  });

  test('value-free offline: structural S1, a pull whose tree holds the private registries is refused and the registries stay', () => {
    const root = path.join(base, 'root');
    mkdirSync(path.join(root, 'pub'), { recursive: true });
    writeFileSync(path.join(root, 'pub', 'clean.txt'), 'clean\n');
    // An adopted registry that registered nothing: no value to find, so only disjointness can refuse it.
    const reg = registries(path.join(root, 'pub', 'registries'), []);
    const verdict = finalCheck({ root, registries: reg, pulls: ['pub/**'] });
    expect(verdict.out.toString(), 'the private registries crossed with the pull').toBe(REFUSED_ENTRY);
    expect(verdict.code).toBe(1);
    expect(exists(path.join(reg, 'run-1', '1.jsonl')), 'the registries are never moved').toBe(true);
    expect(exists(path.join(root, 'pub', 'clean.txt')), 'what the pull reaches is withheld').toBe(false);
  });

  test('value-free offline: structural S1, a control: a clean exact pull with no link on its way is still accepted', () => {
    const root = path.join(base, 'root');
    mkdirSync(path.join(root, 'pub', 'sub'), { recursive: true });
    writeFileSync(path.join(root, 'pub', 'sub', 'clean.txt'), 'clean\n');
    const verdict = finalCheck({ root, registries: registries(path.join(base, 'registries'), [hex(8)]), pulls: ['pub/**'] });
    expect(verdict.out.toString()).toBe(STATUS.clean);
    expect(verdict.code).toBe(0);
  });

  test('value-free offline: structural S1, a link to the working directory or home is a protected path (the seal and export)', () => {
    symlinkSync(process.cwd(), path.join(base, 'to-cwd'));
    symlinkSync(os.homedir(), path.join(base, 'to-home'));
    expect(isProtectedPath(path.join(base, 'to-cwd')), 'a link resolving to the working directory').toBe(true);
    expect(isProtectedPath(path.join(base, 'to-home')), 'a link resolving to home').toBe(true);
    expect(isProtectedPath(path.join(base, 'plain')), 'a control: a plain directory elsewhere').toBe(false);
  });
});

// ---------------------------------------------------------------------------------------------------------- S2
/** A Playwright JSON report every field of which is in the closed vocabulary. */
const jsonReport = () => ({
  config: { rootDir: 'e2e', version: '1.62.1', workers: 1, metadata: {}, projects: [{ id: 'p', name: 'p', retries: 0, timeout: 1000,
    metadata: {} }] },
  suites: [{ title: 'a.spec.ts', file: 'a.spec.ts', line: 0, column: 0, specs: [{ title: 't', ok: true, tags: [], id: 'abc123-def456',
    file: 'a.spec.ts', line: 1, column: 1, tests: [{ timeout: 1000, annotations: [], expectedStatus: 'passed', projectId: 'p',
      projectName: 'p', status: 'expected', results: [{ workerIndex: 0, parallelIndex: 0, status: 'passed', duration: 5, errors: [],
        stdout: [], stderr: [], retry: 0, startTime: '2026-10-31T00:00:00.000Z', annotations: [], attachments: [] as object[] }] }] }] }],
  errors: [],
  stats: { startTime: '2026-10-31T00:00:00.000Z', duration: 5, expected: 1, skipped: 0, unexpected: 0, flaky: 0 } as Record<string, unknown>,
});
type JsonReportShape = ReturnType<typeof jsonReport> & Record<string, unknown>;
const blobLines = (...events: object[]) => Buffer.from(events.map((e) => `${JSON.stringify(e)}\n`).join(''));
const blobControl = () => [{ method: 'onBlobReportMetadata', params: { version: 2, userAgent: 'probe', pathSeparator: '/' } },
  { method: 'onBegin' }, { method: 'onEnd', params: { result: { status: 'passed', startTime: 1, duration: 1 } } }];
const htmlIndex = (report: object) => `<!DOCTYPE html><html><body><template id="playwrightReportBase64">data:application/zip;base64,${
  writeZip([{ name: 'report.json', data: Buffer.from(JSON.stringify(report)) }]).toString('base64')}</template></body></html>`;
const htmlReport = () => ({ metadata: {}, startTime: 1, duration: 1, files: [], projectNames: ['p'], stats: { total: 0, expected: 0,
  unexpected: 0, flaky: 0, skipped: 0, ok: true }, errors: [], options: {}, machines: [] } as Record<string, unknown>);

test.describe('value-free offline: structural S2, a published record holds only its closed vocabulary', () => {
  let base: string;
  test.beforeEach(() => { base = mkdtempSync(path.join(os.tmpdir(), 'e2e-vf-s2-')); });
  test.afterEach(() => { rmSync(base, { recursive: true, force: true }); });

  /** The builder's reasons for one source written from `data`, and whether it published the record. */
  function build(kind: 'json' | 'counts' | 'blob' | 'html', data: string | Buffer): { reasons: string[]; published: boolean } {
    const at = mkdtempSync(path.join(base, 'case-'));
    const source = kind === 'html' ? path.join(at, 'html-report') : path.join(at, `source.${kind === 'blob' ? 'zip' : 'json'}`);
    if (kind === 'html') { mkdirSync(source); writeFileSync(path.join(source, 'index.html'), data); } else writeFileSync(source, data);
    const staging = path.join(at, 'staging');
    mkdirSync(staging);
    const builder = new Builder(staging, new ValueFree(hex(32)));
    builder.source({ label: kind, kind, path: source });
    const rel = kind === 'html' ? 'html/index.html' : kind === 'blob' ? 'reports/blob-1.zip' : `reports/${kind}-1.json`;
    return { reasons: reasonsOf(builder.reasons), published: existsSync(path.join(staging, rel)) };
  }
  const json = (edit: (r: JsonReportShape) => void): string => { const r = jsonReport() as JsonReportShape; edit(r); return JSON.stringify(r); };

  test('value-free offline: structural S2, the builder publishes a json report record only when every field and kind is admitted',
    () => {
      expect(build('json', json(() => {})), 'a control: an admitted record is published').toEqual({ reasons: [], published: true });
      expect(build('json', json((r) => { r.extraField = 1; })), 'an unknown field').toEqual({ reasons: ['unknown-field'], published: false });
      expect(build('json', json((r) => { r.stats.duration = 'long'; })), 'an unknown kind (a name where a count is admitted)')
        .toEqual({ reasons: ['unknown-kind'], published: false });
      expect(build('json', json((r) => { r.stats.expected = { nested: { deeper: true } }; })), 'an arbitrary nested object')
        .toEqual({ reasons: ['unknown-kind'], published: false });
      expect(build('json', json((r) => { (r.config as Record<string, unknown>).custom = { a: { b: 1 } }; })),
        'an arbitrary nested object under a field outside the vocabulary').toEqual({ reasons: ['unknown-field'], published: false });
    });

  test('value-free offline: structural S2, an attachment payload stays a payload: arbitrary keys inside it are sanitized, not refused',
    () => {
      const payload = { anyKey: { deeper: [1, 2] }, [JSON.stringify({ inKey: true })]: 'x' };
      const withPayload = json((r) => { r.suites[0].specs[0].tests[0].results[0].attachments.push({ name: 'evidence',
        contentType: 'application/json', body: Buffer.from(JSON.stringify(payload)).toString('base64') }); });
      expect(build('json', withPayload)).toEqual({ reasons: [], published: true });
    });

  test('value-free offline: structural S2, the counts, blob and html report records are closed too', () => {
    expect(build('counts', JSON.stringify({ planned: 1 })), 'a control').toEqual({ reasons: [], published: true });
    expect(build('counts', JSON.stringify({ planned: 1, extra: 2 }))).toEqual({ reasons: ['unknown-field'], published: false });
    expect(build('blob', writeZip([{ name: 'report.jsonl', data: blobLines(...blobControl()) }])), 'a control')
      .toEqual({ reasons: [], published: true });
    expect(build('blob', writeZip([{ name: 'report.jsonl', data: blobLines(...blobControl(), { method: 'onCustom', params: {} }) }])),
      'an unknown event kind').toEqual({ reasons: ['unknown-kind'], published: false });
    expect(build('blob', writeZip([{ name: 'report.jsonl', data: blobLines({ method: 'onEnd', params: { result: { status: 'passed',
      startTime: 1, duration: 1, extra: { a: 1 } } } }) }])), 'an arbitrary nested object in an event')
      .toEqual({ reasons: ['unknown-field'], published: false });
    expect(build('html', htmlIndex(htmlReport())), 'a control').toEqual({ reasons: [], published: true });
    expect(build('html', htmlIndex({ ...htmlReport(), extra: { a: 1 } }))).toEqual({ reasons: ['unknown-field'], published: false });
  });

  test('value-free offline: structural S2, the publication scan refuses a record holding an unknown field or kind', () => {
    const publication = (files: Record<string, string>): string => {
      const dir = mkdtempSync(path.join(base, 'pub-'));
      for (const [rel, text] of Object.entries(files)) {
        mkdirSync(path.dirname(path.join(dir, rel)), { recursive: true });
        writeFileSync(path.join(dir, rel), text);
      }
      writeManifest(dir);
      return dir;
    };
    const scan = (dir: string) => scanPublication(dir, [hex(8)], []);
    expect(scan(publication({ 'reports/json-1.json': json(() => {}), 'runner.log': 'free text\n' })), 'a control').toEqual([]);
    expect(scan(publication({ 'reports/json-1.json': json((r) => { r.extraField = { a: 1 }; }) })))
      .toEqual([{ reason: 'unknown-field', place: 'content', depth: 0, area: 'reports' }]);
    expect(scan(publication({ 'reports/json-1.json': '[1]\n' }))).toEqual([{ reason: 'unknown-kind', place: 'content', depth: 0, area: 'reports' }]);
    expect(reasonsOf(scan(publication({ 'html/index.html': htmlIndex({ ...htmlReport(), extra: 1 }) })))).toEqual(['unknown-field']);
    const manifest = publication({});
    writeFileSync(path.join(manifest, 'MANIFEST.json'), JSON.stringify({ ...JSON.parse(readFileSync(path.join(manifest, 'MANIFEST.json'),
      'utf8')), extra: { a: 1 } }));
    expect(scan(manifest)).toEqual([{ reason: 'unknown-field', place: 'content', depth: 0, area: 'manifest' }]);
  });

  test('value-free offline: structural S2, the final check refuses a pulled record holding an unknown field', () => {
    const pull = (report: string): { code: number; out: string } => {
      const root = mkdtempSync(path.join(base, 'root-'));
      mkdirSync(path.join(root, 'pub', 'reports'), { recursive: true });
      writeFileSync(path.join(root, 'pub', 'reports', 'json-1.json'), report);
      writeManifest(path.join(root, 'pub'));
      const v = finalCheck({ root, registries: registries(mkdtempSync(path.join(base, 'reg-')), [hex(8)]), pulls: ['pub/**'] });
      return { code: v.code, out: v.out.toString() };
    };
    expect(pull(json(() => {})), 'a control').toEqual({ code: 0, out: STATUS.clean });
    expect(pull(json((r) => { r.extraField = { a: 1 }; }))).toEqual({ code: 1, out: STATUS.value });
  });

  test('value-free offline: structural S2, a junit report has no closed record schema: the plan refuses it before any worker', () => {
    const out = mkdtempSync(path.join(base, 'plan-'));
    const config = reporterConfig(out, [[VALUE_FREE_REPORTER], ['junit', { outputFile: path.join(out, 'junit.xml') }]]);
    const plan = planPublication(config, { configDir: E2E_DIR }, path.join(out, 'value-free-publish'), [E2E_DIR]);
    expect(plan.reasons).toContainEqual(expect.objectContaining({ reason: 'unresolvable-reporter', why: 'it has no closed record schema' }));
    expect(plan.sources.map((s) => s.kind)).not.toContain('junit');
  });

  // R2 (STRUCTURAL-REVIEW-R2.json S2): runner.log is a record too. Its closed schema admits lines of text (the same kind as
  // a title, a message or a stack in every other record); a document in the runner output (a JSON object or array that
  // opens at the start of a line and closes at the end of one: one line, a block of lines or the whole output) or an
  // embedded zip is a record the runner log admits no kind for, and refuses, at assembly, in the publication scan, in the
  // final check and in the output printed at the runner's exit.
  const RUNNER_OUTPUT_REPORT = 'it writes its report to the runner output';
  const recordLine = () => JSON.stringify({ suites: [], config: {}, [`f${hex(4)}`]: { kind: hex(4) } });
  const LINES = 'value-free: 2 tests. The output is held.\n[1/2] [chromium] › a.spec.ts:3:1 › a title\n' +
    '  Error: API 599 on /x: {"detail":"y"}\n{ unquoted: 1 }\n[2/2] done\n';

  test('value-free offline: structural S2 (R2), the builder publishes runner.log only as lines of text: a document in it refuses',
    () => {
      const log = (text: string) => {
        const at = mkdtempSync(path.join(base, 'log-'));
        const builder = new Builder(at, new ValueFree(hex(32)));
        builder.runnerLog(text);
        return { reasons: builder.reasons.map((r) => [r.reason, r.area]), published: existsSync(path.join(at, 'runner.log')) };
      };
      expect(log(LINES), 'a control: lines of text, a fragment inside one included').toEqual({ reasons: [], published: true });
      const refused = { reasons: [['unknown-kind', 'runner-log']], published: false };
      expect(log(`${LINES}${recordLine()}\n`), 'one line that is a document').toEqual(refused);
      expect(log(`${LINES}${JSON.stringify({ a: { b: [hex(4)] } }, null, 2)}\n[2/2] done\n`), 'a block of lines that is one document')
        .toEqual(refused);
      expect(log(JSON.stringify([{ kind: hex(4) }], null, 2)), 'the whole output one document').toEqual(refused);
      expect(log(`${LINES}  ${recordLine()}  \n`), 'a document indented on its line').toEqual(refused);
      expect(log(`${LINES}data:application/zip;base64,${writeZip([{ name: 'report.json', data: Buffer.from('{}') }])
        .toString('base64')}\n`), 'an embedded zip').toEqual(refused);
    });

  test('value-free offline: structural S2 (R2), the publication scan and the final check read runner.log against its closed schema',
    () => {
      const publication = (log: string, root = mkdtempSync(path.join(base, 'pub-'))): string => {
        writeFileSync(path.join(root, 'runner.log'), log);
        writeManifest(root);
        return root;
      };
      const scan = (dir: string) => scanPublication(dir, [hex(8)], []);
      expect(scan(publication(LINES)), 'a control').toEqual([]);
      expect(scan(publication(`${LINES}${recordLine()}\n`)))
        .toEqual([{ reason: 'unknown-kind', place: 'content', depth: 0, area: 'runner-log' }]);
      expect(scan(publication(`${LINES}${JSON.stringify({ a: [hex(4)] }, null, 2)}\n`)), 'a block of lines')
        .toEqual([{ reason: 'unknown-kind', place: 'content', depth: 0, area: 'runner-log' }]);
      const pull = (log: string): { code: number; out: string } => {
        const root = mkdtempSync(path.join(base, 'root-'));
        mkdirSync(path.join(root, 'pub'));
        publication(log, path.join(root, 'pub'));
        const v = finalCheck({ root, registries: registries(mkdtempSync(path.join(base, 'reg-')), [hex(8)]), pulls: ['pub/**'] });
        return { code: v.code, out: v.out.toString() };
      };
      expect(pull(LINES), 'a control').toEqual({ code: 0, out: STATUS.clean });
      expect(pull(`${LINES}${recordLine()}\n`)).toEqual({ code: 1, out: STATUS.value });
    });

  test('value-free offline: structural S2 (R2), a json or junit report written to the runner output: the plan refuses it before any worker',
    () => {
      const out = mkdtempSync(path.join(base, 'plan-'));
      const plan = (...reporters: unknown[][]) =>
        planPublication(reporterConfig(out, [[VALUE_FREE_REPORTER], ...reporters]), { configDir: E2E_DIR }, path.join(out, 'value-free-publish'),
          [E2E_DIR]);
      for (const kind of ['json', 'junit']) {
        expect(plan([kind]).reasons, `${kind} with no output file`)
          .toContainEqual(expect.objectContaining({ reason: 'unresolvable-reporter', why: RUNNER_OUTPUT_REPORT }));
      }
      expect(plan(['line'], ['list'], ['dot']).reasons, 'a control: reporters that print lines').toEqual([]);
    });

  test('value-free offline: structural S2 (R2), the output printed at the runner exit is read as runner.log: a document refuses then',
    () => {
      const vf = new ValueFree(hex(32));
      const assembled = () => {
        const out = mkdtempSync(path.join(base, 'exit-'));
        const plan = { publishDir: path.join(out, 'value-free-publish'), writable: true, sources: [], reasons: [] };
        return { plan, assembly: assemblePublication({ plan, vf, runnerLog: LINES, reasons: [] }) };
      };
      const clean = assembled();
      expect(clean.assembly.refused, 'a control: assembled').toBe(false);
      expect(confirmAtExit(clean.plan, clean.assembly, vf, LINES).refused, 'a control: lines of text').toBe(false);
      const late = assembled();
      expect(late.assembly.refused, 'a control: assembled').toBe(false);
      const verdict = confirmAtExit(late.plan, late.assembly, vf, `${LINES}${recordLine()}\n`);
      expect(verdict.refused, 'a document in the printed output').toBe(true);
      expect(verdict.reasons).toContainEqual(expect.objectContaining({ reason: 'unknown-kind', area: 'runner-output' }));
      expect(readdirSync(late.plan.publishDir), 'only the refusal record').toEqual(['REFUSED.json']);
    });
});

// ---------------------------------------------------------------------------------------------------------- S3
test.describe('value-free offline: structural S3, nothing provisions without the admission the reporter grants', () => {
  test('value-free offline: structural S3, a marker set from outside is no admission', async () => {
    await inRun(async () => {
      setEnv({ [VALUE_FREE_REPORTER_ENV]: '1' });
      expect(() => assertValueFreeRun(OFF), 'a pre-set reporter marker').toThrow(/not admitted/);
      setEnv({ [ADMISSION_ENV]: hex(32) });
      expect(() => assertValueFreeRun(OFF), 'a pre-set marker and an admission proof of no runner').toThrow(/not admitted/);
    });
  });

  test('value-free offline: structural S3, no provisioning entry point sends anything without admission', async () => {
    await inRun(async () => {
      setEnv({ [VALUE_FREE_REPORTER_ENV]: '1' });
      const calls: string[] = [];
      await expect(provisionActor(stubRequest(calls), `s3-${hex(6)}`), 'provisionActor: refused before signup').rejects.toThrow(/not admitted/);
      await expect(new ApiClient(stubRequest(calls), 'http://127.0.0.1:9').post('/accounts/signup/', {}), 'the API client')
        .rejects.toThrow(/not admitted/);
      expect(calls, 'nothing was sent').toEqual([]);
    });
  });

  test('value-free offline: structural S3, a refused publication plan throws in onBegin, before any worker, and admits nothing',
    async ({}, testInfo) => {
      await inRun(async () => {
        const out = privatePath(testInfo, 'refused');
        mkdirSync(out, { recursive: true });
        // A json report with a source extension: the plan refuses it (value-free-publish.ts planPublication).
        const config = reporterConfig(out, [[VALUE_FREE_REPORTER], ['json', { outputFile: path.join(out, 'report.ts') }]]);
        const reporter = new ValueFreeReporter({ configDir: E2E_DIR });
        expect(() => reporter.onBegin(config, emptySuite()), 'the refusal throws now, never only at onEnd').toThrow(/REFUSED/);
        expect(() => assertValueFreeRun(OFF), 'the marker the first reporter set is no admission').toThrow(/not admitted/);
        const calls: string[] = [];
        await expect(provisionActor(stubRequest(calls), `s3-${hex(6)}`)).rejects.toThrow(/not admitted/);
        expect(calls, 'a refusal stops before signup').toEqual([]);
      });
    });

  test('value-free offline: structural S3, a control: the first reporter with a clean plan admits, and provisioning proceeds',
    async ({}, testInfo) => {
      await inRun(async () => {
        const out = privatePath(testInfo, 'admitted');
        mkdirSync(out, { recursive: true });
        const reporter = new ValueFreeReporter({ configDir: E2E_DIR });
        reporter.onBegin(reporterConfig(out, [[VALUE_FREE_REPORTER], ['line']]), emptySuite());
        expect(process.env[VALUE_FREE_REPORTER_ENV]).toBe('1');
        expect(() => assertValueFreeRun(OFF)).not.toThrow();
        const calls: string[] = [];
        await expect(provisionActor(stubRequest(calls), `s3-${hex(6)}`), 'it reaches the stub, which answers 599').rejects.toThrow(/API 599/);
        expect(calls).toEqual(['post /accounts/signup/']);
      });
    });

  // R2 (STRUCTURAL-REVIEW-R2.json S3): worker mode is selected by the environment (TEST_WORKER_INDEX), and every input of
  // the proof a worker accepted before R2 is caller-chosen (the run salt, the registry, the parent's pid). This test runs in
  // a worker of this harness's runner, which is not a value-free runner and admitted nothing: with the marker, the salt, the
  // registry and that proof all set from here, nothing provisions. Its runner answers no admission query, so the worker
  // waits its bound before it refuses: the test is slower for that.
  test('value-free offline: structural S3 (R2), a worker marker, salt, registry and a proof for its parent set from outside are no admission',
    async () => {
      test.setTimeout(90_000);
      expect(process.env.TEST_WORKER_INDEX, 'a precondition: this process is in worker mode by its environment').toBeDefined();
      await inRun(async () => {
        const forged = createHmac('sha256', process.env[VALUE_FREE_SALT_ENV]!)
          .update(`value-free publication admitted\0${process.ppid}\0${process.env[VALUE_FREE_REGISTRY_ENV]}`).digest('hex');
        setEnv({ [VALUE_FREE_REPORTER_ENV]: '1', [ADMISSION_ENV]: forged });
        expect(() => assertValueFreeRun(OFF), 'the fixture').toThrow(/not admitted/);
        const calls: string[] = [];
        await expect(provisionActor(stubRequest(calls), `s3-${hex(6)}`), 'provisionActor').rejects.toThrow(/not admitted/);
        await expect(new ApiClient(stubRequest(calls), 'http://127.0.0.1:9').post('/accounts/signup/', {}), 'the API client')
          .rejects.toThrow(/not admitted/);
        expect(() => assertValueFreeRun(OFF), 'the fixture, after the worker asked').toThrow(/not admitted/);
        expect(calls, 'nothing was sent').toEqual([]);
      });
    });
});

// R2 (S3) controls, green before and after the fix: a real value-free child run (value-free/admission.config.ts, one
// worker, no stack, no network). With lib/value-free-reporter.ts first, its runner admits the publication and its worker's
// provisionActor reaches the stub; listed after line, the runner admits nothing and nothing reaches it.
test.describe('value-free offline: structural S3 (R2), a worker is admitted only through its own runner', () => {
  test.afterAll(() => killChildren());
  for (const mode of ['first', 'late'] as const) {
    test(`value-free offline: structural S3 (R2), a control: the value-free reporter ${mode}, its worker ${mode === 'first'
      ? 'provisions' : 'provisions nothing'}`, async ({}, testInfo) => {
      test.setTimeout(240_000);
      const run = await runChild(`admission-${mode}`, privatePath(testInfo, 'child'), { file: 'admission.child.ts', valueFree: true,
        probes: {}, config: ADMISSION_CONFIG, env: { E2E_VALUE_FREE_ADMISSION_MODE: mode } });
      expect(run.results, 'the child run published its json report').not.toBeNull();
      expect(resultOf(run, ADMISSION_TITLE).status, 'the child test (its assertions are the property)').toBe('passed');
      expect(run.code, 'the child run').toBe(0);
    });
  }
});
