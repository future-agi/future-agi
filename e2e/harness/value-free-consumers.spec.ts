import { execFileSync, spawnSync } from 'node:child_process';
import { randomBytes, randomUUID } from 'node:crypto';
import { chmodSync, cpSync, existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { test, expect } from '@playwright/test';
import { parse as parseYaml } from 'yaml';
import { E2E_DIR, leaks, readTransferred, type Forbidden } from './value-free/scan';

// HR-JRN-01 round 8, offline (no stack, no browser): EVERY consumer of a value-free publication goes through the one
// publication choke point (lib/value-free-boundary.mjs), invariant I3. Sol's round-7 P2 3: the CI job ran the bare seal
// CLI and echoed its status line (a run that registered `intact` put it in the CI log), and main's pull of the default
// publish directory transferred paths whose components were scanned one by one, never whole (a run that registered
// `e2e/value-free-publish` or `value-free-publish/MANIFEST.json` had it in every transferred path).
//   - the inventory: no script or workflow (e2e/**, bin/**, .github/workflows/**, outside the value-free library and these
//     tests) runs the seal or boundary internals, or captures or echoes the choke point's output; each consumer runs it
//     one way, listed here; in the CI job's value-free mode every step after the suite is the choke point, the upload of
//     what it vouched for, or skipped;
//   - the CI job's own value-free steps, run as the runner runs them, around the real value-free boundary child (it
//     registers the value through its adopted registry), with the html report CI uploads: no byte of any step's output,
//     of the step output the upload echoes, or of what the upload takes holds the value;
//   - main's pull of the default publish directory (main-owned runner and nightly: the seal CLI, then the pull), and the
//     same pull through the choke point: no transferred path (whole, below the checkout), file or printed byte holds it.
const repo = (rel: string) => fileURLToPath(new URL(`../../${rel}`, import.meta.url));
const read = (rel: string) => readFileSync(repo(rel), 'utf8');
const CLI = createRequire(import.meta.url).resolve('@playwright/test/cli');
const CHILD_CONFIG = fileURLToPath(new URL('./value-free/boundary.config.ts', import.meta.url));
const BOUNDARY = repo('e2e/lib/value-free-boundary.mjs');
const SEAL = repo('e2e/lib/value-free-seal.mjs');
const quote = (s: string) => `'${s.replace(/'/g, `'\\''`)}'`;
type Step = { name?: string; id?: string; if?: string; run?: string; uses?: string; 'working-directory'?: string };
type Printed = { where: string; text: string };
const ciSteps = () => (parseYaml(read('.github/workflows/e2e-ci.yml')).jobs.e2e.steps) as Step[];

const childEnv = (extra: Record<string, string>): NodeJS.ProcessEnv => {
  const env: NodeJS.ProcessEnv = { ...process.env };
  for (const v of ['TEST_WORKER_INDEX', 'TEST_PARALLEL_INDEX', 'E2E_VALUE_FREE', 'E2E_VALUE_FREE_SALT', 'E2E_VALUE_FREE_REPORTER',
    'E2E_VALUE_FREE_REGISTRY', 'E2E_VALUE_FREE_REGISTRIES', 'E2E_VALUE_FREE_HELD', 'E2E_VALUE_FREE_PUBLISH', 'E2E_VALUE_FREE_OUTPUTS',
    'E2E_WALK_COUNTS', 'E2E_VALUE_FREE_CHILD_LIST_REPORTER', 'E2E_VALUE_FREE_CHILD_JSON', 'PW_TEST_REPORTER', 'PLAYWRIGHT_HTML_REPORT',
    'PLAYWRIGHT_LAST_RUN_OUTPUT_FILE', 'GITHUB_ENV', 'GITHUB_OUTPUT']) delete env[v];
  for (const kind of ['JSON', 'JUNIT', 'HTML', 'BLOB']) for (const what of ['FILE', 'DIR', 'NAME']) delete env[`PLAYWRIGHT_${kind}_OUTPUT_${what}`];
  return { ...env, ...extra };
};
/** Every file below `root/rel` (links are never followed), relative to `root`, POSIX separators. */
function filesBelow(root: string, rel: string): string[] {
  if (!existsSync(path.join(root, rel))) return [];
  return readdirSync(path.join(root, rel), { withFileTypes: true }).flatMap((e) => {
    const at = `${rel}/${e.name}`;
    return e.isDirectory() ? filesBelow(root, at) : e.isFile() ? [at] : [];
  }).sort();
}
const registeredIn = (dir: string): string[] => (existsSync(dir) ? readdirSync(dir) : []).filter((n) => n.endsWith('.jsonl'))
  .flatMap((n) => readFileSync(path.join(dir, n), 'utf8').split('\n').filter(Boolean).map((l) => JSON.parse(l)))
  .filter((v): v is string => typeof v === 'string');
const probesFor = (value: string, scratch: string, publish: string) => ({ value, id: randomUUID(), publish,
  outside: path.join(scratch, 'outside.json'), quoted: `Quoted "${randomBytes(6).toString('hex')}" probe`,
  diagnosticLabel: `Diagnostic label ${randomBytes(6).toString('hex')}` });

type SwitchHit = { file: string; text: string };
// 708J union: these are not publication entrypoints. Pin the entire trimmed line, once per file;
// neither another switch read nor a direct call to the seal/boundary internals is admitted.
const SWITCH_PINS: Record<string, string> = {
  // Additional refusal after the independent, still-false journey acceptance gate.
  'e2e/flows/journeys/publication-contract.ts':
    "if (process.env.E2E_VALUE_FREE !== '1') throw new Error('JOURNEY_VALUE_FREE_REQUIRED');",
  // Require the shared registry before personal-key issuance; register returned credential forms.
  'e2e/lib/personal-key.ts': "if (process.env.E2E_VALUE_FREE !== '1') return undefined;",
  // Register disposable retained fixtures before returning them; refuse a missing registry.
  'e2e/lib/retained-credentials.ts': "if (process.env.E2E_VALUE_FREE === '1') {",
  // Synthetic refusal/list-only/skipped-journey checks, not a publisher: no live actors, zero
  // provisioning requests and no attachments. Linux execution of this test remains required.
  'e2e/scripts/journeys-entrypoint.test.mjs': 'E2E_VALUE_FREE: "1",',
};
function switchInventoryViolations(hits: SwitchHit[]): string[] {
  const violations: string[] = [];
  for (const hit of hits) {
    // Existing publishers retain their independent CHOKE pins and publication assertions below.
    if (['e2e/walks/launch/launch.sh', 'e2e/walks/vm/run-walks.sh', '.github/workflows/e2e-ci.yml'].includes(hit.file)) continue;
    if (!Object.hasOwn(SWITCH_PINS, hit.file)) {
      violations.push(`${hit.file}: a value-free consumer this inventory does not list`);
    } else if (hit.text !== SWITCH_PINS[hit.file]) {
      violations.push(`${hit.file}: a value-free switch use differs from its exact-line pin`);
    }
  }
  for (const file of Object.keys(SWITCH_PINS)) {
    const n = hits.filter((hit) => hit.file === file).length;
    if (n !== 1) violations.push(`${file}: uses the value-free switch ${n} times, not once`);
  }
  return violations;
}

test('value-free offline: every consumer publishes through the choke point, never the seal or boundary internals (I3 inventory)',
  () => {
    const files = execFileSync('git', ['-C', repo(''), 'ls-files', '--', 'e2e', 'bin', '.github/workflows'], { encoding: 'utf8' })
      .split('\n').filter((f) => f && !f.startsWith('e2e/harness/') && !/^e2e\/lib\/value-free[-.]/.test(f) &&
        !/\.(md|png|jpe?g|svg|gif|ico|lock|zip|tar)$/.test(f));
    expect(files.length, 'the scripts and workflows read').toBeGreaterThan(50);
    const comment = (line: string) => /^\s*(#|\/\/|\*|\/\*)/.test(line);
    const INTERNAL = /value-free-(seal|boundary)\b|\b(checkBeforePull|exportForPull|verifySeal|finalCheck|ciCheck|sealCheck|registeredValues|withholdDir)\b/;
    const SWITCH = /\bE2E_VALUE_FREE\b/;
    const hits: { file: string; text: string }[] = [];
    const consumers: SwitchHit[] = [];
    for (const file of files) {
      let text: string;
      try { text = readFileSync(repo(file), 'utf8'); } catch { continue; }
      for (const line of text.split('\n')) {
        if (comment(line)) continue;
        if (INTERNAL.test(line)) hits.push({ file, text: line.trim() });
        if (SWITCH.test(line)) consumers.push({ file, text: line.trim() });
      }
    }
    // The inventory: every consumer that turns value-free mode on, and the one way each runs the choke point. Its output is
    // never captured, piped, redirected or echoed: it is the consumer's own output (a held job output the final check
    // scans, or the CI log). Round 10: the walk launcher runs its declaration check on the pulls it is about to declare,
    // before dispatch, and refuses the launch (exit 64, a fixed line) when the check does.
    const CHOKE: Record<string, RegExp> = {
      'e2e/walks/launch/launch.sh':
        /^node "\$WT\/e2e\/lib\/value-free-boundary\.mjs" declare -- "\$\{pulls\[@\]\}" \|\| \{ echo '[^'$`\\]+'; exit 64; \}$/,
      'e2e/walks/vm/value-free-job.sh':
        /^node e2e\/lib\/value-free-boundary\.mjs publish --registries "\$registries" --print "\$dir\/job-output\.log" -- "\$\{pulls\[@\]\}" \|\| check=\$\?$/,
      'e2e/walks/vm/run-walks.sh': /^node e2e\/lib\/value-free-boundary\.mjs export "\$E2E_VALUE_FREE_PUBLISH" "\$P\/vfout" \|\| seal_rc=\$\?$/,
      '.github/workflows/e2e-ci.yml': new RegExp('^run: node lib/value-free-boundary\\.mjs ci --registries "\\$E2E_VALUE_FREE_REGISTRIES" ' +
        '--print "\\$E2E_VALUE_FREE_HELD" --publish "\\$\\{E2E_VALUE_FREE_PUBLISH:-value-free-publish\\}" --github-output "\\$GITHUB_OUTPUT"$'),
    };
    const violations: string[] = [];
    for (const h of hits) {
      if (!CHOKE[h.file]?.test(h.text)) violations.push(`${h.file}: ${h.text.slice(0, 160)}`);
    }
    for (const file of Object.keys(CHOKE)) {
      const n = hits.filter((h) => h.file === file).length;
      if (n !== 1) violations.push(`${file}: runs the choke point ${n} times, not once`);
    }
    // The walk launcher turns the mode on and runs its job only under value-free-job.sh (which runs the final check).
    violations.push(...switchInventoryViolations(consumers));
    if (!read('e2e/walks/launch/launch.sh').includes('CMD=(bash e2e/walks/vm/value-free-job.sh "$EXIT_FILE" "${pulls[@]}" -- "${CMD[@]}")')) {
      violations.push('e2e/walks/launch/launch.sh: a value-free job not run under value-free-job.sh');
    }
    // CI, value-free: the suite's output is held, and every step after it is the choke point, the upload of what it vouched
    // for (its path is the choke point's step output), or skipped in value-free mode.
    const steps = ciSteps();
    const suite = steps.findIndex((s) => s.name === 'Run E2E suite');
    if (!/bin\/e2e test > "\$E2E_VALUE_FREE_HELD" 2>&1/.test(steps[suite]?.run ?? '')) {
      violations.push('.github/workflows/e2e-ci.yml: the suite output is printed unheld in value-free mode');
    }
    for (const st of steps.slice(suite + 1)) {
      if (st.id === 'value_free_seal' || (st.name === 'Upload report' && st.uses?.startsWith('actions/upload-artifact@'))) continue;
      if (!(st.if ?? '').includes("env.E2E_VALUE_FREE != '1'")) {
        violations.push(`.github/workflows/e2e-ci.yml: "${st.name}" runs in value-free mode outside the choke point`);
      }
    }
    expect(violations, 'consumers that bypass the publication choke point').toEqual([]);
  });

test('value-free offline: the I3 switch inventory rejects unpinned, changed, repeated and missing consumers', () => {
  const pinned = Object.entries(SWITCH_PINS).map(([file, text]) => ({ file, text }));
  expect(pinned).toHaveLength(4);
  expect(switchInventoryViolations(pinned)).toEqual([]);
  const fifth = 'e2e/lib/unreviewed-consumer.ts';
  expect(switchInventoryViolations([...pinned, { file: fifth, text: pinned[0].text }]))
    .toEqual([`${fifth}: a value-free consumer this inventory does not list`]);
  for (const hit of pinned) {
    expect(switchInventoryViolations([...pinned, hit]), `${hit.file}: a duplicate must fail`)
      .toEqual([`${hit.file}: uses the value-free switch 2 times, not once`]);
    expect(switchInventoryViolations(pinned.map((h) => h === hit ? { ...h, text: `${h.text} /* changed */` } : h)),
      `${hit.file}: a changed line must fail`)
      .toEqual([`${hit.file}: a value-free switch use differs from its exact-line pin`]);
    expect(switchInventoryViolations([...pinned, { ...hit, text: 'console.log(process.env.E2E_VALUE_FREE);' }]),
      `${hit.file}: another switch use must fail`)
      .toEqual([`${hit.file}: a value-free switch use differs from its exact-line pin`,
        `${hit.file}: uses the value-free switch 2 times, not once`]);
    expect(switchInventoryViolations(pinned.filter((h) => h !== hit)), `${hit.file}: a missing pin must fail`)
      .toEqual([`${hit.file}: uses the value-free switch 0 times, not once`]);
  }
});

test('value-free offline: the CI job prints and uploads only what the choke point scanned (Sol round-7: a registered status word)',
  async () => {
    test.setTimeout(420_000);
    const value = 'intact';
    const steps = ciSteps();
    const boot = steps.findIndex((s) => s.name === 'Boot the e2e stack');
    const upload = steps.findIndex((s) => s.name === 'Upload report');
    expect(boot > 0 && upload > boot, 'the e2e job: boot, then the upload').toBe(true);
    // A scratch workspace standing for the runner's checkout: e2e/lib copied (the steps run its CLIs), and a bin/e2e whose
    // `test` is the real value-free boundary child (it registers the value) under a reporter list with the html report.
    const scratch = mkdtempSync(path.join(os.tmpdir(), 'vf-ci-'));
    const root = path.join(scratch, 'checkout');
    const runnerTemp = path.join(scratch, 'runner-temp');
    const out = path.join(scratch, 'child');
    for (const dir of [path.join(root, '.git'), path.join(root, 'bin'), runnerTemp, out]) mkdirSync(dir, { recursive: true });
    cpSync(repo('e2e/lib'), path.join(root, 'e2e', 'lib'), { recursive: true });
    const stub = path.join(root, 'bin', 'e2e');
    writeFileSync(stub, ['#!/usr/bin/env bash', '[ "$1" = test ] || exit 2',
      `cd ${quote(E2E_DIR)} && exec node ${quote(CLI)} test --config ${quote(CHILD_CONFIG)} boundary.child.ts`, ''].join('\n'));
    chmodSync(stub, 0o755);
    const pub = path.join(root, 'e2e', 'value-free-publish');
    const githubEnv = path.join(scratch, 'github-env');
    const githubOutput = path.join(scratch, 'github-output');
    writeFileSync(githubEnv, '');
    writeFileSync(githubOutput, '');
    let env = childEnv({ CI: '1', E2E_VALUE_FREE: '1', E2E_VALUE_FREE_PUBLISH: pub, E2E_VALUE_FREE_CHILD_OUT: out,
      E2E_VALUE_FREE_PROBES: JSON.stringify(probesFor(value, scratch, pub)), E2E_VALUE_FREE_BOUNDARY: 'control',
      GITHUB_ENV: githubEnv, GITHUB_OUTPUT: githubOutput, RUNNER_TEMP: runnerTemp });
    // The job's own steps between the boot and the upload, as the runner runs them (bash -eo pipefail, in each one's working
    // directory), with the value-free switch on; what a step appends to GITHUB_ENV holds for the steps after it.
    const printed: Printed[] = [];
    const ran: string[] = [];
    let registered: string[] = [];
    for (const st of steps.slice(boot + 1, upload)) {
      if (!st.run) continue;
      const cond = st.if ?? '';
      if (cond.includes("env.E2E_VALUE_FREE != '1'")) continue;
      expect(['', "${{ env.E2E_VALUE_FREE == '1' }}", "${{ !cancelled() && env.E2E_VALUE_FREE == '1' }}"], `${st.name}: its condition`)
        .toContain(cond);
      const r = spawnSync('bash', ['--noprofile', '--norc', '-eo', 'pipefail', '-c', st.run],
        { cwd: path.join(root, st['working-directory'] ?? ''), encoding: 'utf8', env, timeout: 360_000 });
      printed.push({ where: `${st.name}: stdout`, text: r.stdout ?? '' }, { where: `${st.name}: stderr`, text: r.stderr ?? '' });
      ran.push(`${st.name} rc=${r.status}`);
      for (const line of readFileSync(githubEnv, 'utf8').split('\n')) {
        const at = line.indexOf('=');
        if (at > 0) env = { ...env, [line.slice(0, at)]: line.slice(at + 1) };
      }
      if (env.E2E_VALUE_FREE_REGISTRY && existsSync(env.E2E_VALUE_FREE_REGISTRY)) registered = registeredIn(env.E2E_VALUE_FREE_REGISTRY);
    }
    // What leaves: every step's output, the step output the upload echoes (its path), and what the upload takes (each
    // file below the publish directory, its path and its contents through every layer).
    const stepOutput = readFileSync(githubOutput, 'utf8');
    printed.push({ where: 'the upload path (step output)', text: stepOutput });
    const uploadPath = /^path=(.*)$/m.exec(stepOutput)?.[1];
    const uploaded = uploadPath ? filesBelow(pub, path.relative(pub, uploadPath)) : [];
    const found = await readTransferred(pub, uploaded);
    const forbidden: Forbidden = [['registered status word', value]];
    expect(leaks([...printed, ...found.texts], forbidden), 'a registered value reaches the CI log or the upload').toEqual([]);
    // The round-8 job: the registry prepared and adopted (the value registered there), the suite's output held, the choke
    // point printing exactly what it scanned (the held output, then the clean constant), the html report uploaded alone.
    const boundaryStep = steps.find((s) => s.id === 'value_free_seal')!.name;
    expect(ran).toEqual(['Prepare the value-free registry rc=0', 'Run E2E suite rc=0', `${boundaryStep} rc=0`]);
    expect(registered, 'the run registered the value in the registry the job gave it').toContain(value);
    expect(printed.find((p) => p.where === 'Run E2E suite: stdout')!.text, 'the suite output is held').toBe('');
    const choke = printed.find((p) => p.where === `${boundaryStep}: stdout`)!.text;
    expect(choke.endsWith('\nVFOK\n') && choke.length > 'VFOK\n'.length, 'the held suite output, then the clean constant').toBe(true);
    expect(stepOutput).toBe(`path=${path.join(pub, 'html')}\n`);
    expect(uploaded, 'the html report').toContain('html/index.html');
    expect(readdirSync(runnerTemp).flatMap((d) => readdirSync(path.join(runnerTemp, d))), 'the registries are removed').toEqual(['held.log']);
  });

// Main's runner and nightly (main-owned) run the seal CLI on e2e/value-free-publish, then pull e2e/value-free-publish/**
// from the checkout. The labels never contain the value: a test's own output path is named from its title.
const MAIN_CASES: { label: string; value: () => string; child: number; seal: [number, string]; transferred: (t: string[]) => void }[] = [
  { label: 'a long random name (control)', value: () => `Synthetic main name ${randomBytes(8).toString('hex')}`, child: 0,
    seal: [0, 'VFOK\n'], transferred: (t) => expect(t).toContain('e2e/value-free-publish/MANIFEST.json') },
  { label: "a word of the round-7 seal CLI's status line", value: () => 'intact', child: 0, seal: [0, 'VFOK\n'],
    transferred: (t) => expect(t).toContain('e2e/value-free-publish/MANIFEST.json') },
  { label: 'the publish directory path below the checkout', value: () => ['e2e', 'value-free-publish'].join('/'), child: 1,
    seal: [1, 'VFE4\n'], transferred: (t) => expect(t).toEqual([]) },
  { label: 'the publish directory name and the seal file name', value: () => ['value-free-publish', 'MANIFEST.json'].join('/'), child: 1,
    seal: [1, 'VFRR\n'], transferred: (t) => expect(t).toEqual(['e2e/value-free-publish/REFUSED.json']) },
];

test.describe('value-free offline: main pull of the default publish directory', () => {
  test.describe.configure({ mode: 'parallel' });
  for (const [i, c] of MAIN_CASES.entries()) {
    test(`value-free offline: main pull of the default publish directory, case ${i + 1}: a registered name equal to ${c.label}`,
      async () => {
        test.setTimeout(300_000);
        const value = c.value();
        const scratch = mkdtempSync(path.join(os.tmpdir(), 'vf-main-'));
        const root = path.join(scratch, 'checkout');
        const registries = path.join(scratch, 'registries');
        const registry = path.join(registries, 'run');
        const out = path.join(scratch, 'child');
        for (const dir of [path.join(root, '.git'), registry, out]) mkdirSync(dir, { recursive: true, mode: 0o700 });
        const pub = path.join(root, 'e2e', 'value-free-publish');
        // The run, with the registry a consumer gives it (main's would create one for the choke point).
        const child = spawnSync(process.execPath, [CLI, 'test', '--config', CHILD_CONFIG, 'boundary.child.ts'], { cwd: E2E_DIR,
          encoding: 'utf8', timeout: 240_000, env: childEnv({ CI: '1', E2E_VALUE_FREE: '1', E2E_VALUE_FREE_PUBLISH: pub,
            E2E_VALUE_FREE_REGISTRY: registry, E2E_VALUE_FREE_CHILD_OUT: out, E2E_VALUE_FREE_BOUNDARY: 'control',
            E2E_VALUE_FREE_PROBES: JSON.stringify(probesFor(value, scratch, pub)) }) });
        const registered = registeredIn(registry);
        // Main's way: the seal CLI, then the pull of e2e/value-free-publish/** relative to the checkout.
        const seal = spawnSync(process.execPath, [SEAL, pub], { cwd: root, encoding: 'utf8' });
        const transferred = filesBelow(root, 'e2e/value-free-publish');
        const found = await readTransferred(root, transferred);
        // The same pull through the choke point (the documented way, with the run's registry).
        const choke = spawnSync(process.execPath, [BOUNDARY, 'publish', '--registries', registries, '--', 'e2e/value-free-publish/**'],
          { cwd: root, encoding: 'utf8' });
        const after = filesBelow(root, 'e2e/value-free-publish');
        const printed: Printed[] = [{ where: 'runner stdout', text: child.stdout ?? '' }, { where: 'runner stderr', text: child.stderr ?? '' },
          { where: 'seal CLI stdout', text: seal.stdout ?? '' }, { where: 'seal CLI stderr', text: seal.stderr ?? '' },
          { where: 'choke point stdout', text: choke.stdout ?? '' }, { where: 'choke point stderr', text: choke.stderr ?? '' }];
        const forbidden: Forbidden = [['registered name', value]];
        expect(leaks([...printed, ...found.texts], forbidden), `case ${i + 1}: a registered value crosses main's pull`).toEqual([]);
        expect(registered, `case ${i + 1}: the run registered the value`).toContain(value);
        expect(child.status, `case ${i + 1}: the run`).toBe(c.child);
        expect([seal.status, seal.stdout, seal.stderr], `case ${i + 1}: the seal CLI prints only the choke point's constant`)
          .toEqual([...c.seal, '']);
        c.transferred(transferred);
        expect([choke.status, choke.stdout, choke.stderr], `case ${i + 1}: the choke point`).toEqual([0, 'VFOK\n', '']);
        expect(after, `case ${i + 1}: a clean pull stays in place`).toEqual(transferred);
        expect(existsSync(registries), `case ${i + 1}: the registries are removed`).toBe(false);
      });
  }
});
