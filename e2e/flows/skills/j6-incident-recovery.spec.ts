import { spawn } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { mkdir, readFile, unlink, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { test, expect } from '../../lib/scope-actors';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';
import { startGuest } from '../../lib/skills-r3/j6-guest.mjs';
import { actorOf, prepareJ6Scenario } from '../../lib/skills-r3/j6-scenario.mjs';
import { buildJ6Registration } from '../../lib/skills-r3/j6-registration.mjs';

// Synthetic fixture on the owned cloud VM stack. The registered J6 driver runs the
// whole job through public UI, CLI and packaged-application interfaces; this flow
// prepares its retained incident, then re-reads Postgres and the independent oracle
// artifacts the driver wrote (it read the guest ledgers itself before teardown).
// A VM container guest is not an E2B/Simulation qualification.
test('SKILLS-E2E-063: recover an incident through the declared eight-fault matrix without false recovery or duplicate business effects', {
  tag: ['@flow'],
  annotation: flowAnnotation({ id: 'SKILLS-E2E-063', area: 'skills',
    userGoal: 'Choose an eligible earlier release for a retained incident, roll back with protection, restore the checked-in lock, resume or reconstruct each application thread and see recovered, unknown and lost consumers stay distinct',
    steps: [
      'prepare a retained refund incident, three release identities, a retired child and a quarantined alternative through public APIs and the packaged application',
      'open the retained incident link on the recovery view, where the newest eligible release older than the failing one is preselected, and review its complete closure',
      'request a lower-number rollback from the preselected reviewer (whoever last moved production), let another releaser move production away and back, see the approved stale packet refused and superseded by the fresh request, then approve and execute that fresh rollback from its exact review link',
      'issue a replacement credential and restore the lock of the last reviewed commit with one verified command without losing local work',
      'run the application through a registry outage after a persisted refund and resume the same session and checkpoint',
      'plan every registered thread with one command, review that plan once and apply exactly it: resume the paused thread, close the expired one and start reviewed clean threads without replaying the refund',
      'reconcile the apply output with the business ledger, recheck the original case once from the same reviewed plan and inspect the declared cohort',
    ],
    backendChecks: [
      'Postgres has exactly one lower-number label event, none for the stale ABA packet, and the approved stale request is superseded',
      'the independent business ledger holds one applied refund and one request per order with no duplicate',
      'an apply with any digest other than the reviewed plan is refused and sends no business request',
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
  const provenancePath = process.env.J6_PROVENANCE;
  if (!provenancePath) throw new Error('Supply the GCP registration manifest on the worker');
  const provenance = JSON.parse(await readFile(provenancePath, 'utf8'));
  const author = actorOf(scopeActors.ownerA); // organization admin: lists reviewers and issues credentials
  // A second organization administrator: moving the protected production label needs a current administrator other than the author.
  const releaser = await (scopeActors as any).invite('releaser', 8, [scopeActors.ownerA.workspaceId]);
  const reviewer = actorOf(releaser);
  const prefix = `e2e-j6-${Date.now().toString(36)}`;
  const dir = testInfo.outputPath('j6');
  await mkdir(dir, { recursive: true });
  const inputPath = resolve(dir, 'driver-input.private.json'); // credential-bearing: removed below
  const guest = await startGuest({ runId: prefix, wheelsDir: wheels!, fixtureDir: resolve('fixtures/skills-r3/j6-guest') });
  try {
    const egress = await guest.egress();
    expect(egress, 'the guest reaches only its sidecar').toEqual({ 'skills-fakes': 'open', '1.1.1.1': 'closed', '8.8.8.8': 'closed' });
    const seed = await prepareJ6Scenario({ apiUrl: E2E.apiUrl, appUrl: E2E.appUrl, author, reviewer, probe, guest, python: python!,
      projectDir: resolve(dir, 'project'), prefix, log: (line: string) => console.log(`[j6-prepare] ${line}`) });
    const outputDir = resolve(dir, 'observations');
    const { registration, provenance: filled } = await buildJ6Registration({ seed, provenance, wheelsDir: wheels!, guest, author, apiUrl: E2E.apiUrl });
    if (process.env.SKILLS_GCP_CASE === '063') await testInfo.attach('registration-guest', { contentType: 'application/json', body: JSON.stringify({ status: 'registered', provenance: filled }) });
    await writeFile(resolve(dir, 'registration.json'), JSON.stringify(registration, null, 2), { mode: 0o600 });
    await writeFile(inputPath, JSON.stringify({ ...seed, outputDir, provenance: filled, python, guest: guest.describe(),
      actors: { author, reviewer }, appUrl: E2E.appUrl, apiUrl: E2E.apiUrl }), { mode: 0o600 });
    const runId = `j6-${randomBytes(4).toString('hex')}`;
    const code = await new Promise<number>((done, reject) => {
      const child = spawn('node', [resolve('benchmarks/context-hub/cli.mjs'), 'run', 'J6', runId, resolve(dir, 'registration.json'), 'registered', outputDir],
        { stdio: ['ignore', 'inherit', 'inherit'], env: { ...process.env, J6_DRIVER_INPUT: inputPath }, timeout: 1_800_000 });
      child.once('error', reject);
      child.once('exit', (exit) => done(exit ?? 1));
    });
    expect(code, 'registered J6 driver exit code').toBe(0);
    const report = JSON.parse(await readFile(resolve(outputDir, `${runId}.json`), 'utf8'));
    expect(report.status).toBe('completed');
    expect(report.teardown).toBe('confirmed');
    const matrix = JSON.parse(await readFile(resolve(outputDir, runId, 'fault-matrix.json'), 'utf8'));
    expect(matrix.rows).toHaveLength(8);
    expect(matrix.rows.every((row: any) => row.false_recovered === false && row.duplicate_effect === false)).toBe(true);
    // One-sided fallibility: J6_ORACLE_FAULT changes only the expectation, never the observed system.
    const fault = process.env.J6_ORACLE_FAULT;
    const observed = async (name: string) => JSON.parse(await readFile(resolve(outputDir, runId, `${name}.json`), 'utf8'));
    const business = await observed('business-oracle');
    expect(business.business.request_count, 'independent business request oracle').toBe(fault === 'duplicate' ? 5 : 4);
    expect(business.business.operations).toHaveLength(4);
    expect((await observed('checkpoint-oracle')).value.same_session, 'original session identity').toBe(fault === 'session' ? false : true);
    expect((await observed('original-case')).retired_child_bytes_delivered, 'retired child bytes at the model boundary').toBe(fault === 'child' ? false : true);
    expect((await observed('final-cohort')).verdicts.U.states, 'unobserved consumer').toEqual(fault === 'unknown' ? ['matches_target'] : ['unknown']);
    expect((await observed('denial-oracle')).value.expired_credential_denied, 'expired credential denial').toBe(fault === 'denied' ? false : true);
    expect((await observed('aba-rejection')).status, 'stale ABA execute').toBe(fault === 'aba' ? 200 : 409);
    const refusal = await observed('apply-refusal');
    expect(refusal.refused_exit_nonzero && refusal.business_requests_after === refusal.business_requests_before, 'an unreviewed plan digest applies nothing').toBe(fault === 'unreviewed' ? false : true);
    // The fresh packet closed the approved-but-stale request: Postgres shows an approval and the superseded status, and no label event.
    const aba = await observed('aba-rejection');
    const staleRows = await probe.pg<any>('SELECT r.status, (SELECT count(*)::int FROM model_hub_hubskillreleasedecision d JOIN model_hub_hubskillreleasepacket p ON p.id=d.packet_id WHERE p.request_id=r.id AND d.decision=\'approve\') AS approvals FROM model_hub_hubskillreleaserequest r WHERE r.id=$1', [aba.request_id]);
    expect(staleRows, 'stale approved request closed by the fresh packet').toEqual([{ status: fault === 'superseded' ? 'approved' : 'superseded', approvals: 1 }]);
    const events = await probe.pg<any>('SELECT from_version, to_version FROM model_hub_hubskilllabelevent WHERE skill_id=$1 ORDER BY id', [seed.contextId]);
    const rollbacks = events.filter((row: any) => row.from_version === 3 && row.to_version === 1);
    expect(rollbacks, 'one lower-number rollback event').toHaveLength(process.env.J6_ORACLE_FAULT === 'rollback' ? 2 : 1);
    await testInfo.attach('j6-run-summary', { body: JSON.stringify({ evidence_class: 'synthetic fixture, VM container guest, not E2B/Simulation', measurement: report.measurement, checks: report.checks, matrix }), contentType: 'application/json' });
  } finally {
    // The driver's own teardown already destroyed the guest; stopping again is idempotent and
    // guarantees nothing leaks if the driver died before it could clean up.
    await guest.stop().catch(() => {});
    await unlink(inputPath).catch(() => {});
  }
});
