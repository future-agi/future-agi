import { createHash } from 'node:crypto';
import { mkdtemp, mkdir, readFile, writeFile, rm, access } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
type Witness = { id: string; available: boolean; lock_sha256: null; files: Record<string, string> };
type Inventory = { target_count: number; targets: {
  repository_id: string; state: string; ownership?: string;
  observation?: { inventory_complete: boolean; lock_sha256: string | null;
    findings: { path?: string; sha256?: string }[] };
}[] };
type DriverModule = {
  assertInventory: (inventory: Inventory, expected: Witness[]) => void;
  runInventory: (options: { python: string; manifestPath: string; evidencePath: string }) => Promise<{
    exitCode: number; inventory: Inventory; artifact_sha256: string;
  }>;
};

const sha256 = (bytes: Buffer | string) => createHash('sha256').update(bytes).digest('hex');

// This is a bounded prerequisite flow, not complete J5 or campaign execution
// qualification. The production switch is never changed by the flow.
test('SKILLS-E2E-050: inventory forty selected checkouts and retain the campaign qualification boundary', {
  tag: ['@flow'],
  annotation: flowAnnotation({ id: 'SKILLS-E2E-050', area: 'skills',
    userGoal: 'Inspect existing local instructions without executing repository code or silently dropping unavailable repositories, and see the honest rollout availability boundary',
    steps: [
      'select forty synthetic checkouts including an unavailable target; retain real no-lock local and nested instructions',
      'run the installed public inventory CLI and inspect all selected target dispositions',
      'open Repository rollouts and inspect the current qualification boundary',
    ],
    backendChecks: [
      'independent filesystem hashes and selected IDs equal the complete inventory; local policy remains byte-identical and no hook runs',
      'the independent public capability remains disabled and the campaign list remains empty; no rollout is authorized',
    ],
  }),
}, async ({ page, actor }, testInfo) => {
  test.setTimeout(240_000); // CLI 60s + two UI waits 60s each + public observations/setup 60s.
  const python = process.env.CTXHUB_J5_PYTHON;
  if (!python) throw new Error('CTXHUB_J5_PYTHON must select the installed SDK on the cloud VM');
  // The benchmark harness is native ESM JavaScript; keep a typed boundary in
  // this TypeScript flow rather than relaxing the repository compiler options.
  const driverModule = '../../benchmarks/context-hub/drivers/j5.mjs';
  const runnerModule = '../../benchmarks/context-hub/runner.mjs';
  const jobsModule = '../../benchmarks/context-hub/jobs.mjs';
  const { assertInventory, runInventory } = await import(driverModule) as DriverModule;
  const { unavailableJob } = await import(runnerModule) as {
    unavailableJob: (job: unknown, reason: string) => Record<string, unknown>;
  };
  const { getJob } = await import(jobsModule) as { getJob: (id: string) => unknown };
  const scratch = await mkdtemp(join(tmpdir(), 'j5-inventory-'));
  const selected: { repository_id: string; project: string }[] = [];
  const expected: Witness[] = [];
  try {
    for (let index = 0; index < 40; index++) {
      const project = join(scratch, `repo-${index}`);
      const id = String(index + 100);
      selected.push({ repository_id: id, project });
      const witness = { id, available: index !== 38, lock_sha256: null, files: {} as Record<string, string> };
      expected.push(witness);
      if (!witness.available) continue;
      await mkdir(join(project, 'nested'), { recursive: true });
      await mkdir(join(project, '.git', 'hooks'), { recursive: true });
      const files: Record<string, string> = {
        'AGENTS.md': `Local repository ${id} policy; never discard it.\n`,
        'CLAUDE.local.md': `Private project ${id} instructions.\n`,
        'nested/AGENTS.override.md': `Nested repository ${id} precedence.\n`,
      };
      for (const [path, text] of Object.entries(files)) {
        await writeFile(join(project, path), text);
        witness.files[path] = sha256(await readFile(join(project, path)));
      }
      await writeFile(join(project, '.git/hooks/post-checkout'), '#!/bin/sh\ntouch HOOK_EXECUTED\n', { mode: 0o755 });
    }
    const manifestPath = join(scratch, 'selected.json');
    await writeFile(manifestPath, JSON.stringify(selected));
    const artifact = testInfo.outputPath('inventory.json');
    const outcome = await runInventory({ python, manifestPath, evidencePath: artifact });
    expect(outcome.exitCode).toBe(6); // One unavailable target is explicit, not a successful install.
    assertInventory(outcome.inventory, expected);
    for (const witness of expected.filter((row) => row.available)) {
      const project = selected.find((row) => row.repository_id === witness.id)!.project;
      for (const [path, digest] of Object.entries(witness.files)) {
        expect(sha256(await readFile(join(project, path)))).toBe(digest);
      }
      await expect(access(join(project, 'HOOK_EXECUTED'))).rejects.toThrow();
      await expect(access(join(project, '.futureagi-context.lock.json'))).rejects.toThrow();
    }
    await writeFile(testInfo.outputPath('filesystem-witness.json'), JSON.stringify(expected, null, 2));
    await testInfo.attach('independent-filesystem-witness', { path: testInfo.outputPath('filesystem-witness.json'), contentType: 'application/json' });
    await testInfo.attach('public-cli-inventory', { path: artifact, contentType: 'application/json' });

    // One-sided fallibility: only observed data changes; expected truth remains
    // fixed. Neither a dropped repository nor one modified policy may go green.
    const missing = structuredClone(outcome.inventory);
    missing.targets.pop();
    expect(() => assertInventory(missing, expected)).toThrow('inventory_target_accounting');
    const wrongBytes = structuredClone(outcome.inventory);
    wrongBytes.targets.find((row) => row.state === 'inventoried')!.observation!.findings
      .find((row) => row.sha256)!.sha256 = '0'.repeat(64);
    expect(() => assertInventory(wrongBytes, expected)).toThrow('instruction_inventory_mismatch');
    assertInventory(outcome.inventory, expected); // Restored green oracle.

    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/dashboard/skills/contexts/rollouts');
    await expect(page.getByText('Repository rollouts', { exact: true }).first()).toBeVisible({ timeout: 60_000 });
    await expect(page.getByText('No repository rollouts', { exact: true })).toBeVisible({ timeout: 60_000 });
    const capabilities = await actor.api.get<{ result: { campaigns_enabled: boolean } }>('/model-hub/skills/contexts/capabilities/');
    const campaigns = await actor.api.get<{ result: { count: number; results: unknown[] } }>('/model-hub/skills/contexts/campaigns/');
    const assertGate = (capability: typeof capabilities, list: typeof campaigns) => {
      expect(capability.result.campaigns_enabled).toBe(false);
      expect(list.result.count).toBe(0);
      expect(list.result.results).toEqual([]);
    };
    assertGate(capabilities, campaigns);
    expect(() => assertGate({ result: { campaigns_enabled: true } }, campaigns)).toThrow();
    expect(() => assertGate(capabilities, { result: { count: 1, results: [{}] } })).toThrow();
    assertGate(capabilities, campaigns);
    await writeFile(testInfo.outputPath('capability-and-list.json'), JSON.stringify({ capabilities, campaigns, completeJob: false }, null, 2));
    await testInfo.attach('independent-capability-and-list', {
      path: testInfo.outputPath('capability-and-list.json'), contentType: 'application/json',
    });
    await page.screenshot({ path: testInfo.outputPath('J5-rollouts-light.png'), animations: 'disabled' });
    await page.evaluate(() => {
      const settings = JSON.parse(localStorage.getItem('settings') || '{}');
      localStorage.setItem('settings', JSON.stringify({ ...settings, themeMode: 'dark' }));
    });
    await page.reload();
    await expect(page.getByText('No repository rollouts', { exact: true })).toBeVisible({ timeout: 60_000 });
    await page.screenshot({ path: testInfo.outputPath('J5-rollouts-dark.png'), animations: 'disabled' });
    const reason = 'Checkpoint P and bounded real-GitHub authorization/conformance are absent; campaign execution remains disabled. The J5 driver implements only the inventory prefix, not the complete job.';
    const report = unavailableJob(getJob('J5'), reason);
    report.cohorts = Object.fromEntries(['cold', 'configured'].map((cohort) => [cohort, unavailableJob(getJob('J5'), reason)]));
    report.prerequisite_flow = { id: 'SKILLS-E2E-050', inventory_targets: 40, available: 39, unavailable: 1, complete_job: false };
    await writeFile(testInfo.outputPath('ours.json'), JSON.stringify(report, null, 2));
    await testInfo.attach('harness-complete-job-verdict', { path: testInfo.outputPath('ours.json'), contentType: 'application/json' });
    await writeFile(testInfo.outputPath('fallibility.json'), JSON.stringify({
      omittedTarget: 'rejected', changedLocalHash: 'rejected', falseEnabled: 'rejected', hiddenCampaign: 'rejected', restored: 'pass',
      limitation: 'prerequisite flow only; not 40-target rollout, real GitHub conformance or a complete-job action measurement',
    }, null, 2));
    await testInfo.attach('fallibility-proof', { path: testInfo.outputPath('fallibility.json'), contentType: 'application/json' });
  } finally {
    await rm(scratch, { recursive: true, force: true });
  }
});
