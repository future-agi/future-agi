import test from 'node:test';
import assert from 'node:assert/strict';
import { access, mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { JOBS } from '../../benchmarks/context-hub/jobs.mjs';
import { createRegisteredDriver } from '../../benchmarks/context-hub/dispatcher.mjs';
import { main } from '../../benchmarks/context-hub/cli.mjs';
import { readSkillResult } from '../../benchmarks/context-hub/drivers/j4.mjs';

const benchmarkRoot = new URL('../../benchmarks/context-hub/', import.meta.url);

test('all six jobs register existing prerequisite flows without claiming complete qualification', async () => {
  const manifest = JSON.parse(await readFile(new URL('./qualification-contract.json', import.meta.url), 'utf8'));
  assert.deepEqual(manifest.job_integrations.map(({ job }) => job), JOBS.map(({ id }) => id));
  for (const job of JOBS) {
    const entry = manifest.job_integrations.find(({ job: id }) => job.id === id);
    // The sealed qualification contract is the retained prerequisite snapshot.
    // J1 and J6 now have executable, still-incomplete drivers; do not rewrite
    // that historical snapshot or treat registering a module as qualification.
    const sealedMissing = { J1: './drivers/j1.mjs', J6: './drivers/j6.mjs' };
    const current = job.id in sealedMissing ? {
      ...entry, driver_status: 'incomplete', driver_module: sealedMissing[job.id],
    } : entry;
    if (job.id in sealedMissing) assert.deepEqual(entry, {
      job: job.id, driver_status: 'missing', driver_module: null,
      flow_id: job.integration.flow_id, flow: job.integration.flow, complete_job_qualified: false,
    });
    assert.deepEqual(current, { job: job.id, driver_status: job.integration.status,
      driver_module: job.integration.module, flow_id: job.integration.flow_id,
      flow: job.integration.flow, complete_job_qualified: false });
    assert.equal(job.integration.complete_job_qualified, false);
    const flow = await readFile(new URL(`../../flows/skills/${entry.flow}`, import.meta.url), 'utf8');
    assert.ok(flow.includes(entry.flow_id));
    if (current.driver_module) {
      const module = await import(new URL(current.driver_module, benchmarkRoot).href);
      assert.equal(typeof module.createDriver, 'function');
      assert.equal(current.driver_status, 'incomplete');
    } else {
      assert.equal(entry.driver_status, 'missing');
      // No placeholders are silently presented as complete drivers.
      await assert.rejects(access(new URL(`drivers/${job.id.toLowerCase()}.mjs`, benchmarkRoot)), { code: 'ENOENT' });
    }
  }
});

test('J1 registration fails closed without private cloud configuration in both cohorts', async () => {
  const previous = process.env.J1_DRIVER_INPUT;
  delete process.env.J1_DRIVER_INPUT;
  try {
    for (const cohort of ['cold', 'configured']) {
      // An untrusted registration cannot replace the repository-owned module.
      const registration = { job: 'J1', cohort, driver_module: '/untrusted/ignored.mjs' };
      await assert.rejects(createRegisteredDriver({ jobId: 'J1', runId: 'unconfigured', registration }),
        /Private cloud-local J1 input is required/);
    }
  } finally {
    if (previous === undefined) delete process.env.J1_DRIVER_INPUT;
    else process.env.J1_DRIVER_INPUT = previous;
  }
});

// Every job now registers a driver module; the CLI's not_measured record path
// applies again as soon as any job's registry entry returns to 'missing'.
for (const jobId of JOBS.filter(({ integration }) => integration.status === 'missing').map(({ id }) => id)) {
  test(`${jobId} dispatcher records unavailable cold/configured runs with null counts and nonzero exit`, async () => {
    const directory = await mkdtemp(join(tmpdir(), 'skills-dispatch-'));
    try {
      for (const cohort of ['cold', 'configured']) {
        const registration = { job: jobId, cohort, driver_module: '/untrusted/ignored.mjs' };
        assert.equal(await createRegisteredDriver({ jobId, runId: 'missing', registration }), null);
        const path = join(directory, `${cohort}-input.json`);
        await writeFile(path, JSON.stringify(registration));
        const output = join(directory, cohort);
        const args = ['run', jobId, 'missing', path, 'registered', output];
        assert.equal(await main(args), 1);
        const report = JSON.parse(await readFile(join(output, 'missing.json'), 'utf8'));
        assert.equal(report.status, 'not_measured');
        assert.equal(report.cohort, cohort);
        assert.equal(report.driver_status, 'missing');
        assert.equal(report.claim, 'Not qualified');
        for (const key of ['operator_actions', 'screen_transitions', 'handoffs', 'reconciliations', 'action_ratio', 'ten_x_actions']) {
          assert.equal(report[key], null);
        }
        assert.deepEqual(await readdir(output), ['missing.json'], 'unavailable runs have no invented ledger');
        await assert.rejects(main(args), { code: 'EEXIST' }, 'never overwrite a prior attempt');
      }
    } finally { await rm(directory, { recursive: true, force: true }); }
  });
}

test('an unavailable explicit driver is recorded as not_measured with null counts and no ledger', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'skills-dispatch-explicit-'));
  try {
    const driverPath = join(directory, 'unavailable-driver.mjs');
    await writeFile(driverPath, 'export function createDriver() { return null; }\n');
    for (const cohort of ['cold', 'configured']) {
      const path = join(directory, `${cohort}-input.json`);
      await writeFile(path, JSON.stringify({ job: 'J1', cohort }));
      const output = join(directory, cohort);
      const args = ['run', 'J1', 'missing', path, driverPath, output];
      assert.equal(await main(args), 1);
      const report = JSON.parse(await readFile(join(output, 'missing.json'), 'utf8'));
      assert.equal(report.status, 'not_measured');
      assert.equal(report.cohort, cohort);
      assert.equal(report.driver_status, 'explicit');
      assert.equal(report.claim, 'Not qualified');
      for (const key of ['operator_actions', 'screen_transitions', 'handoffs', 'reconciliations', 'action_ratio', 'ten_x_actions']) {
        assert.equal(report[key], null);
      }
      assert.deepEqual(await readdir(output), ['missing.json'], 'unavailable runs have no invented ledger');
      await assert.rejects(main(args), { code: 'EEXIST' }, 'never overwrite a prior attempt');
    }
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('J6 registered driver refuses to run without its private owned-stack input and invents no ledger', async () => {
  const previous = process.env.J6_DRIVER_INPUT;
  delete process.env.J6_DRIVER_INPUT;
  const directory = await mkdtemp(join(tmpdir(), 'skills-j6-dispatch-'));
  try {
    for (const cohort of ['cold', 'configured']) {
      const path = join(directory, `${cohort}-input.json`);
      await writeFile(path, JSON.stringify({ job: 'J6', cohort }));
      // The driver needs its private input (and, for cold, an application qualification it does not implement): no report.
      await assert.rejects(main(['run', 'J6', 'missing', path, 'registered', join(directory, cohort)]));
      await assert.rejects(readdir(join(directory, cohort)), { code: 'ENOENT' });
    }
  } finally {
    if (previous !== undefined) process.env.J6_DRIVER_INPUT = previous;
    await rm(directory, { recursive: true, force: true });
  }
});

test('dispatcher refuses unknown jobs instead of resolving preregistration paths', async () => {
  await assert.rejects(createRegisteredDriver({ jobId: 'J7', runId: 'invalid', registration: {} }), /Unknown benchmark job/);
});

test('J4 independent API observation uses native Fetch boolean status and refuses errors', async (t) => {
  const input = { apiUrl: 'http://localhost:8100', headers: {}, path: 'opaque/draft/', signal: new AbortController().signal };
  const mock = t.mock.method(globalThis, 'fetch', async () => new Response(JSON.stringify({ result: { revision: 2 } }), { status: 200 }));
  assert.deepEqual(await readSkillResult(input), { revision: 2 });
  assert.equal(mock.mock.calls[0].arguments[0], 'http://localhost:8100/model-hub/skills/opaque/draft/');
  assert.equal(mock.mock.calls[0].arguments[1].signal, input.signal);
  mock.mock.mockImplementation(async () => new Response('{}', { status: 403 }));
  await assert.rejects(readSkillResult(input), /Independent public observation unavailable/);
});
