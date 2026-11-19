import { createHash, randomUUID } from 'node:crypto';
import { mkdir, writeFile } from 'node:fs/promises';
import { test, expect } from '../../lib/fixtures';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

type Repository = { id: string; name: string; base: string; disposition: string; files: Record<string, string> };
type Receipt = { state: string; repository_commit: string | null; pull_request_number: number | null; installed: boolean; delivered: boolean };
type Git = { fetch: typeof fetch; pulls: (id: string) => unknown[]; files: (commit: string) => Record<string, string>;
  requests: { method: string }[] };
const sha = (text: string) => createHash('sha256').update(text).digest('hex');

// This flow qualifies orchestration against the synthetic Git boundary, not
// installation/model delivery, real GitHub conformance or an action comparison.
test('SKILLS-E2E-051: execute forty fixed campaign targets and reconcile an unknown PR without losing local instructions', {
  tag: ['@flow'],
  annotation: flowAnnotation({ id: 'SKILLS-E2E-051', area: 'skills',
    userGoal: 'Authorize exact repository patches, retain intentional exclusions, and reconcile unknown Git outcomes without duplicating PRs or claiming delivery',
    steps: ['publish a synthetic context and bind all forty selected repositories including pins and local exceptions',
      'submit preimage-bound plans retaining real no-lock local instructions and explicitly review every disposition',
      'execute deterministic PRs against the repository fake Git server, pause, and reconcile the unknown response',
      'inspect fixed targets and independent delivery observations, then review a non-executable reverse plan'],
    backendChecks: ['Postgres and the public API retain exactly forty targets and all seven explicit exclusions',
      'the independent Git fixture contains exactly one PR for each of thirty-three included targets with exact authorized trees',
      'the unknown target reconciles without another Git write; installed and delivered remain unobserved',
      'one-sided mutations of membership, local policy and PR multiplicity are rejected',
      'a PR head moved or reviewed bytes changed before merge-reviewed is refused with no merge write; a rerun reconciles the merged PR without another write'],
  }),
}, async ({ page, actor, request, probe }, testInfo) => {
  test.setTimeout(600_000); // 40 binding/plan reviews + 33 executions (360s), UI and independent probes (240s).
  const capability = await actor.api.get<{ result: { campaigns_enabled: boolean } }>('/model-hub/skills/contexts/capabilities/');
  expect(capability.result.campaigns_enabled, 'Run only with E2E_SKILLS_CAMPAIGNS_ENABLED=true in the isolated test overlay').toBe(true);
  const executorModule = '../../../bin/lib/context-campaign.mjs';
  const fixtureModule = '../../lib/skills-r3/j5-git-fixture.mjs';
  const libraryModule = '../../fixtures/skills-r3/library.mjs';
  const canonicalModule = '../../lib/skills-r3/canonical.mjs';
  const { CampaignExecutor } = await import(executorModule) as { CampaignExecutor: new (options: unknown) => { execute: (input: unknown) => Promise<Receipt> } };
  const { J5GitFixture } = await import(fixtureModule) as { J5GitFixture: new (repositories: Repository[]) => Git };
  const { repositoryFixture } = await import(libraryModule) as { repositoryFixture: () => Repository[] };
  const { digest } = await import(canonicalModule) as { digest: (value: unknown) => string };
  const repos = repositoryFixture();
  // Additional local-only instructions remain outside every managed patch.
  for (const repo of repos) Object.assign(repo.files, { 'CLAUDE.local.md': `Local-only policy ${repo.id}.\n`, 'nested/AGENTS.md': 'Retain nested scope and precedence.\n' });
  const git = new J5GitFixture(repos);
  const headers = { Authorization: `Bearer ${actor.tokens.access}`, 'X-Organization-Id': actor.organizationId, 'X-Workspace-Id': actor.workspaceId };
  const api = async (method: string, path: string, data?: unknown): Promise<any> => {
    const response = await request.fetch(`${E2E.apiUrl}${path}`, { method, headers: { ...headers, 'Idempotency-Key': randomUUID() }, data });
    const body = await response.json();
    expect(response.ok(), JSON.stringify({ method, path, status: response.status(), body })).toBe(true);
    return body.result;
  };
  const root = '/model-hub/skills/contexts/';
  const name = `e2e-j5-${Date.now().toString(36)}`;
  const text = 'Reviewed synthetic managed instructions; preserve all repository-owned policy.\n';
  const created = await api('POST', root, { name, description: 'Synthetic campaign orchestration fixture.', tags: [], files: [{ path: 'AGENTS.md', content: text }] });
  const contextRoot = `${root}${created.context_id}/`;
  const head = await api('POST', `${contextRoot}draft/head-review/`, { revision: created.revision });
  const ack = await api('POST', `${contextRoot}draft/acknowledge-head/`, { revision: head.draft_revision, expected_digest: head.candidate_digest,
    expected_head_version: head.expected_head_version, review_fingerprint: head.review_fingerprint });
  const published = await api('POST', `${contextRoot}draft/publish/`, { revision: ack.revision, expected_digest: head.candidate_digest,
    expected_head_version: head.expected_head_version, review_fingerprint: ack.review_fingerprint, base_version: null, note: '', acknowledge_warnings: true });
  const bindings: { id: string; revision: number }[] = [];
  for (const repo of repos) {
    bindings.push(await api('POST', `${contextRoot}bindings/`, { name: repo.name, kind: 'repository',
      selector: repo.disposition === 'pin' ? { version: published.version.number } : { label: 'production' },
      update_mode: 'repository_lock', profile: 'claude-code', profile_version: '1',
      repository: { provider: 'github', repository_id: repo.id, default_branch: 'main', project_scope: '' }, expected_report_interval_seconds: 3600 }));
  }
  const campaign = await api('POST', `${root}campaigns/`, { name, parent_campaign_id: null,
    targets: bindings.map((binding) => ({ binding_id: binding.id, version: published.version.number })) });
  const campaignRoot = `${root}campaigns/${campaign.id}/`;
  const plans: any[] = [];
  const decisions = [];
  let revision = campaign.revision;
  for (let index = 0; index < repos.length; index++) {
    const repo = repos[index]; const binding = bindings[index];
    const include = ['follower', 'unknown_response'].includes(repo.disposition);
    if (!include) {
      plans.push(null);
      decisions.push({ binding_id: binding.id, plan_digest: null, decision: 'exclude', reason: `Explicitly retain ${repo.disposition}; no repository write authorized.` });
      continue;
    }
    const after = repo.files['AGENTS.md'] + '\n' + text;
    const fields = { binding_revision: binding.revision, repository_id: repo.id, base_commit: repo.base,
      previous_lock_digest: null, desired_version_id: published.version.id, materialization_digest: `sha256:${sha(after)}`,
      changes: [{ path: 'AGENTS.md', action: 'replace', before_sha256: sha(repo.files['AGENTS.md']), after_sha256: sha(after),
        ownership: 'adopted', diff_preview: ` ${repo.files['AGENTS.md']}\n+${text}` }], local_review_required: true, blockers: [] };
    const plan = { ...fields, campaign_revision: revision, plan_digest: `sha256:${digest(fields)}` };
    const recorded = await api('PUT', `${campaignRoot}targets/${binding.id}/plan/`, plan);
    revision = recorded.revision; plans.push(plan);
    decisions.push({ binding_id: binding.id, plan_digest: plan.plan_digest, decision: 'include',
      reason: 'Reviewed existing no-lock AGENTS.md; retained its complete local policy plus local-only and nested instruction files.' });
  }
  const before = await api('GET', campaignRoot);
  await api('POST', `${campaignRoot}authorize/`, { revision: before.revision, expected_manifest_digest: before.manifest_digest,
    decisions, acknowledge_repository_writes: true });
  const executor = new CampaignExecutor({ hubUrl: E2E.apiUrl, hubHeaders: headers, githubToken: 'synthetic-fixture-only',
    fetch: (url: string, init: RequestInit) => url.startsWith('https://api.github.com/') ? git.fetch(url, init) : fetch(url, init) });
  const receipts: { repository: string; receipt: Receipt }[] = [];
  const execute = (index: number) => executor.execute({ campaignId: campaign.id, bindingId: bindings[index].id,
    repository: `synthetic/${repos[index].name}`, idempotencyKey: randomUUID(),
    contents: { 'AGENTS.md': Buffer.from(repos[index].files['AGENTS.md'] + '\n' + text).toString('base64') } });
  for (let index = 0; index < repos.length; index++) {
    if (!plans[index]) continue;
    const receipt = await execute(index);
    expect(receipt.state).toBe(index === 39 ? 'unknown' : 'pr_open');
    receipts.push({ repository: repos[index].id, receipt });
  }
  const unknown = await api('GET', campaignRoot);
  await api('POST', `${campaignRoot}pause/`, { revision: unknown.revision, reason: 'Inspect unknown response before any retry.' });
  const writes = git.requests.filter((row) => row.method !== 'GET').length;
  const recovered = await execute(39);
  expect(recovered.state).toBe('pr_open');
  expect(git.requests.filter((row) => row.method !== 'GET')).toHaveLength(writes);
  receipts[receipts.length - 1].receipt = recovered;
  const targets = [];
  for (let current = 1; ; current++) {
    const batch = await api('GET', `${campaignRoot}targets/?page=${current}`);
    targets.push(...batch.results);
    if (current >= batch.total_pages) break;
  }
  const targetIds = targets.map((row) => row.binding_id).sort();
  const assertMembership = (ids: string[]) => expect(ids).toEqual(bindings.map((row) => row.id).sort());
  assertMembership(targetIds);
  expect(() => assertMembership(targetIds.slice(1))).toThrow();
  const persisted = await probe.pg<{ binding_id: string; decision: string }>(
    'SELECT binding_id, decision FROM model_hub_hubskillcontextcampaigntarget WHERE campaign_id = $1 ORDER BY binding_id', [campaign.id]);
  expect(persisted).toEqual(decisions.map(({ binding_id, decision }) => ({ binding_id, decision })).sort((a, b) => a.binding_id.localeCompare(b.binding_id)));
  const trees = [];
  for (let index = 0; index < repos.length; index++) {
    const repo = repos[index]; const receipt = receipts.find((row) => row.repository === repo.id)?.receipt;
    const pulls = git.pulls(repo.id);
    const assertMultiplicity = (value: unknown[]) => expect(value).toHaveLength(receipt ? 1 : 0);
    assertMultiplicity(pulls);
    if (!receipt) continue;
    expect(() => assertMultiplicity([...pulls, ...pulls])).toThrow();
    expect(receipt.installed).toBe(false); expect(receipt.delivered).toBe(false);
    const files = git.files(receipt.repository_commit!);
    const expected = { ...repo.files, 'AGENTS.md': repo.files['AGENTS.md'] + '\n' + text };
    expect(files).toEqual(expected);
    expect(() => expect({ ...files, 'CLAUDE.local.md': 'lost' }).toEqual(expected)).toThrow();
    trees.push({ repository: repo.id, hashes: Object.fromEntries(Object.entries(files).map(([path, bytes]) => [path, sha(bytes)])) });
    const observations = await api('GET', `${campaignRoot}targets/${bindings[index].id}/observations/`);
    expect(observations.installed).toBeNull(); expect(observations.instructions_delivered).toBeNull();
  }
  // Full-stack merge controls through the public merge-reviewed library on two
  // reviewed single-repository units: a PR head moved after review, or changed
  // reviewed bytes, refuses the unit before any merge write; the restored unit
  // merges with a per-target receipt; a rerun reconciles it without another write.
  const batchModule = '../../../bin/lib/context-campaign-batch.mjs';
  const { CampaignBatch } = await import(batchModule) as { CampaignBatch: new (options: unknown) => { mergeAll: (input: unknown) => Promise<any[]> } };
  const mergeBatch = new CampaignBatch({ hub: async () => null, executor });
  const followers = repos.map((repo, index) => ({ repo, index })).filter(({ repo }) => repo.disposition === 'follower').slice(0, 2);
  const unitFor = (index: number) => {
    const receipt = receipts.find((row) => row.repository === repos[index].id)!.receipt;
    return { targets: [{ repository_id: repos[index].id, repository: `synthetic/${repos[index].name}`, pull_request_number: receipt.pull_request_number,
      commit: receipt.repository_commit, plan: plans[index], contents: { 'AGENTS.md': Buffer.from(repos[index].files['AGENTS.md'] + '\n' + text).toString('base64') } }] };
  };
  const mergeWrites = () => git.requests.filter((row) => row.method === 'PUT' && /\/merge$/.test((row as any).suffix)).length;
  const [moved, tampered] = followers;
  const movedPr = git.pulls(moved.repo.id)[0] as any; const refs = (git as any).refs as Map<string, string>;
  const refKey = `${moved.repo.id}/${movedPr.head.ref}`; const reviewedHead = refs.get(refKey)!;
  refs.set(refKey, moved.repo.base); // A collaborator force-pushes the PR branch after review.
  await expect(mergeBatch.mergeAll(unitFor(moved.index))).rejects.toThrow(/pull_request_head_changed/);
  const tamperedUnit = unitFor(tampered.index);
  tamperedUnit.targets[0].contents['AGENTS.md'] = Buffer.from('Unreviewed bytes\n').toString('base64');
  await expect(mergeBatch.mergeAll(tamperedUnit)).rejects.toThrow(/patch_hash_mismatch/);
  expect(mergeWrites()).toBe(0);
  expect((git.pulls(moved.repo.id)[0] as any).merged).toBeFalsy(); expect((git.pulls(tampered.repo.id)[0] as any).merged).toBeFalsy();
  refs.set(refKey, reviewedHead);
  const [mergedReceipt] = await mergeBatch.mergeAll(unitFor(moved.index));
  expect(mergedReceipt).toMatchObject({ repository_id: moved.repo.id, state: 'merged', merged: true });
  const observedMerge = git.pulls(moved.repo.id)[0] as any;
  expect(observedMerge.merged).toBe(true); expect(observedMerge.merge_commit_sha).toBe(mergedReceipt.merge_commit);
  expect(mergeWrites()).toBe(1);
  const [reconciledReceipt] = await mergeBatch.mergeAll(unitFor(moved.index));
  expect(reconciledReceipt).toMatchObject({ state: 'merged', reconciled: true, merge_commit: mergedReceipt.merge_commit });
  expect(mergeWrites()).toBe(1);
  expect(git.pulls(moved.repo.id)).toHaveLength(1);
  const mergeControls = { head_moved_after_review: 'refused_before_write', changed_reviewed_bytes: 'refused_before_write',
    restored_unit: mergedReceipt.state, rerun: reconciledReceipt.reconciled ? 'reconciled_without_write' : 'unexpected', merge_writes: mergeWrites() };
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(`/dashboard/skills/contexts/rollouts/${campaign.id}`);
  await expect(page.getByRole('table', { name: 'Fixed rollout targets' })).toBeVisible({ timeout: 60_000 });
  await expect(page.getByRole('table', { name: 'Fixed rollout targets' }).getByRole('row')).toHaveCount(41);
  const evidence = process.env.J5_EVIDENCE_DIR || testInfo.outputDir;
  await mkdir(`${evidence}/shots`, { recursive: true });
  await page.screenshot({ path: `${evidence}/shots/j5-forty-targets.png` });
  await page.getByRole('button', { name: `Review target ${repos[39].name}`, exact: true }).click();
  await expect(page.getByText(/A merged PR doesn.t mean the app has the change/)).toBeVisible();
  await expect(page.getByText('Not observed', { exact: true })).toHaveCount(3);
  await page.getByText('Delivery reports', { exact: true }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: `${evidence}/shots/j5-reconciled-not-delivered.png` });
  await page.getByRole('button', { name: 'Close target', exact: true }).click();
  await page.getByRole('button', { name: 'Preview undo', exact: true }).click();
  await expect(page.getByText('Reverse-plan review', { exact: true })).toBeVisible();
  await page.getByText('Reverse-plan review', { exact: true }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: `${evidence}/shots/j5-reverse-preview.png` });
  const paused = await api('GET', campaignRoot);
  const reverse = await api('POST', `${campaignRoot}rollback/preview/`, { revision: paused.revision });
  expect(reverse.executable).toBe(false); expect(reverse.targets).toHaveLength(40);
  await api('POST', `${campaignRoot}cancel/`, { revision: paused.revision, reason: 'Synthetic fixture complete; retain PR observations, no further writes.' });
  await writeFile(`${evidence}/campaign-executor.json`, JSON.stringify({ evidence_class: 'synthetic fixture', complete_job: false,
    limitations: ['No SDK materialization/install or model-boundary delivery', 'Reverse preview only; second update and applied reverse plan not qualified', 'No real GitHub conformance or competitive comparison'],
    campaign_id: campaign.id, target_count: targets.length, decisions, persisted, receipts, trees, reverse, merge_controls: mergeControls,
    fallibility: { omitted_target: 'rejected', extra_pr: 'rejected', lost_local_policy: 'rejected', restored: 'pass' } }, null, 2));
});
