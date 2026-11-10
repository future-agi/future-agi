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
    assert.deepEqual(entry, { job: job.id, driver_status: job.integration.status,
      driver_module: job.integration.module, flow_id: job.integration.flow_id,
      flow: job.integration.flow, complete_job_qualified: false });
    assert.equal(job.integration.complete_job_qualified, false);
    const flow = await readFile(new URL(`../../flows/skills/${entry.flow}`, import.meta.url), 'utf8');
    assert.ok(flow.includes(entry.flow_id));
    if (entry.driver_module) {
      const module = await import(new URL(entry.driver_module, benchmarkRoot).href);
      assert.equal(typeof module.createDriver, 'function');
      assert.equal(entry.driver_status, 'incomplete');
    } else {
      assert.equal(entry.driver_status, 'missing');
      // No placeholders are silently presented as complete drivers.
      await assert.rejects(access(new URL(`drivers/${job.id.toLowerCase()}.mjs`, benchmarkRoot)), { code: 'ENOENT' });
    }
  }
});

for (const jobId of ['J1', 'J2', 'J3', 'J6']) {
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
