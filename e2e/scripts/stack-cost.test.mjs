import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { parse } from 'yaml';

const repo = fileURLToPath(new URL('../../', import.meta.url));
const loadWorkflow = file => parse(readFileSync(path.join(repo, '.github/workflows', file), 'utf8'));
const baseline = loadWorkflow('e2e-ci.yml');
const workflow = loadWorkflow('e2e-stack-profile.yml');
const steps = workflow.jobs.e2e.steps;
const run = (script, args, env = {}) => spawnSync('bash', ['-eo', 'pipefail', '-c', script, 'test', ...args], {
  encoding: 'utf8', cwd: repo, env: { HOME: process.env.HOME, PATH: process.env.PATH, ...env },
});

test('the frozen frontend install precedes source and bundle flags, but still runs on a manifest change', () => {
  const build = readFileSync(path.join(repo, 'frontend/Dockerfile'), 'utf8').split(' AS build\n')[1].split('\nFROM ')[0];
  const install = build.indexOf('RUN yarn install --frozen-lockfile --non-interactive');
  const manifests = build.indexOf('COPY package.json yarn.lock ./');
  assert.ok(manifests >= 0 && manifests < install);
  for (const line of ['COPY . ./', 'ARG VITE_', 'RUN yarn build']) assert.ok(build.indexOf(line) > install, line);
});

test('the disk fast path needs both byte and inode reserves on the actual devices', () => {
  const gib = 1024 * 1024;
  for (const [same, dk, di, wk, wi, ok] of [
    ['yes', 80, 2500000, 80, 2500000, true],
    ['yes', 79, 2500000, 79, 2500000, false],
    ['yes', 80, 2499999, 80, 2499999, false],
    ['no', 64, 2000000, 16, 500000, true],
    ['no', 64, 1999999, 100, 500000, false],
    ['no', 63, 2000000, 100, 500000, false],
    ['no', 64, 2000000, 15, 500000, false],
    ['no', 64, 2000000, 16, '-', false],
  ]) {
    const result = run('source scripts/reclaim-runner-disk.sh; enough_headroom "$@"',
      [same, dk*gib, di, wk*gib, wi].map(String));
    assert.equal(result.status === 0, ok, result.stderr);
  }
});

function fixture(t) {
  const dir = mkdtempSync(path.join(tmpdir(), 'stack-cost-'));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  return dir;
}

test('both topologies must run the pre-boot image IDs, not just matching tags', t => {
  const dir = fixture(t);
  const id = `sha256:${'ab'.repeat(32)}`;
  // This exercises bin/e2e through the Docker/Compose CLI boundary, without a daemon.
  writeFileSync(path.join(dir, 'docker'), `#!/usr/bin/env bash
set -eu
case "$1" in
  image) printf '%s %s\\n' "$ID" "\${PLATFORM:-linux/amd64}" ;;
  compose) service="\${!#}"; if [[ "\${FAIL:-}" == missing ]]; then exit 0; fi
           echo "$service" > "$STATE"; echo abcd ;;
  inspect) service=$(cat "$STATE")
           case $service in mock-google|backend|worker*) image=future-agi;; app) image=standalone;; fi-property-catalog-consumer) image=fi-collector;; *) image=$service;; esac
           echo "futureagi/$image:\${TAG:-e2e} $ID" ;;
  *) exit 2 ;;
esac
`, { mode: 0o755 });
  for (const topology of ['distributed', 'standalone']) {
    const env = { PATH: `${dir}:${process.env.PATH}`, STATE: path.join(dir, 'service'), E2E_STACK: topology, ID: id };
    const call = (command, overrides = {}) => run(command, [], { ...env, ...overrides });
    const capture = call('bin/e2e image-plan e2e');
    assert.equal(capture.status, 0, capture.stderr);
    assert.equal(capture.stdout.trim().split('\n').length, topology === 'standalone' ? 6 : 5);
    assert.notEqual(call('bin/e2e image-plan e2e', { PLATFORM: 'linux/arm64' }).status, 0);
    const PLAN = path.join(dir, 'plan');
    writeFileSync(PLAN, capture.stdout);
    const verify = 'bin/e2e verify-images "$PLAN" e2e';
    const correct = call(verify, { PLAN });
    assert.equal(correct.status, 0, correct.stderr);
    assert.match(correct.stdout, /mock-google futureagi\/future-agi:e2e/);
    for (const change of [{ ID: `sha256:${'ff'.repeat(32)}` }, { TAG: 'latest' }, { FAIL: 'missing' }]) {
      assert.notEqual(call(verify, { PLAN, ...change }).status, 0);
    }
    writeFileSync(PLAN, '');
    assert.notEqual(call(verify, { PLAN }).status, 0);
  }
});

test('cache import failure, success and absent build history have distinct receipts', t => {
  const dir = fixture(t);
  writeFileSync(path.join(dir, 'docker'), `#!/bin/sh
cat <<'LOG'
#1 importing cache manifest from registry/backend
#1 ERROR: denied
#2 importing cache manifest from registry/other
#2 DONE 0.3s
#4 CACHED
#5 CACHED
LOG
`, { mode: 0o755 });
  const step = steps.find(step => step.name === 'Record BuildKit cache import and layer-hit counts');
  const result = run('mkdir -p "$RUNNER_TEMP/stack-profile"\n' + step.run, [], {
    PATH: `${dir}:${process.env.PATH}`, RUNNER_TEMP: dir, BACKEND_REF: 'builder/node/build',
    COLLECTOR_REF: '', EXECUTOR_REF: '', FRONTEND_REF: '', GATEWAY_REF: '',
  });
  assert.equal(result.status, 0, result.stderr);
  const receipt = readFileSync(path.join(dir, 'stack-profile/build-cache.tsv'), 'utf8');
  assert.match(receipt, /^BACKEND\t2\t1\t1$/m);
  assert.match(receipt, /^EXECUTOR\tunavailable\tunavailable\tunavailable$/m);
});

test('cold benchmarks disable all external BuildKit imports, without changing the source build', () => {
  const job = workflow.jobs.e2e;
  const expr = job.env.E2E_COLD_BUILD.match(/^\$\{\{ (.*) \}\}$/)[1];
  const cold = new Function('github', 'inputs', `return ${expr}`);
  assert.equal(cold({event_name: 'workflow_dispatch'}, {ordinary_cold_build: true}), true);
  assert.equal(cold({event_name: 'push'}, {ordinary_cold_build: true}), false);
  const builds = steps.filter(step => step.uses?.startsWith('docker/build-push-action@'));
  assert.equal(builds.length, 5);
  for (const step of builds) {
    assert.equal(step.with.load, true);
    for (const line of step.with['cache-from'].trim().split('\n')) {
      assert.match(line, /^\$\{\{ env\.E2E_COLD_BUILD != 'true' && .* \|\| '' \}\}$/);
    }
  }
});

test('profiling is a separate dispatch, not an alternative required check', () => {
  assert.deepEqual(Object.keys(workflow.on), ['workflow_dispatch']);
  assert.notEqual(workflow.jobs['e2e-tests'].name, baseline.jobs['e2e-tests'].name);
  assert.match(baseline.jobs['e2e-tests'].name, /&& 'E2E metadata edit' \|\| 'E2E Tests Pass' \}\}$/);
  assert.equal(workflow.jobs['e2e-tests'].name, 'E2E profile parity');
  assert.equal(workflow.jobs.e2e.name, baseline.jobs.e2e.name); // receipt producer lookup
  const harness = loadWorkflow('e2e-harness-selftest.yml');
  for (const {paths} of [harness.on.pull_request, harness.on.push]) {
    for (const path of ['scripts/reclaim-runner-disk.sh', 'frontend/Dockerfile', '.github/workflows/e2e-stack-profile.yml']) assert.ok(paths.includes(path));
  }
  const choice = workflow.on.workflow_dispatch.inputs.ordinary_shards;
  assert.deepEqual(choice.options, ['1', '2', '4']);
  assert.equal(choice.default, '4');
  const count = new Function('github', 'inputs', `return ${workflow.env.E2E_SHARDS.slice(3,-2)}`);
  for (const input of [...choice.options, 'unknown', undefined]) {
    assert.equal(count({event_name: 'workflow_dispatch'}, {ordinary_shards: input}), choice.options.includes(input) ? input : '4');
  }
  // The dispatch copy must still run the ordinary suite, fallibility proof,
  // SDK tests and parity checks. A new gate must enter the comparison too.
  assert.deepEqual(workflow.jobs.report.steps, baseline.jobs.report.steps);
  assert.deepEqual(workflow.jobs['e2e-tests'].steps, baseline.jobs['e2e-tests'].steps);
  const baseSteps = baseline.jobs.e2e.steps;
  const baseSuite = baseSteps.findIndex(step => step.id === 'suite');
  const suite = steps.findIndex(step => step.id === 'suite');
  assert.equal(steps[suite-2].id, 'boot');
  assert.ok(steps[suite-2].run.indexOf('bin/e2e verify-images') > steps[suite-2].run.indexOf('bin/e2e up'));
  assert.equal(steps[suite-2]['continue-on-error'], undefined);
  assert.deepEqual(steps.slice(suite-1,suite+5), baseSteps.slice(baseSuite-1,baseSuite+5));
  for (const name of ['Upload blob report','Record the once-per-stack steps','Upload the once-per-stack record']) {
    assert.deepEqual(steps.find(step => step.name === name), baseSteps.find(step => step.name === name));
  }
  for (const id of ['catalog','sdk-python','sdk-typescript']) {
    assert.deepEqual(steps.find(step => step.id === id), baseSteps.find(step => step.id === id));
  }
});

test('only a validated dev/main profiling dispatch can start an authenticated reader', () => {
  const credentialed = Object.entries(workflow.jobs).filter(([, job]) =>
    job.steps.some(step => step.uses?.startsWith('docker/login-action@')));
  assert.deepEqual(credentialed.map(([id]) => id), ['profile-cache-import']);
  const probe = workflow.jobs['profile-cache-import'];
  assert.equal(probe.needs, 'changes');
  assert.deepEqual(probe.permissions, { contents: 'read', packages: 'read' });
  const login = probe.steps.find(step => step.uses?.startsWith('docker/login-action@'));
  assert.equal(login['continue-on-error'], undefined);
  assert.ok(!probe.steps.some(step => /--push|--cache-to|sdk-ref/.test(step.run ?? '')));
  assert.deepEqual(workflow.permissions, { contents: 'read' });
  assert.equal(workflow.jobs.e2e.permissions, undefined);
  const allow = new Function('github', 'inputs', `return ${probe.if}`);
  const reject = new Function('github', 'inputs', `return ${workflow.jobs.changes.steps[0].if}`);
  for (const event_name of ['push', 'schedule', 'pull_request', 'merge_group', 'workflow_dispatch']) {
    for (const ref of ['refs/heads/dev', 'refs/heads/main', 'refs/heads/topic']) {
      for (const profile_private_cache of [false,true]) for (const host_update_fallibility of [false,true]) for (const stack of ['distributed','standalone']) {
        const inputs = {profile_private_cache, host_update_fallibility, stack};
        const github = {event_name, ref};
        assert.equal(!reject(github, inputs) && allow(github, inputs), event_name === 'workflow_dispatch' && ref !== 'refs/heads/topic' && profile_private_cache && !(host_update_fallibility && stack === 'standalone'));
      }
    }
  }
});

test('a private import needs the matching terminal record for every requested ref', t => {
  const dir = fixture(t);
  const step = workflow.jobs['profile-cache-import'].steps.at(-1);
  const backend = 'registry/cache:backend', slim = 'registry/release:backend-slim-amd64';
  const prefix = `#1 importing cache manifest from ${backend}\n#2 importing cache manifest from ${slim}\n`;
  for (const [log, expected] of [
    [prefix+'#2 DONE 0.1s\n#1 DONE 0.2s', ['completed','completed']],
    [prefix+'#1 ERROR: manifest unknown\n#2 DONE 0.2s', ['missed','completed']],
    [prefix+'#1 ERROR: denied\n#1 DONE 0.2s', ['unavailable','unavailable']],
    ['#1 importing cache manifest from registry/cache:backend-other\n#1 DONE 0.1s', ['unavailable','unavailable']],
    ['', ['unavailable','unavailable']],
  ]) {
    const summary = path.join(dir,'summary');
    writeFileSync(summary, ''); writeFileSync(path.join(dir,'build-progress'), log);
    const result = run(step.run, [], {TARGET:'backend', STACK:'standalone', CACHE_REPO:'registry/cache', BUILD_CACHE:'registry/release', RUNNER_TEMP:dir, GITHUB_STEP_SUMMARY:summary});
    assert.equal(result.status === 0, expected.every(value => value === 'completed'), result.stderr);
    assert.equal(readFileSync(summary,'utf8'), `\`${backend}\`: ${expected[0]}\n\n\`${slim}\`: ${expected[1]}\n\n`);
  }
});

test('empty, partial and prematurely terminated service traces are not published as samples', t => {
  const boot = steps.find(step => step.id === 'boot');
  for (const stack of ['distributed','standalone']) for (const mode of ['complete','empty','partial','exited']) {
    const dir = fixture(t);
    mkdirSync(path.join(dir,'bin')); mkdirSync(path.join(dir,'stack-profile'));
    writeFileSync(path.join(dir,'docker'), `#!/bin/bash
if [[ $MODE != empty ]]; then
  printf '1 %s start\\n2 postgres start\\n3 postgres health_status: healthy\\n4 clickhouse start\\n' "$APP"
  if [[ $MODE != partial ]]; then echo '5 clickhouse health_status: healthy'; fi
fi
touch "$RUNNER_TEMP/started"
[[ $MODE != exited ]] || exit 1
exec sleep 15
`, {mode:0o755});
    writeFileSync(path.join(dir,'bin/e2e'), `#!/bin/bash
if [[ $1 == up ]]; then
  for i in {1..100}; do [[ ! -e $RUNNER_TEMP/started ]] || break; sleep 0.01; done
  if [[ $MODE == exited ]]; then sleep 0.1; fi
fi
`, {mode:0o755});
    const result = run('cd "$RUNNER_TEMP"\n'+boot.run, [], {RUNNER_TEMP:dir,E2E_PHASE_LOG:path.join(dir,'phase'), PATH:`${dir}:${process.env.PATH}`, APP:stack==='standalone'?'app':'backend', E2E_STACK:stack, MODE:mode, IMAGE_TAG:'e2e'});
    assert.equal(result.status,0,result.stderr);
    const receipt = path.join(dir,'stack-profile/service-events.tsv');
    assert.equal(existsSync(receipt), mode==='complete');
    assert.equal(existsSync(receipt+'.status'), mode!=='complete');
  }
});
