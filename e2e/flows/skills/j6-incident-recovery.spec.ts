import { spawn } from 'node:child_process';
import { createHash, randomBytes, randomUUID } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { test, expect } from '../../lib/scope-actors';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';
import { startGuest } from '../../lib/skills-r3/j6-guest.mjs';
import { actorOf, prepareJ6Scenario } from '../../lib/skills-r3/j6-scenario.mjs';
import { buildJ6Registration } from '../../lib/skills-r3/j6-registration.mjs';

// Synthetic fixture on the owned cloud VM stack. The registered J6 driver runs the
// whole job through public UI, CLI and packaged-application interfaces; this flow
// prepares its retained incident and independently re-reads PG, API and guest
// ledgers afterwards. A VM container guest is not an E2B/Simulation qualification.
test('SKILLS-E2E-063: recover an incident through the declared eight-fault matrix without false recovery or duplicate business effects', {
  tag: ['@flow'],
  annotation: flowAnnotation({ id: 'SKILLS-E2E-063', area: 'skills',
    userGoal: 'Choose an eligible earlier release for a retained incident, roll back with protection, restore the checked-in lock, resume or reconstruct each application thread and see recovered, unknown and lost consumers stay distinct',
    steps: [
      'prepare a retained refund incident, three release identities, a retired child and a quarantined alternative through public APIs and the packaged application',
      'open the declared context from the retained incident and review the complete closure of an eligible lower-numbered release',
      'request, reject a stale ABA packet for, approve and execute the lower-number rollback through the reviewer inbox',
      'issue a replacement credential and restore the exact checked-in lock without losing local work',
      'run the application through a registry outage after a persisted refund and resume the same session and checkpoint',
      'inspect expired-credential and lost-mapping threads, close what can be closed and start reviewed clean threads without replaying the refund',
      'recheck the original case once and inspect the declared cohort',
    ],
    backendChecks: [
      'Postgres has exactly one lower-number label event and none for the stale ABA packet',
      'the independent business ledger holds one applied refund and one request per order with no duplicate',
      'the resumed thread keeps its original session identity and the receipt precedes the checkpoint advance',
      'the retired child delivers its exact retained bytes and the quarantined alternative is refused',
      'the expired-credential session is denied by the public access check and the lost-mapping and never-observed consumers stay unknown',
    ],
  }),
}, async ({ scopeActors, scopeProbe: probe }, testInfo) => {
  test.setTimeout(2_400_000); // fixture 360s, driver deadline 1500s, oracle reads 120s.
  expect(process.platform, 'cloud Linux only').toBe('linux');
  const python = process.env.J6_APP_PYTHON;
  const wheels = process.env.J6_WHEELS;
  expect(python && wheels, 'Install the pinned SDK and application wheels on the cloud VM').toBeTruthy();
  const author = actorOf(scopeActors.member);
  const reviewer = actorOf(scopeActors.ownerA);
  const prefix = `e2e-j6-${Date.now().toString(36)}`;
  const dir = testInfo.outputPath('j6');
  await mkdir(dir, { recursive: true });
  const guest = await startGuest({ runId: prefix, wheelsDir: wheels!, fixtureDir: resolve('fixtures/skills-r3/j6-guest') });
  let stopped = false;
  try {
    const egress = await guest.egress();
    expect(egress, 'the guest reaches only its sidecar').toEqual({ 'skills-fakes': 'open', '1.1.1.1': 'closed', '8.8.8.8': 'closed' });
    const seed = await prepareJ6Scenario({ apiUrl: E2E.apiUrl, appUrl: E2E.appUrl, author, reviewer, probe, guest, python: python!,
      projectDir: resolve(dir, 'project'), prefix, log: (line: string) => console.log(`[j6-prepare] ${line}`) });
    const inputPath = resolve(dir, 'driver-input.private.json');
    const outputDir = resolve(dir, 'observations');
    const provenance = JSON.parse(await readFile(process.env.J6_PROVENANCE ?? '/dev/null', 'utf8').catch(() => 'null') ?? 'null');
    const { registration, provenance: filled } = await buildJ6Registration({ seed, provenance, wheelsDir: wheels!, guest, author, apiUrl: E2E.apiUrl });
    await writeFile(resolve(dir, 'registration.json'), JSON.stringify(registration, null, 2), { mode: 0o600 });
    await writeFile(inputPath, JSON.stringify({ ...seed, outputDir, provenance: filled, python, guest: guest.describe(),
      actors: { author, reviewer }, appUrl: E2E.appUrl, apiUrl: E2E.apiUrl }), { mode: 0o600 });
    if (process.env.J6_PREPARE_ONLY) {
      console.log(`[j6-prepare] prepared only; guest left running; input ${inputPath}`);
      stopped = true; // development rehearsal: run the driver by hand, then stop the guest
      return;
    }
    const runId = `j6-${randomBytes(4).toString('hex')}`;
    const code = await new Promise<number>((done, reject) => {
      const child = spawn('node', [resolve('benchmarks/context-hub/cli.mjs'), 'run', 'J6', runId, resolve(dir, 'registration.json'), 'registered', outputDir],
        { stdio: ['ignore', 'inherit', 'inherit'], env: { ...process.env, J6_DRIVER_INPUT: inputPath }, timeout: 1_800_000 });
      child.once('error', reject);
      child.once('exit', (exit) => done(exit ?? 1));
    });
    stopped = true; // the driver's own teardown destroys the guest
    expect(code, 'registered J6 driver exit code').toBe(0);
    const report = JSON.parse(await readFile(resolve(outputDir, `${runId}.json`), 'utf8'));
    expect(report.status).toBe('completed');
    expect(report.teardown).toBe('confirmed');
    const matrix = JSON.parse(await readFile(resolve(outputDir, runId, 'fault-matrix.json'), 'utf8'));
    expect(matrix.rows).toHaveLength(8);
    expect(matrix.rows.every((row: any) => row.false_recovered === false && row.duplicate_effect === false)).toBe(true);
    const events = await probe.pg<any>('SELECT from_version, to_version FROM model_hub_hubskilllabelevent WHERE skill_id=$1 ORDER BY id', [seed.contextId]);
    const rollbacks = events.filter((row: any) => row.from_version === 3 && row.to_version === 1);
    expect(rollbacks, 'one lower-number rollback event').toHaveLength(process.env.J6_ORACLE_FAULT === 'rollback' ? 2 : 1);
    await testInfo.attach('j6-run-summary', { body: JSON.stringify({ evidence_class: 'synthetic fixture, VM container guest, not E2B/Simulation', measurement: report.measurement, checks: report.checks, matrix }), contentType: 'application/json' });
  } finally {
    if (!stopped) await guest.stop().catch(() => {});
    void createHash; void randomUUID;
  }
});
