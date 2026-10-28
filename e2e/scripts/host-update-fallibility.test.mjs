import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, writeFile, readdir, rm, realpath, symlink } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
import { expect } from '@playwright/test';
import { parse as parseYaml } from 'yaml';
import ts from 'typescript';
import {
  BACKEND_CHECKS, BUDGET, ORIGINAL_SHA256, SPEC, STEPS, TITLE,
  buildMutants, childCommand, parseArgs, runChild, runProof, sha256, validateEnvironment, validateReport,
} from './host-update-fallibility.mjs';

const e2e = fileURLToPath(new URL('../', import.meta.url)).replace(/\/$/, '');
const original = await readFile(join(e2e, SPEC), 'utf8');
const titleLine = original.slice(0, original.indexOf(`test('${TITLE}', {`)).split('\n').length;
const mutations = buildMutants(original);
const playwrightVersion = JSON.parse(await readFile(join(e2e, 'node_modules/@playwright/test/package.json'), 'utf8')).version;
const flow = {
  id: 'SET-E2E-002', area: 'settings',
  userGoal: original.match(/userGoal: '([^']+)',/)[1],
  steps: [...original.match(/    steps: \[([\s\S]*?)\n    \]/)[1].matchAll(/'([^']+)'/g)].map(match => match[1]),
  backendChecks: BACKEND_CHECKS,
};
const annotations = [{ type: 'flow', description: JSON.stringify(flow) }];
const successfulProcess = exitCode => ({ exitCode, signal: null, launchError: null, timedOut: false, aborted: false });
const context = { original, e2eDir: '/checkout/e2e', outputDir: '/proof/run/test-results', statusDirectory: '/slot', playwrightVersion };

// Unit fixtures, NOT qualification evidence. Shape follows JSONReport/TestResult in the
// installed reporter, compared with the retained CI HTML record (which uses a different shape).
// Scalar/object/array diffs below come from real Playwright expect, with no browser or app.
function failureMessage(callback) {
  try { callback(); } catch (error) { return error.message; }
  throw new Error('Unit fixture must fail');
}
function seedFor(directory) {
  return { suffix: '3-mvprmmed', versionA: 'v9.3.3586099844042', versionB: 'v9.3.3586099844043', installedVersion: 'v1.2.3',
    slotId: '0123456789abcdef0123456789abcdef', organizationId: '00000000-0000-4000-8000-000000000001',
    statusDirectory: directory, storageLane: 'Not applicable: host update reporting owns no Postgres or ClickHouse rows' };
}
function messageFor(mutation, seed) {
  switch (mutation.id) {
    case 'check1-initial-api': return failureMessage(() => expect(seed.versionA).toBe(seed.versionB));
    case 'check2-ui-version': {
      const diff = failureMessage(() => expect([seed.versionA]).toEqual([seed.versionB])).split('\n\n').slice(1).join('\n\n');
      return `expect(locator).toHaveText(expected) failed\n\nLocator: getByTestId('host-updates-available-version')\nTimeout: 1000ms\n${diff}\nCall log:\n  - locator resolved to 1 element\n`;
    }
    case 'check3-atomic-replace': return failureMessage(() => expect(seed.versionB).toBe(seed.versionA));
    case 'check4-missing-report': return failureMessage(() => expect({ api_schema_version: 1, availability: 'available', reason_code: null,
      snapshot: { discovery: { candidate_version: seed.versionB } } }).toEqual({ api_schema_version: 1, availability: 'unavailable', reason_code: 'not_reported', snapshot: null }));
    case 'check5-read-only': return failureMessage(() => expect(405, 'POST').toBe(200));
    case 'check5-anonymous': return failureMessage(() => expect(401).toBe(200));
  }
}
function fixture(mutation = null, ctx = context) {
  const seed = seedFor(ctx.statusDirectory);
  const location = { file: join(ctx.e2eDir, SPEC), line: mutation?.assertionLine ?? titleLine, column: 63 };
  const error = mutation ? { message: messageFor(mutation, seed), stack: `Error\n    at ${location.file}:${location.line}:${location.column}` } : undefined;
  const steps = STEPS.slice(0, mutation?.check ?? 5).map(title => ({ title, duration: 15 }));
  if (mutation) steps.at(-1).error = structuredClone(error);
  const result = { workerIndex: 3, parallelIndex: 0, status: mutation ? 'failed' : 'passed', duration: 7113,
    error, errors: mutation ? [{ message: `Error: ${error.message}\n    at ${location.file}:${location.line}:${location.column}`, location }] : [],
    stdout: [], stderr: [], retry: 0, steps, startTime: '2026-10-26T21:25:21.898Z', annotations: structuredClone(annotations),
    attachments: [{ name: 'host-update-seed', contentType: 'application/json', body: Buffer.from(JSON.stringify(seed)).toString('base64') }],
    ...(mutation ? { errorLocation: location } : {}),
  };
  return {
    config: { rootDir: ctx.e2eDir, workers: 1, forbidOnly: true, version: ctx.playwrightVersion, reporter: [['json']],
      projects: [{ outputDir: ctx.outputDir, repeatEach: 1, retries: 0, id: '', name: '', testDir: ctx.e2eDir, timeout: 120_000 }] },
    suites: [{ title: SPEC, file: SPEC, line: 0, column: 0, specs: [{ title: TITLE, file: SPEC, line: titleLine, column: 1,
      ok: !mutation, id: 'b1e79397b0d1cf97c454-0d3127ee98e23492f07e', tags: ['flow'],
      tests: [{ timeout: 240_000, annotations: structuredClone(annotations), expectedStatus: 'passed', projectId: '', projectName: '',
        results: [result], status: mutation ? 'unexpected' : 'expected' }] }] }],
    errors: [], stats: { startTime: result.startTime, duration: 8000, expected: mutation ? 0 : 1, unexpected: mutation ? 1 : 0, flaky: 0, skipped: 0 },
  };
}
const theTest = report => report.suites[0].specs[0].tests[0];
const resultOf = report => theTest(report).results[0];
function changeMessage(report, message) {
  const result = resultOf(report);
  result.error.message = message;
  result.steps.at(-1).error.message = message;
  result.errors[0].message = `Error: ${message}`;
}

async function temp(t) {
  const path = await realpath(await mkdtemp(join(tmpdir(), 'host-fallibility-unit-')));
  t.after(() => rm(path, { recursive: true, force: true })); // Only this test's private fixture, never the public slot.
  return path;
}
async function checkout(t) {
  const root = await temp(t);
  const e2eDir = join(root, 'checkout/e2e');
  const statusDirectory = join(root, 'slot');
  await mkdir(join(e2eDir, dirname(SPEC)), { recursive: true });
  await mkdir(join(e2eDir, 'node_modules/@playwright/test'), { recursive: true });
  await mkdir(statusDirectory);
  await writeFile(join(e2eDir, SPEC), original);
  await writeFile(join(e2eDir, 'node_modules/@playwright/test/package.json'), JSON.stringify({ version: playwrightVersion }));
  for (const path of ['playwright.config.ts', 'yarn.lock']) await writeFile(join(e2eDir, path), await readFile(join(e2e, path)));
  const env = { CI: 'true', GITHUB_ACTIONS: 'true', GITHUB_EVENT_NAME: 'workflow_dispatch', HOST_UPDATE_FALLIBILITY: 'true',
    E2E_STACK: 'distributed', GITHUB_WORKSPACE: dirname(e2eDir), E2E_HOST_UPDATE_STATUS_DIR: statusDirectory };
  return { root, e2eDir, env, outputDir: join(root, 'proof'), statusDirectory };
}
async function fakeChild(command) {
  const runDir = dirname(command.stdoutPath);
  const text = await readFile(join(command.cwd, SPEC), 'utf8');
  const mutation = mutations.find(item => item.text === text) ?? null;
  assert.ok(mutation || text === original, 'fake child saw unrecognized source');
  await mkdir(command.outputDir);
  await writeFile(command.stdoutPath, 'Unit double, not a browser run.\n', { flag: 'wx' });
  await writeFile(command.stderrPath, '', { flag: 'wx' });
  const report = fixture(mutation, { ...context, e2eDir: command.cwd, outputDir: command.outputDir, statusDirectory: command.env.E2E_HOST_UPDATE_STATUS_DIR });
  await writeFile(join(runDir, 'result.json'), JSON.stringify(report), { flag: 'wx' });
  return successfulProcess(mutation ? 1 : 0);
}

// Execute the actual pinned flow's fixture-construction fragments, not a second
// handwritten snapshot. No browser, updater, host publication or qualification.
test('restored wire fixture recovers known active component observations before claiming consistency', async () => {
  const start = original.indexOf('  const now = Date.now();');
  const setupEnd = original.indexOf('  const headers =');
  const replacementStart = original.indexOf('      snapshot.sequence += 1;');
  const replacementEnd = original.indexOf('      await writeFile(temporary, JSON.stringify(snapshot)', replacementStart);
  assert.ok(start > 0 && setupEnd > start && replacementStart > setupEnd && replacementEnd > replacementStart);
  const source = original.slice(start, setupEnd) +
    '\nconst restoring = structuredClone(snapshot);\n' +
    original.slice(replacementStart, replacementEnd) +
    '\nreturn { restoring, restored: snapshot, installedVersion };';
  const javascript = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
  const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
  const result = await new AsyncFunction('join', 'directory', 'testInfo', 'readFile', 'CONTRACT', javascript)(
    join, '/unit-fixture-not-a-host-slot', { workerIndex: 3 }, readFile,
    new URL('../../api_contracts/host-update/', import.meta.url));
  assert.equal(result.restoring.versions.runtime_state, 'unknown');
  assert.equal(result.restoring.versions.running_release, null);
  assert.equal(result.restoring.last_run.effective_version, null);
  assert.deepEqual(result.restoring.versions.components, []);
  assert.equal(result.restored.versions.runtime_state, 'consistent');
  assert.equal(result.restored.versions.running_release, result.installedVersion);
  assert.equal(result.restored.last_run.effective_version, result.installedVersion);
  assert.equal(result.restored.last_run.forward_result, 'failed');
  const active = result.restored.versions.components.filter(c => c.observation !== 'not_deployed');
  assert.ok(active.length > 0, 'A consistent restored runtime needs an active observed component');
  for (const component of active) {
    assert.equal(component.observation, 'known');
    assert.deepEqual(component.observed_versions, [result.installedVersion]);
  }
});

// Mutation integrity, including a frozen spec whose seed/actual cannot silently drift.
test('six independent mutants preserve the original five-check contract and all other bytes', () => {
  assert.equal(sha256(original), ORIGINAL_SHA256);
  assert.equal(mutations.length, 6);
  assert.deepEqual(mutations.map(m => m.check), [1, 2, 3, 4, 5, 5]);
  for (const m of mutations) {
    assert.equal(m.text, original.replace(m.before, m.after));
    assert.equal(m.text.replace(m.after, m.before), original);
    assert.equal(m.sha256, sha256(m.text));
    assert.ok(m.text.includes('snapshot.discovery.candidate_version = versionA;'));
    assert.ok(m.text.includes('snapshot.discovery.candidate_version = versionB;'));
    assert.ok(m.text.includes('test.setTimeout(240_000);'));
    assert.ok(m.text.includes('const UI_READY = 60_000;'));
    assert.ok(m.text.includes('if (ownsStatus) await unlink(statusPath);'));
    assert.ok(m.text.includes(m.assertion ?? m.after));
    assert.ok(m.text.split('\n')[m.assertionLine - 1].includes((m.assertion ?? m.after).trim()));
  }
  assert.ok(mutations[1].after.includes('timeout: 1_000'));
  assert.ok(mutations[4].after.includes("method === 'POST' ? 200 : 405"));
  assert.ok(mutations[5].text.includes(mutations[4].before));
  assert.equal(mutations[3].text.includes('      ownsStatus = false;'), false);
});
for (const mutation of mutations) {
  test(`all anchors preflight: missing/duplicate ${mutation.id}`, () => {
    assert.throws(() => buildMutants(original.replace(mutation.before, '')), /exactly one source anchor/);
    assert.throws(() => buildMutants(original + mutation.before), /exactly one source anchor/);
  });
}
test('rejects changed shared seed, annotations, timeout and new bypasses even with valid mutation anchors', () => {
  for (const [from, to] of [['snapshot.discovery.candidate_version = versionA;', 'snapshot.discovery.candidate_version = versionB;'],
    ['240_000', '480_000'], ["tag: ['@flow']", "tag: ['@flow', '@quarantined']"], ["test('SET-", "test.only('SET-"]]) {
    assert.throws(() => buildMutants(original.replace(from, to)), /SHA256 changed/);
  }
});

test('original baseline JSON passes, not the retained HTML report shape', () => {
  assert.equal(validateReport(fixture(), context).failure, null);
  assert.throws(() => validateReport({ tests: [{}], outcome: 'expected' }, context));
});
for (const mutation of mutations) test(`${mutation.id}: accepts only the real intended assertion/seed shape`, () => {
  const validated = validateReport(fixture(mutation), { ...context, mutation });
  assert.equal(validated.failure, messageFor(mutation, validated.seed));
  assert.equal(validated.steps.at(-1), mutation.step);
});

const invalidReports = [
  ['missing report', () => null],
  ['missing global errors', r => { delete r.errors; }],
  ['global setup failure', r => { r.errors.push({ message: 'ECONNREFUSED' }); }],
  ['extra test', r => { r.suites[0].specs.push(structuredClone(r.suites[0].specs[0])); }],
  ['no tests', r => { r.suites = []; }],
  ['malformed nested suite', r => { r.suites[0].suites = [{}]; }],
  ['wrong test', r => { r.suites[0].specs[0].title = 'other test'; }],
  ['wrong file', r => { r.suites[0].specs[0].file = 'other.spec.ts'; }],
  ['wrong tags', r => { r.suites[0].specs[0].tags = ['@flow']; }],
  ['wrong source location', r => { r.suites[0].specs[0].line = titleLine + 1; }],
  ['retries configured', r => { r.config.projects[0].retries = 1; }],
  ['multiple workers', r => { r.config.workers = 2; }],
  ['wrong output', r => { r.config.projects[0].outputDir = '/ordinary/test-results'; }],
  ['extra reporter', r => { r.config.reporter.push(['html']); }],
  ['unknown runtime', r => { r.config.version = '0.0.0'; }],
  ['test.only allowed', r => { r.config.forbidOnly = false; }],
  ['repeated test', r => { r.config.projects[0].repeatEach = 2; }],
  ['multiple results', r => { theTest(r).results.push(structuredClone(resultOf(r))); }],
  ['missing errors', r => { delete resultOf(r).errors; }],
  ['missing stdout', r => { delete resultOf(r).stdout; }],
  ['missing stderr', r => { delete resultOf(r).stderr; }],
  ['retry attempt', r => { resultOf(r).retry = 1; }],
  ['skipped outcome', r => { theTest(r).status = 'skipped'; }],
  ['flaky outcome', r => { theTest(r).status = 'flaky'; }],
  ['expected failure annotation', r => { theTest(r).expectedStatus = 'failed'; }],
  ['changed test timeout', r => { theTest(r).timeout = 120_000; }],
  ['timedOut result', r => { resultOf(r).status = 'timedOut'; }],
  ['interrupted result', r => { resultOf(r).status = 'interrupted'; }],
  ['teardown error', r => { resultOf(r).errors.push({ message: 'cleanup failed' }); }],
  ['no annotations', r => { theTest(r).annotations = []; }],
  ['runtime skip annotation', r => { resultOf(r).annotations.push({ type: 'skip', description: 'skip' }); }],
  ['changed contract', r => { theTest(r).annotations[0].description = JSON.stringify({ ...flow, backendChecks: [] }); }],
  ['stats skip', r => { r.stats.skipped = 1; }],
  ['stats flake', r => { r.stats.flaky = 1; }],
  ['missing result step', r => { resultOf(r).steps.pop(); }],
  ['wrong step', r => { resultOf(r).steps.at(-1).title = STEPS[2]; }],
  ['skipped step', r => { resultOf(r).steps.at(-1).skipped = true; }],
  ['nested failure', r => { resultOf(r).steps.at(-1).steps = [{ title: 'extra', error: {} }]; }],
  ['wrong error location', r => { resultOf(r).errorLocation.line += 1; }],
  ['wrong formatted location', r => { resultOf(r).errors[0].location.file = '/other'; }],
  ['wrong step error', r => { resultOf(r).steps.at(-1).error.message = 'HTTP failure'; }],
  ['no seed', r => { resultOf(r).attachments = []; }],
  ['duplicate seed', r => { resultOf(r).attachments.push(structuredClone(resultOf(r).attachments[0])); }],
  ['HTML plain seed', r => { resultOf(r).attachments[0].body = JSON.stringify(seedFor('/slot')); }],
  ['invalid base64', r => { resultOf(r).attachments[0].body += '!'; }],
  ['invalid JSON seed', r => { resultOf(r).attachments[0].body = Buffer.from('{').toString('base64'); }],
  ['wrong seed versions', r => { resultOf(r).attachments[0].body = Buffer.from(JSON.stringify({ ...seedFor('/slot'), versionB: 'v9.3.0' })).toString('base64'); }],
  ['wrong seed scope', r => { resultOf(r).attachments[0].body = Buffer.from(JSON.stringify(seedFor('/other'))).toString('base64'); }],
];
for (const [name, change] of invalidReports) test(`parser fails closed: ${name}`, () => {
  const report = fixture(mutations[0]);
  const replacement = change(report);
  assert.throws(() => validateReport(replacement === undefined ? report : replacement, { ...context, mutation: mutations[0] }));
});
for (const mutation of mutations) test(`${mutation.id}: rejects infrastructure, deadline, wrong-stage and wrong-anchor failures`, () => {
  const r = fixture(mutation);
  for (const message of ['Test timeout of 240000ms exceeded', 'Step timeout of 60000ms exceeded', 'Global timeout exceeded',
    'apiRequestContext.get: connect ECONNREFUSED', 'Target page, context or browser has been closed',
    failureMessage(() => expect(500).toBe(200)), 'Error: element(s) not found']) {
    const report = structuredClone(r); changeMessage(report, message);
    assert.throws(() => validateReport(report, { ...context, mutation }));
  }
  if (mutation.check > 1) {
    r.suites[0].specs[0].tests[0].results[0].steps[0].error = { message: 'earlier assertion failed' };
    assert.throws(() => validateReport(r, { ...context, mutation }), /earlier assertion/);
  }
});
test('POST evidence cannot be PUT; UI evidence cannot be another locator or reversed A/B', () => {
  for (const [mutation, from, to] of [[mutations[4], 'POST', 'PUT'], [mutations[1], 'host-updates-available-version', 'other-locator'],
    [mutations[1], '-   "v9.3.3586099844043"', '-   "v9.3.3586099844042"']]) {
    const r = fixture(mutation);
    const before = resultOf(r).error.message;
    const after = before.replace(from, to);
    assert.notEqual(after, before);
    changeMessage(r, after);
    assert.throws(() => validateReport(r, { ...context, mutation }));
  }
});

test('argument and environment safety have no arbitrary Playwright passthrough', () => {
  assert.deepEqual(parseArgs(['--help']), { help: true });
  assert.deepEqual(parseArgs(['--output-dir', '/new path/$(no shell);x']), { outputDir: '/new path/$(no shell);x' });
  for (const args of [[], ['--output-dir'], ['--output-dir', 'relative'], ['--output-dir', '/x\nfoo'],
    ['--output-dir', '/x', '--grep-invert', ''], ['--spec', 'other'], ['--help', 'extra']]) assert.throws(() => parseArgs(args));
  const env = { CI: 'true', GITHUB_ACTIONS: 'true', GITHUB_EVENT_NAME: 'workflow_dispatch', HOST_UPDATE_FALLIBILITY: 'true',
    E2E_STACK: 'distributed', GITHUB_WORKSPACE: '/checkout', E2E_HOST_UPDATE_STATUS_DIR: '/slot' };
  validateEnvironment(env, '/checkout/e2e');
  for (const patch of [{ CI: 'false' }, { GITHUB_ACTIONS: '' }, { GITHUB_EVENT_NAME: 'push' }, { HOST_UPDATE_FALLIBILITY: 'false' },
    { E2E_STACK: 'standalone' }, { GITHUB_WORKSPACE: '/other' }, { E2E_INCLUDE_QUARANTINED: '0' }, { E2E_LIVE_LLM: '1' },
    { PWDEBUG: '1' }, { PW_TEST_REPORTER: 'html' }, { E2E_HOST_UPDATE_STATUS_DIR: 'relative' }]) {
    assert.throws(() => validateEnvironment({ ...env, ...patch }, '/checkout/e2e'));
  }
  const command = childCommand('/checkout/e2e', '/new path/$(no shell);x', env);
  assert.equal(command.executable, process.execPath);
  assert.equal(command.args.filter(arg => arg === SPEC).length, 1);
  for (const arg of ['--workers=1', '--retries=0', '--repeat-each=1', '--forbid-only', '--reporter=json']) assert.ok(command.args.includes(arg));
  assert.ok(!command.args.some(arg => /grep|quarantine|\.only|npx/.test(arg)));
  assert.equal(command.env.PLAYWRIGHT_JSON_OUTPUT_FILE, '/new path/$(no shell);x/result.json');
  assert.equal(command.env.PLAYWRIGHT_LAST_RUN_OUTPUT_FILE, join(command.outputDir, '.last-run.json'));
});

test('complete unit-double sequence restores exact bytes, isolates evidence and NEVER claims real qualification', async t => {
  const options = await checkout(t);
  const seen = [];
  const { report, reportPath } = await runProof(options, { childRunner: async (command, limits) => {
    seen.push(await readFile(join(options.e2eDir, SPEC), 'utf8'));
    assert.ok(limits.timeoutMs <= BUDGET.childMs);
    return fakeChild(command);
  } });
  assert.equal(report.status, 'unit-fixtures-validated', JSON.stringify(report.errors));
  assert.equal(report.qualified, false);
  assert.equal(report.evidenceKind, 'unit-test-double');
  assert.equal(report.runs.length, 8);
  assert.deepEqual(seen, [original, ...mutations.map(m => m.text), original]);
  assert.equal(report.restoration.sha256, ORIGINAL_SHA256);
  assert.equal(report.restoredBeforeFinal.sha256, ORIGINAL_SHA256);
  assert.equal(await readFile(join(options.e2eDir, SPEC), 'utf8'), original);
  assert.equal(await readFile(join(options.outputDir, 'original-spec.txt'), 'utf8'), original);
  assert.deepEqual(await readdir(options.statusDirectory), []);
  assert.equal(JSON.parse(await readFile(reportPath, 'utf8')).qualified, false);
  for (const run of report.runs) {
    assert.equal(run.accepted, true);
    assert.ok(run.outputs.every(file => file.path.startsWith(run.runDir + '/')));
    assert.match(await readFile(join(run.runDir, 'summary.txt'), 'utf8'), /accepted/);
    assert.equal(sha256(await readFile(join(run.runDir, 'result.json'))), run.outputs[0].sha256);
  }
  for (const name of ['playwright-report', 'test-results', 'blob-report']) await assert.rejects(readFile(join(options.e2eDir, name)), /ENOENT/);
  await assert.rejects(readFile(join(options.e2eDir, SPEC + '.fallibility-lock')), /ENOENT/);
});
for (let failedRun = 0; failedRun < 8; failedRun++) test(`source restoration and stop on unqualified child at sequence index ${failedRun}`, async t => {
  const options = await checkout(t);
  let calls = 0;
  const { report } = await runProof(options, { childRunner: async command => {
    const outcome = await fakeChild(command);
    if (calls++ === failedRun) return { ...outcome, exitCode: 2 };
    return outcome;
  } });
  assert.equal(report.status, 'unqualified');
  assert.equal(report.qualified, false);
  assert.equal(calls, failedRun + 1);
  assert.equal(report.restoration.verified, true);
  assert.equal(await readFile(join(options.e2eDir, SPEC), 'utf8'), original);
});
for (const scenario of ['launch', 'timeout', 'signal', 'throw', 'missing-json', 'malformed-json', 'wrong-assertion', 'cleanup', 'attachment-escape']) {
  test(`restores and preserves evidence after ${scenario} during a mutant, never attempts final green`, async t => {
    const options = await checkout(t);
    let calls = 0;
    const { report } = await runProof(options, { childRunner: async command => {
      const outcome = await fakeChild(command);
      if (calls++ === 0) return outcome;
      switch (scenario) {
        case 'launch': return { ...outcome, launchError: 'ENOENT' };
        case 'timeout': return { ...outcome, timedOut: true };
        case 'signal': return { ...outcome, signal: 'SIGTERM' };
        case 'throw': throw new Error('launch threw');
        case 'missing-json': await rm(command.env.PLAYWRIGHT_JSON_OUTPUT_FILE); break;
        case 'malformed-json': await writeFile(command.env.PLAYWRIGHT_JSON_OUTPUT_FILE, '{'); break;
        case 'wrong-assertion': {
          const r = JSON.parse(await readFile(command.env.PLAYWRIGHT_JSON_OUTPUT_FILE, 'utf8'));
          resultOf(r).errorLocation.line = 127;
          await writeFile(command.env.PLAYWRIGHT_JSON_OUTPUT_FILE, JSON.stringify(r)); break;
        }
        case 'cleanup': await writeFile(join(options.statusDirectory, 'status.json'), 'preserve me'); break;
        case 'attachment-escape': {
          const path = join(options.root, 'not-owned.txt'); await writeFile(path, 'preserve');
          const r = JSON.parse(await readFile(command.env.PLAYWRIGHT_JSON_OUTPUT_FILE, 'utf8'));
          resultOf(r).attachments.push({ name: 'trace', contentType: 'text/plain', path });
          await writeFile(command.env.PLAYWRIGHT_JSON_OUTPUT_FILE, JSON.stringify(r)); break;
        }
      }
      return outcome;
    } });
    assert.equal(report.status, 'unqualified');
    assert.equal(calls, 2);
    assert.equal(report.restoration.verified, true);
    assert.equal(await readFile(join(options.e2eDir, SPEC), 'utf8'), original);
    assert.ok((await readdir(report.runs[1].runDir)).includes('summary.txt'));
    if (scenario === 'cleanup') assert.equal(await readFile(join(options.statusDirectory, 'status.json'), 'utf8'), 'preserve me');
  });
}
test('concurrent source edits are preserved, not overwritten by finally', async t => {
  const options = await checkout(t);
  let calls = 0;
  const external = 'user concurrent change\n';
  const { report } = await runProof(options, { childRunner: async command => {
    const outcome = await fakeChild(command);
    if (calls++ === 1) await writeFile(join(options.e2eDir, SPEC), external);
    return outcome;
  } });
  assert.equal(report.status, 'unqualified');
  assert.equal(report.restoration.verified, false);
  assert.equal(calls, 2);
  assert.equal(await readFile(join(options.e2eDir, SPEC), 'utf8'), external);
  assert.equal(await readFile(join(options.outputDir, 'original-spec.txt'), 'utf8'), original);
});
test('preexisting host reports, flow locks, temporaries and source locks are never removed', async t => {
  for (const name of ['status.json', '.set-e2e-002-lock', '.status-leftover.tmp', 'source-lock']) {
    const options = await checkout(t);
    const path = name === 'source-lock' ? join(options.e2eDir, SPEC + '.fallibility-lock') : join(options.statusDirectory, name);
    await writeFile(path, 'preexisting');
    const { report } = await runProof(options, { childRunner: () => assert.fail('must not launch') });
    assert.equal(report.status, 'unqualified');
    assert.equal(await readFile(path, 'utf8'), 'preexisting');
    assert.equal(await readFile(join(options.e2eDir, SPEC), 'utf8'), original);
  }
});
test('a stale staging file prevents any source edit and is preserved', async t => {
  const options = await checkout(t);
  const path = join(options.e2eDir, SPEC + '.fallibility-next');
  await writeFile(path, 'previous crash evidence');
  let calls = 0;
  const { report } = await runProof(options, { childRunner: command => { calls++; return fakeChild(command); } });
  assert.equal(report.status, 'unqualified');
  assert.equal(calls, 1);
  assert.equal(report.restoration.verified, true);
  assert.equal(await readFile(path, 'utf8'), 'previous crash evidence');
  assert.equal(await readFile(join(options.e2eDir, SPEC), 'utf8'), original);
});
test('symlinked source cannot edit a file outside the checkout', async t => {
  const options = await checkout(t);
  const other = join(options.root, 'outside-spec.txt');
  await writeFile(other, original);
  await rm(join(options.e2eDir, SPEC));
  await symlink(other, join(options.e2eDir, SPEC));
  const { report } = await runProof(options, { childRunner: () => assert.fail('must not launch') });
  assert.equal(report.status, 'unqualified');
  assert.match(report.errors[0], /symlinks/);
  assert.equal(await readFile(other, 'utf8'), original);
});
test('valid isolated trace attachments are retained and hashed, missing attachments fail closed', async t => {
  for (const missing of [false, true]) {
    const options = await checkout(t);
    const { report } = await runProof(options, { childRunner: async command => {
      const outcome = await fakeChild(command);
      const path = join(command.outputDir, 'trace.zip');
      if (!missing) await writeFile(path, 'unit fixture, not a browser trace');
      const r = JSON.parse(await readFile(command.env.PLAYWRIGHT_JSON_OUTPUT_FILE, 'utf8'));
      resultOf(r).attachments.push({ name: 'trace', contentType: 'application/zip', path });
      await writeFile(command.env.PLAYWRIGHT_JSON_OUTPUT_FILE, JSON.stringify(r));
      return outcome;
    } });
    assert.equal(report.status, missing ? 'unqualified' : 'unit-fixtures-validated');
    assert.equal(report.restoration.verified, true);
    if (!missing) assert.equal(report.runs[0].attachments[0].sha256, sha256('unit fixture, not a browser trace'));
  }
});
test('output may not alias checkout, public slot, existing evidence or a symlink into the checkout', async t => {
  const options = await checkout(t);
  await mkdir(options.outputDir);
  await symlink(options.e2eDir, join(options.root, 'alias'));
  for (const outputDir of [options.e2eDir, join(options.e2eDir, 'proof'), options.statusDirectory,
    join(options.statusDirectory, 'proof'), options.root, options.outputDir, join(options.root, 'alias/proof')]) {
    await assert.rejects(runProof({ ...options, outputDir }, { childRunner: () => assert.fail('must not launch') }));
  }
  assert.equal(await readFile(join(options.e2eDir, SPEC), 'utf8'), original);
});
test('overall cancellation stops a mutant and restores before returning', async t => {
  const options = await checkout(t);
  const controller = new AbortController();
  let calls = 0;
  const { report } = await runProof({ ...options, signal: controller.signal }, { childRunner: async command => {
    const result = await fakeChild(command);
    if (calls++ === 1) controller.abort('unit cancellation');
    return result;
  } });
  assert.equal(calls, 2);
  assert.equal(report.status, 'unqualified');
  assert.equal(report.restoration.verified, true);
});
test('aggregate deadline limits later children, then aborts without a retry', async t => {
  const options = await checkout(t);
  let calls = 0;
  const { report } = await runProof(options, { budget: { ...BUDGET, overallMs: 1_000 }, childRunner: async (command, { signal, timeoutMs }) => {
    assert.ok(timeoutMs <= 1_000);
    calls++;
    await new Promise(resolve => signal.addEventListener('abort', resolve, { once: true }));
    return { ...successfulProcess(0), aborted: true };
  } });
  assert.equal(calls, 1);
  assert.equal(report.status, 'unqualified');
  assert.equal(report.restoration.verified, true);
});

for (const scenario of ['success', 'exit1', 'launch', 'throw', 'timeout', 'abort', 'pre-abort']) {
  test(`actual child lifecycle (Node only, no browser): ${scenario}`, async t => {
    const root = await temp(t);
    const controller = new AbortController();
    const command = { executable: process.execPath, args: ['-e', 'process.stdout.write("unit child\\n")'], cwd: root, env: process.env,
      stdoutPath: join(root, 'stdout.log'), stderrPath: join(root, 'stderr.log') };
    if (scenario === 'exit1') command.args = ['-e', 'process.exit(1)'];
    if (scenario === 'launch') command.executable = join(root, 'does-not-exist');
    if (scenario === 'throw') command.args = ['\0'];
    if (['timeout', 'abort', 'pre-abort'].includes(scenario)) command.args = ['-e', 'process.on("SIGTERM",()=>{}); setInterval(()=>{},1000)'];
    if (scenario === 'pre-abort') controller.abort('before launch');
    const timer = scenario === 'abort' ? setTimeout(() => controller.abort('cancelled'), 200) : null;
    const result = await runChild(command, { signal: controller.signal, timeoutMs: scenario === 'timeout' ? 200 : 3000, termGraceMs: 30, killGraceMs: 30 });
    clearTimeout(timer);
    if (scenario === 'success') { assert.equal(result.exitCode, 0); assert.match(await readFile(command.stdoutPath, 'utf8'), /unit child/); }
    if (scenario === 'exit1') assert.equal(result.exitCode, 1);
    if (['launch', 'throw'].includes(scenario)) assert.ok(result.launchError);
    if (scenario === 'timeout') assert.equal(result.timedOut, true);
    if (['abort', 'pre-abort'].includes(scenario)) assert.equal(result.aborted, true);
  });
}

test('installed JSON reporter contract smoke uses only a temporary browser-free unit fixture', async t => {
  const root = await temp(t);
  const config = join(root, 'playwright.config.mjs');
  const resultPath = join(root, 'result.json');
  const outputDir = join(root, 'results');
  await writeFile(config, 'export default { testDir: ".", testMatch: "serializer.spec.mjs", forbidOnly: true, workers: 1, retries: 0 };');
  await writeFile(join(root, 'serializer.spec.mjs'), `import { test, expect } from ${JSON.stringify(new URL('../node_modules/@playwright/test/index.mjs', import.meta.url).href)};
    test('unit serializer fixture, not SET-E2E-002', async ({}, info) => {
      await info.attach('unit-seed', { body: '{"unit":true}', contentType: 'application/json' });
      await test.step('unit assertion', async () => { expect(1).toBe(2); });
    });`);
  const result = spawnSync(process.execPath, [join(e2e, 'node_modules/playwright/cli.js'), 'test', '--config', config, '--reporter=json', '--output', outputDir],
    { cwd: e2e, env: { ...process.env, CI: 'true', PLAYWRIGHT_JSON_OUTPUT_FILE: resultPath }, timeout: 15000, encoding: 'utf8' });
  assert.equal(result.status, 1, result.stderr);
  const report = JSON.parse(await readFile(resultPath, 'utf8'));
  assert.deepEqual(report.config.reporter, [['json']]);
  assert.equal(report.config.version, playwrightVersion);
  assert.equal(report.config.projects[0].outputDir, outputDir);
  const spec = report.suites[0].specs[0];
  const child = spec.tests[0].results[0];
  assert.equal(child.steps.length, 1, 'JSON reporter omits expect and hook steps');
  assert.equal(child.steps[0].title, 'unit assertion');
  assert.equal(child.error.message, child.steps[0].error.message);
  assert.equal(child.errors.length, 1);
  assert.equal(child.errors[0].location.line, child.errorLocation.line);
  assert.equal(child.attachments[0].body, Buffer.from('{"unit":true}').toString('base64'));
});

test('workflow opt-in is dispatch-only, validates before builds, follows the unchanged suite and isolates artifacts', async () => {
  const workflow = parseYaml(await readFile(new URL('../../.github/workflows/e2e-ci.yml', import.meta.url), 'utf8'));
  const inputs = workflow.on.workflow_dispatch.inputs;
  assert.equal(inputs.stack.default, 'standalone');
  assert.deepEqual(inputs.stack.options, ['distributed', 'standalone']);
  assert.equal(inputs.host_update_fallibility.type, 'boolean');
  assert.equal(inputs.host_update_fallibility.default, false);
  assert.deepEqual(workflow.on.pull_request.branches, ['dev', 'main']);
  assert.deepEqual(workflow.on.push.branches, ['dev', 'main']);
  assert.deepEqual(workflow.on.schedule, [{ cron: '37 2 * * *' }]);
  const validation = workflow.jobs.changes.steps[0];
  assert.equal(validation.if, "github.event_name == 'workflow_dispatch' && inputs.host_update_fallibility && inputs.stack != 'distributed'");
  assert.match(validation.run, /requires stack=distributed/);
  assert.match(validation.run, /exit 1/);
  assert.equal(workflow.jobs.e2e.needs, 'changes');
  const steps = workflow.jobs.e2e.steps;
  const normal = steps.findIndex(step => step.name === 'Run E2E suite');
  const proof = steps[normal + 1];
  assert.equal(steps[normal].run, 'bin/e2e test');
  assert.equal(proof.id, 'host-update-fallibility');
  assert.equal(proof.if, "github.event_name == 'workflow_dispatch' && inputs.host_update_fallibility && matrix.stack == 'distributed'");
  assert.equal(proof.timeoutMinutes, undefined);
  assert.equal(proof['timeout-minutes'], 22);
  assert.ok(proof['timeout-minutes'] * 60_000 > BUDGET.overallMs + BUDGET.termGraceMs + BUDGET.killGraceMs);
  assert.equal(proof['continue-on-error'], undefined);
  assert.match(proof.run, /--output-dir "\$RUNNER_TEMP\/host-update-fallibility"$/);
  const artifact = steps[normal + 2];
  assert.equal(artifact.with.name, 'host-update-fallibility-distributed');
  assert.equal(artifact.with.path, '${{ runner.temp }}/host-update-fallibility');
  assert.equal(artifact.with['retention-days'], 7);
  assert.equal(artifact.with['if-no-files-found'], 'error');
  assert.match(artifact.if, /!cancelled\(\).*workflow_dispatch/);
  const ordinaryReport = steps.find(step => step.name === 'Upload report');
  assert.equal(ordinaryReport.with.path, 'e2e/playwright-report');
  assert.equal(ordinaryReport.with['retention-days'], 7);
  assert.equal(workflow.jobs.e2e['timeout-minutes'], 90);
  const allow = new Function('github', 'inputs', 'matrix', `return ${proof.if};`);
  const invalid = new Function('github', 'inputs', `return ${validation.if};`);
  for (const event_name of ['pull_request', 'push', 'schedule', 'merge_group', 'workflow_dispatch']) {
    for (const flag of [true, false]) for (const stack of ['distributed', 'standalone']) {
      const args = [{ event_name }, { host_update_fallibility: flag, stack }, { stack }];
      assert.equal(Boolean(allow(...args)), event_name === 'workflow_dispatch' && flag && stack === 'distributed');
      assert.equal(Boolean(invalid(...args)), event_name === 'workflow_dispatch' && flag && stack !== 'distributed');
    }
  }
  const rejected = spawnSync('/bin/bash', ['-c', validation.run], { encoding: 'utf8' });
  assert.equal(rejected.status, 1);
  assert.match(rejected.stdout, /no builds were started/);
  assert.deepEqual(workflow.permissions, { contents: 'read' });
  assert.deepEqual(workflow.jobs.changes.permissions, { contents: 'read', 'pull-requests': 'read' });
  assert.deepEqual(workflow.jobs.e2e.permissions, { contents: 'read', packages: 'write' });
  assert.equal(Object.values(workflow.jobs).filter(job => job.permissions).length, 2);
  const builds = steps.filter(step => step.uses === 'docker/build-push-action@v5');
  assert.equal(builds.length, 4);
  for (const step of builds) {
    assert.equal(step.with.push, undefined);
    assert.equal(step.with.load, true);
    assert.match(step.with['cache-to'], /^\$\{\{ github.event_name == 'push' && format\('type=registry,ref=\{0\}:(backend|fi-collector|frontend|gateway),mode=max', env.CACHE_REPO\) \|\| '' \}\}$/);
  }
  assert.equal(steps.find(step => step.name === 'Save Playwright browser cache').if,
    "github.event_name == 'push' && steps.pw-cache.outputs.cache-hit != 'true'");
});
