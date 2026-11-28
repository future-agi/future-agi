import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createRegisteredDriver } from '../../benchmarks/context-hub/dispatcher.mjs';
import { getJob } from '../../benchmarks/context-hub/jobs.mjs';

test('J2 registration loads a fail-closed driver, not prerequisite artifacts as native proof', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'j2-driver-'));
  const previous = { input: process.env.J2_DRIVER_INPUT, output: process.env.J2_DRIVER_OUTPUT };
  try {
    process.env.J2_DRIVER_INPUT = join(directory, 'input.json');
    process.env.J2_DRIVER_OUTPUT = directory;
    const input = { pgUrl: 'postgresql://invalid@localhost:1/none', appUrl: 'http://localhost:3100', apiUrl: 'http://localhost:8100', contextId: '10000000-0000-4000-8000-000000000001', actor: { access: 'synthetic', organizationId: 'org', workspaceId: 'workspace' }, provenance: { dirty: true } };
    await writeFile(process.env.J2_DRIVER_INPUT, JSON.stringify(input), { mode: 0o600 });
    const args = { jobId: 'J2', runId: 'j2-abort', registration: { evidence_scope: 'synthetic', cohort: 'configured' } };
    assert.equal(getJob('J2').integration.status, 'incomplete');
    assert.equal(getJob('J2').integration.complete_job_qualified, false);
    const driver = await createRegisteredDriver(args);
    for (const method of ['perform', 'observe', 'teardown']) assert.equal(typeof driver[method], 'function');
    // No stage ran, so no independent evidence exists to observe.
    await assert.rejects(driver.observe({ id: 'historical-reproduced' }, { signal: new AbortController().signal }));
    await assert.rejects(driver.perform({ id: 'qualify-application' }, { signal: AbortSignal.abort() }), /cancelled/);
    assert.deepEqual(await driver.teardown(), { qualified: true });
    await writeFile(process.env.J2_DRIVER_INPUT, JSON.stringify({ ...input, apiUrl: 'https://example.invalid' }));
    await assert.rejects(createRegisteredDriver(args), /owned localhost/);
    await writeFile(process.env.J2_DRIVER_INPUT, JSON.stringify(input));
    await assert.rejects(createRegisteredDriver({ ...args, registration: { evidence_scope: 'customer' } }), /synthetic fixture/);
  } finally {
    if (previous.input === undefined) delete process.env.J2_DRIVER_INPUT; else process.env.J2_DRIVER_INPUT = previous.input;
    if (previous.output === undefined) delete process.env.J2_DRIVER_OUTPUT; else process.env.J2_DRIVER_OUTPUT = previous.output;
    await rm(directory, { recursive: true, force: true });
  }
});
