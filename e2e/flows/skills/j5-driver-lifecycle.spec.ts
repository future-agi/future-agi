import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { test, expect } from '../../lib/fixtures';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// Exercise the registered implementation, not a duplicate successful lifecycle.
// This is explicitly a partial-driver qualification: the protected runner still
// requires Checkpoint P, and installed bytes are never promoted to native load.
for (const { native, cohort, id } of [{ native: false, cohort: 'cold', id: 'SKILLS-E2E-053' }, { native: true, cohort: 'cold', id: 'SKILLS-E2E-054' },
  { native: true, cohort: 'configured', id: 'SKILLS-E2E-055' }]) {
const configured = cohort === 'configured';
test(`${id}: registered campaign driver ${configured ? 'reaches the configured precondition separately, then rolls out and recovers' : native ? 'observes actual pinned native load' : 'refuses installation-only delivery'} without losing local policy`, {
  tag: ['@flow'],
  annotation: flowAnnotation({ id, area: 'skills',
    userGoal: 'Use the actual registered driver for real SDK campaign plans and distinguish verified installations from unobserved native load',
    steps: [configured ? 'reach the configured precondition through public interfaces on a separate setup ledger and verify it before measuring'
      : 'inventory forty no-lock repositories and measure explicit ownership, pin and exception reviews',
      'plan every fixed target with one command; review one proven-identical managed group plus every exception individually',
      'freeze exact targets, review plans and authorize every exposed repository with stage-local evidence',
      'create deterministic PRs, reconcile the lost response and verify merge and filesystem installations independently',
      'open Repository rollouts from Skills navigation and review each rollout\'s fixed forty targets',
      native ? 'observe exact root instructions in the actual pinned Codex request after cold install, second update and reverse' : 'refuse a completed load stage, then exercise second-update and reverse mechanisms as explicitly partial evidence'],
    backendChecks: ['public API and independent Postgres preserve forty exact targets per round',
      'all registered decision stages contain current per-item reviews bound to their stage receipts',
      'driver observes local files and deterministic Git PRs rather than product toasts',
      'installation-only, omitted-target and changed-local-policy controls cannot become successful complete-job observations',
      'review grouping admits only byte-identical managed deltas whose local bytes are proven preserved; mutated members fall out',
      'pins are installed at their pinned release, then held; a pin lock advanced past that release is counted from the filesystem and fails pins-retained'],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  test.setTimeout(2_400_000); // Three 33-37-target rounds (plus configured setup) and public API/storage probes.
  page.setDefaultTimeout(30_000); // A missing row action must not consume the whole benchmark deadline.
  const python = process.env.CTXHUB_J5_PYTHON;
  if (!python) throw new Error('Select the installed SDK on the approved VM');
  const driverModule = '../../benchmarks/context-hub/drivers/j5.mjs';
  const jobsModule = '../../benchmarks/context-hub/jobs.mjs';
  const ledgerModule = '../../lib/skills-r3/ledger.mjs';
  const oracleModule = '../../lib/skills-r3/oracles.mjs';
  const { createDriver, requireNativeDelivery, j5Registration } = await import(driverModule) as any;
  // Grouping fallibility runs against the installed SDK before any measured stage.
  const grouping = await promisify(execFile)(python, ['-I', resolve('harness/skills-r3/j5_review_group_check.py')], { timeout: 120_000 });
  const groupingResult = JSON.parse(grouping.stdout.trim().split('\n').at(-1) as string);
  expect(Object.values(groupingResult).every((value) => value === true)).toBe(true);
  expect(groupingResult).toMatchObject({ positive_same_group: true, dropped_local_byte_excluded: true, stale_preimage_excluded: true });
  const { getJob } = await import(jobsModule) as any;
  const { ActionLedger, summarizeLedger } = await import(ledgerModule) as any;
  const { assertObservation } = await import(oracleModule) as any;
  const evidence = join(process.env.J5_EVIDENCE_DIR || testInfo.outputDir, configured ? 'configured-native' : native ? 'native' : 'installation-only');
  const nativeConfig = native ? { executable: process.env.J5_CODEX_BINARY, sha256: process.env.J5_CODEX_SHA256 } : undefined;
  if (native && (!nativeConfig?.executable || !nativeConfig.sha256)) throw new Error('Select the pinned native Codex executable and digest on the approved VM');
  await mkdir(evidence, { recursive: true });
  const runId = `j5-prefix-${Date.now().toString(36)}`;
  const before = { url: process.env.J5_HUB_URL, headers: process.env.J5_HUB_HEADERS_JSON };
  process.env.J5_HUB_URL = E2E.apiUrl;
  process.env.J5_HUB_HEADERS_JSON = JSON.stringify({ Authorization: `Bearer ${actor.tokens.access}`,
    'X-Organization-Id': actor.organizationId, 'X-Workspace-Id': actor.workspaceId });
  const flowRegistration = j5Registration({ label: configured ? 'recurring_rollout' : 'cold_adoption', provenance: null,
    python, evidenceDir: evidence, native: nativeConfig });
  const driver = await createDriver({ jobId: 'J5', runId, registration: flowRegistration });
  const ledger = new ActionLedger({ runId, job: 'J5', mode: 'product', path: join(evidence, `${runId}-partial.jsonl`) });
  // Six parallel lifecycle workers share the VM; the configured cohort's
  // separate setup round plus three native rounds needs ~28 minutes there.
  const signal = AbortSignal.timeout(2_250_000); const job = getJob('J5');
  const missingLoad: string[] = []; const storage: unknown[] = []; const qualifiedChecks: string[] = [];
  let cleanup;
  try {
    for (const stage of job.steps) {
      // The configured cohort skips cold stages exactly as the runner does.
      if (configured && stage.phase === 'cold') continue;
      const boundary = ledger.events().at(-1)?.sequence ?? 0;
      if (['review-merge-install-load', 'second-update', 'reverse-plan'].includes(stage.id)) {
        if (native) {
          await driver.perform(stage, { ledger, signal });
          const events = ledger.events();
          const receipt = events.find((event: any) => event.type === 'stage' && event.step === stage.id);
          expect(receipt.source).toBe('model_boundary');
          for (const primitive of stage.human_actions) {
            const reviews = events.filter((event: any) => event.type === 'start' && event.step === stage.id && event.primitive === primitive
              && event.sequence > boundary && event.sequence < receipt.sequence
              && JSON.stringify(event.decision) === JSON.stringify(receipt.decision));
            // Grouped review units still expose all forty repositories exactly once.
            expect(reviews.flatMap((event: any) => event.items)).toHaveLength(40);
            expect(new Set(reviews.flatMap((event: any) => event.items)).size).toBe(40);
          }
        } else {
          await expect(driver.perform(stage, { ledger, signal })).rejects.toThrow('native_discovery_unobserved');
          expect(ledger.events().some((event: any) => event.type === 'stage' && event.step === stage.id)).toBe(false);
          missingLoad.push(stage.id);
        }
        const campaign = driver.campaign;
        const rows = await probe.pg<{ binding_id: string; decision: string }>(
          'SELECT binding_id, decision FROM model_hub_hubskillcontextcampaigntarget WHERE campaign_id = $1 ORDER BY binding_id', [campaign.id]);
        expect(rows).toEqual(campaign.decisions.map(({ binding_id, decision }: any) => ({ binding_id, decision }))
          .sort((a: any, b: any) => a.binding_id.localeCompare(b.binding_id)));
        expect(rows).toHaveLength(40); storage.push({ campaign_id: campaign.id, rows });
        await page.setViewportSize({ width: 1440, height: 900 });
        // Reach the rollout the way a user does: Skills navigation, the enabled
        // Repository rollouts tab, then the newest-first rollout list.
        expect(campaign.name).toBeTruthy();
        await page.goto('/dashboard/skills/contexts');
        const rolloutsTab = page.getByRole('tab', { name: 'Repository rollouts', exact: true });
        await expect(rolloutsTab).toBeEnabled({ timeout: 60_000 });
        await rolloutsTab.click();
        await expect(page).toHaveURL(/\/dashboard\/skills\/contexts\/rollouts\/?$/);
        await expect(page.getByRole('table', { name: 'Repository rollouts' })).toBeVisible({ timeout: 60_000 });
        await mkdir(join(evidence, 'shots'), { recursive: true });
        await page.screenshot({ path: join(evidence, 'shots', `j5-driver-${campaign.label}-list.png`), animations: 'disabled' });
        // A cancelled predecessor (base movement) can share the name; the
        // current revision from the public API selects the live rollout.
        const current = await actor.api.get<{ result: { revision: number; status: string } }>(`/model-hub/skills/contexts/campaigns/${campaign.id}/`);
        expect(current.result.status).not.toBe('cancelled');
        const named = page.getByRole('table', {name:'Repository rollouts'}).getByRole('row')
          .filter({has:page.getByText(campaign.name,{exact:true})});
        for (const row of await named.all()) await row.getByRole('button',{name:'Rollout details'}).click();
        const live = named.filter({has:page.getByText(`Revision ${current.result.revision}`,{exact:true})});
        await expect(live).toHaveCount(1);
        await live.click();
        await expect(page).toHaveURL(new RegExp(`/dashboard/skills/contexts/rollouts/${campaign.id}$`));
        const table = page.getByRole('table', { name: 'Fixed rollout targets' });
        await expect(table).toBeVisible({ timeout: 60_000 }); await expect(table.getByRole('row')).toHaveCount(41);
        await mkdir(join(evidence, 'shots'), { recursive: true });
        await page.screenshot({ path: join(evidence, 'shots', `j5-driver-${campaign.label}.png`), animations: 'disabled' });
      } else {
        await driver.perform(stage, { ledger, signal });
        const events = ledger.events();
        const receipt = events.find((event: any) => event.type === 'stage' && event.step === stage.id);
        expect(receipt).toBeTruthy();
        for (const primitive of stage.human_actions) {
          const reviews = events.filter((event: any) => event.type === 'start' && event.step === stage.id
            && event.primitive === primitive && event.sequence > boundary && event.sequence < receipt.sequence
            && JSON.stringify(event.decision) === JSON.stringify(receipt.decision));
          expect(reviews.flatMap((event: any) => event.items)).toHaveLength(40);
          expect(new Set(reviews.flatMap((event: any) => event.items)).size).toBe(40);
          if (stage.id === 'establish-ownership') {
            // Ownership is reviewed once per distinct shared instruction content
            // (repo-1000's AGENTS.md differs) and once for the unavailable target.
            expect(reviews.map((event: any) => event.items.length)).toEqual([1, 38, 1]);
            const edits = events.filter((event: any) => event.type === 'start' && event.step === stage.id && event.primitive === 'field_edit');
            // Two explicit local-modification holds and four deliberate pins stay counted edits.
            expect(edits.map((event: any) => event.items[0]).sort()).toEqual(['repo-1032', 'repo-1033', 'repo-1034', 'repo-1035', 'repo-1036', 'repo-1037']);
          }
          if (stage.id === 'review-plans') {
            const review = driver.campaign.review;
            if (configured) {
              // Next release: one proven-identical group for the 33 followers;
              // the four installed pins are held individually at their release.
              expect(review.groups.map((group: any) => group.members.length)).toEqual([33]);
              expect(review.exceptions.map((row: any) => row.kind).sort()).toEqual(['operator_exclusion', 'operator_exclusion',
                'pinned_selector', 'pinned_selector', 'pinned_selector', 'pinned_selector', 'unavailable']);
              // One review for the proven group and one for the held list (pins,
              // operator exclusions, unavailable), matching the comparator's granularity.
              expect(driver.campaign.packet.units.map((unit: any) => [unit.kind, unit.repository_ids.length])).toEqual([['group', 33], ['held', 7]]);
              expect(reviews).toHaveLength(2);
            } else {
              // Cold adoption at the pinned release: the four pins are adopted with
              // the 33 followers in one proven group; three individual exceptions.
              expect(review.groups.map((group: any) => group.members.length)).toEqual([37]);
              expect(review.exceptions.map((row: any) => row.kind).sort()).toEqual(['operator_exclusion', 'operator_exclusion', 'unavailable']);
              expect(driver.campaign.packet.units.map((unit: any) => [unit.kind, unit.repository_ids.length])).toEqual([['group', 37], ['held', 3]]);
              expect(reviews).toHaveLength(2);
            }
          }
        }
      }
    }
    const targetsCheck = job.checks.find((check: any) => check.id === 'exact-forty-targets');
    const membership = await driver.observe(targetsCheck, { signal });
    const ids = driver.repos.map((repo: any) => repo.id);
    expect(assertObservation(targetsCheck, ids, membership)).toBe(true);
    expect(() => assertObservation(targetsCheck, ids, { ...membership, value: membership.value.slice(1) })).toThrow();
    expect(assertObservation(targetsCheck, ids, membership)).toBe(true);
    qualifiedChecks.push(targetsCheck.id);
    const policyCheck = job.checks.find((check: any) => check.id === 'local-policy-preserved');
    const policy = await driver.observe(policyCheck, { signal });
    const { createHash } = await import('node:crypto');
    // Original local policy includes the adopted AGENTS segment as well as
    // unmanaged files; managed additions are independently checked in Git.
    const original = driver.repos.filter((repo: any) => repo.disposition !== 'unavailable').flatMap((repo: any) =>
      Object.entries(repo.files).map(([path, text]) => ({ path: `${repo.id}/${path}`,
        sha256: createHash('sha256').update(text as string).digest('hex'), size_bytes: Buffer.byteLength(text as string) })));
    expect(assertObservation(policyCheck, original, policy)).toBe(true);
    const changed = structuredClone(policy); changed.value[0].content = 'Local policy lost';
    expect(() => assertObservation(policyCheck, original, changed)).toThrow();
    expect(assertObservation(policyCheck, original, policy)).toBe(true);
    qualifiedChecks.push(policyCheck.id);
    const expectedChecks: Record<string, unknown> = { 'one-pr-per-target': { duplicates: 0 },
      'authorized-base-preimages': { unauthorized_effects: 0 }, 'pins-retained': { deliberate_pins: 4, advanced_pins: 0 },
      'stage-accounting': { target_count: 40, merge_install_load_distinct: true } };
    for (const [id, expected] of Object.entries(expectedChecks)) {
      const check = job.checks.find((row: any) => row.id === id);
      if (id === 'stage-accounting' && !native) {
        await expect(driver.observe(check, { signal })).rejects.toThrow('native_discovery_unobserved');
        continue;
      }
      const observation = await driver.observe(check, { signal });
      expect(assertObservation(check, expected, observation)).toBe(true);
      expect(() => assertObservation(check, expected, { ...observation, value: { incomplete: true } })).toThrow();
      expect(assertObservation(check, expected, observation)).toBe(true);
      qualifiedChecks.push(id);
    }
    // Change only the independently observed PR head; the approved plan and
    // expected check remain fixed. Restore it and prove the same check passes.
    const firstPatch = driver.rounds[0].plans[0];
    const branchKey = `${firstPatch.repository_id}/futureagi/context/${driver.rounds[0].campaign_id}/${firstPatch.binding_id}`;
    const originalHead = driver.git.refs.get(branchKey);
    const baseCheck = job.checks.find((check: any) => check.id === 'authorized-base-preimages');
    try {
      driver.git.refs.set(branchKey, firstPatch.plan.base_commit);
      await expect(driver.observe(baseCheck, { signal })).rejects.toThrow('unauthorized_parent');
    } finally { driver.git.refs.set(branchKey, originalHead); }
    expect(assertObservation(baseCheck, expectedChecks[baseCheck.id], await driver.observe(baseCheck, { signal }))).toBe(true);
    const rootPolicy = join(driver.projects.get(driver.repos[0].id), 'AGENTS.md');
    const preservedPolicy = await readFile(rootPolicy);
    try {
      await writeFile(rootPolicy, 'Local policy was removed.\n');
      await expect(driver.observe(policyCheck, { signal })).rejects.toThrow('local_segment_missing_or_duplicated');
    } finally { await writeFile(rootPolicy, preservedPolicy); }
    expect(assertObservation(policyCheck, original, await driver.observe(policyCheck, { signal }))).toBe(true);
    // A pin whose installed lock silently advanced to a later release must be
    // counted from the filesystem, not assumed; restoring its bytes restores the pass.
    const pinsCheck = job.checks.find((check: any) => check.id === 'pins-retained');
    const pinnedLock = join(driver.projects.get(driver.repos.find((repo: any) => repo.disposition === 'pin').id), '.futureagi-context.lock.json');
    const pinnedLockBytes = await readFile(pinnedLock);
    const laterRelease = driver.rounds.find((round: any) => round.label === 'second-update').installs[0].version_id;
    expect(JSON.parse(pinnedLockBytes.toString()).version_id).not.toBe(laterRelease);
    let advancedPin: any;
    try {
      await writeFile(pinnedLock, JSON.stringify({ ...JSON.parse(pinnedLockBytes.toString()), version_id: laterRelease }));
      advancedPin = await driver.observe(pinsCheck, { signal });
      expect(advancedPin.value).toEqual({ deliberate_pins: 4, advanced_pins: 1 });
      expect(() => assertObservation(pinsCheck, expectedChecks[pinsCheck.id], advancedPin)).toThrow();
    } finally { await writeFile(pinnedLock, pinnedLockBytes); }
    expect(assertObservation(pinsCheck, expectedChecks[pinsCheck.id], await driver.observe(pinsCheck, { signal }))).toBe(true);
    await writeFile(join(evidence, 'observation-fallibility.json'), JSON.stringify({ evidence_class: 'synthetic fixture',
      changed_git_head: { rejected: 'unauthorized_parent', restored: 'pass' },
      changed_local_policy: { rejected: 'local_segment_missing_or_duplicated', restored: 'pass' },
      advanced_pin_lock: { observed: advancedPin?.value, rejected: true, restored: 'pass' }, qualified_checks: qualifiedChecks }, null, 2));
    // Pins install only at their pinned release (cold adoption, or the
    // configured cohort's separate setup round) and are held afterwards.
    expect(driver.rounds.map((report: any) => report.installs.length)).toEqual(configured ? [33, 33, 33] : [37, 33, 33]);
    for (const report of driver.rounds) {
      expect(report.installs).toHaveLength(report.expected.length);
      const check = () => requireNativeDelivery(report.installs, report.installs.map((row: any) => row.repository_id), report.installs[0].version_id);
      if (native) {
        expect(check).not.toThrow();
        const nativeModule = '../../lib/skills-r3/j5-native-fixture.mjs';
        const { assertCodexBoundary } = await import(nativeModule) as any;
        for (const row of report.installs) {
          const boundary = JSON.parse(await readFile(join(evidence, `${runId}-native-${report.label}-${row.repository_id}.json`), 'utf8'));
          const expectedInstructions = driver.git.files(row.merge)['AGENTS.md'];
          const observation = { requests: boundary.requests, events: boundary.process_events, expectedInstructions };
          expect(() => assertCodexBoundary(observation)).not.toThrow();
          const missing = structuredClone(observation); missing.requests[0].input = [];
          expect(() => assertCodexBoundary(missing)).toThrow('native_instruction_delivery_missing');
          expect(() => assertCodexBoundary(observation)).not.toThrow();
        }
      } else expect(check).toThrow('native_discovery_unobserved');
      const installationOnly = report.installs.map((row: any) => ({ ...row, discovery: 'not_observed', delivery: 'not_observed', source: 'filesystem' }));
      expect(() => requireNativeDelivery(installationOnly, installationOnly.map((row: any) => row.repository_id), installationOnly[0].version_id))
        .toThrow('native_discovery_unobserved');
    }
    expect([...new Set(ledger.events().filter((event: any) => event.type === 'fault').map((event: any) => event.id))].sort())
      .toEqual([...job.fault_ids].sort());
    expect(driver.baseMovement.recovered).toBe(true);
    expect(driver.baseMovement.after).not.toBe(driver.baseMovement.before);
    expect(driver.campaigns).toHaveLength(configured ? 5 : 4);
    if (configured) {
      const precondition = JSON.parse(await readFile(join(evidence, `${runId}-configured-precondition.json`), 'utf8'));
      expect(precondition.realised.installed_repositories).toBe(37);
      expect(precondition.realised.pins_installed_at_pinned_release).toBe(4);
      expect(precondition.setup_ledger.integrity.complete).toBe(true);
      // Setup actions live only on the separate setup ledger.
      expect(ledger.events().filter((event: any) => event.type === 'start' && event.phase === 'cold')).toHaveLength(0);
      expect(driver.setupRounds).toHaveLength(1);
    }
    const abandoned = await probe.pg<{ status: string; targets: string }>(
      'SELECT c.status, COUNT(t.id)::text AS targets FROM model_hub_hubskillcontextcampaign c JOIN model_hub_hubskillcontextcampaigntarget t ON t.campaign_id = c.id WHERE c.id = $1 GROUP BY c.status',
      [driver.baseMovement.campaign_id]);
    expect(abandoned).toEqual([{ status: 'cancelled', targets: '40' }]);
    storage.push({ cancelled_campaign_id: driver.baseMovement.campaign_id, rows: abandoned });
    expect(driver.reverseConflicts).toEqual({ dirty: 'local_modifications', held_until_resolved: true, stale: 'stale_plan', lock_unchanged: true, local_work_exported: true });
    for (const kind of ['dirty', 'stale']) {
      const retained = JSON.parse(await readFile(join(evidence, `${runId}-retained-${kind}-local-work.json`), 'utf8'));
      expect(createHash('sha256').update(retained.content).digest('hex')).toBe(retained.sha256);
    }
    await expect(driver.observe(job.checks.find((check: any) => check.id === 'real-github-conformance'), { signal }))
      .rejects.toThrow('external_real_github_conformance_not_supplied');
  } finally {
    cleanup = await driver.teardown(); ledger.close();
    if (before.url === undefined) delete process.env.J5_HUB_URL; else process.env.J5_HUB_URL = before.url;
    if (before.headers === undefined) delete process.env.J5_HUB_HEADERS_JSON; else process.env.J5_HUB_HEADERS_JSON = before.headers;
    await writeFile(join(evidence, 'driver-prefix.json'), JSON.stringify({ evidence_class: 'synthetic fixture', complete_job: false,
      measured_by_registered_runner: false, native_scope: native ? 'codex-0.167.1-linux-root-AGENTS-only' : 'not_observed',
      missing_load_stages: missingLoad, storage, cleanup, qualified_checks: qualifiedChecks,
      exercised_faults: ledger.events().filter((event: any) => event.type === 'fault').map((event: any) => event.id),
      base_movement: driver.baseMovement, reverse_conflicts: driver.reverseConflicts,
      partial_ledger_integrity: summarizeLedger(ledger.events()).integrity, cohort,
      flow_counts_not_harness_measurement: summarizeLedger(ledger.events()).phases,
      limitations: ['Flow ledger only: harness-produced complete-job runs remain gated by the protected Checkpoint P check.',
        native ? 'Native evidence is bounded to the pinned Linux root-AGENTS fixture, not universal client compatibility or model behavior.' : 'Native discovery and actual model-boundary load remain unobserved.',
        configured ? 'Configured precondition was reached on a separate setup ledger and verified before measured stages.' : 'Cold adoption from lockless repositories with real local instructions.',
        'No real GitHub conformance, customer comparison or human-time qualification.'] }, null, 2));
  }
  expect(missingLoad).toEqual(native ? [] : ['review-merge-install-load', 'second-update', 'reverse-plan']);
  expect(cleanup.qualified).toBe(true);
});
}
