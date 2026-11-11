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
