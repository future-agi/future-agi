// Manual CI experiment: partition whole files. The independent reference is
// an unrestricted Vitest run of the same tree, config and worker count.
import assert from 'node:assert/strict';
import { readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const read = file => JSON.parse(readFileSync(file, 'utf8'));
const write = (file, value) => writeFileSync(file, JSON.stringify(value));

// Use Vitest 3's reporter API for stable test IDs (including parameterized or
// identically named cases) and file timings including jsdom/setup/collection.
// Keep the native blob separately for diagnosis. Neither report sets the exit.
function measure(module) {
  const { prepareDuration, environmentSetupDuration, setupDuration, collectDuration, duration } = module.diagnostic();
  return {
    file: path.relative(process.cwd(), module.moduleId),
    milliseconds: prepareDuration + environmentSetupDuration + setupDuration + collectDuration + duration,
    state: module.state(),
    cases: [...module.children.allTests()].map(test => [test.id, test.fullName, test.options.mode, test.result().state]),
  };
}

export default class InventoryReporter {
  onTestRunEnd(modules, errors, reason) {
    write(process.env.CI_VITEST_INVENTORY, {
      passed: reason === 'passed' && errors.length === 0,
      modules: modules.map(measure),
    });
  }
}

function inventory(report) {
  assert.equal(report.passed, true, 'Vitest reference or leg did not pass');
  const rows = new Map();
  for (const { file, milliseconds, state, cases } of report.modules) {
    assert(file && !file.startsWith('../') && !path.isAbsolute(file) && !rows.has(file), `invalid or duplicate file: ${file}`);
    assert(Number.isFinite(milliseconds) && milliseconds >= 0, `invalid duration: ${file}`);
    assert(['passed', 'skipped'].includes(state), `unexpected module state: ${file}: ${state}`);
    const ids = new Set();
    for (const [id, title, , result] of cases) {
      assert(typeof id === 'string' && id && !ids.has(id) && typeof title === 'string', `invalid or duplicate test ID: ${file}: ${id}`);
      ids.add(id);
      assert(['passed', 'skipped'].includes(result), `unexpected case state: ${file}: ${result}`);
    }
    rows.set(file, { milliseconds, state, cases: cases.map(item => JSON.stringify(item)).sort() });
  }
  assert([...rows.values()].some(row => row.cases.length > 0), 'no test cases in inventory');
  return rows;
}

function plan(discovered, history) {
  assert(discovered.length >= 2, 'two shards require at least two files');
  const bins = [[], []];
  const totals = [0, 0];
  const weight = file => Math.max(1, history.get(file)?.milliseconds ?? 1000);
  const ordered = [...discovered].sort((a, b) => weight(b) - weight(a) || (a < b ? -1 : a > b ? 1 : 0));
  for (const file of ordered) {
    const i = totals[0] <= totals[1] ? 0 : 1;
    bins[i].push(file);
    totals[i] += weight(file);
  }
  return bins;
}

export function recordPlan(discovered, historyFile, outputFile) {
  const bins = plan(discovered, historyFile ? inventory(read(historyFile)) : new Map());
  write(outputFile, bins);
  return bins;
}

export function selectedFiles(bins, index, discovered) {
  assert(bins.length === 2 && [1, 2].includes(index) && bins.every(bin => bin.length), 'invalid two-way shard');
  const all = bins.flat();
  assert.equal(new Set(all).size, all.length, 'file assigned twice');
  assert.deepEqual(all.sort(), [...discovered].sort(), 'plan differs from unfiltered discovery');
  return new Set(bins[index - 1]);
}

function verify(reference, bins, legs) {
  assert.equal(legs.length, 2, 'missing shard');
  for (const [i, leg] of legs.entries()) {
    const expected = selectedFiles(bins, i + 1, reference.keys());
    assert.deepEqual(new Set(leg.keys()), expected, `shard ${i + 1} files do not match plan`);
    for (const [file, result] of leg) {
      assert.equal(result.state, reference.get(file).state, file);
      assert.deepEqual(result.cases, reference.get(file).cases, `test identity/outcome mismatch: ${file}`);
    }
  }
}

function verdictSeconds(createdAt, jobs) {
  const dispatch = Date.parse(createdAt);
  const elapsed = (jobName, stepName) => {
    const matches = jobs.filter(job => job.name === jobName);
    assert.equal(matches.length, 1, `expected one ${jobName} job`);
    const step = matches[0].steps.find(step => step.name === stepName);
    assert(step?.status === 'completed' && step.conclusion === 'success', `missing successful ${jobName} verdict`);
    const seconds = (Date.parse(step.completed_at) - dispatch) / 1000;
    assert(Number.isFinite(seconds) && seconds >= 0, `invalid timestamp for ${jobName}`);
    return seconds;
  };
  return [elapsed('profile-reference', 'Profile unrestricted reference'),
    Math.max(...[1, 2].map(i => elapsed(`profile-shard-${i}`, 'Profile exact file shard')))];
}

function selfTest() {
  const fixture = (file, milliseconds, state = 'passed') => ({
    file, milliseconds, state: 'passed', cases: [[`${file}_0`, 'a case', 'run', state]],
  });
  const report = (...modules) => ({ passed: true, modules });
  const a = fixture('long.test.js', 9), b = fixture('medium.test.js', 5, 'skipped'), c = fixture('short.test.js', 4);
  const ref = inventory(report(c, a, b)), files = [...ref.keys()], bins = plan(files, ref);
  assert.deepEqual(bins, [[a.file], [b.file, c.file]]);
  assert.deepEqual(plan([c.file, a.file, 'new.js'], ref), [['new.js'], [a.file, c.file]]);
  assert.deepEqual(plan(files, new Map()), [[a.file, c.file], [b.file]]);
  assert.deepEqual(plan([...files].reverse(), new Map()), plan(files, new Map()));
  const time = s => new Date(s * 1000).toISOString();
  const job = (name, step, s) => ({ name, steps: [{ name: step, status: 'completed', conclusion: 'success', completed_at: time(s) }] });
  const jobs = [job('profile-shard-2', 'Profile exact file shard', 55), job('profile-shard-1', 'Profile exact file shard', 80),
    job('profile-reference', 'Profile unrestricted reference', 90)];
  assert.deepEqual(verdictSeconds(time(10), jobs), [80, 70]);
  assert.throws(() => verdictSeconds(time(10), jobs.slice(1)), /expected one/);
  assert.throws(() => verdictSeconds(time(100), jobs), /invalid timestamp/);
  assert.throws(() => verdictSeconds(time(10), [{ ...jobs[0], steps: [] }, ...jobs.slice(1)]), /missing successful/);
  const good = [inventory(report(a)), inventory(report(b, c))];
  verify(ref, bins, good);
  assert.throws(() => verify(ref, bins, good.slice(0, 1)), /missing shard/);
  assert.throws(() => verify(ref, bins, [good[0], good[0]]), /files do not match/);
  assert.throws(() => selectedFiles(bins, 1, ['new.test.js']), /unfiltered discovery/);
  assert.throws(() => selectedFiles([[a.file], [a.file]], 2, ref.keys()), /assigned twice/);
  assert.throws(() => inventory(report(a, a)), /duplicate file/);
  assert.throws(() => inventory(report({ ...a, cases: [...a.cases, ...a.cases] })), /duplicate test ID/);
  assert.throws(() => inventory(report(fixture('bad.test.js', 1, 'failed'))), /unexpected case state/);
  assert.throws(() => inventory({ ...report(a), passed: false }), /did not pass/);
  assert.throws(() => verify(ref, bins, [inventory(report(fixture(a.file, 1, 'skipped'))), good[1]]), /outcome mismatch/);
}

if (import.meta.url === (process.argv[1] && pathToFileURL(process.argv[1]).href)) {
  const [, , command, ...files] = process.argv;
  if (command === 'self-test' && files.length === 0) selfTest();
  else if (command === 'latency' && files.length === 2) {
    const [reference, shards] = verdictSeconds(read(files[0]).created_at, read(files[1]).jobs);
    console.log(`Dispatch to successful Vitest exit (includes queue, setup and reporters): unrestricted ${reference}s; both shards ${shards}s. Excludes post-run parity verification.`);
  } else if (command === 'verify' && files.length === 4) {
    const ref = inventory(read(files[0]));
    verify(ref, read(files[1]), files.slice(2).map(file => inventory(read(file))));
    console.log(`Both shards matched the unsharded reference: ${ref.size} files, ${[...ref.values()].reduce((n, row) => n + row.cases.length, 0)} test cases.`);
  } else throw new Error('Expected self-test | latency RUN JOBS | verify REFERENCE PLAN LEG1 LEG2');
}
