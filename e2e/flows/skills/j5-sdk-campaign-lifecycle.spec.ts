import { createHash, randomUUID } from 'node:crypto';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { mkdtemp, mkdir, readFile, writeFile, rm, access } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve, dirname } from 'node:path';
import { test, expect } from '../../lib/fixtures';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

const execute = promisify(execFile);
const hash = (value: string | Buffer) => createHash('sha256').update(value).digest('hex');
type Repository = { id: string; name: string; base: string; disposition: string; files: Record<string, string> };
type Receipt = { state: string; repository_commit: string; pull_request_number: number; installed: boolean; delivered: boolean };
type Git = { fetch: typeof fetch; pulls: (id: string) => unknown[]; files: (commit: string) => Record<string, string>; requests: { method: string }[] };
type Patch = { plan: any; contents: Record<string, string> };

// Native materialization is currently preview-only. This flow proves exact Git
// trees and verified local installations, NOT host discovery/model delivery.
test('SKILLS-E2E-052: SDK plans preserve local policy through cold adoption, a second rollout and reviewed reverse application', {
  tag: ['@flow'],
  annotation: flowAnnotation({ id: 'SKILLS-E2E-052', area: 'skills',
    userGoal: 'Use real SDK materialization plans for all included repositories, merge exact patches, install separately, then update and reverse without deleting local instructions',
    steps: ['inventory forty selected no-lock repositories; adopt the four deliberate pins at their pinned release in the cold round, then hold them; exclude two local modifications and one unavailable target',
      'export and authorize real SDK plans, create deterministic PRs and reconcile the unknown response',
      'review and merge each PR, apply its preimage-bound plan and independently compare installed files against the merged Git tree',
      'publish a second immutable version and repeat the configured rollout without advancing held pins or excluded repositories',
      'reject dirty managed bytes and changed preimages, then review and apply reverse plans to the original release'],
    backendChecks: ['all three campaigns retain forty exact targets and explicit exclusion reasons in API and Postgres',
      'each included repository has exactly one PR per campaign and a distinct merge commit; Git trees equal SDK-installed files',
      'original local-only and nested instructions remain byte-identical and adopted local segments survive all three operations',
      'each held pin keeps its one adoption PR, its pinned-release lock and its adopted managed bytes in every later round',
      'dirty-managed and stale-preimage controls fail before Git writes; reported delivery remains unobserved'],
  }),
}, async ({ page, actor, request, probe }, testInfo) => {
  test.setTimeout(1_800_000); // 99 public plan/export/apply/verify cycles plus three full 40-target reviews.
  const python = process.env.CTXHUB_J5_PYTHON;
  if (!python) throw new Error('Select the installed SDK with CTXHUB_J5_PYTHON on the cloud VM');
  const scratch = await mkdtemp(join(tmpdir(), 'j5-sdk-lifecycle-'));
  const evidence = process.env.J5_EVIDENCE_DIR || testInfo.outputDir;
  const root = '/model-hub/skills/contexts/';
  const headers = { Authorization: `Bearer ${actor.tokens.access}`, 'X-Organization-Id': actor.organizationId, 'X-Workspace-Id': actor.workspaceId };
  const api = async (method: string, path: string, data?: unknown): Promise<any> => {
    const response = await request.fetch(`${E2E.apiUrl}${path}`, { method, headers: { ...headers, 'Idempotency-Key': randomUUID() }, data });
    const body = response.status() === 204 ? {} : await response.json();
    expect(response.ok(), JSON.stringify({ method, path, status: response.status(), body })).toBe(true);
    return body.result;
  };
  const capability = await api('GET', `${root}capabilities/`);
  expect(capability.campaigns_enabled).toBe(true);
  const libraryModule = '../../fixtures/skills-r3/library.mjs';
  const fixtureModule = '../../lib/skills-r3/j5-git-fixture.mjs';
  const executorModule = '../../../bin/lib/context-campaign.mjs';
  const inventoryModule = '../../benchmarks/context-hub/drivers/j5.mjs';
  const { repositoryFixture } = await import(libraryModule) as { repositoryFixture: () => Repository[] };
  const { J5GitFixture } = await import(fixtureModule) as { J5GitFixture: new (repos: Repository[]) => Git };
  const { CampaignExecutor } = await import(executorModule) as { CampaignExecutor: new (options: unknown) => { execute: (input: unknown) => Promise<Receipt> } };
  const { runInventory, assertInventory } = await import(inventoryModule) as any;
  const repositories = repositoryFixture();
  for (const repo of repositories) Object.assign(repo.files, { 'CLAUDE.local.md': `Local private instruction ${repo.id}.\n`, 'nested/AGENTS.md': 'Keep nested precedence.\n' });
  const git = new J5GitFixture(repositories);
  const selected = repositories.map(repo => ({ repository_id: repo.id, project: join(scratch, repo.name) }));
  const included = (repo: Repository) => ['follower', 'unknown_response'].includes(repo.disposition);
  // Deliberate pins are adopted once, in the cold round at their own pinned release, then held.
  const planned = (repo: Repository, ordinal: number) => included(repo) || (repo.disposition === 'pin' && ordinal === 1);
  const pinAdoptions = new Map<string, string>();
  const projects = new Map(selected.map(row => [row.repository_id, row.project]));
  const bases = new Map(repositories.map(repo => [repo.id, repo.base]));
  const campaigns: string[] = []; const credentialIds: string[] = [];
  const reports: unknown[] = [];
  let env: NodeJS.ProcessEnv = { ...process.env };
  const command = async (args: string[], expected = 0) => {
    let stdout: string; let code = 0;
    try { ({ stdout } = await execute(python, ['-I', '-m', 'fi.context', ...args], { env, timeout: 60_000, maxBuffer: 8 * 1024 * 1024 })); }
    catch (error: any) { if (typeof error.code !== 'number' || typeof error.stdout !== 'string') throw new Error('SDK process did not complete'); code = error.code; stdout = error.stdout; }
    const result = JSON.parse(stdout!);
    expect(code, JSON.stringify({ command: args[0], result })).toBe(expected);
    return result;
  };
  const name = `e2e-j5-sdk-${Date.now().toString(36)}`;
  const created = await api('POST', root, { name, description: 'Synthetic SDK campaign lifecycle; host delivery not qualified.', tags: [],
    files: [{ path: 'AGENTS.md', content: 'Managed release one: reviewed synthetic policy.\n' }] });
  const contextRoot = `${root}${created.context_id}/`;
  const publish = async (revision: number, baseVersion: number | null) => {
    const head = await api('POST', `${contextRoot}draft/head-review/`, { revision });
    const ack = await api('POST', `${contextRoot}draft/acknowledge-head/`, { revision: head.draft_revision, expected_digest: head.candidate_digest,
      expected_head_version: head.expected_head_version, review_fingerprint: head.review_fingerprint });
    return (await api('POST', `${contextRoot}draft/publish/`, { revision: ack.revision, expected_digest: head.candidate_digest,
      expected_head_version: head.expected_head_version, review_fingerprint: ack.review_fingerprint, base_version: baseVersion, note: '', acknowledge_warnings: true })).version;
  };
  const first = await publish(created.revision, null);
  const grant = async (versionIds: string[]) => {
    const credential = await api('POST', `${root}credentials/`, { purpose: 'consume',
      grants: [{ kind: 'agent_context', resource_id: created.context_id, labels: [], version_ids: versionIds }], binding_ids: [],
      expires_at: new Date(Date.now() + 3_600_000).toISOString() });
    credentialIds.push(credential.id);
    env = { ...process.env, FI_CONTEXT_ORIGIN: E2E.apiUrl, FI_CONTEXT_ORGANIZATION_ID: actor.organizationId,
      FI_CONTEXT_WORKSPACE_ID: actor.workspaceId, FI_CONTEXT_CREDENTIAL: credential.secret };
  };
  try {
    await grant([first.id]);
    for (const repo of repositories) {
      if (repo.disposition === 'unavailable') continue;
      for (const [path, text] of Object.entries(repo.files)) {
        const file = join(projects.get(repo.id)!, path); await mkdir(dirname(file), { recursive: true }); await writeFile(file, text);
      }
    }
    const manifest = join(scratch, 'selected.json'); await writeFile(manifest, JSON.stringify(selected));
    await mkdir(evidence, { recursive: true });
    const inventory = await runInventory({ python, manifestPath: manifest, evidencePath: join(evidence, 'sdk-inventory.json') });
    const witness = repositories.map(repo => ({ id: repo.id, available: repo.disposition !== 'unavailable', lock_sha256: null,
      // The inventory contract lists instruction discovery files, not every
      // unrelated repository document. All files are checked after each round.
      files: Object.fromEntries(Object.entries(repo.files).filter(([path]) => path !== 'references/local-policy.md').map(([path, text]) => [path, hash(text)])) }));
    assertInventory(inventory.inventory, witness); expect(inventory.exitCode).toBe(6);
    const bindings: { id: string; revision: number }[] = [];
    for (const repo of repositories) bindings.push(await api('POST', `${contextRoot}bindings/`, { name: repo.name, kind: 'repository',
      selector: repo.disposition === 'pin' ? { version: first.number } : { label: 'production' }, update_mode: 'repository_lock',
      profile: 'claude-code', profile_version: '1', repository: { provider: 'github', repository_id: repo.id, default_branch: 'main', project_scope: '' }, expected_report_interval_seconds: 3600 }));
    const executor = new CampaignExecutor({ hubUrl: E2E.apiUrl, hubHeaders: headers, githubToken: 'synthetic-fixture-only',
      fetch: (url: string, init: RequestInit) => url.startsWith('https://api.github.com/') ? git.fetch(url, init) : fetch(url, init) });
    const exportPlan = async (path: string, index: number, revision: number): Promise<Patch> => {
      const { stdout } = await execute(process.execPath, [resolve('../bin/context-campaign.mjs'), 'export', path, repositories[index].id,
        bases.get(repositories[index].id)!, String(bindings[index].revision), String(revision)],
      { env: { ...env, CONTEXT_SDK_PYTHON: python }, timeout: 65_000, maxBuffer: 8 * 1024 * 1024 });
      return JSON.parse(stdout);
    };
    const rounds = async (label: string, version: any, ordinal: number) => {
      const campaign = await api('POST', `${root}campaigns/`, { name: `${name}-${label}`, parent_campaign_id: campaigns.at(-1) ?? null,
        targets: bindings.map(binding => ({ binding_id: binding.id, version: version.number })) });
      campaigns.push(campaign.id); const campaignRoot = `${root}campaigns/${campaign.id}/`;
      const patches = new Map<number, Patch>(); const paths = new Map<number, string>(); const decisions = [];
      let revision = campaign.revision;
      for (let index = 0; index < repositories.length; index++) {
        const repo = repositories[index]; const binding = bindings[index];
        if (!planned(repo, ordinal)) { decisions.push({ binding_id: binding.id, plan_digest: null, decision: 'exclude', reason: repo.disposition === 'pin'
          ? 'Hold deliberate pin at its installed pinned release; no write authorized.' : `Retain intentional ${repo.disposition}; no write authorized.` }); continue; }
        const path = join(scratch, `${label}-${repo.id}.json`); const project = projects.get(repo.id)!;
        if (label === 'reverse') await command(['restore', '--project', project, '--lock', join(scratch, `${repo.id}-original-lock.json`), '--allow-preview', '--out', path]);
        else await command(['plan', '--name', name, '--version', String(version.number), '--client', 'claude-code', '--project', project, '--adopt-local', '--allow-preview', '--out', path]);
        await command(['inspect', '--plan', path, '--diff']);
        const patch = await exportPlan(path, index, revision);
        expect(patch.plan.desired_version_id).toBe(version.id);
        const recorded = await api('PUT', `${campaignRoot}targets/${binding.id}/plan/`, patch.plan);
        expect(recorded.plan_digest).toBe(patch.plan.plan_digest); revision = recorded.revision;
        patches.set(index, patch); paths.set(index, path);
        decisions.push({ binding_id: binding.id, plan_digest: patch.plan.plan_digest, decision: 'include',
          reason: 'Reviewed exact SDK diff and preimages, including ownership and preservation of existing local segments.' });
      }
      const before = await api('GET', campaignRoot);
      await api('POST', `${campaignRoot}authorize/`, { revision: before.revision, expected_manifest_digest: before.manifest_digest,
        decisions, acknowledge_repository_writes: true });
      const persisted = await probe.pg<{ binding_id: string; decision: string }>('SELECT binding_id, decision FROM model_hub_hubskillcontextcampaigntarget WHERE campaign_id = $1 ORDER BY binding_id', [campaign.id]);
      expect(persisted).toEqual(decisions.map(({ binding_id, decision }) => ({ binding_id, decision })).sort((a, b) => a.binding_id.localeCompare(b.binding_id)));
      expect(persisted).toHaveLength(40);
      const installs = [];
      for (const [index, patch] of patches) {
        const repo = repositories[index]; const project = projects.get(repo.id)!;
        const input = { campaignId: campaign.id, bindingId: bindings[index].id, repository: `synthetic/${repo.name}`,
          idempotencyKey: randomUUID(), contents: patch.contents };
        let receipt = await executor.execute(input);
        if (ordinal === 1 && repo.disposition === 'unknown_response') {
          expect(receipt.state).toBe('unknown');
          const beforePause = await api('GET', campaignRoot);
          await api('POST', `${campaignRoot}pause/`, { revision: beforePause.revision, reason: 'Review uncertain PR before retry.' });
          const writes = git.requests.filter(row => row.method !== 'GET').length;
          receipt = await executor.execute({ ...input, idempotencyKey: randomUUID() });
          expect(git.requests.filter(row => row.method !== 'GET')).toHaveLength(writes);
          const paused = await api('GET', campaignRoot);
          await api('POST', `${campaignRoot}resume/`, { revision: paused.revision, reason: 'Exact branch and tree reconciled; continue reviewed work.' });
        }
        expect(receipt.state).toBe('pr_open'); expect(receipt.installed).toBe(false); expect(receipt.delivered).toBe(false);
        expect(git.pulls(repo.id)).toHaveLength(ordinal);
        const mergeResponse = await git.fetch(`https://api.github.com/repos/synthetic/${repo.name}/pulls/${receipt.pull_request_number}/merge`, {
          method: 'PUT', body: JSON.stringify({ sha: receipt.repository_commit, merge_method: 'merge' }),
        });
        expect(mergeResponse.status).toBe(200); const merge = await mergeResponse.json();
        expect(merge.merged).toBe(true); expect(merge.sha).not.toBe(receipt.repository_commit);
        const observed = await executor.execute({ ...input, idempotencyKey: randomUUID() });
        expect(observed.state).toBe('merged'); expect(observed.installed).toBe(false); expect(observed.delivered).toBe(false);
        const installed = await command(['apply', '--plan', paths.get(index)!, '--quiescent']);
        expect(installed.state).toBe('installed'); expect(installed.delivery).toBe('not_observed');
        const verified = await command(['verify', '--project', project]); expect(verified.verified).toBe(true);
        const files = git.files(merge.sha); const hashes: Record<string, string> = {};
        for (const [path, text] of Object.entries(files)) { const actual = await readFile(join(project, path)); expect(actual.toString('utf8')).toBe(text); hashes[path] = hash(actual); }
        // Adoption may wrap AGENTS but must retain its complete original local segment.
        for (const [path, text] of Object.entries(repo.files)) {
          const actual = await readFile(join(project, path), 'utf8');
          if (path === 'AGENTS.md') expect(actual).toContain(text); else expect(actual).toBe(text);
        }
        const lock = JSON.parse(await readFile(join(project, '.futureagi-context.lock.json'), 'utf8'));
        expect(lock.version_id).toBe(version.id);
        if (ordinal === 1) await writeFile(join(scratch, `${repo.id}-original-lock.json`), await readFile(join(project, '.futureagi-context.lock.json')));
        bases.set(repo.id, merge.sha);
        if (repo.disposition === 'pin') pinAdoptions.set(repo.id, merge.sha);
        installs.push({ repository: repo.id, pr: receipt.pull_request_number, head: receipt.repository_commit, merge: merge.sha,
          lock_version_id: lock.version_id, hashes, installed: true, discovery: 'not_observed', delivery: 'not_observed' });
      }
      for (const repo of repositories.filter(repo => !planned(repo, ordinal) && repo.disposition !== 'unavailable')) {
        const project = projects.get(repo.id)!;
        if (repo.disposition === 'pin') {
          // Held pin: one adoption PR, lock still at the pinned release, managed bytes still the adoption merge's.
          const adoption = pinAdoptions.get(repo.id)!;
          expect(adoption).toBeTruthy(); expect(git.pulls(repo.id)).toHaveLength(1);
          const lock = JSON.parse(await readFile(join(project, '.futureagi-context.lock.json'), 'utf8'));
          expect(lock.version_id).toBe(first.id);
          for (const [path, text] of Object.entries(git.files(adoption))) expect(await readFile(join(project, path), 'utf8')).toBe(text);
          for (const [path, text] of Object.entries(repo.files)) {
            const actual = await readFile(join(project, path), 'utf8');
            if (path === 'AGENTS.md') expect(actual).toContain(text); else expect(actual).toBe(text);
          }
          continue;
        }
        for (const [path, text] of Object.entries(repo.files)) expect(await readFile(join(project, path), 'utf8')).toBe(text);
        await expect(access(join(project, '.futureagi-context.lock.json'))).rejects.toThrow();
        expect(git.pulls(repo.id)).toHaveLength(0);
      }
      await page.setViewportSize({ width: 1440, height: 900 });
      await page.goto(`/dashboard/skills/contexts/rollouts/${campaign.id}`);
      const table = page.getByRole('table', { name: 'Fixed rollout targets' });
      await expect(table).toBeVisible({ timeout: 60_000 }); await expect(table.getByRole('row')).toHaveCount(41);
      await mkdir(join(evidence, 'shots'), { recursive: true });
      await page.screenshot({ path: join(evidence, 'shots', `j5-sdk-${label}.png`) });
      const current = await api('GET', campaignRoot);
      // A fully observed campaign is already terminal; settled is not a
      // cancellable state and must not be confused with delivery completion.
      expect(current.status).toBe('settled');
      const report = { label, campaign_id: campaign.id, target_count: persisted.length, decisions, persisted, installs };
      reports.push(report); await writeFile(join(evidence, `sdk-${label}.json`), JSON.stringify(report, null, 2));
    };
    await rounds('cold', first, 1);
    const draft = await api('POST', `${contextRoot}draft/start/`, { revision: 0, from_version: first.number });
    const saved = await api('PUT', `${contextRoot}draft/`, { revision: draft.revision, base_version: first.number,
      files: [{ path: 'AGENTS.md', content: 'Managed release two: revised reviewed synthetic policy.\n' }], description: 'Synthetic SDK lifecycle.', note: '' });
    const second = await publish(saved.revision, first.number);
    await grant([first.id, second.id]);
    await rounds('second-update', second, 2);
    const project = projects.get(repositories[0].id)!;
    const lock = JSON.parse(await readFile(join(project, '.futureagi-context.lock.json'), 'utf8'));
    const managed = lock.materialization.files.find((row: any) => row.ownership === 'managed' && row.destination.endsWith('AGENTS.md'));
    expect(managed).toBeTruthy();
    const managedPath = join(project, managed.destination); const original = await readFile(managedPath);
    await writeFile(managedPath, 'Locally modified managed bytes; retain rather than overwrite.\n');
    const dirty = await command(['restore', '--project', project, '--lock', join(scratch, `${repositories[0].id}-original-lock.json`), '--allow-preview', '--out', join(scratch, 'dirty.json')], 5);
    expect(dirty.error.code).toBe('local_modifications'); await expect(access(join(scratch, 'dirty.json'))).rejects.toThrow();
    await writeFile(managedPath, original);
    const stalePath = join(scratch, 'stale.json');
    await command(['restore', '--project', project, '--lock', join(scratch, `${repositories[0].id}-original-lock.json`), '--allow-preview', '--out', stalePath]);
    const agentsPath = join(project, 'AGENTS.md'); const agents = await readFile(agentsPath);
    await writeFile(agentsPath, Buffer.concat([agents, Buffer.from('\nNew local work after review.\n')]));
    const stale = await command(['apply', '--plan', stalePath, '--quiescent'], 5);
    expect(stale.error.code).toBe('stale_plan'); expect(await readFile(agentsPath, 'utf8')).toContain('New local work after review.');
    await writeFile(agentsPath, agents);
    await rounds('reverse', first, 3);
    await writeFile(join(evidence, 'sdk-lifecycle.json'), JSON.stringify({ evidence_class: 'synthetic fixture', complete_job: false,
      reports, fallibility: { dirty_managed: dirty.error.code, changed_preimage: stale.error.code, restored: 'pass' },
      limitations: ['Native host discovery and actual model-boundary delivery remain unqualified.', 'No registered complete-job counts, Checkpoint P receipt, real GitHub conformance or customer comparison.'] }, null, 2));
  } finally {
    for (const id of campaigns) {
      const current = await api('GET', `${root}campaigns/${id}/`);
      if (!['settled', 'cancelled'].includes(current.status)) await api('POST', `${root}campaigns/${id}/cancel/`, { revision: current.revision, reason: 'Synthetic lifecycle cleanup; retain uncertain observations.' });
    }
    for (const id of credentialIds) await api('DELETE', `${root}credentials/${id}/`);
    await rm(scratch, { recursive: true, force: true });
  }
});
