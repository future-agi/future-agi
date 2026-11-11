import { execFile } from 'node:child_process';
import { readFile, writeFile, lstat, mkdtemp, mkdir, rm } from 'node:fs/promises';
import { join, dirname } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { randomUUID } from 'node:crypto';
import assert from 'node:assert/strict';
import { CampaignExecutor } from '../../../../bin/lib/context-campaign.mjs';
import { repositoryFixture } from '../../../fixtures/skills-r3/library.mjs';
import { J5GitFixture } from '../../../lib/skills-r3/j5-git-fixture.mjs';
import { observeCodexLoad } from '../../../lib/skills-r3/j5-native-fixture.mjs';
import { canonical } from '../../../lib/skills-r3/canonical.mjs';
import { promisify } from 'node:util';
import { createHash } from 'node:crypto';

const execute = promisify(execFile);
const hash = (value) => createHash('sha256').update(value).digest('hex');

/** Independent filesystem oracle: neither SDK verification nor a UI toast is truth. */
export function assertInventory(inventory, expected) {
  const ids = inventory.targets.map((row) => row.repository_id).sort();
  if (inventory.target_count !== expected.length || new Set(ids).size !== expected.length
      || JSON.stringify(ids) !== JSON.stringify(expected.map((row) => row.id).sort())) {
    throw new Error('inventory_target_accounting');
  }
  for (const witness of expected) {
    const target = inventory.targets.find((row) => row.repository_id === witness.id);
    if (!witness.available) {
      if (target.state !== 'unavailable') throw new Error('unavailable_target_lost');
      continue;
    }
    if (target.state !== 'inventoried' || target.ownership !== 'not_authorized'
        || target.observation.inventory_complete !== true) throw new Error('inventory_false_green');
    const actual = target.observation.findings.filter((row) => row.sha256 !== undefined)
      .map((row) => [row.path, row.sha256]).sort();
    const wanted = Object.entries(witness.files).sort();
    if (JSON.stringify(actual) !== JSON.stringify(wanted)) throw new Error('instruction_inventory_mismatch');
    if (target.observation.lock_sha256 !== witness.lock_sha256) throw new Error('lock_observation_mismatch');
  }
}

/** Executes the released CLI surface, with no imports of the SDK's implementation. */
export async function runInventory({ python, manifestPath, evidencePath, signal }) {
  let stdout;
  let exitCode = 0;
  try {
    ({ stdout } = await execute(python, ['-I', '-m', 'fi.context', 'inventory', '--repositories', manifestPath],
      { signal, timeout: 60_000, maxBuffer: 8 * 1024 * 1024 }));
  } catch (error) {
    // Exit 6 is the public CLI's explicit incomplete/unavailable result, not
    // success. Retain it so the independent oracle can account for every target.
    if (error.code !== 6 || typeof error.stdout !== 'string') throw error;
    stdout = error.stdout;
    exitCode = error.code;
  }
  const inventory = JSON.parse(stdout);
  const artifact = JSON.stringify({ exitCode, inventory }, null, 2);
  await writeFile(evidencePath, artifact, { flag: 'wx', mode: 0o600 });
  return { exitCode, inventory, artifact_sha256: hash(artifact) };
}

/**
 * J5 public-interface driver. The explicit synthetic campaign mode exercises
 * real public authoring APIs, the installed SDK CLI and the public executor.
 * Native load is still a hard boundary, not an installed-files approximation.
 * The runner's Checkpoint-P and external-GitHub gates are deliberately unchanged.
 * Legacy registrations continue to exercise only their read-only inventory.
 */
export async function createDriver({ jobId, runId, registration }) {
  if (jobId !== 'J5' || !runId) throw new Error('J5 driver requires its registered job/run');
  const config = registration.driver_config;
  if (config?.campaign_fixture === true) {
    if (registration.evidence_scope !== 'synthetic') throw new Error('synthetic_fixture_cannot_claim_customer_evidence');
    return new CampaignJourney({ runId, registration });
  }
  if (!config?.python || !config.manifest_path || !config.evidence_path || !config.expected_path) {
    throw new Error('J5 requires explicit installed SDK and selected repository inputs');
  }
  let observed;
  return {
    mode: 'product',
    provenance: registration.driver_provenance,
    async perform(stage, { ledger, signal }) {
      signal.throwIfAborted();
      if (stage.id !== 'inventory-existing-repositories') {
        throw new Error('j5_incomplete: ownership/executor/merge/install/load/reverse and authorized GitHub conformance are not qualified');
      }
      const witnessBytes = await readFile(config.expected_path, 'utf8');
      const expected = JSON.parse(witnessBytes);
      if (expected.length !== 40) throw new Error('J5 requires all forty selected repositories');
      observed = await ledger.measure({ dimension: 'A', primitive: 'command', actor: 'operator',
        step: stage.id, phase: stage.phase, target: 'selected-repositories',
        items: expected.map((row) => `repo-${row.id}`),
        exceptionItems: expected.filter((row) => !row.available).map((row) => `repo-${row.id}`) },
      () => runInventory({ python: config.python, manifestPath: config.manifest_path,
        evidencePath: config.evidence_path, signal }));
      assertInventory(observed.inventory, expected);
      const selected = JSON.parse(await readFile(config.manifest_path, 'utf8'));
      const current = [];
      for (const witness of expected) {
        signal.throwIfAborted();
        const project = selected.find((row) => row.repository_id === witness.id)?.project;
        if (!project) throw new Error('independent_selection_mismatch');
        if (!witness.available) {
          try {
            await lstat(project);
          } catch (error) {
            if (error.code !== 'ENOENT') throw error;
            current.push({ ...witness, files: {} });
            continue;
          }
          throw new Error('unavailable_repository_now_present');
        }
        const files = {};
        for (const path of Object.keys(witness.files)) {
          if (path.startsWith('/') || path.split('/').some((part) => ['', '.', '..'].includes(part))) {
            throw new Error('invalid_witness_relative_path');
          }
          files[path] = hash(await readFile(join(project, path)));
        }
        let lock = null;
        try { lock = hash(await readFile(join(project, '.futureagi-context.lock.json'))); }
        catch (error) { if (error.code !== 'ENOENT') throw error; }
        current.push({ ...witness, files, lock_sha256: lock });
      }
      assertInventory(observed.inventory, current);
      const filesystemArtifact = JSON.stringify(current, null, 2);
      await writeFile(config.evidence_path + '.filesystem.json', filesystemArtifact, { flag: 'wx', mode: 0o600 });
      ledger.stage({ step: stage.id, phase: stage.phase, source: 'filesystem',
        artifactHash: hash(filesystemArtifact), actionSequences: [] });
    },
    async observe(check, { signal }) {
      signal.throwIfAborted();
      // A local selection inventory is not the hub's frozen campaign membership,
      // PR accounting, pin policy, model delivery or real GitHub conformance.
      throw new Error(`j5_unobserved:${check.id}`);
    },
    async teardown() {
      // Inputs belong to the caller. Destruction/settlement of the complete job
      // is not established by finishing this read-only prefix.
      return { qualified: false };
    },
  };
}

const ROOT = '/model-hub/skills/contexts/';
const exporter = fileURLToPath(new URL('../../../../bin/context-campaign.mjs', import.meta.url));
const included = (repo) => ['follower', 'unknown_response'].includes(repo.disposition);

/** Synthetic orchestration is useful evidence, but never a native-load receipt. */
export function requireNativeDelivery(observations, repositoryIds, versionId) {
  assert.equal(repositoryIds.length, 33, 'native_delivery_included_population');
  assert.equal(new Set(repositoryIds).size, 33, 'native_delivery_duplicate_target');
  assert.deepEqual(observations.map((row) => row.repository_id).sort(), [...repositoryIds].sort(), 'native_delivery_target_set');
  for (const row of observations) {
    assert.equal(row.version_id, versionId, 'native_delivery_version');
    assert.equal(row.discovery, 'observed', 'native_discovery_unobserved');
    assert.equal(row.delivery, 'observed', 'native_delivery_unobserved');
    assert.equal(row.source, 'model_boundary', 'installation_is_not_delivery');
    assert.match(row.artifact_sha256 ?? '', /^[a-f0-9]{64}$/, 'native_delivery_artifact_missing');
  }
}

class CampaignJourney {
  constructor({ runId, registration }) {
    this.mode = 'product'; this.provenance = registration.driver_provenance;
    this.config = registration.driver_config; this.runId = runId;
    assert.ok(this.config.python && this.config.evidence_dir, 'installed_sdk_and_evidence_directory_required');
    this.hubUrl = process.env.J5_HUB_URL;
    this.headers = JSON.parse(process.env.J5_HUB_HEADERS_JSON ?? 'null');
    assert.ok(this.hubUrl && this.headers, 'test_stack_actor_required');
    assert.ok(['localhost', '127.0.0.1', '[::1]'].includes(new URL(this.hubUrl).hostname), 'synthetic_driver_requires_loopback_stack');
    this.executor = new CampaignExecutor({ hubUrl: this.hubUrl, hubHeaders: this.headers, githubToken: 'synthetic-fixture-only',
      fetch: (url, init) => url.startsWith('https://api.github.com/') ? this.git.fetch(url, init) : fetch(url, init) });
    this.cohort = registration.cohort;
    this.profile = this.config.native ? 'codex' : 'claude-code';
    this.campaigns = []; this.credentials = []; this.rounds = []; this.serial = 0;
    this.env = { ...process.env };
    this.repos = repositoryFixture().map((repo) => ({ ...repo, files: { ...repo.files,
      'CLAUDE.local.md': `Private local instructions for synthetic repository ${repo.id}.\n`,
      'nested/AGENTS.md': 'Retain nested precedence and all local policy.\n' } }));
    this.git = new J5GitFixture(this.repos);
  }

  async save(name, value) {
    const bytes = JSON.stringify(value, null, 2);
    await mkdir(this.config.evidence_dir, { recursive: true });
    await writeFile(join(this.config.evidence_dir, `${this.runId}-${name}.json`), bytes, { flag: 'wx', mode: 0o600 });
    return hash(bytes);
  }

  async api(method, path, data, signal = this.signal) {
    const response = await fetch(`${this.hubUrl}${path}`, { method, redirect: 'error', signal,
      headers: { ...this.headers, 'Content-Type': 'application/json', 'Idempotency-Key': randomUUID() },
      ...(data === undefined ? {} : { body: JSON.stringify(data) }) });
    // Payloads can contain credentials, diffs or private local instructions.
    assert.ok(response.ok, `j5_public_api_${response.status}`);
    return response.status === 204 ? null : (await response.json()).result;
  }

  async action(primitive, effect, { dimension = 'A', repos = this.repos, decision = null, target = 'campaign' } = {}) {
    this.signal.throwIfAborted();
    return this.ledger.measure({ dimension, primitive, actor: 'synthetic-operator', step: this.stage.id,
      phase: this.stage.phase, target, items: repos.map((repo) => `repo-${repo.id}`),
      exceptionItems: repos.filter((repo) => !included(repo)).map((repo) => `repo-${repo.id}`), decision }, effect);
  }

  mutation(method, path, data, primitive = 'command') {
    return this.action(primitive, () => this.api(method, path, data));
  }

  async sdk(args, expectedCode = 0) {
    return this.action('command', async () => {
      let stdout; let code = 0;
      try { ({ stdout } = await execute(this.config.python, ['-I', '-m', 'fi.context', ...args],
        { env: this.env, signal: this.signal, timeout: 60_000, maxBuffer: 8 * 1024 * 1024 })); }
      catch (error) { if (typeof error.code !== 'number' || typeof error.stdout !== 'string') throw new Error('sdk_process_did_not_complete'); code = error.code; stdout = error.stdout; }
      const value = JSON.parse(stdout);
      assert.equal(code, expectedCode, 'sdk_unexpected_exit');
      return value;
    });
  }

  async targets() {
    const rows = [];
    for (let page = 1; page <= 100; page++) {
      const response = await this.api('GET', `${this.campaign.root}targets/?page=${page}`);
      assert.ok(Number.isInteger(response.total_pages) && response.total_pages <= 100, 'invalid_target_pagination');
      rows.push(...response.results);
      if (page >= response.total_pages) return rows;
    }
    throw new Error('incomplete_target_pagination');
  }

  async review(primitive, packet, preconditions, effect = async () => {}) {
    const decision = { packet_sha256: hash(canonical(packet)), preconditions_sha256: hash(canonical(preconditions)) };
    // One exposed item is not one group-sized review. Record every repository's
    // actual scripted review, including the deliberate pin/local/unavailable cases.
    for (const repo of this.repos) await this.action(primitive, async () => {
      assert.ok(packet.some((row) => row.repository_id === repo.id), 'review_item_not_exposed');
    }, { repos: [repo], decision });
    await effect(); this.decision = decision;
    await this.save(`decision-${this.stage.id}-${++this.serial}`, { decision, packet, preconditions });
  }

  async prepareInventory() {
    assert.equal(this.cohort, 'cold', 'configured_campaign_requires_retained_state_not_a_reseeded_cold_fixture');
    this.scratch = await mkdtemp(join(tmpdir(), 'j5-driver-'));
    this.projects = new Map(this.repos.map((repo) => [repo.id, join(this.scratch, repo.name)]));
    this.bases = new Map(this.repos.map((repo) => [repo.id, repo.base]));
    for (const repo of this.repos) {
      if (repo.disposition === 'unavailable') continue;
      for (const [path, text] of Object.entries(repo.files)) {
        const file = join(this.projects.get(repo.id), path); await mkdir(dirname(file), { recursive: true }); await writeFile(file, text);
      }
    }
    const selected = this.repos.map((repo) => ({ repository_id: repo.id, project: this.projects.get(repo.id) }));
    const manifest = join(this.scratch, 'selected.json'); await writeFile(manifest, JSON.stringify(selected));
    await mkdir(this.config.evidence_dir, { recursive: true });
    const observed = await this.action('command', () => runInventory({ python: this.config.python, manifestPath: manifest,
      evidencePath: join(this.config.evidence_dir, `${this.runId}-inventory.json`), signal: this.signal }));
    const expected = this.repos.map((repo) => ({ id: repo.id, available: repo.disposition !== 'unavailable', lock_sha256: null,
      files: Object.fromEntries(Object.entries(repo.files).filter(([path]) => path !== 'references/local-policy.md').map(([path, text]) => [path, hash(text)])) }));
    assertInventory(observed.inventory, expected); assert.equal(observed.exitCode, 6);
    for (const repo of this.repos.filter((repo) => repo.disposition !== 'unavailable')) {
      for (const [path, text] of Object.entries(repo.files)) assert.equal(await readFile(join(this.projects.get(repo.id), path), 'utf8'), text);
      await assert.rejects(lstat(join(this.projects.get(repo.id), '.futureagi-context.lock.json')), { code: 'ENOENT' });
    }
    this.ledger.fault('no-lock-local-instructions', 'cold-inventory');
    this.ledger.fault('unavailable-target', 'cold-inventory');
    return { source: 'filesystem', value: expected };
  }

  async publish(revision, baseVersion) {
    const head = await this.mutation('POST', `${this.contextRoot}draft/head-review/`, { revision });
    const ack = await this.mutation('POST', `${this.contextRoot}draft/acknowledge-head/`, { revision: head.draft_revision,
      expected_digest: head.candidate_digest, expected_head_version: head.expected_head_version, review_fingerprint: head.review_fingerprint }, 'review');
    return (await this.mutation('POST', `${this.contextRoot}draft/publish/`, { revision: ack.revision, expected_digest: head.candidate_digest,
      expected_head_version: head.expected_head_version, review_fingerprint: ack.review_fingerprint, base_version: baseVersion, note: '', acknowledge_warnings: true }, 'approve')).version;
  }

  async grant() {
    const credential = await this.mutation('POST', `${ROOT}credentials/`, { purpose: 'consume', grants: [{ kind: 'agent_context',
      resource_id: this.contextId, labels: [], version_ids: [...new Set([this.first.id, this.version.id])] }], binding_ids: [],
      expires_at: new Date(Date.now() + 3_600_000).toISOString() }, 'configuration_edit');
    this.credentials.push(credential.id);
    this.env = { ...process.env, FI_CONTEXT_ORIGIN: this.hubUrl, FI_CONTEXT_ORGANIZATION_ID: this.headers['X-Organization-Id'],
      FI_CONTEXT_WORKSPACE_ID: this.headers['X-Workspace-Id'], FI_CONTEXT_CREDENTIAL: credential.secret };
  }

  async ownership() {
    assert.ok(this.projects, 'inventory_required');
    const capability = await this.api('GET', `${ROOT}capabilities/`);
    assert.equal(capability.campaigns_enabled, true, 'enable_campaigns_only_in_test_stack');
    const packet = [];
    for (const repo of this.repos) {
      const localFiles = {};
      if (repo.disposition !== 'unavailable') {
        for (const path of Object.keys(repo.files)) localFiles[path] = await readFile(join(this.projects.get(repo.id), path), 'utf8');
        await assert.rejects(lstat(join(this.projects.get(repo.id), '.futureagi-context.lock.json')), { code: 'ENOENT' });
      }
      packet.push({ repository_id: repo.id, disposition: repo.disposition, local_files: localFiles });
    }
    await this.review('review', packet, { locks: 'absent', selected: this.repos.map((repo) => repo.id) });
    this.name = `e2e-j5-driver-${randomUUID().slice(0, 12)}`;
    const created = await this.mutation('POST', ROOT, { name: this.name, description: 'Synthetic campaign driver; native delivery is not qualified.',
      tags: [], files: [{ path: 'AGENTS.md', content: 'Reviewed managed release one. Preserve all local instructions.\n' }] }, 'configuration_edit');
    this.contextId = created.context_id; this.contextRoot = `${ROOT}${this.contextId}/`;
    this.first = this.version = await this.publish(created.revision, null);
    await this.grant(); this.bindings = [];
    for (const repo of this.repos) this.bindings.push(await this.mutation('POST', `${this.contextRoot}bindings/`, { name: repo.name, kind: 'repository',
      selector: repo.disposition === 'pin' ? { version: this.first.number } : { label: 'production' }, update_mode: 'repository_lock',
      profile: this.profile, profile_version: '1', repository: { provider: 'github', repository_id: repo.id, default_branch: 'main', project_scope: '' },
      expected_report_interval_seconds: 3600 }, 'configuration_edit'));
    return { source: 'api', value: this.bindings };
  }

  async freeze(label) {
    assert.equal(this.bindings?.length, 40, 'forty_bindings_required');
    const created = await this.mutation('POST', `${ROOT}campaigns/`, { name: `${this.name}-${label}`,
      parent_campaign_id: this.campaigns.at(-1) ?? null, targets: this.bindings.map((binding) => ({ binding_id: binding.id, version: this.version.number })) });
    this.campaigns.push(created.id);
    this.campaign = { ...created, root: `${ROOT}campaigns/${created.id}/`, label, patches: new Map(), decisions: [], receipts: new Map() };
    const targets = await this.targets();
    assert.deepEqual(targets.map((row) => row.binding_id).sort(), this.bindings.map((row) => row.id).sort());
    return { source: 'api', value: targets };
  }

  async plans(reverse = false) {
    let revision = this.campaign.revision;
    for (const [index, repo] of this.repos.entries()) {
      const binding = this.bindings[index];
      if (!included(repo)) {
        this.campaign.decisions.push({ binding_id: binding.id, plan_digest: null, decision: 'exclude', reason: `Preserve deliberate ${repo.disposition}; no write authorized.` });
        continue;
      }
      const path = join(this.scratch, `${this.campaign.label}-${repo.id}.json`);
      if (reverse) await this.sdk(['restore', '--project', this.projects.get(repo.id), '--lock', join(this.scratch, `${repo.id}-original-lock.json`), '--allow-preview', '--out', path]);
      else await this.sdk(['plan', '--name', this.name, '--version', String(this.version.number), '--client', this.profile, '--project', this.projects.get(repo.id), '--adopt-local', '--allow-preview', '--out', path]);
      await this.sdk(['inspect', '--plan', path, '--diff']);
      const patch = await this.action('command', async () => {
        const { stdout } = await execute(process.execPath, [exporter, 'export', path, repo.id, this.bases.get(repo.id), String(binding.revision), String(revision)],
          { env: { ...this.env, CONTEXT_SDK_PYTHON: this.config.python }, signal: this.signal, timeout: 65_000, maxBuffer: 8 * 1024 * 1024 });
        return JSON.parse(stdout);
      }, { repos: [repo] });
      assert.equal(patch.plan.desired_version_id, this.version.id);
      const recorded = await this.mutation('PUT', `${this.campaign.root}targets/${binding.id}/plan/`, patch.plan);
      assert.equal(recorded.plan_digest, patch.plan.plan_digest); revision = recorded.revision;
      this.campaign.patches.set(index, { ...patch, path });
      this.campaign.decisions.push({ binding_id: binding.id, plan_digest: patch.plan.plan_digest, decision: 'include', reason: 'Reviewed exact local preimages, ownership and preserved local segments.' });
    }
    const targets = await this.targets();
    await this.review('review', targets.map((row) => ({ ...row, repository_id: row.repository.repository_id })), await this.api('GET', this.campaign.root));
    return { source: 'api', value: targets };
  }

  async authorize() {
    const before = await this.api('GET', this.campaign.root);
    const targets = await this.targets();
    const packet = targets.map((row) => ({ ...row, repository_id: row.repository.repository_id }));
    // Each explicit per-repository approval is counted; one API batch is not
    // presented as one human review of forty independent local-policy decisions.
    await this.review('approve', packet, before, () => this.api('POST', `${this.campaign.root}authorize/`, {
      revision: before.revision, expected_manifest_digest: before.manifest_digest, decisions: this.campaign.decisions, acknowledge_repository_writes: true }));
    return { source: 'api', value: await this.targets() };
  }

  executeTarget(index) {
    const repo = this.repos[index]; const patch = this.campaign.patches.get(index);
    return this.action('command', () => this.executor.execute({ campaignId: this.campaign.id, bindingId: this.bindings[index].id,
      repository: `synthetic/${repo.name}`, contents: patch.contents, idempotencyKey: randomUUID(), signal: this.signal }), { repos: [repo] });
  }

  async createPRs() {
    for (const [index] of this.campaign.patches) {
      const receipt = await this.executeTarget(index);
      assert.equal(receipt.state, this.campaigns.length === 1 && this.repos[index].disposition === 'unknown_response' ? 'unknown' : 'pr_open');
      this.campaign.receipts.set(index, receipt);
    }
    return { source: 'git', value: this.repos.map((repo) => ({ repository_id: repo.id, pulls: this.git.pulls(repo.id) })) };
  }

  async reconcile() {
    for (const [index, receipt] of this.campaign.receipts) {
      if (receipt.state !== 'unknown') continue;
      this.ledger.fault('unknown-pr-response', `campaign-${this.campaigns.length}`);
      const before = await this.api('GET', this.campaign.root);
      await this.mutation('POST', `${this.campaign.root}pause/`, { revision: before.revision, reason: 'Inspect uncertain Git result before retry.' }, 'recovery_decision');
      const writes = this.git.requests.filter((row) => row.method !== 'GET').length;
      const observed = await this.action('reconcile', () => this.executeTarget(index), { dimension: 'R', repos: [this.repos[index]] });
      assert.equal(observed.state, 'pr_open'); assert.equal(this.git.requests.filter((row) => row.method !== 'GET').length, writes);
      this.campaign.receipts.set(index, observed);
      const paused = await this.api('GET', this.campaign.root);
      await this.mutation('POST', `${this.campaign.root}resume/`, { revision: paused.revision, reason: 'Exact deterministic PR and complete tree reconciled.' }, 'recovery_decision');
    }
    return { source: 'git', value: [...this.campaign.receipts.values()] };
  }

  async install() {
    const installs = [];
    for (const [index, patch] of this.campaign.patches) {
      const repo = this.repos[index]; const receipt = this.campaign.receipts.get(index);
      assert.equal(receipt.state, 'pr_open');
      const prUrl = `https://api.github.com/repos/synthetic/${repo.name}/pulls/${receipt.pull_request_number}`;
      const pr = await this.action('handoff', async () => (await this.git.fetch(prUrl)).json(), { dimension: 'H', repos: [repo], target: 'repository-review' });
      await this.action('review', async () => {
        assert.equal(pr.head.sha, receipt.repository_commit); assert.equal(pr.merged, false);
        const tree = this.git.files(pr.head.sha);
        for (const change of patch.plan.changes) assert.equal(tree[change.path] === undefined ? null : hash(tree[change.path]), change.after_sha256);
      }, { repos: [repo] });
      const merge = await this.action('approve', async () => {
        const response = await this.git.fetch(`${prUrl}/merge`, { method: 'PUT', body: JSON.stringify({ sha: pr.head.sha, merge_method: 'merge' }) });
        assert.equal(response.status, 200); return response.json();
      }, { repos: [repo] });
      assert.equal(merge.merged, true); assert.notEqual(merge.sha, receipt.repository_commit);
      const observed = await this.executeTarget(index); assert.equal(observed.state, 'merged'); assert.equal(observed.delivered, false);
      const installed = await this.sdk(['apply', '--plan', patch.path, '--quiescent']);
      assert.equal(installed.state, 'installed'); assert.equal(installed.delivery, 'not_observed');
      assert.equal((await this.sdk(['verify', '--project', this.projects.get(repo.id)])).verified, true);
      const hashes = {};
      for (const [path, text] of Object.entries(this.git.files(merge.sha))) {
        const bytes = await readFile(join(this.projects.get(repo.id), path)); assert.equal(bytes.toString(), text); hashes[path] = hash(bytes);
      }
      for (const [path, text] of Object.entries(repo.files)) {
        const actual = await readFile(join(this.projects.get(repo.id), path), 'utf8');
        if (path === 'AGENTS.md') assert.ok(actual.includes(text), 'adopted_local_segment_lost'); else assert.equal(actual, text);
      }
      const lockBytes = await readFile(join(this.projects.get(repo.id), '.futureagi-context.lock.json'));
      assert.equal(JSON.parse(lockBytes).version_id, this.version.id);
      if (this.campaigns.length === 1) await writeFile(join(this.scratch, `${repo.id}-original-lock.json`), lockBytes);
      this.bases.set(repo.id, merge.sha);
      assert.equal(this.git.pulls(repo.id).length, this.campaigns.length, 'duplicate_pr');
      let native = { discovery: 'not_observed', delivery: 'not_observed', source: 'filesystem' };
      if (this.config.native) {
        const boundary = await this.action('command', () => observeCodexLoad({ executable: this.config.native.executable,
          executableSha256: this.config.native.sha256, project: this.projects.get(repo.id),
          expectedInstructions: this.git.files(merge.sha)['AGENTS.md'], signal: this.signal }), { repos: [repo], target: 'native-host' });
        native = { discovery: boundary.discovery, delivery: boundary.delivery, source: boundary.source,
          scope: boundary.scope, artifact_sha256: await this.save(`native-${this.campaign.label}-${repo.id}`, boundary) };
      }
      installs.push({ repository_id: repo.id, version_id: this.version.id, pr: pr.number, head: pr.head.sha, merge: merge.sha, hashes,
        installed: true, ...native });
    }
    await this.checkExcluded();
    const report = { evidence_class: 'synthetic fixture', complete_job: false, label: this.campaign.label,
      campaign_id: this.campaign.id, targets: await this.targets(), installs };
    this.rounds.push(report); await this.save(this.campaign.label, report);
    return report;
  }

  async checkExcluded() {
    for (const repo of this.repos.filter((repo) => !included(repo))) {
      assert.equal(this.git.pulls(repo.id).length, 0, 'excluded_repository_written');
      if (repo.disposition === 'unavailable') { await assert.rejects(lstat(this.projects.get(repo.id)), { code: 'ENOENT' }); continue; }
      for (const [path, text] of Object.entries(repo.files)) assert.equal(await readFile(join(this.projects.get(repo.id), path), 'utf8'), text);
      await assert.rejects(lstat(join(this.projects.get(repo.id), '.futureagi-context.lock.json')), { code: 'ENOENT' });
    }
  }

  async update(reverse) {
    assert.ok(this.rounds.length, 'initial_install_required');
    if (reverse) this.version = this.first;
    else {
      const draft = await this.mutation('POST', `${this.contextRoot}draft/start/`, { revision: 0, from_version: this.first.number });
      const saved = await this.mutation('PUT', `${this.contextRoot}draft/`, { revision: draft.revision, base_version: this.first.number,
        files: [{ path: 'AGENTS.md', content: 'Reviewed managed release two. Preserve all local instructions.\n' }], description: 'Synthetic fixture.', note: '' }, 'field_edit');
      this.version = await this.publish(saved.revision, this.first.number); await this.grant();
    }
    await this.freeze(reverse ? 'reverse' : 'second-update'); await this.plans(reverse);
    const reviewed = this.decision;
    await this.authorize(); await this.createPRs(); await this.reconcile();
    // Reverse-plan's required review must bind to its actual review action, not
    // the later authorization packet. Both actions remain separately counted.
    if (reverse) this.decision = reviewed;
    return this.install();
  }

  async perform(stage, { ledger, signal }) {
    this.stage = stage; this.ledger = ledger; this.signal = signal; this.decision = null;
    signal.throwIfAborted(); let result;
    switch (stage.id) {
      case 'inventory-existing-repositories': result = await this.prepareInventory(); break;
      case 'establish-ownership': result = await this.ownership(); break;
      case 'freeze-targets': result = await this.freeze('cold'); break;
      case 'review-plans': result = await this.plans(); break;
      case 'authorize-exact-patches': result = await this.authorize(); break;
      case 'create-prs': result = await this.createPRs(); break;
      case 'reconcile-before-retry': result = await this.reconcile(); break;
      case 'review-merge-install-load': {
        const report = await this.install();
        // Fail before emitting a completion receipt. Neither the executor's
        // merged state nor the SDK's verify result is native discovery/delivery.
        requireNativeDelivery(report.installs, this.repos.filter(included).map((repo) => repo.id), this.version.id);
        result = { source: 'model_boundary', value: report }; break;
      }
      case 'second-update':
      case 'reverse-plan': {
        const report = await this.update(stage.id === 'reverse-plan');
        requireNativeDelivery(report.installs, this.repos.filter(included).map((repo) => repo.id), this.version.id);
        result = { source: 'model_boundary', value: report }; break;
      }
      default: throw new Error('j5_unknown_stage');
    }
    const artifactHash = await this.save(`stage-${stage.id}`, result.value);
    ledger.stage({ step: stage.id, phase: stage.phase, source: result.source, artifactHash, decision: this.decision });
  }

  async observe(check, { signal }) {
    signal.throwIfAborted(); this.signal = signal;
    if (check.id === 'real-github-conformance') throw new Error('external_real_github_conformance_not_supplied');
    assert.ok(this.campaign, 'campaign_unobserved');
    let source; let value;
    if (check.id === 'exact-forty-targets') {
      source = 'api'; value = (await this.targets()).map((row) => row.repository.repository_id);
    } else if (check.id === 'one-pr-per-target') {
      source = 'git'; value = this.repos.map((repo) => ({ repository_id: repo.id, pulls: this.git.pulls(repo.id) }));
    } else if (check.id === 'authorized-base-preimages') {
      source = 'git'; value = [];
      for (const repo of this.repos) for (const pr of this.git.pulls(repo.id)) {
        const response = await this.git.fetch(`https://api.github.com/repos/synthetic/${repo.name}/git/commits/${pr.head.sha}`);
        value.push({ repository_id: repo.id, pr: pr.number, commit: await response.json(), files: this.git.files(pr.head.sha) });
      }
    } else if (check.id === 'local-policy-preserved') {
      source = 'filesystem'; value = [];
      for (const repo of this.repos.filter((repo) => repo.disposition !== 'unavailable')) for (const path of Object.keys(repo.files)) {
        value.push({ path: `${repo.id}/${path}`, content: await readFile(join(this.projects.get(repo.id), path), 'utf8') });
      }
    } else if (check.id === 'pins-retained') {
      source = 'filesystem'; await this.checkExcluded();
      value = this.repos.filter((repo) => repo.disposition === 'pin').map((repo) => ({ repository_id: repo.id, unchanged: true }));
    } else if (check.id === 'stage-accounting') {
      source = 'api'; value = [];
      for (const binding of this.bindings) value.push({ binding_id: binding.id,
        observations: await this.api('GET', `${this.campaign.root}targets/${binding.id}/observations/`) });
    } else throw new Error('j5_unknown_check');
    return { source, value, artifact_sha256: await this.save(`observation-${check.id}-${++this.serial}`, value) };
  }

  async teardown() {
    // The runner has aborted the operation signal; cleanup has a separate bounded
    // signal. Retain audit records, settle/cancel campaigns and revoke credentials.
    const signal = AbortSignal.timeout(25_000); let qualified = true;
    for (const id of this.campaigns) {
      try {
        const root = `${ROOT}campaigns/${id}/`; const current = await this.api('GET', root, undefined, signal);
        if (!['settled', 'cancelled'].includes(current.status)) await this.api('POST', `${root}cancel/`, {
          revision: current.revision, reason: 'Synthetic driver teardown; preserve uncertain results and prohibit new writes.' }, signal);
        assert.ok(['settled', 'cancelled'].includes((await this.api('GET', root, undefined, signal)).status));
      } catch { qualified = false; }
    }
    for (const id of this.credentials) {
      try { await this.api('DELETE', `${ROOT}credentials/${id}/`, undefined, signal); }
      catch { qualified = false; }
    }
    if (this.credentials.length) {
      try {
        const observed = new Map();
        for (let page = 1; page <= 100; page++) {
          const result = await this.api('GET', `${ROOT}credentials/?page=${page}`, undefined, signal);
          assert.ok(Number.isInteger(result.total_pages) && result.total_pages <= 100, 'credential_inventory_incomplete');
          for (const row of result.results) observed.set(row.id, row);
          if (page >= result.total_pages) break;
        }
        for (const id of this.credentials) assert.ok(observed.get(id)?.revoked_at, 'credential_revocation_unobserved');
      } catch { qualified = false; }
    }
    if (this.scratch) {
      try { await rm(this.scratch, { recursive: true, force: true }); await assert.rejects(lstat(this.scratch), { code: 'ENOENT' }); }
      catch { qualified = false; }
    }
    const nativeObserved = this.rounds.length === 3 && this.rounds.every((round) => round.installs.length === 33
      && round.installs.every((row) => row.source === 'model_boundary' && row.discovery === 'observed' && row.delivery === 'observed'));
    this.git = null; this.executor = null; this.env = {}; this.headers = {};
    await this.save('teardown', { settled_or_cancelled: qualified, credentials_revoked: qualified, fixture_destroyed: qualified,
      complete_job: false, discovery: nativeObserved ? 'observed' : 'not_observed', delivery: nativeObserved ? 'observed' : 'not_observed',
      scope: nativeObserved ? 'codex-0.167.1-linux-root-AGENTS-only' : null });
    return { qualified };
  }
}
