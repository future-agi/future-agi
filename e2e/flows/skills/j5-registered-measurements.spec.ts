import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { test, expect } from '../../lib/fixtures';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// The three separate J5 measurements through the unchanged registered-driver
// CLI. Without an external Checkpoint P receipt the protected runner must
// refuse before any campaign write, and must not fabricate counts.
test('SKILLS-E2E-056: registered J5 measurements run through the protected CLI without fabricated counts or campaign writes', {
  tag: ['@flow'],
  annotation: flowAnnotation({ id: 'SKILLS-E2E-056', area: 'skills',
    userGoal: 'Run cold adoption, recurring rollout and recovery as three separate preregistered complete-job measurements',
    steps: ['preregister each measurement with its declarative initial condition, fixture-derived expectations and real provenance',
      'run the registered J5 driver through the benchmark CLI for each measurement',
      'retain each result and confirm the protected Checkpoint P gate before any campaign write'],
    backendChecks: ['independent Postgres shows no campaign created by any refused measurement',
      'refused results keep null counts, null ratios and the explicit Checkpoint P reason'],
  }),
}, async ({ actor, probe }, testInfo) => {
  test.setTimeout(600_000);
  const python = process.env.CTXHUB_J5_PYTHON;
  const provenancePath = process.env.J5_PROVENANCE_JSON;
  if (!python || !provenancePath) throw new Error('Select the installed SDK and retained provenance on the approved VM');
  const nativeConfig = { executable: process.env.J5_CODEX_BINARY, sha256: process.env.J5_CODEX_SHA256 };
  if (!nativeConfig.executable || !nativeConfig.sha256) throw new Error('Select the pinned native Codex executable and digest on the approved VM');
  const driverModule = '../../benchmarks/context-hub/drivers/j5.mjs';
  const { j5Registration, J5_MEASUREMENTS } = await import(driverModule) as any;
  const provenance = JSON.parse(await readFile(provenancePath, 'utf8'));
  const output = join(process.env.J5_EVIDENCE_DIR || testInfo.outputDir, 'registered');
  await mkdir(output, { recursive: true });
  const env = { ...process.env, J5_HUB_URL: E2E.apiUrl, J5_HUB_HEADERS_JSON: JSON.stringify({ Authorization: `Bearer ${actor.tokens.access}`,
    'X-Organization-Id': actor.organizationId, 'X-Workspace-Id': actor.workspaceId }) };
  const campaigns = async () => (await probe.pg<{ count: string }>(
    'SELECT COUNT(*)::text AS count FROM model_hub_hubskillcontextcampaign WHERE organization_id = $1', [actor.organizationId]))[0].count;
  const before = await campaigns();
  const attempts = [];
  const stamp = Date.now().toString(36);
  for (const label of Object.keys(J5_MEASUREMENTS)) {
    const registration = j5Registration({ label, provenance, python, evidenceDir: join(output, label), native: nativeConfig });
    expect(registration.checkpoint_P_receipt).toBeUndefined();
    const registrationPath = join(output, `${label}-${stamp}-registration.json`);
    await writeFile(registrationPath, JSON.stringify(registration, null, 2), { flag: 'wx', mode: 0o600 });
    const runId = `r6-j5-${label.replace(/_/g, '-')}-${stamp}`;
    const args = [resolve('benchmarks/context-hub/cli.mjs'), 'run', 'J5', runId, registrationPath, 'registered', output];
    let exitCode = 0;
    try { await promisify(execFile)(process.execPath, args, { env, timeout: 2_000_000, maxBuffer: 1024 * 1024 }); }
    catch (error: any) { if (typeof error.code !== 'number') throw new Error('registered_driver_process_did_not_complete'); exitCode = error.code; }
    const result = JSON.parse(await readFile(join(output, `${runId}.json`), 'utf8'));
    attempts.push({ label, cohort: registration.cohort, exit_code: exitCode, command: [process.execPath, ...args], result });
    expect(exitCode).toBe(1);
    expect(result.status).toBe('not_measured');
    expect(result.reason).toBe('Campaign execution is deferred until Checkpoint P');
    expect(result.operator_actions).toBeNull(); expect(result.action_ratio).toBeNull(); expect(result.ten_x_actions).toBeNull();
    expect(result.cohort).toBe(registration.cohort); expect(result.driver_status).toBe('incomplete');
  }
  // Independent storage witness: the refused attempts created no campaign.
  expect(await campaigns()).toBe(before);
  await writeFile(join(output, `driver-attempts-${stamp}.json`), JSON.stringify(attempts, null, 2), { flag: 'wx', mode: 0o600 });
});

// Pre-Checkpoint-P measurement-only mode (orchestrator decision 2026-11-11):
// each of the three separate J5 measurements runs end to end through the same
// protected CLI on the synthetic fixture and repository fake Git server. Counts
// are real; the claim stays blocked and real-GitHub conformance stays open.
for (const [label, id] of [['cold_adoption', 'SKILLS-E2E-057'], ['recurring_rollout', 'SKILLS-E2E-058'], ['recovery', 'SKILLS-E2E-059']] as const) {
test(`${id}: registered J5 ${label.replace(/_/g, ' ')} measurement completes through the protected CLI with real counts and a blocked claim`, {
  tag: ['@flow'],
  annotation: flowAnnotation({ id, area: 'skills',
    userGoal: `Measure J5 ${label.replace(/_/g, ' ')} as its own preregistered complete-job run before Checkpoint P without a supported claim`,
    steps: ['preregister the measurement with the explicit pre-Checkpoint-P opt-in, fixture-derived expectations and real provenance',
      'run the registered J5 driver through the benchmark CLI on the forty-repository synthetic fixture and fake Git server',
      'retain the report and its append-only ledger and compare it against the retained ledger file'],
    backendChecks: ['every non-external preregistered check passes from independent API, Postgres, Git, filesystem or model-boundary evidence',
      'real-GitHub conformance is reported external_open and never observed from the fake',
      'independent Postgres shows every campaign the run created is settled or cancelled after teardown',
      'the comparison command refuses the report, and a mutated retained ledger event fails hash-chain verification'],
  }),
}, async ({ actor, probe }, testInfo) => {
  test.setTimeout(3_900_000); // The J5 jobs.mjs entry documents a bounded 60-minute journey deadline.
  const python = process.env.CTXHUB_J5_PYTHON;
  const provenancePath = process.env.J5_PROVENANCE_JSON;
  const nativeConfig = { executable: process.env.J5_CODEX_BINARY, sha256: process.env.J5_CODEX_SHA256 };
  if (!python || !provenancePath || !nativeConfig.executable || !nativeConfig.sha256) throw new Error('Select the installed SDK, provenance and pinned native Codex executable on the approved VM');
  const modules = { driver: '../../benchmarks/context-hub/drivers/j5.mjs', runner: '../../benchmarks/context-hub/runner.mjs',
    jobs: '../../benchmarks/context-hub/jobs.mjs', ledger: '../../lib/skills-r3/ledger.mjs' };
  const { j5Registration } = await import(modules.driver) as any;
  const { compareActionRuns } = await import(modules.runner) as any;
  const { getJob } = await import(modules.jobs) as any;
  const { summarizeLedger } = await import(modules.ledger) as any;
  const provenance = JSON.parse(await readFile(provenancePath, 'utf8'));
  const output = join(process.env.J5_EVIDENCE_DIR || testInfo.outputDir, 'measured');
  await mkdir(output, { recursive: true });
  const stamp = Date.now().toString(36);
  const registration = j5Registration({ label, provenance, python, evidenceDir: join(output, `${label}-${stamp}`), native: nativeConfig, measurementOnly: true });
  expect(registration.checkpoint_P_receipt).toBeUndefined();
  expect(registration.pre_checkpoint_p_measurement).toBe(true);
  const registrationPath = join(output, `${label}-${stamp}-registration.json`);
  await writeFile(registrationPath, JSON.stringify(registration, null, 2), { flag: 'wx', mode: 0o600 });
  const env = { ...process.env, J5_HUB_URL: E2E.apiUrl, J5_HUB_HEADERS_JSON: JSON.stringify({ Authorization: `Bearer ${actor.tokens.access}`,
    'X-Organization-Id': actor.organizationId, 'X-Workspace-Id': actor.workspaceId }) };
  const campaignRows = () => probe.pg<{ id: string; status: string }>(
    'SELECT id::text AS id, status FROM model_hub_hubskillcontextcampaign WHERE organization_id = $1', [actor.organizationId]);
  const before = new Set((await campaignRows()).map((row) => row.id));
  const runId = `r6-j5-measured-${label.replace(/_/g, '-')}-${stamp}`;
  const args = [resolve('benchmarks/context-hub/cli.mjs'), 'run', 'J5', runId, registrationPath, 'registered', output];
  let exitCode = 0;
  try { await promisify(execFile)(process.execPath, args, { env, timeout: 3_750_000, maxBuffer: 4 * 1024 * 1024 }); }
  catch (error: any) { if (typeof error.code !== 'number') throw new Error('registered_driver_process_did_not_complete'); exitCode = error.code; }
  const report = JSON.parse(await readFile(join(output, `${runId}.json`), 'utf8'));
  await writeFile(join(output, `${runId}-attempt.json`), JSON.stringify({ label, cohort: registration.cohort, exit_code: exitCode,
    command: [process.execPath, ...args], status: report.status, error_code: report.error_code, stages: report.stages, checks: report.checks,
    counts: report.measurement?.counts, components: report.components }, null, 2), { flag: 'wx', mode: 0o600 });
  expect({ exitCode, status: report.status, error: report.error_code, stages: report.stages }).toMatchObject({ exitCode: 0, status: 'completed', error: null });
  expect(report.evidence_class).toBe('synthetic fixture + fake Git server');
  expect(report.claim_status).toBe('blocked_pending_checkpoint_p_and_real_github');
  expect(report.teardown).toBe('confirmed');
  const job = getJob('J5');
  expect(report.checks.find((check: any) => check.id === 'real-github-conformance')?.status).toBe('external_open');
  expect(report.checks.filter((check: any) => check.status === 'pass').map((check: any) => check.id).sort())
    .toEqual(job.checks.map((check: any) => check.id).filter((checkId: string) => checkId !== 'real-github-conformance').sort());
  // Independent witness: the retained append-only ledger file reproduces the report's counts.
  const fileEvents = (await readFile(join(output, `${runId}.jsonl`), 'utf8')).trim().split('\n').map((line) => JSON.parse(line));
  const fromFile = summarizeLedger(fileEvents);
  expect(fromFile.integrity.complete).toBe(true);
  expect(fromFile.counts).toEqual(report.measurement.counts);
  expect(fromFile.integrity.hash).toBe(report.measurement.integrity.hash);
  const phase = { cold_adoption: 'cold', recurring_rollout: 'recurring', recovery: 'recovery' }[label];
  expect(report.components[label].counts).toEqual(fromFile.phases[phase]);
  expect(report.components[label].counts.A).toBeGreaterThan(0);
  // One-sided fallibility: relabelling one retained operator action breaks the hash chain; the original still verifies.
  const mutated = structuredClone(fileEvents); const start = mutated.find((event: any) => event.type === 'start' && event.dimension === 'A');
  start.dimension = 'S';
  expect(() => summarizeLedger(mutated)).toThrow(/Ledger chain mismatch/);
  expect(summarizeLedger(fileEvents).integrity.hash).toBe(fromFile.integrity.hash);
  // Independent Postgres: every campaign created by this run is settled or cancelled.
  const created = (await campaignRows()).filter((row) => !before.has(row.id));
  expect(created.length).toBeGreaterThanOrEqual(3);
  expect(created.every((row) => ['settled', 'cancelled'].includes(row.status))).toBe(true);
  // The compare command refuses any pre-Checkpoint-P report as either side.
  const review = { reviewer_id: 'reviewer', author_id: 'author', strongest_configured_route_hash: 'a'.repeat(64), receipt_sha256: 'a'.repeat(64) };
  const blocked = compareActionRuns(report, { ...report, mode: 'competitor' }, review);
  expect(blocked).toMatchObject({ ten_x_actions: null, action_ratio: null, claim_status: 'blocked_pending_checkpoint_p_and_real_github' });
});
}
