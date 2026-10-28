#!/usr/bin/env node
// SET-E2E-002 only. This is a proof driver, not a mutation framework or a stack launcher.
import { createHash } from 'node:crypto';
import { spawn } from 'node:child_process';
import { lstat, mkdir, open, readFile, readdir, realpath, rename, unlink, writeFile } from 'node:fs/promises';
import { dirname, isAbsolute, join, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { stripVTControlCharacters } from 'node:util';

export const SPEC = 'flows/settings/host-update-status.spec.ts';
export const TITLE = 'SET-E2E-002: admin reads fresh host update reports and missing reporting stays unknown';
// Deliberately fail closed on flow drift; review this driver again if the approved flow changes.
// Reviewed restore-state wire/text update: all five checks and all six one-sided
// mutation anchors are unchanged. This pin permits proof execution, not a claim
// that the managed browser flow or its assertion-fallibility proof has passed.
export const ORIGINAL_SHA256 = '46c81c9b7c1ac234ba797145de3e6b8bcc7c7ccb05acd3dbb9f7abce4d6271c7';
export const BUDGET = Object.freeze({ childMs: 360_000, overallMs: 1_200_000, termGraceMs: 5_000, killGraceMs: 2_000 });
export const STEPS = [
  '1 API: fresh sanitized projection contains the first minted candidate',
  '2 UI + API: rendered version and freshness match the actual browser response',
  '3 UI + API: atomic host replacement reaches the same running stack',
  '4 UI + API: missing file is unavailable, never up to date',
  '5 API: mutations are 405 and anonymous access is 401',
];
export const BACKEND_CHECKS = [
  'GET /api/host-update-status/ returns 200 with the minted version and its keys are a subset of the closed public schema, with no path, env, command or secret strings',
  'the page version and freshness equal the API values',
  'an atomic replace on the host is seen by the container without restarting anything',
  'with the file removed the API reports the unavailable state and the page shows reporting unavailable and never up to date',
  'POST, PUT, PATCH and DELETE return 405 and an unauthenticated GET returns 401',
];
const E2E_DIR = fileURLToPath(new URL('../', import.meta.url));
const DRIVER = fileURLToPath(import.meta.url);
export const sha256 = bytes => createHash('sha256').update(bytes).digest('hex');
const requireThat = (condition, message) => { if (!condition) throw new Error(message); };
const plain = text => stripVTControlCharacters(text).replace(/\r/g, '');
const inside = (parent, child) => child === parent || child.startsWith(parent + sep);
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

function unique(source, anchor) {
  requireThat(source.split(anchor).length === 2, `Expected exactly one source anchor: ${anchor}`);
  return source.indexOf(anchor);
}

export function buildMutants(source) {
  const mutations = [
    { id: 'check1-initial-api', check: 1,
      before: 'expect(body.result.snapshot!.discovery.candidate_version).toBe(versionA);',
      after: 'expect(body.result.snapshot!.discovery.candidate_version).toBe(versionB);',
      explanation: 'Expect candidate B while the original host snapshot still publishes A.' },
    { id: 'check2-ui-version', check: 2,
      before: "await expect(page.getByTestId('host-updates-available-version')).toHaveText([versionA], { timeout: UI_READY });",
      after: "await expect(page.getByTestId('host-updates-available-version')).toHaveText([versionB], { timeout: 1_000 });",
      explanation: 'Expect only B in the available-version locator while API and rendered report remain A; only this expect gets 1s.' },
    { id: 'check3-atomic-replace', check: 3,
      before: 'expect(result.snapshot!.discovery.candidate_version).toBe(versionB);',
      after: 'expect(result.snapshot!.discovery.candidate_version).toBe(versionA);',
      explanation: 'Expect candidate A after the original atomic replacement actually publishes B.' },
    { id: 'check4-missing-report', check: 4,
      before: '      await unlink(statusPath);\n      ownsStatus = false;',
      after: '      // Fallibility: omit the owned report deletion only.\n      // Keep ownsStatus true for the original finally cleanup.',
      assertion: "expect(body.result).toEqual({ api_schema_version: 1, availability: 'unavailable', reason_code: 'not_reported',",
      explanation: 'Leave the owned report B available; the unchanged missing-report assertion must reject it. Finally still owns cleanup.' },
    { id: 'check5-read-only', check: 5,
      before: 'expect(response.status(), method).toBe(405);',
      after: "expect(response.status(), method).toBe(method === 'POST' ? 200 : 405);",
      explanation: 'Expect 200 for the original bodyless POST only, which must actually return 405; other methods stay unchanged.' },
    { id: 'check5-anonymous', check: 5,
      before: 'expect(anonymous.status()).toBe(401);',
      after: 'expect(anonymous.status()).toBe(200);',
      explanation: 'Expect 200 for the original anonymous GET, which must actually return 401; all method checks remain unchanged.' },
  ];
  // Validate ALL anchors before allowing any write, not just the next mutation.
  for (const mutation of mutations) {
    unique(source, mutation.before);
    if (mutation.assertion) unique(source, mutation.assertion);
  }
  requireThat(sha256(source) === ORIGINAL_SHA256, 'Approved source SHA256 changed; review the flow and mutation anchors, do not bypass the pin');
  return mutations.map(mutation => {
    const text = source.replace(mutation.before, mutation.after);
    const assertion = mutation.assertion ?? mutation.after;
    const assertionLine = text.slice(0, unique(text, assertion)).split('\n').length;
    return { ...mutation, text, assertionLine, sha256: sha256(text), step: STEPS[mutation.check - 1] };
  });
}

export function parseArgs(args) {
  if (same(args, ['--help'])) return { help: true };
  requireThat(args.length === 2 && args[0] === '--output-dir', 'Usage: node e2e/scripts/host-update-fallibility.mjs --output-dir /absolute/new/directory');
  requireThat(isAbsolute(args[1]) && !/[\0\r\n]/.test(args[1]), '--output-dir must be an absolute path without control characters');
  return { outputDir: resolve(args[1]) };
}

export function validateEnvironment(env, e2eDir) {
  requireThat(env.CI === 'true' && env.GITHUB_ACTIONS === 'true' && env.GITHUB_EVENT_NAME === 'workflow_dispatch' &&
    env.HOST_UPDATE_FALLIBILITY === 'true', 'Only the explicitly opted-in workflow_dispatch CI checkout may run this proof');
  requireThat(env.E2E_STACK === 'distributed', 'Host update fallibility requires stack=distributed');
  requireThat(env.GITHUB_WORKSPACE && resolve(env.GITHUB_WORKSPACE, 'e2e') === e2eDir, 'GITHUB_WORKSPACE must identify this CI checkout');
  requireThat(!env.E2E_INCLUDE_QUARANTINED && !env.E2E_LIVE_LLM && !env.PWDEBUG &&
    !Object.keys(env).some(key => key.startsWith('PW_TEST_') && env[key]), 'Quarantine, live-provider, debug and extra reporter overrides are forbidden');
  requireThat(env.E2E_HOST_UPDATE_STATUS_DIR && isAbsolute(env.E2E_HOST_UPDATE_STATUS_DIR), 'An absolute mounted E2E_HOST_UPDATE_STATUS_DIR is required');
}

export function childCommand(e2eDir, runDir, env) {
  const outputDir = join(runDir, 'test-results');
  return {
    executable: process.execPath,
    args: [join(e2eDir, 'node_modules/playwright/cli.js'), 'test', SPEC,
      '--config', join(e2eDir, 'playwright.config.ts'), '--workers=1', '--retries=0', '--repeat-each=1',
      '--forbid-only', '--reporter=json', '--output', outputDir],
    cwd: e2eDir,
    env: { ...env, PLAYWRIGHT_JSON_OUTPUT_FILE: join(runDir, 'result.json'),
      PLAYWRIGHT_LAST_RUN_OUTPUT_FILE: join(outputDir, '.last-run.json'), FORCE_COLOR: '0' },
    stdoutPath: join(runDir, 'stdout.log'), stderrPath: join(runDir, 'stderr.log'), outputDir,
  };
}

// No shell, npx downloads, or unbounded buffered stdout. Terminate the whole child group on
// deadline/cancellation, then STOP the proof even if the process later produces a report.
export async function runChild(command, { signal, timeoutMs, termGraceMs = BUDGET.termGraceMs, killGraceMs = BUDGET.killGraceMs }) {
  const stdout = await open(command.stdoutPath, 'wx', 0o600);
  let stderr;
  try {
    stderr = await open(command.stderrPath, 'wx', 0o600);
    return await new Promise(resolveResult => {
      let child, timer, killTimer, closeTimer, finished = false, stopping = false;
      const outcome = { exitCode: null, signal: null, timedOut: false, aborted: false, launchError: null };
      const finish = () => {
        if (finished) return;
        finished = true;
        clearTimeout(timer); clearTimeout(killTimer); clearTimeout(closeTimer);
        signal?.removeEventListener('abort', onAbort);
        resolveResult(outcome);
      };
      const kill = sig => {
        if (!child?.pid) return;
        try { process.kill(-child.pid, sig); } catch (error) {
          if (error.code !== 'ESRCH') outcome.killError = error.message;
        }
      };
      const stop = () => {
        if (stopping) return;
        stopping = true;
        kill('SIGTERM');
        killTimer = setTimeout(() => {
          kill('SIGKILL');
          closeTimer = setTimeout(() => { outcome.closeDeadline = true; finish(); }, killGraceMs);
        }, termGraceMs);
      };
      const onAbort = () => { outcome.aborted = true; outcome.abortReason = String(signal.reason); stop(); };
      if (signal?.aborted) { outcome.aborted = true; finish(); return; }
      try {
        child = spawn(command.executable, command.args, { cwd: command.cwd, env: command.env,
          shell: false, detached: true, stdio: ['ignore', stdout.fd, stderr.fd] });
      } catch (error) { outcome.launchError = error.message; finish(); return; }
      child.once('error', error => { outcome.launchError = error.message; finish(); });
      child.once('close', (code, sig) => {
        // The group leader may exit on TERM while a grandchild ignores it.
        if (outcome.timedOut || outcome.aborted) kill('SIGKILL');
        outcome.exitCode = code; outcome.signal = sig; finish();
      });
      signal?.addEventListener('abort', onAbort, { once: true });
      timer = setTimeout(() => { outcome.timedOut = true; stop(); }, timeoutMs);
    });
  } finally { await stdout.close(); await stderr?.close(); }
}

function checkAnnotation(annotations, original) {
  requireThat(Array.isArray(annotations) && annotations.length === 1 && annotations[0].type === 'flow', 'Missing/extra/skip/fail annotations');
  const flow = JSON.parse(annotations[0].description);
  requireThat(flow.id === 'SET-E2E-002' && flow.area === 'settings' && same(flow.backendChecks, BACKEND_CHECKS) &&
    typeof flow.userGoal === 'string' && original.includes(`userGoal: '${flow.userGoal}',`) &&
    Array.isArray(flow.steps) && flow.steps.length === 5 && flow.steps.every(step => original.includes(`'${step}',`)), 'Flow contract changed');
}

function decodeSeed(attachments, result, statusDirectory) {
  requireThat(Array.isArray(attachments), 'Missing attachments');
  const seeds = attachments.filter(item => item.name === 'host-update-seed');
  requireThat(seeds.length === 1 && seeds[0].contentType === 'application/json' && typeof seeds[0].body === 'string' && !seeds[0].path, 'Missing/invalid inline host-update-seed');
  const bytes = Buffer.from(seeds[0].body, 'base64');
  requireThat(bytes.toString('base64') === seeds[0].body, 'Seed body must be canonical JSON-reporter base64 (not HTML-report text)');
  const seed = JSON.parse(bytes.toString('utf8'));
  const a = /^v9\.(\d+)\.(\d+)$/.exec(seed.versionA);
  const b = /^v9\.(\d+)\.(\d+)$/.exec(seed.versionB);
  requireThat(a && b && Number.isSafeInteger(Number(a[2])) && Number.isSafeInteger(Number(b[2])) &&
    Number(a[1]) === result.workerIndex && b[1] === a[1] &&
    BigInt(a[2]) % 2n === 0n && BigInt(b[2]) === BigInt(a[2]) + 1n, 'Seed A/B identities are not independently minted consecutive versions');
  requireThat(seed.suffix === `${result.workerIndex}-${(Number(a[2]) / 2).toString(36)}` &&
    /^v\d+\.\d+\.\d+(?:-(?:beta|rc)\.\d+)?$/.test(seed.installedVersion) &&
    /^[a-f0-9]{32}$/.test(seed.slotId) && /^[a-f0-9-]{36}$/.test(seed.organizationId) &&
    seed.statusDirectory === statusDirectory &&
    seed.storageLane === 'Not applicable: host update reporting owns no Postgres or ClickHouse rows', 'Seed scope/metadata does not match this run');
  return { seed, bytes };
}

function intendedMessage(message, mutation, seed) {
  const text = plain(message);
  requireThat(!/Test timeout|Step timeout|Global timeout|TimeoutError|ECONN|Target page|browser.*closed|element\(s\) not found|worker.*exit/i.test(text), 'Infrastructure/deadline/absent-page failure is not proof');
  const lines = text.split('\n').map(line => line.trim());
  const scalar = (expected, received) => {
    requireThat(text.includes('expect(received).toBe(expected)') &&
      lines.filter(line => line.startsWith('Expected:')).length === 1 && lines.includes(`Expected: ${JSON.stringify(expected)}`) &&
      lines.filter(line => line.startsWith('Received:')).length === 1 && lines.includes(`Received: ${JSON.stringify(received)}`), 'Wrong scalar assertion expected/received evidence');
  };
  const diff = (sign, fragment) => lines.some(line => line.startsWith(sign) && line.slice(1).trim() === fragment);
  switch (mutation.id) {
    case 'check1-initial-api': scalar(seed.versionB, seed.versionA); break;
    case 'check3-atomic-replace': scalar(seed.versionA, seed.versionB); break;
    case 'check2-ui-version':
      requireThat(text.includes('expect(locator).toHaveText(expected)') &&
        /^Locator:\s+getByTestId\('host-updates-available-version'\)$/m.test(text) &&
        /^Timeout:\s+1000ms$/m.test(text) &&
        diff('-', `"${seed.versionB}",`) && diff('+', `"${seed.versionA}",`), 'Wrong locator/array assertion or no displayed A vs expected B evidence');
      break;
    case 'check4-missing-report':
      requireThat(text.includes('expect(received).toEqual(expected)') &&
        diff('-', '"availability": "unavailable",') && diff('+', '"availability": "available",') &&
        diff('-', '"snapshot": null,') && diff('+', `"candidate_version": "${seed.versionB}",`), 'Missing available-vs-unavailable API assertion with the retained B snapshot');
      break;
    case 'check5-read-only':
      scalar(200, 405);
      requireThat(lines[0] === 'POST' || lines[0] === 'Error: POST', 'Failure must be the original POST, not another method');
      break;
    case 'check5-anonymous': scalar(200, 401); break;
    default: throw new Error('Unknown mutation');
  }
  return text;
}

// JSON reporter is NOT the HTML artifact format: tags omit @, seed bodies are base64,
// steps include only test.step, errors[] are formatted while error/step.error are raw.
// See playwright/types/testReporter.d.ts and the installed JSONReporter serializer.
export function validateReport(report, { mutation = null, original, e2eDir, outputDir, statusDirectory, playwrightVersion }) {
  requireThat(report && Array.isArray(report.errors) && report.errors.length === 0, 'Global/teardown errors or missing errors array');
  const config = report.config;
  requireThat(config?.rootDir === e2eDir && config.workers === 1 && config.forbidOnly === true &&
    config.version === playwrightVersion && Array.isArray(config.projects) && config.projects.length === 1 &&
    config.projects[0].retries === 0 && config.projects[0].repeatEach === 1 && config.projects[0].outputDir === outputDir &&
    same(config.reporter, [['json']]), 'Unexpected runner config/version/output identity');
  requireThat(Array.isArray(report.suites) && report.suites.length === 1 && report.suites[0].file === SPEC &&
    !report.suites[0].suites && Array.isArray(report.suites[0].specs) && report.suites[0].specs.length === 1,
  'Expected exactly the original file and test, no extra/missing/nested suites');
  const spec = report.suites[0].specs[0];
  const titleLine = original.slice(0, unique(original, `test('${TITLE}', {`)).split('\n').length;
  requireThat(spec.title === TITLE && spec.file === SPEC && spec.line === titleLine && same(spec.tags, ['flow']) &&
    typeof spec.id === 'string' && spec.id.length > 0 && Array.isArray(spec.tests) && spec.tests.length === 1, 'Wrong original test identity/tags');
  const test = spec.tests[0];
  const expectedStatus = mutation ? 'failed' : 'passed';
  const outcome = mutation ? 'unexpected' : 'expected';
  requireThat(test.timeout === 240_000 && test.expectedStatus === 'passed' && test.status === outcome && spec.ok === !mutation &&
    Array.isArray(test.results) && test.results.length === 1, 'Unexpected/skipped/flaky/retried/expected-failure test');
  checkAnnotation(test.annotations, original);
  const result = test.results[0];
  requireThat(result.retry === 0 && result.status === expectedStatus && Number.isInteger(result.workerIndex) && result.workerIndex >= 0 &&
    Number.isFinite(result.duration) && result.duration >= 0 && Number.isFinite(Date.parse(result.startTime)) &&
    Array.isArray(result.stdout) && Array.isArray(result.stderr) &&
    Array.isArray(result.errors) && result.errors.length === (mutation ? 1 : 0), 'Bad result status/retry/errors/timing');
  checkAnnotation(result.annotations, original);
  const stats = report.stats;
  requireThat(stats && stats.expected === (mutation ? 0 : 1) && stats.unexpected === (mutation ? 1 : 0) &&
    stats.skipped === 0 && stats.flaky === 0 && Number.isFinite(stats.duration), 'Unexpected report totals');
  const { seed, bytes } = decodeSeed(result.attachments, result, statusDirectory);
  const steps = result.steps;
  requireThat(Array.isArray(steps) && steps.length === (mutation?.check ?? 5), 'Wrong/missing executed steps');
  for (const [index, step] of steps.entries()) {
    requireThat(step.title === STEPS[index] && Number.isFinite(step.duration) && step.duration >= 0 && !step.skipped &&
      (!step.steps || (Array.isArray(step.steps) && step.steps.length === 0)), 'Wrong/nested/skipped step');
    if (!mutation || index < mutation.check - 1) requireThat(!step.error, 'Failure at an earlier assertion/step');
  }
  let failure = null;
  if (mutation) {
    const locationOK = location => location && location.file === join(e2eDir, SPEC) && location.line === mutation.assertionLine;
    requireThat(locationOK(result.errorLocation) && locationOK(result.errors[0].location), 'Failure at the wrong source assertion');
    requireThat(typeof result.error?.message === 'string' && typeof steps.at(-1).error?.message === 'string' &&
      plain(result.error.message) === plain(steps.at(-1).error.message) &&
      typeof result.errors[0].message === 'string' && plain(result.errors[0].message).includes(plain(result.error.message)), 'Not exactly one consistent intended assertion failure');
    failure = intendedMessage(result.error.message, mutation, seed);
  } else requireThat(!result.error && !result.errorLocation, 'Baseline has an error');
  return { seed, seedBytes: bytes, failure, durationMs: result.duration, testId: spec.id,
    steps: steps.map(step => step.title), attachments: result.attachments };
}

// Only inspect the public slot. Never unlink, recursively clear, or repair it.
async function cleanSlot(directory) {
  const stat = await lstat(directory);
  requireThat(stat.isDirectory() && !stat.isSymbolicLink(), 'Status directory must be a real directory');
  const entries = await readdir(directory);
  requireThat(!entries.some(name => name === 'status.json' || name === '.set-e2e-002-lock' || /^\.status-.*\.tmp$/.test(name)),
    'Host report/flow lock/temporary remains; preserve state and stop as unqualified');
}

async function fileIdentity(path) {
  const stat = await lstat(path);
  requireThat(stat.isFile() && !stat.isSymbolicLink() && stat.nlink === 1, `Expected single-link regular file: ${path}`);
  return { path, bytes: stat.size, sha256: sha256(await readFile(path)) };
}

export async function runProof({ e2eDir = E2E_DIR, outputDir, env = process.env, signal }, { childRunner = runChild, budget = BUDGET } = {}) {
  e2eDir = await realpath(e2eDir);
  validateEnvironment(env, e2eDir);
  requireThat(isAbsolute(outputDir), 'Output directory must be absolute');
  outputDir = join(await realpath(dirname(outputDir)), relative(dirname(outputDir), outputDir));
  const statusDirectory = await realpath(env.E2E_HOST_UPDATE_STATUS_DIR);
  requireThat(statusDirectory === env.E2E_HOST_UPDATE_STATUS_DIR, 'Status directory must be canonical');
  const repoDir = dirname(e2eDir);
  requireThat(!inside(repoDir, outputDir) && !inside(outputDir, repoDir) &&
    !inside(statusDirectory, outputDir) && !inside(outputDir, statusDirectory), 'Proof output must be separate from the checkout and public status directory');
  await mkdir(outputDir, { mode: 0o700 }); // EEXIST is a refusal, including a stale previous proof.
  const reportPath = join(outputDir, 'proof-report.json');
  const specPath = join(e2eDir, SPEC);
  const started = performance.now();
  const controller = new AbortController();
  const abort = () => controller.abort(signal.reason);
  signal?.addEventListener('abort', abort, { once: true });
  if (signal?.aborted) abort();
  const timer = setTimeout(() => controller.abort('Overall proof deadline exceeded'), budget.overallMs);
  const report = { schemaVersion: 1, flowId: 'SET-E2E-002', backendChecks: BACKEND_CHECKS, mutantCount: 6,
    status: 'unqualified', qualified: false, evidenceKind: childRunner === runChild ? 'playwright' : 'unit-test-double',
    startedAt: new Date().toISOString(), outputDir, specPath, statusDirectory, budgetsMs: budget,
    runs: [], restoration: { verified: false }, errors: [] };
  let original, expected, sourceReady = false, lockHandle, identity;
  const lockPath = `${specPath}.fallibility-lock`;
  const checkSource = async () => {
    const stat = await lstat(specPath);
    requireThat(stat.dev === identity.dev && stat.ino === identity.ino && stat.isFile() && stat.nlink === 1 &&
      (await readFile(specPath)).equals(expected), 'Concurrent source change detected; refusing to overwrite it. Original backup retained.');
  };
  const replaceSource = async bytes => {
    await checkSource();
    if (!expected.equals(bytes)) {
      // Stage beside the spec, so a failed/partial write cannot corrupt the live source.
      // The source lock excludes other drivers; compare bytes + inode again before rename.
      const nextPath = `${specPath}.fallibility-next`;
      const next = await open(nextPath, 'wx', identity.mode & 0o777);
      let renamed = false;
      try {
        await next.writeFile(bytes);
        await next.sync();
        const nextIdentity = await next.stat();
        const staged = await lstat(nextPath);
        requireThat(staged.dev === nextIdentity.dev && staged.ino === nextIdentity.ino &&
          (await readFile(nextPath)).equals(bytes), 'Concurrent mutation of staged source; refusing replacement');
        await checkSource();
        await rename(nextPath, specPath);
        renamed = true;
        expected = Buffer.from(bytes);
        identity = nextIdentity;
      } finally {
        try {
          if (!renamed) {
            const owned = await next.stat();
            const current = await lstat(nextPath);
            if (owned.dev === current.dev && owned.ino === current.ino) await unlink(nextPath);
          }
        } finally { await next.close(); }
      }
    }
    await checkSource();
  };
  const restore = async () => {
    if (!sourceReady) return;
    await replaceSource(original);
    const hash = sha256(await readFile(specPath));
    requireThat(hash === report.original.sha256, 'Source restoration SHA256 mismatch');
    report.restoration = { verified: true, sha256: hash };
  };
  const checkDeadline = () => {
    controller.signal.throwIfAborted();
    requireThat(performance.now() - started < budget.overallMs, 'Overall proof deadline exceeded');
  };
  try {
    checkDeadline();
    await cleanSlot(statusDirectory);
    requireThat(await realpath(specPath) === specPath, 'Spec path may not escape the CI checkout through symlinks');
    lockHandle = await open(lockPath, 'wx', 0o600);
    identity = await lstat(specPath);
    requireThat(identity.isFile() && !identity.isSymbolicLink() && identity.nlink === 1, 'Spec must be a regular, non-symlink file');
    original = await readFile(specPath);
    expected = Buffer.from(original);
    report.original = await fileIdentity(specPath);
    await writeFile(join(outputDir, 'original-spec.txt'), original, { flag: 'wx', mode: 0o600 });
    const mutations = buildMutants(original.toString('utf8'));
    sourceReady = true;
    await checkSource();
    const packageJson = JSON.parse(await readFile(join(e2eDir, 'node_modules/@playwright/test/package.json'), 'utf8'));
    report.runtime = { node: process.version, playwright: packageJson.version, platform: process.platform, arch: process.arch,
      github: Object.fromEntries(['GITHUB_SHA', 'GITHUB_REF', 'GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT'].map(key => [key, env[key] ?? null])),
      stack: env.E2E_STACK, defaultWrapper: 'bin/e2e (unchanged)', defaultStack: 'distributed (unchanged)',
      dispatchDefaultStack: 'standalone (unchanged)' };
    report.inputs = await Promise.all([DRIVER, join(e2eDir, 'playwright.config.ts'), join(e2eDir, 'yarn.lock')].map(fileIdentity));
    async function execute(id, mutation = null) {
      checkDeadline();
      await cleanSlot(statusDirectory);
      await checkSource();
      const runDir = join(outputDir, id);
      await mkdir(runDir, { mode: 0o700 });
      const command = childCommand(e2eDir, runDir, env);
      const run = { id, check: mutation?.check ?? null, expected: mutation ? 'intended assertion failure' : 'passed',
        explanation: mutation?.explanation ?? 'Original unmodified flow must pass once without retries.',
        sourceSha256: sha256(expected), assertionLine: mutation?.assertionLine ?? null, runDir,
        command: { executable: command.executable, args: command.args, cwd: command.cwd }, accepted: false };
      report.runs.push(run);
      await writeFile(join(runDir, 'spec.txt'), expected, { flag: 'wx', mode: 0o600 });
      try {
        run.process = await childRunner(command, { signal: controller.signal,
          timeoutMs: Math.min(budget.childMs, budget.overallMs - (performance.now() - started)),
          termGraceMs: budget.termGraceMs, killGraceMs: budget.killGraceMs });
        checkDeadline();
        requireThat(!run.process.launchError && !run.process.timedOut && !run.process.aborted && !run.process.signal &&
          !run.process.killError && !run.process.closeDeadline && run.process.exitCode === (mutation ? 1 : 0), 'Child launch/deadline/signal/exit failure is not proof');
        await checkSource();
        await cleanSlot(statusDirectory); // A crashed child is never followed by an attempted repair/retry.
        const resultPath = join(runDir, 'result.json');
        const resultIdentity = await fileIdentity(resultPath);
        const result = JSON.parse(await readFile(resultPath, 'utf8'));
        const validated = validateReport(result, { mutation, original: original.toString('utf8'), e2eDir,
          outputDir: command.outputDir, statusDirectory, playwrightVersion: packageJson.version });
        // Retain all referenced files under this run's output, never ordinary suite artifacts.
        run.attachments = [];
        for (const attachment of validated.attachments) {
          if (!attachment.path) continue;
          const path = await realpath(attachment.path);
          requireThat(inside(command.outputDir, path), 'Attachment escapes the isolated run output');
          run.attachments.push(await fileIdentity(path));
        }
        await writeFile(join(runDir, 'host-update-seed.json'), validated.seedBytes, { flag: 'wx', mode: 0o600 });
        run.result = { seed: validated.seed, failure: validated.failure, testId: validated.testId,
          durationMs: validated.durationMs, steps: validated.steps };
        run.outputs = [resultIdentity, ...await Promise.all([command.stdoutPath, command.stderrPath,
          join(runDir, 'host-update-seed.json'), join(runDir, 'spec.txt')].map(fileIdentity))];
        run.accepted = true;
      } catch (error) { run.error = String(error); throw error; }
      finally {
        await writeFile(join(runDir, 'summary.txt'), `${id}: ${run.accepted ? 'accepted' : 'UNQUALIFIED'}\n${run.explanation}\n${run.error ?? run.result?.failure ?? 'Original flow passed.'}\n`, { flag: 'wx', mode: 0o600 });
      }
    }
    await execute('00-original');
    for (const [index, mutation] of mutations.entries()) {
      checkDeadline();
      try {
        report.restoration.verified = false;
        await replaceSource(Buffer.from(mutation.text));
        await execute(`${String(index + 1).padStart(2, '0')}-${mutation.id}`, mutation);
      } finally { await restore(); }
    }
    await restore(); // Check exact bytes BEFORE the final green, not only afterwards.
    report.restoredBeforeFinal = { ...report.restoration };
    await execute('07-restored-original');
    checkDeadline();
    report.status = childRunner === runChild ? 'qualified' : 'unit-fixtures-validated';
    report.qualified = childRunner === runChild;
  } catch (error) { report.errors.push(String(error)); }
  finally {
    try { await restore(); } catch (error) {
      report.restoration = { verified: false, error: String(error) };
      report.errors.push(String(error));
    }
    if (lockHandle) {
      try {
        const owned = await lockHandle.stat();
        const current = await lstat(lockPath);
        requireThat(owned.dev === current.dev && owned.ino === current.ino, 'Source lock was replaced; preserve it');
        await unlink(lockPath);
      } catch (error) { report.errors.push(`Source lock cleanup: ${error}`); }
      finally { await lockHandle.close(); }
    }
    clearTimeout(timer);
    signal?.removeEventListener('abort', abort);
    if (controller.signal.aborted) report.abortReason = String(controller.signal.reason);
    if (report.errors.length || controller.signal.aborted) { report.status = 'unqualified'; report.qualified = false; }
    report.finishedAt = new Date().toISOString();
    report.durationMs = Math.round(performance.now() - started);
    await writeFile(reportPath, JSON.stringify(report, null, 2) + '\n', { flag: 'wx', mode: 0o600 });
  }
  return { report, reportPath };
}

if (process.argv[1] && resolve(process.argv[1]) === DRIVER) {
  const controller = new AbortController();
  const interrupt = signal => controller.abort(`Interrupted by ${signal}`);
  const onINT = () => interrupt('SIGINT');
  const onTERM = () => interrupt('SIGTERM');
  process.on('SIGINT', onINT); process.on('SIGTERM', onTERM);
  try {
    const options = parseArgs(process.argv.slice(2));
    if (options.help) console.log('CI-only SET-E2E-002 proof: --output-dir /absolute/new/directory. Six mutants / five checks; no stack startup or cleanup.');
    else {
      const { report, reportPath } = await runProof({ ...options, signal: controller.signal });
      console.log(`${report.status}: ${reportPath}`);
      if (!report.qualified) process.exitCode = 1;
    }
  } catch (error) { console.error(String(error)); process.exitCode = 1; }
  finally { process.off('SIGINT', onINT); process.off('SIGTERM', onTERM); }
}
