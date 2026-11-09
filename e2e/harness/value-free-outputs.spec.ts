import { randomBytes, randomUUID } from 'node:crypto';
import { existsSync, linkSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { test, expect } from '@playwright/test';
import type { FullConfig, Suite } from '@playwright/test/reporter';
import { refusalMessage, sharedValueFree, ValueFree, ValueFreeRefusal, VALUE_FREE_ENV, VALUE_FREE_MIN_LENGTH,
  VALUE_FREE_REGISTRY_ENV, VALUE_FREE_REPORTER, VALUE_FREE_REPORTER_ENV, VALUE_FREE_SALT_ENV } from '../lib/value-free';
import ValueFreeReporter from '../lib/value-free-reporter';
import { existingParent, outputTargets, renameTarget, scrubTree } from '../lib/value-free-scrub';
import { checkBeforePull, refusalReasons } from '../lib/value-free-seal.mjs';
import { privatePath, removePrivate } from './value-free/private-dir';
import { E2E_DIR } from './value-free/scan';

// Fixtures live in a private directory per test, outside the evidence tree (value-free/private-dir.ts).
test.afterEach(async ({}, testInfo) => removePrivate(testInfo));

// HR-JRN-01 round 3, offline (no stack): what value-free mode does with the escapes that split
// a value, a value too short to hide, every place a reporter writes and the names of what it
// writes. The live half is value-free-live.spec.ts. Every sample value is generated at run time.
const hex = (bytes: number) => randomBytes(bytes).toString('hex');
function setEnv(vars: Record<string, string | undefined>): Record<string, string | undefined> {
  const previous = Object.fromEntries(Object.keys(vars).map((k) => [k, process.env[k]]));
  for (const [k, v] of Object.entries(vars)) if (v === undefined) delete process.env[k]; else process.env[k] = v;
  return previous;
}

test('value-free offline: colon-form colours, OSC hyperlinks, C1 and charset escapes, raw and as JSON writes them', () => {
  const vf = new ValueFree(hex(32));
  const id = randomUUID();
  const [head, middle, tail] = [id.slice(0, 9), id.slice(9, 13), id.slice(13)];
  const forms = [
    `${head}\u001b[38:2:255:0:0m${middle}\u001b[0m${tail}`,
    `${head}\u001b]8;;https://example.invalid/\u0007${middle}\u001b]8;;\u0007${tail}`,
    `${head}\u001b]8;;https://example.invalid/\u001b\\${middle}\u001b]8;;\u001b\\${tail}`,
    `${head}\u009b31m${middle}\u001b(B${tail}`,
    `${head}\u001b[1A\u001b[2K${middle}\u001b[?25l${tail}`,
  ];
  for (const form of forms) {
    expect(vf.text(`log ${form}`), JSON.stringify(form)).toBe(`log ${vf.token(id)}`);
    expect(vf.text(JSON.stringify(form)), `${JSON.stringify(form)} as JSON`).toBe(`"${vf.token(id)}"`);
  }
  expect(vf.text(JSON.stringify('\\u001b[31m')), 'an escaped backslash is text, not an escape').toBe(JSON.stringify('\\u001b[31m'));
});

test('value-free offline: a value too short to hide refuses the run, by its length only, and every process sees it', () => {
  const salt = hex(32);
  const dir = mkdtempSync(path.join(os.tmpdir(), 'e2e-vf-offline-'));
  try {
    const worker = new ValueFree(salt, dir);
    const short = `Q${hex(2).slice(0, VALUE_FREE_MIN_LENGTH - 2)}`;
    const long = `Probe organization ${hex(6)}`;
    expect(short.length).toBe(VALUE_FREE_MIN_LENGTH - 1);
    let thrown: unknown;
    try { worker.registerAs('workspace name', long, short, ''); } catch (error) { thrown = error; }
    expect(thrown).toBeInstanceOf(ValueFreeRefusal);
    const message = (thrown as Error).message;
    expect(message).toContain(`a workspace name of ${short.length} characters`);
    expect(message).toContain(`shorter than ${VALUE_FREE_MIN_LENGTH} characters`);
    expect(message, 'the refusal never quotes the value').not.toContain(short);
    const runner = new ValueFree(salt, dir);
    expect(runner.text(`org ${long}`), 'the long value of the same call is registered').toBe(`org ${worker.token(long)}`);
    expect(runner.refusals()).toEqual([{ refused: short.length, what: 'workspace name', at: expect.stringMatching(/^value-free-outputs\.spec\.ts:\d+$/) }]);
    expect(worker.refusals(), 'the registering process reads its own refusal back once').toHaveLength(1);
    const registry = readdirSync(dir).map((f) => readFileSync(path.join(dir, f), 'utf8')).join('');
    expect(registry, 'the registry records a length, never the short value').not.toContain(short);
    const said = refusalMessage(runner.refusals());
    expect(said).toMatch(/^value-free: REFUSED\. a workspace name of 5 characters \(value-free-outputs\.spec\.ts:\d+\)/);
    expect(said).not.toContain(short);
    expect(() => worker.register(''), 'an empty value hides nothing').not.toThrow();
    expect(() => new ValueFree(salt).register(short), 'without a registry too').toThrow(ValueFreeRefusal);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('value-free offline: a refused run fails, deletes every output it wrote and publishes only its refusal record',
  async ({}, testInfo) => {
  const run = { salt: hex(32), dir: mkdtempSync(path.join(os.tmpdir(), 'e2e-vf-offline-')) };
  const previous = setEnv({ [VALUE_FREE_ENV]: '1', [VALUE_FREE_SALT_ENV]: run.salt, [VALUE_FREE_REGISTRY_ENV]: run.dir,
    [VALUE_FREE_REPORTER_ENV]: undefined });
  try {
    const out = privatePath(testInfo, 'refused');
    const results = path.join(out, 'test-results');
    const report = path.join(out, `report-${hex(4)}.json`);
    const html = path.join(out, 'html');
    const config = { reporter: [[VALUE_FREE_REPORTER], ['json', { outputFile: report }], ['html', { outputFolder: html }]],
      rootDir: E2E_DIR, configFile: path.join(E2E_DIR, 'playwright.config.ts'), projects: [{ outputDir: results, testDir: E2E_DIR }] };
    const reporter = new ValueFreeReporter({ configDir: E2E_DIR });
    reporter.onBegin(config as unknown as FullConfig, { title: '', tests: [], suites: [] } as unknown as Suite);
    for (const dir of [results, html]) mkdirSync(dir, { recursive: true });
    writeFileSync(path.join(results, 'loose.txt'), 'Qabcd');
    writeFileSync(path.join(html, 'index.html'), '<p>Qabcd</p>');
    writeFileSync(report, '{"title":"Qabcd"}');
    expect(await reporter.onEnd(), 'nothing refused yet').toBeUndefined();
    // Another process of the run (a worker) registers a value too short to hide.
    expect(() => new ValueFree(run.salt, run.dir).registerAs('workspace name', 'Qabcd')).toThrow(ValueFreeRefusal);
    expect(await reporter.onEnd(), 'the run fails').toEqual({ status: 'failed' });
    await reporter.onExit();
    for (const target of [results, report, html]) expect(existsSync(target), path.basename(target)).toBe(false);
    expect(readdirSync(out), 'nothing is left but the publish directory').toEqual(['value-free-publish']);
    const publish = path.join(out, 'value-free-publish');
    expect(readdirSync(publish), 'which holds only the refusal record').toEqual(['REFUSED.json']);
    expect(refusalReasons(publish)).toEqual([{ reason: 'short-value', kind: 'workspace name', length: 5,
      at: expect.stringMatching(/^value-free-outputs\.spec\.ts:\d+$/) }]);
    expect(readFileSync(path.join(publish, 'REFUSED.json'), 'utf8'), 'never the value').not.toContain('Qabcd');
    expect(checkBeforePull(publish).code, 'a refused run: nothing but the record is pulled').toBe(1);
    expect(sharedValueFree().refusals()).toHaveLength(1);
  } finally { setEnv(previous); rmSync(run.dir, { recursive: true, force: true }); }
});

test('value-free offline: every report location Playwright resolves is a scrub target, exactly as it resolves it', () => {
  const configDir = path.join(E2E_DIR, 'harness');
  const base = path.join(os.tmpdir(), `vf-targets-${hex(4)}`);
  const config = (reporter: unknown[]) => ({ reporter, projects: [] }) as unknown as FullConfig;
  const ctx = { configDir, commandHash: '', shard: null };
  const names = ['JSON', 'JUNIT', 'HTML', 'BLOB'].flatMap((k) => ['FILE', 'DIR', 'NAME'].map((w) => `PLAYWRIGHT_${k}_OUTPUT_${w}`));
  const previous = setEnv(Object.fromEntries([...names, 'PLAYWRIGHT_HTML_REPORT', 'PW_TEST_REPORTER', 'PLAYWRIGHT_LAST_RUN_OUTPUT_FILE',
    'E2E_WALK_COUNTS', 'E2E_VALUE_FREE_OUTPUTS'].map((n) => [n, undefined])));
  try {
    // Options against the config directory; html and blob by default next to the nearest package.json; a json report
    // with neither a file nor a name goes to stdout (held by the runner), so it names no file.
    expect(outputTargets(config([['json', { outputFile: 'r.json' }], ['html'], ['blob'], ['json'], ['line']]), ctx)).toEqual({
      dirs: [path.join(E2E_DIR, 'playwright-report'), path.join(E2E_DIR, 'blob-report')],
      files: [path.join(configDir, 'r.json'), path.join(E2E_DIR, 'blob-report', 'report.zip')] });
    // An OUTPUT_FILE wins over the option, whose file is then no target.
    setEnv({ PLAYWRIGHT_JSON_OUTPUT_FILE: path.join(base, 'j.json') });
    expect(outputTargets(config([['json', { outputFile: 'r.json' }]]), ctx).files).toEqual([path.join(base, 'j.json')]);
    // OUTPUT_NAME: in OUTPUT_DIR, else the option's directory, else the config directory; an absolute name as it is.
    setEnv({ PLAYWRIGHT_JSON_OUTPUT_FILE: undefined, PLAYWRIGHT_JSON_OUTPUT_NAME: 'named.json' });
    expect(outputTargets(config([['json']]), ctx).files).toEqual([path.join(configDir, 'named.json')]);
    expect(outputTargets(config([['json', { outputDir: 'reports' }]]), ctx).files).toEqual([path.join(configDir, 'reports', 'named.json')]);
    setEnv({ PLAYWRIGHT_JSON_OUTPUT_DIR: base });
    expect(outputTargets(config([['json', { outputDir: 'reports' }]]), ctx).files).toEqual([path.join(base, 'named.json')]);
    setEnv({ PLAYWRIGHT_JSON_OUTPUT_DIR: undefined, PLAYWRIGHT_JSON_OUTPUT_NAME: path.join(base, 'abs.json') });
    expect(outputTargets(config([['json']]), ctx).files).toEqual([path.join(base, 'abs.json')]);
    // The html and blob directory overrides, the blob name from the command hash and shard, PW_TEST_REPORTER's extra
    // reporter and Playwright's last-run record.
    setEnv({ PLAYWRIGHT_JSON_OUTPUT_NAME: undefined, PLAYWRIGHT_HTML_OUTPUT_DIR: path.join(base, 'html'),
      PLAYWRIGHT_BLOB_OUTPUT_DIR: path.join(base, 'blob'), PW_TEST_REPORTER: 'junit', PLAYWRIGHT_JUNIT_OUTPUT_NAME: 'j.xml',
      PLAYWRIGHT_LAST_RUN_OUTPUT_FILE: path.join(base, 'last.json') });
    expect(outputTargets(config([['html', { outputFolder: 'unused' }], ['blob']]),
      { ...ctx, commandHash: 'a b', shard: { current: 2, total: 10 } })).toEqual({
      dirs: [path.join(base, 'html'), path.join(base, 'blob')],
      files: [path.join(base, 'blob', 'report-a-b-02.zip'), path.join(configDir, 'j.xml'), path.join(base, 'last.json')] });
    setEnv({ PLAYWRIGHT_HTML_OUTPUT_DIR: undefined, PLAYWRIGHT_HTML_REPORT: path.join(base, 'legacy'),
      PLAYWRIGHT_BLOB_OUTPUT_FILE: path.join(base, 'b.zip'), PW_TEST_REPORTER: undefined, PLAYWRIGHT_LAST_RUN_OUTPUT_FILE: undefined });
    expect(outputTargets(config([['html'], ['blob', { outputDir: 'unused' }]]), ctx))
      .toEqual({ dirs: [path.join(base, 'legacy')], files: [path.join(base, 'b.zip')] });
    // A custom reporter: the options it is given for where it writes.
    setEnv({ PLAYWRIGHT_HTML_REPORT: undefined, PLAYWRIGHT_BLOB_OUTPUT_FILE: undefined });
    expect(outputTargets(config([['./custom.cjs', { outputFile: 'c.txt', outputFolder: 'c' }]]), ctx))
      .toEqual({ dirs: [path.join(configDir, 'c')], files: [path.join(configDir, 'c.txt')] });
  } finally { setEnv(previous); }
});

test('value-free offline: a target\'s own name and every directory the run made on the way to it are renamed', async ({}, testInfo) => {
  const vf = new ValueFree(hex(32));
  const id = randomUUID();
  const prefix = hex(4);
  vf.register(prefix);
  const base = privatePath(testInfo, 'rename');
  const before = path.join(base, `there-${id}`);
  mkdirSync(before, { recursive: true });
  const file = path.join(base, `report-${prefix}.json`);
  const nested = path.join(base, `made-${id}`, `deeper-${prefix}`, 'r.json');
  const kept = path.join(before, 'inside.json');
  // When the run begins: the deepest directory that exists on each target's path.
  const roots = [file, nested, kept].map((t) => existingParent(t));
  expect(roots).toEqual([base, base, before]);
  writeFileSync(file, JSON.stringify({ title: `key ${prefix}` }));
  mkdirSync(path.dirname(nested), { recursive: true });
  writeFileSync(nested, `owner ${id}`);
  writeFileSync(kept, `owner ${id}`);
  const targets = [file, nested, kept];
  for (const target of targets) scrubTree(target, vf, true);
  targets.forEach((target, i) => renameTarget(target, roots[i], vf));
  expect(readdirSync(base).sort(), 'the made directory and the file renamed; the one that was there kept')
    .toEqual([`made-${vf.token(id)}`, `report-${vf.token(prefix)}.json`, `there-${id}`].sort());
  expect(readFileSync(path.join(base, `report-${vf.token(prefix)}.json`), 'utf8')).toBe(JSON.stringify({ title: `key ${vf.token(prefix)}` }));
  expect(readFileSync(path.join(base, `made-${vf.token(id)}`, `deeper-${vf.token(prefix)}`, 'r.json'), 'utf8')).toBe(`owner ${vf.token(id)}`);
  expect(readFileSync(kept, 'utf8')).toBe(`owner ${vf.token(id)}`);
  const snapshot = () => readdirSync(base, { recursive: true }).map(String).sort();
  const once = snapshot();
  targets.forEach((target, i) => renameTarget(target, roots[i], vf));
  expect(snapshot(), 'a second pass changes nothing').toEqual(once);
});

test('value-free offline: the on-disk scrub removes links and hard links, never rewriting what they point to', async ({}, testInfo) => {
  const vf = new ValueFree(hex(32));
  const value = `Probe organization ${hex(6)}`;
  vf.register(value);
  const outside = privatePath(testInfo, 'outside.txt');
  writeFileSync(outside, value);
  const dir = privatePath(testInfo, 'results');
  mkdirSync(dir, { recursive: true });
  linkSync(outside, path.join(dir, 'hard.txt'));
  symlinkSync(outside, path.join(dir, 'soft.txt'));
  writeFileSync(path.join(dir, 'own.txt'), value);
  scrubTree(dir, vf, true);
  expect(readFileSync(outside, 'utf8'), 'the file outside is untouched').toBe(value);
  expect(readdirSync(dir).sort()).toEqual(['own.txt']);
  expect(readFileSync(path.join(dir, 'own.txt'), 'utf8')).toBe(vf.token(value));
});
