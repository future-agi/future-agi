import { randomBytes, randomUUID } from 'node:crypto';
import { existsSync, linkSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, statSync, symlinkSync,
  writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import os from 'node:os';
import path from 'node:path';
import { Readable } from 'node:stream';
import { test, expect, type ReporterDescription, type TestInfo } from '@playwright/test';
import type { FullConfig, Suite, TestCase, TestResult, TestStep } from '@playwright/test/reporter';
import { ScopeActors, type ScopeActor } from '../lib/scope-actors';
import { assertValueFreeRun, attachEvidence, bindValueFree, displayedKeys, sharedValueFree, TOKEN_SHAPE, ValueFree,
  ValueFreeRefusal, valueFreeRequested, VALUE_FREE_ENV, VALUE_FREE_REGISTRY_ENV, VALUE_FREE_REPORTER, VALUE_FREE_REPORTER_ENV,
  VALUE_FREE_SALT_ENV, withValueFreeReporter, withValueFreeUse } from '../lib/value-free';
import { STDIO_POINTER } from '../lib/value-free-publish';
import ValueFreeReporter from '../lib/value-free-reporter';
import { guardTargets, readZip, scrubBytes, writeZip } from '../lib/value-free-scrub';
import { checkBeforePull } from '../lib/value-free-seal.mjs';
import { WalkReceipt } from '../lib/walk/receipt';
import { assertValueFreeReceipt } from '../lib/walk/receipt-values.mjs';
import { privatePath, removePrivate } from './value-free/private-dir';
import { E2E_DIR, unzip } from './value-free/scan';

// HR-JRN-01: the value-free publication mode's offline tests (no stack). The live
// half is value-free-live.spec.ts. Every sample value is generated at run time.
// Fixtures live in a private directory per test, outside the evidence tree (value-free/private-dir.ts); a walk receipt
// is evidence by design and stays in the test's output directory.
test.afterEach(async ({}, testInfo) => removePrivate(testInfo));

const PROBE = 'hr-jrn-01-probe';
const hex = (bytes: number) => randomBytes(bytes).toString('hex');
const b64 = () => randomBytes(12).toString('base64url');
function sample() {
  const run = `e2e-h1-0-${Date.now().toString(36)}`;
  return { run, email: `${run}-member@futureagi.com`, id: randomUUID(), key: hex(16), jwt: `eyJ${b64()}.${b64()}.${b64()}`,
    org: `Probe organization ${hex(6)}` };
}
const highlight = (id: string) => `${id.slice(0, 9)}\u001b[7m${id.slice(9, 13)}\u001b[27m${id.slice(13)}`;
function setEnv(vars: Record<string, string | undefined>): Record<string, string | undefined> {
  const previous = Object.fromEntries(Object.keys(vars).map((k) => [k, process.env[k]]));
  for (const [k, v] of Object.entries(vars)) if (v === undefined) delete process.env[k]; else process.env[k] = v;
  return previous;
}
/** The environment a value-free runner gives its workers: the switch, the run salt and a 0700 registry. */
async function inRun<T>(body: (vf: ValueFree, run: { salt: string; dir: string }) => Promise<T> | T): Promise<T> {
  const run = { salt: hex(32), dir: mkdtempSync(path.join(os.tmpdir(), 'e2e-vf-offline-')) };
  const previous = setEnv({ [VALUE_FREE_ENV]: '1', [VALUE_FREE_SALT_ENV]: run.salt, [VALUE_FREE_REGISTRY_ENV]: run.dir,
    [VALUE_FREE_REPORTER_ENV]: undefined });
  try { return await body(sharedValueFree(), run); } finally { setEnv(previous); rmSync(run.dir, { recursive: true, force: true }); }
}
const yazl = (createRequire(import.meta.url)('playwright-core/lib/utilsBundle') as { yazl: any }).yazl;
/** A zip as Playwright writes them: a buffer entry and a stream entry (data descriptor), deflated and stored. */
function yazlZip(entries: { name: string; data: Buffer; compress: boolean; stream?: boolean }[]): Promise<Buffer> {
  const zip = new yazl.ZipFile();
  for (const e of entries) {
    if (e.stream) zip.addReadStream(Readable.from([e.data]), e.name, { compress: e.compress });
    else zip.addBuffer(e.data, e.name, { compress: e.compress });
  }
  zip.end();
  const chunks: Buffer[] = [];
  return new Promise((resolve, reject) => zip.outputStream.on('data', (c: Buffer) => chunks.push(c))
    .on('end', () => resolve(Buffer.concat(chunks))).on('error', reject));
}

test('value-free offline: a token is keyed by kind, stable under one salt and different under the next', () => {
  const s = sample();
  const salt = hex(32);
  const [one, sameRun, nextRun] = [new ValueFree(salt), new ValueFree(salt), new ValueFree(hex(32))];
  expect(one.segment, 'the run segment: five letters the salt picks').toMatch(/^[ghjkmnqtvwyz]{5}$/);
  expect(sameRun.segment).toBe(one.segment);
  for (const [kind, raw] of [['email', s.email], ['id', s.id], ['key', s.key], ['jwt', s.jwt], ['run', s.run], ['value', PROBE]]) {
    const token = one.token(raw);
    expect(token).toMatch(new RegExp(`^vf-${one.segment}-${kind}-[0-9a-f]{16}$`));
    expect(token).toMatch(TOKEN_SHAPE);
    expect(token.length, 'under the receipt guard\'s 32-character opaque-material threshold').toBeLessThan(32);
    expect(sameRun.token(raw), 'another worker of the same run').toBe(token);
    expect(nextRun.token(raw), 'the next run').not.toBe(token);
    expect(one.text(token), 'a token is never re-tokenized').toBe(token);
  }
  expect(() => new ValueFree(''), 'no process-local salt').toThrow(/run salt/);
});

test('value-free offline: shapes first, then registered values, colours removed, shared through the run registry', () => {
  const s = sample();
  const vf = new ValueFree(hex(32)).register('org-compat-a', 'quoted "name"');
  const digest = `sha256:${hex(32)}`;
  const head = hex(20);
  const out = vf.json({ [s.id]: { email: s.email, path: `/accounts/workspace/${s.id}/members/role/`, key: s.key,
    auth: `Bearer ${s.jwt}`, workspace: `${s.run}-empty`, org: 'org-compat-a', other: 'org-compat-b',
    quoted: 'quoted "name"', digest, head } });
  expect(out).toEqual({ [vf.token(s.id)]: { email: vf.token(s.email), path: `/accounts/workspace/${vf.token(s.id)}/members/role/`,
    key: vf.token(s.key), auth: `Bearer ${vf.token(s.jwt)}`, workspace: `${vf.token(s.run)}-empty`, org: vf.token('org-compat-a'),
    other: 'org-compat-b', quoted: vf.token('quoted "name"'), digest, head } });
  // A UUID split by colour codes, raw and as JSON writes it.
  expect(vf.text(`log ${highlight(s.id)}`)).toBe(`log ${vf.token(s.id)}`);
  expect(vf.text(JSON.stringify(`\u001b[31m${highlight(s.id)}\u001b[39m`))).toBe(`"${vf.token(s.id)}"`);
  // A registered prefix or local part never changes the token of the email that contains it.
  vf.register(s.run, `${s.run}-member`);
  expect(vf.text(s.email)).toBe(vf.token(s.email));
  expect(vf.text(`login ${s.run}-member`), 'the local part alone, whole').toBe(`login ${vf.token(`${s.run}-member`)}`);
  // Displayed keys: the whole key keeps its own token, and its prefix and masked form get theirs.
  const [prefix, masked] = displayedKeys(s.key);
  expect(masked).toBe(`${s.key.slice(0, 4)}**********${s.key.slice(-4)}`);
  vf.register(s.key, prefix, masked);
  expect(vf.text(`${s.key} ${prefix} ${masked}`)).toBe(`${vf.token(s.key)} ${vf.token(prefix)} ${vf.token(masked)}`);

  // Two processes of one run: what one registers, the other scrubs.
  const salt = hex(32);
  const dir = mkdtempSync(path.join(os.tmpdir(), 'e2e-vf-offline-'));
  try {
    const worker = new ValueFree(salt, dir).register(s.org);
    const runner = new ValueFree(salt, dir);
    expect(runner.text(`org: ${s.org}`)).toBe(`org: ${worker.token(s.org)}`);
    const [file] = readdirSync(dir);
    expect(statSync(path.join(dir, file)).mode & 0o777, 'the registry file is private').toBe(0o600);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('value-free offline: one switch; the explicit option alone, an opt-out and a missing run salt are refused', async () => {
  expect(process.env[VALUE_FREE_ENV], 'this suite runs with the switch unset').toBeUndefined();
  expect(() => valueFreeRequested(true)).toThrow('value-free mode is one switch: set E2E_VALUE_FREE=1 for the run');
  expect(() => new ScopeActors('compat', {}, { valueFree: true })).toThrow(/one switch/);
  expect(valueFreeRequested(false)).toBe(false);
  expect(valueFreeRequested()).toBe(false);
  expect(() => sharedValueFree()).toThrow(/creates the run salt/);
  await inRun(() => {
    expect(() => valueFreeRequested(false)).toThrow(/cannot opt out/);
    expect(valueFreeRequested(true)).toBe(true);
    expect(new ScopeActors('compat').valueFree).toBe(sharedValueFree());
  });
});

test('value-free offline: off, ScopeActors evidence, attachEvidence and the reporter list are exactly as before', async ({}, testInfo) => {
  const actor = (label: string): ScopeActor => ({ userId: randomUUID(), email: `compat-${label}@futureagi.com`,
    tokens: { access: 'synthetic-access', refresh: 'synthetic-refresh' }, organizationId: randomUUID(),
    workspaceId: randomUUID(), api: undefined as never });
  const actors = { ownerA: actor('a'), ownerB: actor('b'), member: actor('member'), viewer: actor('viewer') };
  const emptyWorkspace = { id: randomUUID(), name: `compat-empty-${hex(4)}` };
  const key = hex(16);
  const build = (options?: { valueFree?: boolean }) => {
    const scopes = options ? new ScopeActors('compat', {}, options) : new ScopeActors('compat');
    Object.assign(scopes, actors, { emptyWorkspace });
    scopes.owners.push({ ...actors.ownerA, password: 'synthetic-only', apiKey: key, secretKey: hex(16) });
    scopes.receipts.push({ method: 'GET', path: `/accounts/workspace/${emptyWorkspace.id}/members/`, status: 200,
      organizationId: actors.ownerA.organizationId, workspaceId: emptyWorkspace.id, authenticated: true });
    return scopes;
  };
  const off = build();
  expect(off.valueFree).toBeNull();
  const legacy = { prefix: 'compat', emptyWorkspace, limitations: [], actors: Object.values(actors).map(
    ({ userId, email, organizationId, workspaceId }) => ({ userId, email, organizationId, workspaceId })), requests: off.receipts };
  expect(JSON.stringify(off.evidence())).toBe(JSON.stringify(legacy));
  expect(off.evidence().requests, 'the same objects, untouched').toBe(off.receipts);
  const calls: unknown[][] = [];
  const info = { attach: async (...args: unknown[]) => { calls.push(args); }, project: testInfo.project } as unknown as TestInfo;
  const options = { contentType: 'application/json', body: JSON.stringify({ email: actors.ownerA.email }) };
  await attachEvidence(info, 'plain', options);
  expect(calls).toEqual([['plain', options]]);
  expect(calls[0][1], 'off passes the caller\'s own options object').toBe(options);
  const reporters: ReporterDescription[] = [['line']];
  expect(withValueFreeReporter(reporters)).toBe(reporters);
  const use = { baseURL: 'http://localhost:1', trace: 'retain-on-failure', screenshot: 'only-on-failure', video: 'off' } as const;
  expect(withValueFreeUse(use), 'off: the config\'s own use object, untouched').toBe(use);

  await inRun(async (vf) => {
    expect(withValueFreeUse(use), 'on: pixels and traces off, everything else kept')
      .toEqual({ baseURL: 'http://localhost:1', trace: 'off', screenshot: 'off', video: 'off' });
    const on = build({ valueFree: true });
    const published = JSON.stringify(on.evidence());
    const raws = [...Object.values(actors).flatMap((a) => [a.userId, a.email, a.organizationId, a.workspaceId]),
      emptyWorkspace.id, emptyWorkspace.name, 'compat'];
    for (const raw of raws) expect(published).not.toContain(raw);
    expect(Object.keys(on.evidence())).toEqual(Object.keys(legacy));
    expect(on.evidence().actors[0].email).toBe(vf.token(actors.ownerA.email));
    // Names and displayed keys are registered, so any later text that quotes them is scrubbed.
    const quoted = vf.text(JSON.stringify({ workspace: emptyWorkspace.name, shown: displayedKeys(key) }));
    for (const raw of [emptyWorkspace.name, ...displayedKeys(key)]) expect(quoted).not.toContain(raw);

    // This test's fixtures live in its private directory (value-free/private-dir.ts), and that directory is the bound
    // TestInfo's output directory: "a file under the test output directory" reads exactly as before.
    const bound = { attach: async (...args: unknown[]) => { calls.push(args); },
      project: { ...testInfo.project, outputDir: privatePath(testInfo) } } as unknown as TestInfo;
    bindValueFree(bound, vf);
    calls.length = 0;
    await attachEvidence(bound, 'scrubbed', options);
    await attachEvidence(bound, 'shot', { contentType: 'image/png', body: randomBytes(8) });
    const source = privatePath(testInfo, 'source.json');
    writeFileSync(source, JSON.stringify({ name: emptyWorkspace.name }));
    await attachEvidence(bound, 'source', { path: source });
    const binary = privatePath(testInfo, 'source.bin');
    writeFileSync(binary, Buffer.from([0, 1, 2]));
    await attachEvidence(bound, 'binary', { path: binary });
    expect(calls).toEqual([
      ['scrubbed', { contentType: 'application/json', body: JSON.stringify({ email: vf.token(actors.ownerA.email) }) }],
      ['shot', { contentType: 'text/plain', body: 'withheld in value-free mode: image/png' }],
      ['source', { contentType: 'application/json', body: JSON.stringify({ name: vf.token(emptyWorkspace.name) }) }],
      ['binary', { contentType: 'text/plain', body: 'withheld in value-free mode: application/octet-stream' }]]);
    expect(readFileSync(source, 'utf8'), 'the source file is rewritten in place').toBe(JSON.stringify({ name: vf.token(emptyWorkspace.name) }));
    expect(existsSync(binary), 'a binary source is deleted').toBe(false);
    const outside = path.join(os.tmpdir(), `vf-outside-${hex(4)}.txt`);
    await expect(attachEvidence(bound, 'outside', { path: outside }), 'a file outside the output directory is refused')
      .rejects.toThrow(/under the test output directory/);
    // A link or a hard link under the output directory is refused too, and its target is never rewritten.
    const target = privatePath(testInfo, 'target.json');
    writeFileSync(target, JSON.stringify({ name: emptyWorkspace.name }));
    symlinkSync(target, privatePath(testInfo, 'link.json'));
    linkSync(target, privatePath(testInfo, 'hard.json'));
    for (const link of ['link.json', 'hard.json']) {
      await expect(attachEvidence(bound, link, { path: privatePath(testInfo, link) })).rejects.toThrow(/not a link, a hard link/);
    }
    expect(readFileSync(target, 'utf8')).toBe(JSON.stringify({ name: emptyWorkspace.name }));
  });
});

test('value-free offline: the reporter rewrites titles, errors, steps, logs and attachments in place, then every output',
  async ({}, testInfo) => {
    expect(() => new ValueFreeReporter(), 'listed without the switch, it refuses the run').toThrow(/E2E_VALUE_FREE=1 is not set/);

    await inRun(async (vf, run) => {
      const s = sample();
      const results = privatePath(testInfo, 'outputs', 'test-results');
      mkdirSync(results, { recursive: true });
      const config = (first: string) => ({ reporter: [[first], ['line']], rootDir: E2E_DIR, configFile: path.join(E2E_DIR, 'playwright.config.ts'),
        projects: [{ outputDir: results, testDir: E2E_DIR }] }) as unknown as FullConfig;
      const second = new ValueFreeReporter({ configDir: E2E_DIR });
      second.onBegin(config('line'), { title: '', tests: [], suites: [] } as unknown as Suite);
      expect(process.env[VALUE_FREE_REPORTER_ENV], 'second place does not count').toBeUndefined();
      const reporter = new ValueFreeReporter({ configDir: E2E_DIR });
      expect(reporter.printsToStdio(), 'on, Playwright puts no default printer ahead of it').toBe(true);
      const testCase = { title: `t ${s.id}`, location: { file: `/x/${s.run}.ts` }, _tags: [`@${s.run}`],
        annotations: [{ type: 'skip', description: `gap ${s.id}` }] };
      const suite = { title: `file ${s.email}`, tests: [testCase], suites: [{ title: `describe ${s.id}`, tests: [], suites: [] }] };
      reporter.onBegin(config(VALUE_FREE_REPORTER), suite as unknown as Suite);
      expect(process.env[VALUE_FREE_REPORTER_ENV]).toBe('1');
      expect([suite.title, suite.suites[0].title, testCase.title, testCase.location.file, testCase._tags[0]])
        .toEqual([`file ${vf.token(s.email)}`, `describe ${vf.token(s.id)}`, `t ${vf.token(s.id)}`, `/x/${vf.token(s.run)}.ts`, `@${vf.token(s.run)}`]);

      // A worker of the run registers an org name only it knows; the runner learns it from the registry.
      new ValueFree(run.salt, run.dir).register(s.org);
      const error = { message: `Expected: "${s.email}"`, stack: `Error at /x/${s.id}`, value: `\u001b[31m${highlight(s.id)}\u001b[39m`,
        snippet: `> ${s.org}`, cause: { message: `${s.jwt} ${s.key}` } };
      const text = { name: `att ${s.email}`, contentType: 'application/json', body: Buffer.from(JSON.stringify({ org: s.org })) };
      const png = { name: 'shot', contentType: 'image/png', body: randomBytes(8) };
      const contextFile = path.join(results, 'error-context.md');
      writeFileSync(contextFile, `Expected: "${s.email}"\nReceived: "${s.id}"\n`);
      const binaryFile = path.join(results, 'evidence.bin');
      writeFileSync(binaryFile, Buffer.concat([Buffer.from([0]), Buffer.from(s.email)]));
      const files = [{ name: 'error-context', contentType: 'text/markdown', path: contextFile },
        { name: 'bin', contentType: 'application/octet-stream', path: binaryFile }];
      const step = { title: `step ${s.email}`, error: { message: s.id }, steps: [{ title: s.run, steps: [] }] };
      const result = { errors: [error], error, steps: [step], stdout: [`log ${highlight(s.id)}`], stderr: [Buffer.from(s.org)],
        annotations: [{ type: 'note', description: s.email }], attachments: [text, png, ...files] as object[] };
      reporter.onStepEnd(testCase as unknown as TestCase, result as unknown as TestResult, step as unknown as TestStep);
      reporter.onTestEnd(testCase as unknown as TestCase, result as unknown as TestResult);
      const published = JSON.stringify({ result, testCase }, (_k, v) => (v?.type === 'Buffer' ? Buffer.from(v.data).toString('utf8') : v));
      for (const raw of [s.email, s.id, s.id.slice(13), s.key, s.jwt, s.run, s.org]) expect(published).not.toContain(raw);
      expect(error.message, 'the same token the worker publishes').toBe(`Expected: "${vf.token(s.email)}"`);
      expect(error.value, 'colours removed, then the whole UUID tokenized').toBe(vf.token(s.id));
      expect([result.stdout, result.stderr], 'every report points to runner.log instead of carrying the chunks')
        .toEqual([[STDIO_POINTER], [STDIO_POINTER]]);
      expect(JSON.parse(text.body.toString('utf8')), 'the worker\'s registered name').toEqual({ org: vf.token(s.org) });
      expect(png).toEqual({ name: 'shot', contentType: 'text/plain', body: Buffer.from('withheld in value-free mode: image/png') });
      expect(files[0], 'a text file stays attached by path').toEqual({ name: 'error-context', contentType: 'text/markdown', path: contextFile });
      expect(readFileSync(contextFile, 'utf8')).toBe(`Expected: "${vf.token(s.email)}"\nReceived: "${vf.token(s.id)}"\n`);
      expect(files[1]).toEqual({ name: 'bin', contentType: 'text/plain', body: Buffer.from('withheld in value-free mode: application/octet-stream') });
      expect(existsSync(binaryFile), 'a binary attachment file is deleted').toBe(false);

      // End of run: loose files, a zip as blob writes it, html's embedded zip and a name carrying a UUID.
      const named = path.join(results, `t-${s.id}`);
      mkdirSync(named);
      writeFileSync(path.join(named, 'loose.txt'), s.email);
      writeFileSync(path.join(named, 'shot.png'), Buffer.from([0x89, 0x50, 0, 1]));
      const b64 = (text: string) => Buffer.from(text).toString('base64');
      const jsonl = [{ email: s.email }, { method: 'onAttach', params: { attachments: [{ name: 'a', contentType: 'text/plain', base64: b64(s.org) }] } },
        { method: 'onStdIO', params: { data: b64(s.id), isBase64: true } }].map((m) => JSON.stringify(m)).join('\n');
      const zip = await yazlZip([{ name: 'report.jsonl', data: Buffer.from(`${jsonl}\n`), compress: true, stream: true },
        { name: 'resources/a.dat', data: Buffer.from([0, 1, 2, 3]), compress: false }]);
      writeFileSync(path.join(results, 'report.zip'), zip);
      // The json reporter's shape: attachment bodies and Buffer stdio are base64.
      const jsonReport = { config: {}, suites: [{ specs: [{ tests: [{ results: [{ attachments: [
        { name: 'a', contentType: 'application/json', body: b64(JSON.stringify({ org: s.org })) },
        { name: 'b', contentType: 'image/png', body: b64('\u0000png') }], stdout: [{ buffer: b64(s.email) }] }] }] }] }], stats: {} };
      writeFileSync(path.join(results, 'results.json'), JSON.stringify(jsonReport, null, 2));
      writeFileSync(path.join(results, 'index.html'), `<p>${s.org}</p><template>data:application/zip;base64,${
        (await yazlZip([{ name: 'report.json', data: Buffer.from(JSON.stringify({ title: `t ${s.id}` })), compress: true }])).toString('base64')}</template>`);
      reporter.onEnd();
      expect(readFileSync(path.join(named, 'loose.txt'), 'utf8'), 'onEnd: contents').toBe(vf.token(s.email));
      expect(existsSync(path.join(named, 'shot.png'))).toBe(false);
      await reporter.onExit();
      expect(existsSync(named), 'onExit: names').toBe(false);
      expect(readFileSync(path.join(results, `t-${vf.token(s.id)}`, 'loose.txt'), 'utf8')).toBe(vf.token(s.email));
      const unzipped = await unzip(readFileSync(path.join(results, 'report.zip')));
      expect(unzipped.map((e) => e.name), 'binary entries dropped').toEqual(['report.jsonl']);
      expect(unzipped[0].data.toString('utf8').split('\n').filter(Boolean).map((l) => JSON.parse(l)), 'text entries and their base64 scrubbed')
        .toEqual([{ email: vf.token(s.email) }, { method: 'onAttach', params: { attachments: [{ name: 'a', contentType: 'text/plain',
          base64: b64(vf.token(s.org)) }] } }, { method: 'onStdIO', params: { data: b64(vf.token(s.id)), isBase64: true } }]);
      const rewritten = JSON.parse(readFileSync(path.join(results, 'results.json'), 'utf8'));
      const [textBody, binaryBody] = rewritten.suites[0].specs[0].tests[0].results[0].attachments;
      expect(Buffer.from(textBody.body, 'base64').toString('utf8')).toBe(JSON.stringify({ org: vf.token(s.org) }));
      expect(binaryBody).toEqual({ name: 'b', contentType: 'text/plain', body: b64('withheld in value-free mode: image/png') });
      expect(rewritten.suites[0].specs[0].tests[0].results[0].stdout).toEqual([{ buffer: b64(vf.token(s.email)) }]);
      const html = readFileSync(path.join(results, 'index.html'), 'utf8');
      expect(html).toContain(`<p>${vf.token(s.org)}</p>`);
      const embedded = Buffer.from(/base64,([^<]+)/.exec(html)![1], 'base64');
      expect(await unzip(embedded)).toEqual([{ name: 'report.json', data: Buffer.from(JSON.stringify({ title: `t ${vf.token(s.id)}` })) }]);
      // Round 7: the registry is its creator's to remove (here this test's, in a run the runner's own exit hook): the
      // reporter leaves it, so a consumer that owns it (the walk job) can read it at its final boundary check.
      expect(existsSync(run.dir), 'the reporter leaves the run registry to its creator').toBe(true);
      // The publication: next to the output directory, sealed; the run's on-disk outputs are not in it.
      const publish = privatePath(testInfo, 'outputs', 'value-free-publish');
      expect(readdirSync(publish).sort()).toEqual(['MANIFEST.json', 'runner.log']);
      expect(checkBeforePull(publish).code).toBe(0);
      const before = readFileSync(path.join(results, 'report.zip'));
      await reporter.onExit();
      expect(readFileSync(path.join(results, 'report.zip')).equals(before), 'a second pass changes nothing').toBe(true);
      expect(readdirSync(publish).sort(), 'assembled once').toEqual(['MANIFEST.json', 'runner.log']);
    });
  });

test('value-free offline: the scrub never walks a source tree; its zip codec agrees with yazl and yauzl', async ({}, testInfo) => {
  const scratch = privatePath(testInfo, 'scratch');
  mkdirSync(scratch, { recursive: true });
  const report = path.join(scratch, 'results.json');
  const guarded = guardTargets({ dirs: [E2E_DIR, path.dirname(E2E_DIR), '/', scratch],
    files: [path.join(E2E_DIR, 'lib', 'value-free.ts'), report] }, [E2E_DIR]);
  expect(guarded.dirs).toEqual([scratch]);
  expect(guarded.files).toEqual([report]);
  expect(guarded.refused).toHaveLength(4);
  const entries = [{ name: 'a.json', data: Buffer.from('{"a":1}'.repeat(50)) }, { name: 'b.bin', data: randomBytes(16) }];
  expect(await unzip(writeZip(entries)), 'yauzl reads what the scrub writes').toEqual(entries);
  const yazled = await yazlZip([{ name: 'x.txt', data: Buffer.from('x'.repeat(100)), compress: true, stream: true },
    { name: 'y.txt', data: Buffer.from('y'), compress: false }]);
  expect(readZip(yazled), 'the scrub reads data descriptors').toEqual([{ name: 'x.txt', data: Buffer.from('x'.repeat(100)) },
    { name: 'y.txt', data: Buffer.from('y') }]);
  const vf = new ValueFree(hex(32));
  expect(scrubBytes(Buffer.from([0, 1]), vf), 'a binary cannot be sanitized').toBeNull();
  expect(scrubBytes(yazled.subarray(0, yazled.length - 30), vf), 'nor a zip it cannot read').toBeNull();
});

test('value-free offline: the fixture refuses value-free mode without the reporter first or with pixels on', async ({}, testInfo) => {
  const previous = setEnv({ [VALUE_FREE_REPORTER_ENV]: undefined });
  try { expect(() => assertValueFreeRun({ trace: 'off', screenshot: 'off', video: 'off' })).toThrow(/FIRST reporter/); } finally { setEnv(previous); }
  // HR-JRN-01 structural S3: a reporter marker set from outside is no admission (harness/value-free-structural.spec.ts); the
  // reporter itself, FIRST and with a clean publication plan, admits the run before any worker.
  await inRun(async () => {
    const results = privatePath(testInfo, 'admitted', 'test-results');
    mkdirSync(results, { recursive: true });
    new ValueFreeReporter({ configDir: E2E_DIR }).onBegin({ reporter: [[VALUE_FREE_REPORTER], ['line']], rootDir: E2E_DIR,
      configFile: path.join(E2E_DIR, 'playwright.config.ts'), projects: [{ outputDir: results, testDir: E2E_DIR }] } as unknown as
      FullConfig, { title: '', tests: [], suites: [] } as unknown as Suite);
    expect(() => assertValueFreeRun({ trace: 'off', screenshot: { mode: 'off' }, video: 'off' })).not.toThrow();
    expect(() => assertValueFreeRun({ trace: 'retain-on-failure', screenshot: { mode: 'only-on-failure' }, video: 'off' }))
      .toThrow('value-free mode needs trace, screenshot off');
  });
});

test('value-free offline: a value overlapping the token syntax refuses the run; vf-value- and hex alone do not', () => {
  const vf = new ValueFree(hex(32));
  const fixed = `vf-${vf.segment}-value-`;
  for (const raw of [`vf-${vf.segment}`, `${fixed}`, `x ${fixed}0123456789abcdef`, 'value-', '-email-', `-value-${hex(2)}`,
    `vf-qqqqq-email-${hex(8)}`]) {
    let thrown: unknown;
    try { vf.registerAs('workspace name', raw); } catch (error) { thrown = error; }
    expect(thrown, JSON.stringify(raw)).toBeInstanceOf(ValueFreeRefusal);
    expect((thrown as ValueFreeRefusal).refusals).toEqual([{ refused: raw.length, what: 'workspace name',
      at: expect.stringMatching(/^value-free\.spec\.ts:\d+$/), reason: 'token-syntax' }]);
    expect((thrown as Error).message).toContain('overlaps the token syntax');
    // Never the value: the message is exactly the one its length, kind and file:line give.
    expect((thrown as Error).message).toBe(new ValueFreeRefusal((thrown as ValueFreeRefusal).refusals).message);
    expect((thrown as Error).message).not.toContain(JSON.stringify(raw));
  }
  // Sol's round-3 name: no longer the start of every token, so it is hidden like any other name.
  for (const raw of ['vf-value-', hex(4), `Probe ${hex(4)}`]) expect(() => vf.register(raw), raw).not.toThrow();
  const text = vf.text('workspace vf-value- and more');
  expect(text).toBe(`workspace ${vf.token('vf-value-')} and more`);
  expect(text).not.toContain('vf-value-');
});

test('value-free offline: tokens of any run never trip the walk receipt guard', () => {
  const s = sample();
  for (let i = 0; i < 200; i++) {
    const vf = new ValueFree(hex(32));
    const receipt = JSON.stringify({ title: `id ${vf.token(s.id)}`, verdicts: [{ where: vf.token(s.email),
      detail: { key: vf.token(s.key), jwt: vf.token(s.jwt), run: vf.token(s.run), value: vf.token(`${PROBE}-${i}`) } }] });
    expect(() => assertValueFreeReceipt(receipt), receipt).not.toThrow();
  }
});

// The merge with gw/E2E-WALK-HARNESS-1 (cde8c6b272): its receipt guard runs on every receipt in both modes. Off, a raw
// identity is refused before any IO, exactly as on that branch; value-free, the receipt is sanitized first, and the guard
// still refuses what the sanitizer does not recognise.
const receiptFields = () => ({ profile: 'stock', mountedConfig: null,
  boundary: { attempts: [], ledger: [] } as unknown as Parameters<WalkReceipt['write']>[0]['boundary'], browser: { name: 'chromium' } });

test('value-free offline: walk receipt compat, off mode refuses a raw identity before any IO (walk-harness guard)',
  async ({}, testInfo) => {
    const s = sample();
    const receipt = new WalkReceipt('compat', testInfo);
    receipt.verdict({ feature: 'compat', lane: 'api', where: s.email, verdict: 'RECORDED' });
    await expect(receipt.write(receiptFields())).rejects.toThrow('walk receipt refused: email-shaped value');
    expect(existsSync(testInfo.outputPath('walk-receipt.json')), 'nothing written').toBe(false);
    expect(testInfo.attachments.filter((a) => a.name === 'walk-receipt'), 'nothing attached').toEqual([]);
    // A receipt with no identity is written as before.
    const clean = new WalkReceipt('compat', testInfo);
    clean.verdict({ feature: 'compat', lane: 'api', where: 'settings page', verdict: 'OFF' });
    await clean.write(receiptFields());
    expect(JSON.parse(readFileSync(testInfo.outputPath('walk-receipt.json'), 'utf8')).verdicts[0].where).toBe('settings page');
  });

test('value-free offline: walk receipt compat, value-free mode sanitizes first and the guard still runs', async ({}, testInfo) => {
  const s = sample();
  const vf = new ValueFree(hex(32)).register(s.org);
  bindValueFree(testInfo, vf);
  const receipt = new WalkReceipt('compat', testInfo);
  receipt.verdict({ feature: 'compat', lane: 'api', where: s.email, verdict: 'RECORDED',
    detail: { organizationId: s.id, key: s.key, org: s.org } });
  await receipt.write(receiptFields());
  const written = readFileSync(testInfo.outputPath('walk-receipt.json'), 'utf8');
  expect(() => assertValueFreeReceipt(written)).not.toThrow();
  expect(JSON.parse(written).verdicts[0]).toEqual({ feature: 'compat', lane: 'api', where: vf.token(s.email), verdict: 'RECORDED',
    detail: { organizationId: vf.token(s.id), key: vf.token(s.key), org: vf.token(s.org) } });
  for (const raw of [s.email, s.id, s.key, s.org]) expect(written).not.toContain(raw);
  // A credential shape the sanitizer does not know (not registered, not an email, UUID, 32-hex key or JWT): refused.
  const leaky = new WalkReceipt('compat', testInfo);
  leaky.verdict({ feature: 'compat', lane: 'api', where: `Bearer ${b64()}`, verdict: 'RECORDED' });
  await expect(leaky.write(receiptFields())).rejects.toThrow('walk receipt refused: key-shaped value');
  expect(readFileSync(testInfo.outputPath('walk-receipt.json'), 'utf8'), 'the refused receipt wrote nothing').toBe(written);
});
