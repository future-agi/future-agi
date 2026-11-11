import { execFile } from 'node:child_process';
import { readFile, writeFile, lstat, mkdtemp, mkdir, rm } from 'node:fs/promises';
import { join, dirname } from 'node:path';
import { tmpdir } from 'node:os';
import { randomUUID, createHash } from 'node:crypto';
import assert from 'node:assert/strict';
import { CampaignExecutor } from '../../../../bin/lib/context-campaign.mjs';
import { CampaignBatch } from '../../../../bin/lib/context-campaign-batch.mjs';
import { ActionLedger, summarizeLedger } from '../../../lib/skills-r3/ledger.mjs';
import { repositoryFixture } from '../../../fixtures/skills-r3/library.mjs';
import { J5GitFixture } from '../../../lib/skills-r3/j5-git-fixture.mjs';
import { observeCodexLoad, assertCodexBoundary } from '../../../lib/skills-r3/j5-native-fixture.mjs';
import { canonical } from '../../../lib/skills-r3/canonical.mjs';
import { getJob } from '../jobs.mjs';
import { promisify } from 'node:util';

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
const included = (repo) => ['follower', 'unknown_response'].includes(repo.disposition);
const LOCK = '.futureagi-context.lock.json';

/**
 * Declarative precondition for the already-configured cohort. A configured run
 * reaches it through the same public interfaces on a separate setup ledger and
 * verifies the realised state before its first measured action. It is never
 * relabelled as recurring work, and no compatible lock is seeded.
 */
export const CONFIGURED_PRECONDITION = Object.freeze({
  schema: 1, population: 40, bindings: 40, installed_release: 1, installed_repositories: 33,
  lockless_excluded_with_local_instructions: 6, unavailable: 1, deliberate_pins: 4,
  open_pull_requests: 0, next_release_published: 2, reached_by: 'public interfaces on a separate setup ledger',
});
export const COLD_PRECONDITION = Object.freeze({
  schema: 1, population: 40, bindings: 0, locks: 0, repositories_with_local_instructions: 39, unavailable: 1,
});

/** The 40-repository synthetic fixture with real local, private and nested instructions; no locks. */
export function fixtureRepositories() {
  return repositoryFixture().map((repo) => ({ ...repo, files: { ...repo.files,
    'CLAUDE.local.md': `Private local instructions for synthetic repository ${repo.id}.\n`,
    'nested/AGENTS.md': 'Retain nested precedence and all local policy.\n' } }));
}

export const J5_MEASUREMENTS = Object.freeze({ cold_adoption: 'cold', recurring_rollout: 'configured', recovery: 'configured' });

/**
 * Preregistration for one of the three separate J5 measurements. Expected
 * observations derive from the fixture, never from a run. Checkpoint P and real
 * GitHub conformance are external: no receipt or GitHub observation is supplied.
 */
export function j5Registration({ label, provenance, python, evidenceDir, native }) {
  const cohort = J5_MEASUREMENTS[label];
  assert.ok(cohort, 'unknown_j5_measurement');
  const job = getJob('J5'); const repos = fixtureRepositories();
  const initial = { label, precondition: { ...(cohort === 'cold' ? COLD_PRECONDITION : CONFIGURED_PRECONDITION) } };
  const original = repos.filter((repo) => repo.disposition !== 'unavailable').flatMap((repo) => Object.entries(repo.files)
    .map(([path, text]) => ({ path: `${repo.id}/${path}`, sha256: hash(text), size_bytes: Buffer.byteLength(text) })));
  return { schema: 1, job: 'J5', cohort, evidence_scope: 'synthetic', population: job.population, fault_ids: job.fault_ids,
    customer_input_hash: hash(canonical(repos)), initial_conditions_hash: hash(canonical(initial)),
    safety_requirements_hash: hash(canonical(job.checks)), complete_job_boundary_hash: hash(canonical(job.steps)),
    expected_observations: { 'exact-forty-targets': repos.map((repo) => repo.id), 'one-pr-per-target': { duplicates: 0 },
      'authorized-base-preimages': { unauthorized_effects: 0 }, 'local-policy-preserved': original,
      'pins-retained': { deliberate_pins: 4, advanced_pins: 0 }, 'stage-accounting': { target_count: 40, merge_install_load_distinct: true },
      'real-github-conformance': { qualified: true } },
    driver_provenance: provenance,
    driver_config: { campaign_fixture: true, python, evidence_dir: evidenceDir, native, initial_conditions: initial },
    limitations: ['Synthetic fixture on the repository fake Git server; no customer, comparator, human-time or GitHub conformance claim.',
      'Checkpoint P acceptance is external; no receipt is supplied or fabricated.',
      'real-github-conformance requires an authorized disposable-repository exercise and is never observed from the fake.',
      cohort === 'configured' ? 'Configured precondition is reached through public interfaces on a separate retained setup ledger.' : 'Cold adoption starts from lockless repositories with real local instructions.'] };
}

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

/** Every exposed repository is reviewed exactly once across groups and exceptions. */
export function reviewUnits(review, repos) {
  const byId = new Map(repos.map((repo) => [repo.id, repo]));
  const units = [
    ...review.groups.map((group) => ({ kind: 'group', key: group.key, repos: group.members.map((row) => byId.get(row.repository_id)) })),
    ...review.exceptions.map((row) => ({ kind: row.kind, key: `${row.kind}:${row.repository_id}`, repos: [byId.get(row.repository_id)] })),
  ];
  const ids = units.flatMap((unit) => unit.repos.map((repo) => repo?.id));
  assert.ok(ids.every(Boolean), 'review_item_unknown');
  assert.equal(ids.length, repos.length, 'review_item_count');
  assert.deepEqual([...new Set(ids)].sort(), repos.map((repo) => repo.id).sort(), 'review_items_not_exactly_once');
  return units;
}

class CampaignJourney {
  constructor({ runId, registration }) {
    this.mode = 'product'; this.provenance = registration.driver_provenance;
    this.config = registration.driver_config; this.runId = runId;
    assert.ok(this.config.python && this.config.evidence_dir, 'installed_sdk_and_evidence_directory_required');
    assert.ok(['cold', 'configured'].includes(registration.cohort), 'cohort_required');
    if (registration.initial_conditions_hash !== undefined) {
      // The preregistered initial condition is a declarative document; the
      // driver verifies the realised state against it, not just its label.
      const document = this.config.initial_conditions;
      const expected = registration.cohort === 'cold' ? COLD_PRECONDITION : CONFIGURED_PRECONDITION;
      assert.deepEqual(document?.precondition, { ...expected }, 'initial_conditions_document_mismatch');
      assert.equal(hash(canonical(document)), registration.initial_conditions_hash, 'initial_conditions_hash_mismatch');
    }
    this.hubUrl = process.env.J5_HUB_URL;
    this.headers = JSON.parse(process.env.J5_HUB_HEADERS_JSON ?? 'null');
    assert.ok(this.hubUrl && this.headers, 'test_stack_actor_required');
    assert.ok(['localhost', '127.0.0.1', '[::1]'].includes(new URL(this.hubUrl).hostname), 'synthetic_driver_requires_loopback_stack');
    this.executor = new CampaignExecutor({ hubUrl: this.hubUrl, hubHeaders: this.headers, githubToken: 'synthetic-fixture-only',
      fetch: (url, init) => url.startsWith('https://api.github.com/') ? this.git.fetch(url, init) : fetch(url, init) });
    this.cohort = registration.cohort;
    this.profile = this.config.native ? 'codex' : 'claude-code';
    this.campaigns = []; this.credentials = []; this.rounds = []; this.setupRounds = []; this.serial = 0; this.releases = 0;
    this.env = { ...process.env };
    this.repos = fixtureRepositories();
    this.git = new J5GitFixture(this.repos);
  }

  batch() {
    return new CampaignBatch({ hub: (method, path, data) => this.api(method, path, data), executor: this.executor,
      python: this.config.python, env: this.env, signal: this.signal });
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
    return this.batch().targets(this.campaign.id);
  }

  /** One decision per exposed unit; the packet hash binds all units of the stage. */
  async review(primitive, units, packet, preconditions, effect = async () => {}) {
    const decision = { packet_sha256: hash(canonical(packet)), preconditions_sha256: hash(canonical(preconditions)) };
    for (const unit of units) await this.action(primitive, async () => {
      assert.ok(unit.repos.length > 0 && unit.repos.every((repo) => packet.some((row) => row.repository_id === repo.id)), 'review_item_not_exposed');
    }, { repos: unit.repos, decision });
    await effect(); this.decision = decision;
    await this.save(`decision-${this.stage.id}-${++this.serial}`, { decision, units: units.map((unit) => ({ kind: unit.kind, key: unit.key,
      repositories: unit.repos.map((repo) => repo.id) })), packet, preconditions });
  }

  async prepareInventory() {
    assert.ok(this.cohort === 'cold' || this.setup, 'configured_campaign_requires_retained_state_not_a_reseeded_cold_fixture');
    this.scratch = await mkdtemp(join(tmpdir(), 'j5-driver-'));
    this.projects = new Map(this.repos.map((repo) => [repo.id, join(this.scratch, repo.name)]));
    this.bases = new Map(this.repos.map((repo) => [repo.id, repo.base]));
    for (const repo of this.repos) {
      if (repo.disposition === 'unavailable') continue;
      for (const [path, text] of Object.entries(repo.files)) {
        const file = join(this.projects.get(repo.id), path); await mkdir(dirname(file), { recursive: true }); await writeFile(file, text);
      }
    }
    this.selected = this.repos.map((repo) => ({ repository_id: repo.id, repository: `synthetic/${repo.name}`, project: this.projects.get(repo.id) }));
    const manifest = join(this.scratch, 'selected.json');
    await writeFile(manifest, JSON.stringify(this.selected.map(({ repository_id, project }) => ({ repository_id, project }))));
    await mkdir(this.config.evidence_dir, { recursive: true });
    const observed = await this.action('command', () => runInventory({ python: this.config.python, manifestPath: manifest,
      evidencePath: join(this.config.evidence_dir, `${this.runId}${this.setup ? '-setup' : ''}-inventory.json`), signal: this.signal }));
    const expected = this.repos.map((repo) => ({ id: repo.id, available: repo.disposition !== 'unavailable', lock_sha256: null,
      files: Object.fromEntries(Object.entries(repo.files).filter(([path]) => path !== 'references/local-policy.md').map(([path, text]) => [path, hash(text)])) }));
    assertInventory(observed.inventory, expected); assert.equal(observed.exitCode, 6);
    for (const repo of this.repos.filter((repo) => repo.disposition !== 'unavailable')) {
      for (const [path, text] of Object.entries(repo.files)) assert.equal(await readFile(join(this.projects.get(repo.id), path), 'utf8'), text);
      await assert.rejects(lstat(join(this.projects.get(repo.id), LOCK)), { code: 'ENOENT' });
    }
    this.ledger.fault('no-lock-local-instructions', 'cold-inventory');
    this.ledger.fault('unavailable-target', 'cold-inventory');
    return { source: 'filesystem', value: expected };
  }

  async publish(revision, baseVersion) {
    const head = await this.mutation('POST', `${this.contextRoot}draft/head-review/`, { revision });
    const ack = await this.mutation('POST', `${this.contextRoot}draft/acknowledge-head/`, { revision: head.draft_revision,
      expected_digest: head.candidate_digest, expected_head_version: head.expected_head_version, review_fingerprint: head.review_fingerprint }, 'review');
    this.releases += 1;
    return (await this.mutation('POST', `${this.contextRoot}draft/publish/`, { revision: ack.revision, expected_digest: head.candidate_digest,
      expected_head_version: head.expected_head_version, review_fingerprint: ack.review_fingerprint, base_version: baseVersion, note: '', acknowledge_warnings: true }, 'approve')).version;
  }

  async publishNext() {
    const draft = await this.mutation('POST', `${this.contextRoot}draft/start/`, { revision: 0, from_version: this.version.number });
    const saved = await this.mutation('PUT', `${this.contextRoot}draft/`, { revision: draft.revision, base_version: this.version.number,
      files: [{ path: 'AGENTS.md', content: `Reviewed managed release ${this.releases + 1}. Preserve all local instructions.\n` }], description: 'Synthetic fixture.', note: '' }, 'field_edit');
    this.version = await this.publish(saved.revision, this.version.number); await this.grant();
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
        await assert.rejects(lstat(join(this.projects.get(repo.id), LOCK)), { code: 'ENOENT' });
      }
      packet.push({ repository_id: repo.id, disposition: repo.disposition, local_files: localFiles });
    }
    // Ownership is decided per repository: local instructions differ, so no
    // grouping is claimed for this cold decision.
    await this.review('review', this.repos.map((repo) => ({ kind: 'ownership', key: repo.id, repos: [repo] })), packet,
      { locks: 'absent', selected: this.repos.map((repo) => repo.id) });
    this.exclusions = Object.fromEntries(this.repos.filter((repo) => repo.disposition === 'local_modification')
      .map((repo) => [repo.id, 'Locally modified repository; retain local policy and hold until its owners adopt explicitly.']));
    this.name = `e2e-j5-driver-${randomUUID().slice(0, 12)}`;
    const created = await this.mutation('POST', ROOT, { name: this.name, description: 'Synthetic campaign driver.',
      tags: [], files: [{ path: 'AGENTS.md', content: 'Reviewed managed release 1. Preserve all local instructions.\n' }] }, 'configuration_edit');
    this.contextId = created.context_id; this.contextRoot = `${ROOT}${this.contextId}/`;
    this.first = this.version = await this.publish(created.revision, null);
    await this.grant();
    const rows = [];
    for (const repo of this.repos) {
      const row = { name: repo.name, repository_id: repo.id, profile: this.profile, selector: { label: 'production' } };
      // Each deliberate pin is a separate edited selector in the binding manifest.
      if (repo.disposition === 'pin') await this.action('field_edit', async () => { row.selector = { version: this.first.number }; }, { repos: [repo] });
      rows.push(row);
    }
    this.bindings = await this.action('configuration_edit', () => this.batch().bindAll({ contextId: this.contextId, rows }));
    assert.deepEqual(this.bindings.map((row) => row.repository.repository_id), this.repos.map((repo) => repo.id), 'binding_order');
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
    const restoreLocks = reverse ? Object.fromEntries(this.repos.filter(included).map((repo) => [repo.id, join(this.scratch, `${repo.id}-original-lock.json`)])) : {};
    const review = await this.action('command', () => this.batch().planAll({ campaignId: this.campaign.id, selected: this.selected,
      exclusions: this.exclusions, mode: reverse ? 'restore' : 'plan', name: this.name, version: this.version.number,
      client: this.profile, restoreLocks, outDir: this.scratch }));
    const index = new Map(this.bindings.map((binding, i) => [binding.id, i]));
    // Independent expectation: exactly the deliberate followers are planned and
    // each exception is the product's own classification of the fixture case.
    const expectedKind = { pin: 'pinned_selector', local_modification: 'operator_exclusion', unavailable: 'unavailable' };
    for (const row of review.exceptions) {
      const repo = this.repos[index.get(row.binding_id)];
      assert.equal(row.kind, expectedKind[repo.disposition] ?? 'ungrouped', 'exception_classification');
    }
    assert.deepEqual([...review.patches.keys()].map((id) => this.repos[index.get(id)].id).sort(),
      this.repos.filter(included).map((repo) => repo.id).sort(), 'planned_population');
    for (const [bindingId, patch] of review.patches) {
      assert.equal(patch.plan.desired_version_id, this.version.id);
      this.campaign.patches.set(index.get(bindingId), patch);
    }
    this.campaign.decisions = review.decisions; this.campaign.review = review;
    await this.observeExceptions(review);
    const targets = await this.targets();
    this.campaign.units = reviewUnits(review, this.repos);
    const packet = targets.map((row) => ({ ...row, repository_id: row.repository.repository_id }));
    await this.review('review', this.campaign.units, packet, { campaign: await this.api('GET', this.campaign.root),
      groups: review.groups, exceptions: review.exceptions });
    return { source: 'api', value: { targets, groups: review.groups, exceptions: review.exceptions } };
  }

  /** The plan command, not the driver, must surface lockless local policy and unavailable targets. */
  async observeExceptions(review) {
    const local = review.exceptions.filter((row) => row.kind === 'operator_exclusion');
    assert.equal(local.length, 2, 'lockless_local_exceptions');
    for (const row of local) {
      const repo = this.repos.find((item) => item.id === row.repository_id);
      assert.equal(row.observed.lock, 'absent');
      await assert.rejects(lstat(join(this.projects.get(repo.id), LOCK)), { code: 'ENOENT' });
      const witnessed = {};
      for (const path of ['AGENTS.md', 'CLAUDE.local.md', 'nested/AGENTS.md']) witnessed[path] = hash(await readFile(join(this.projects.get(repo.id), path)));
      assert.deepEqual(row.observed.local_instructions, witnessed, 'local_instruction_observation');
      assert.deepEqual(witnessed, Object.fromEntries(['AGENTS.md', 'CLAUDE.local.md', 'nested/AGENTS.md'].map((path) => [path, hash(repo.files[path])])), 'local_instructions_changed');
    }
    const unavailable = review.exceptions.filter((row) => row.kind === 'unavailable');
    assert.equal(unavailable.length, 1, 'unavailable_exception');
    assert.equal(unavailable[0].observed.git_status, 404);
    await assert.rejects(lstat(this.projects.get(unavailable[0].repository_id)), { code: 'ENOENT' });
    this.ledger.fault('no-lock-local-instructions', `plan-${this.campaign.label}`);
    this.ledger.fault('unavailable-target', `plan-${this.campaign.label}`);
  }

  async authorize() {
    const before = await this.api('GET', this.campaign.root);
    const targets = await this.targets();
    const packet = targets.map((row) => ({ ...row, repository_id: row.repository.repository_id }));
    // Approval mirrors the reviewed units: one per proven-identical group and
    // one per exception. Each exposure still names every repository it covers.
    await this.review('approve', this.campaign.units, packet, before, () => this.api('POST', `${this.campaign.root}authorize/`, {
      revision: before.revision, expected_manifest_digest: before.manifest_digest, decisions: this.campaign.decisions, acknowledge_repository_writes: true }));
    return { source: 'api', value: await this.targets() };
  }

  executeTargets(indices, { key = () => randomUUID() } = {}) {
    return this.batch().executeAll({ campaignId: this.campaign.id, targets: indices.map((index) => ({
      bindingId: this.bindings[index].id, repository: `synthetic/${this.repos[index].name}`,
      contents: this.campaign.patches.get(index).contents, idempotencyKey: key(index) })) });
  }

  async createPRs() {
    const indices = [...this.campaign.patches.keys()].sort((a, b) => a - b);
    if (!this.baseMovement && !this.setup) {
      // Fixture fault, not an operator action: a concurrent contributor advances
      // main after review. The batch must stop before any write; recovery must
      // review a replacement campaign.
      const repo = this.repos[indices[0]];
      const before = (await (await this.git.fetch(`https://api.github.com/repos/synthetic/${repo.name}/commits/main`)).json()).sha;
      const after = this.git.advanceUnchangedBase(repo.id);
      const writes = this.git.requests.filter((row) => row.method !== 'GET').length;
      const receipts = await this.action('command', () => this.executeTargets(indices));
      assert.equal(receipts[0].state, 'unknown'); assert.match(receipts[0].reason, /Base moved/);
      assert.ok(receipts.slice(1).every((row) => row.state === 'not_attempted'), 'batch_continued_after_base_movement');
      assert.equal(this.git.requests.filter((row) => row.method !== 'GET').length, writes, 'stale_base_wrote_git');
      assert.ok(this.repos.every((row) => this.git.pulls(row.id).length === this.prCount(row)), 'stale_base_created_pr');
      this.campaign.receipts.set(indices[0], receipts[0]);
      this.baseMovement = { repository_id: repo.id, before, after, campaign_id: this.campaign.id, receipt: receipts[0], recovered: false };
      return { source: 'git', value: this.baseMovement };
    }
    const receipts = await this.action('command', () => this.executeTargets(indices));
    for (const [position, index] of indices.entries()) {
      const unknown = this.rounds.length === 0 && this.repos[index].disposition === 'unknown_response';
      assert.equal(receipts[position].state, unknown ? 'unknown' : 'pr_open');
      this.campaign.receipts.set(index, receipts[position]);
    }
    return { source: 'git', value: this.repos.map((repo) => ({ repository_id: repo.id, pulls: this.git.pulls(repo.id) })) };
  }

  /** Completed PRs for this repository before the current round. */
  prCount(repo) {
    return included(repo) ? this.setupRounds.length + this.rounds.length : 0;
  }

  async reconcile() {
    if (this.baseMovement && !this.baseMovement.recovered) {
      const before = await this.api('GET', this.campaign.root);
      await this.mutation('POST', `${this.campaign.root}cancel/`, { revision: before.revision,
        reason: 'Concurrent base movement invalidated reviewed ancestry. Cancel before reviewing a replacement campaign.' }, 'recovery_decision');
      const cancelled = await this.api('GET', this.campaign.root);
      assert.equal(cancelled.status, 'cancelled');
      const oldTargets = await this.targets();
      assert.equal(oldTargets.length, 40, 'cancelled_target_accounting');
      const repo = this.repos.find((row) => row.id === this.baseMovement.repository_id);
      const actual = await this.action('reconcile', async () => {
        const response = await this.git.fetch(`https://api.github.com/repos/synthetic/${repo.name}/commits/main`);
        assert.equal(response.status, 200); return response.json();
      }, { dimension: 'R', repos: [repo] });
      assert.equal(actual.sha, this.baseMovement.after);
      assert.deepEqual(this.git.files(actual.sha), this.git.files(this.baseMovement.before), 'concurrent_instruction_change_unreviewed');
      this.bases.set(repo.id, actual.sha);
      this.baseMovement.recovered = true;
      await this.save('base-movement', { ...this.baseMovement, cancelled, targets: oldTargets, duplicate_prs: 0 });
      this.ledger.fault('base-movement', 'reviewed-replacement-campaign');
      // Do not edit/retry the stale authorized plan or erase its uncertain
      // receipt. All forty targets and every review unit are decided again.
      await this.freeze(this.campaign.label); await this.plans(); await this.authorize(); await this.createPRs();
    }
    for (const [index, receipt] of this.campaign.receipts) {
      if (receipt.state !== 'unknown') continue;
      this.ledger.fault('unknown-pr-response', `campaign-${this.campaigns.length}`);
      const before = await this.api('GET', this.campaign.root);
      await this.mutation('POST', `${this.campaign.root}pause/`, { revision: before.revision, reason: 'Inspect uncertain Git result before retry.' }, 'recovery_decision');
      const writes = this.git.requests.filter((row) => row.method !== 'GET').length;
      const [observed] = await this.action('reconcile', () => this.executeTargets([index]), { dimension: 'R', repos: [this.repos[index]] });
      assert.equal(observed.state, 'pr_open'); assert.equal(this.git.requests.filter((row) => row.method !== 'GET').length, writes);
      this.campaign.receipts.set(index, observed);
      const paused = await this.api('GET', this.campaign.root);
      await this.mutation('POST', `${this.campaign.root}resume/`, { revision: paused.revision, reason: 'Exact deterministic PR and complete tree reconciled.' }, 'recovery_decision');
    }
    return { source: 'git', value: [...this.campaign.receipts.values()] };
  }

  async install() {
    const indices = [...this.campaign.patches.keys()].sort((a, b) => a - b);
    const merges = new Map();
    for (const index of indices) {
      const repo = this.repos[index]; const patch = this.campaign.patches.get(index); const receipt = this.campaign.receipts.get(index);
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
      merges.set(index, { pr, merge });
    }
    // One reconciliation command records every merged target; Git merge is
    // never treated as installation or delivery.
    const observed = await this.action('command', () => this.executeTargets(indices), { repos: indices.map((index) => this.repos[index]) });
    assert.ok(observed.every((row) => row.state === 'merged' && row.delivered === false), 'merge_observation');
    const applied = await this.action('command', () => this.batch().applyAll({ plans: indices.map((index) => ({
      repository_id: this.repos[index].id, path: this.campaign.patches.get(index).path, project: this.projects.get(this.repos[index].id) })) }),
    { repos: indices.map((index) => this.repos[index]) });
    assert.ok(applied.every((row) => row.state === 'installed' && row.delivery === 'not_observed' && row.verified === true), 'installation_unverified');
    const installs = [];
    for (const index of indices) {
      const repo = this.repos[index]; const { pr, merge } = merges.get(index);
      const hashes = {};
      for (const [path, text] of Object.entries(this.git.files(merge.sha))) {
        const bytes = await readFile(join(this.projects.get(repo.id), path)); assert.equal(bytes.toString(), text); hashes[path] = hash(bytes);
      }
      for (const [path, text] of Object.entries(repo.files)) {
        const actual = await readFile(join(this.projects.get(repo.id), path), 'utf8');
        if (path === 'AGENTS.md') assert.ok(actual.includes(text), 'adopted_local_segment_lost'); else assert.equal(actual, text);
      }
      const lockBytes = await readFile(join(this.projects.get(repo.id), LOCK));
      assert.equal(JSON.parse(lockBytes).version_id, this.version.id);
      if (this.rounds.length === 0 && this.setupRounds.length === 0) await writeFile(join(this.scratch, `${repo.id}-original-lock.json`), lockBytes);
      this.bases.set(repo.id, merge.sha);
      // A cancelled stale-base campaign created no PR. Count successful rounds,
      // and independently forbid duplicate branches in the retained Git history.
      const pulls = this.git.pulls(repo.id);
      assert.equal(pulls.length, this.prCount(repo) + 1, 'duplicate_pr');
      assert.equal(new Set(pulls.map((row) => row.head.ref)).size, pulls.length, 'duplicate_pr_branch');
      let native = { discovery: 'not_observed', delivery: 'not_observed', source: 'filesystem' };
      if (this.config.native && !this.setup) {
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
    const report = { evidence_class: 'synthetic fixture', complete_job: false, label: this.campaign.label, setup: Boolean(this.setup),
      campaign_id: this.campaign.id, targets: await this.targets(), installs, review_units: this.campaign.units.map((unit) => ({ kind: unit.kind,
        repositories: unit.repos.map((repo) => repo.id) })),
      plans: [...this.campaign.patches].map(([index, patch]) => ({ repository_id: this.repos[index].id,
        binding_id: this.bindings[index].id, plan: patch.plan, contents: patch.contents })) };
    this.rounds.push(report); await this.save(`${this.setup ? 'setup-' : ''}${this.campaign.label}`, report);
    return report;
  }

  async checkExcluded() {
    for (const repo of this.repos.filter((repo) => !included(repo))) {
      assert.equal(this.git.pulls(repo.id).length, 0, 'excluded_repository_written');
      if (repo.disposition === 'unavailable') { await assert.rejects(lstat(this.projects.get(repo.id)), { code: 'ENOENT' }); continue; }
      for (const [path, text] of Object.entries(repo.files)) assert.equal(await readFile(join(this.projects.get(repo.id), path), 'utf8'), text);
      await assert.rejects(lstat(join(this.projects.get(repo.id), LOCK)), { code: 'ENOENT' });
    }
  }

  async reverseConflictChecks() {
    const repo = this.repos[0]; const project = this.projects.get(repo.id);
    const lockPath = join(project, '.futureagi-context.lock.json');
    const lockBytes = await readFile(lockPath);
    const lock = JSON.parse(lockBytes);
    const managed = lock.materialization.files.find((row) => row.ownership === 'managed' && row.destination.endsWith('AGENTS.md'));
    assert.ok(managed, 'managed_instruction_missing');
    const managedPath = join(project, managed.destination); const original = await readFile(managedPath);
    const dirtyBytes = 'Locally modified managed policy; retain for explicit conflict resolution.\n';
    // Synthetic concurrent edit, never hidden setup for the measured recovery.
    await writeFile(managedPath, dirtyBytes);
    const dirtyPath = join(this.scratch, 'dirty-reverse.json');
    const restoreArgs = ['restore', '--project', project, '--lock', join(this.scratch, `${repo.id}-original-lock.json`), '--allow-preview'];
    const dirty = await this.sdk([...restoreArgs, '--out', dirtyPath], 5);
    assert.equal(dirty.error.code, 'local_modifications');
    await assert.rejects(lstat(dirtyPath), { code: 'ENOENT' });
    assert.equal(await readFile(managedPath, 'utf8'), dirtyBytes, 'dirty_local_work_lost');
    assert.deepEqual(await readFile(lockPath), lockBytes, 'dirty_check_mutated_lock');
    await this.action('recovery_decision', async () => {
      // Export the conflicting bytes before the scripted operator chooses the
      // previous managed content. The export survives scratch-tree teardown.
      await this.save('retained-dirty-local-work', { repository_id: repo.id, path: managed.destination, content: dirtyBytes, sha256: hash(dirtyBytes) });
      await writeFile(managedPath, original);
    }, { repos: [repo] });
    const stalePath = join(this.scratch, 'stale-reverse.json');
    await this.sdk([...restoreArgs, '--out', stalePath]);
    const agentsPath = join(project, 'AGENTS.md'); const agents = await readFile(agentsPath);
    const changedAgents = Buffer.concat([agents, Buffer.from('\nNew local work after review; retain for resolution.\n')]);
    await writeFile(agentsPath, changedAgents);
    const stale = await this.sdk(['apply', '--plan', stalePath, '--quiescent'], 5);
    assert.equal(stale.error.code, 'stale_plan');
    assert.deepEqual(await readFile(agentsPath), changedAgents, 'changed_preimage_lost');
    assert.deepEqual(await readFile(lockPath), lockBytes, 'stale_apply_mutated_lock');
    await this.action('recovery_decision', async () => {
      await this.save('retained-stale-local-work', { repository_id: repo.id, path: 'AGENTS.md', content: changedAgents.toString(), sha256: hash(changedAgents) });
      await writeFile(agentsPath, agents);
    }, { repos: [repo] });
    this.ledger.fault('dirty-managed-segment', 'reverse-refuses-local-conflict');
    this.reverseConflicts = { dirty: dirty.error.code, stale: stale.error.code, lock_unchanged: true, local_work_exported: true };
    await this.save('reverse-conflicts', this.reverseConflicts);
  }

  async update(reverse) {
    assert.ok(this.rounds.length, 'initial_install_required');
    if (reverse) { await this.reverseConflictChecks(); this.version = this.first; }
    else await this.publishNext();
    await this.freeze(reverse ? 'reverse' : 'second-update'); await this.plans(reverse);
    const reviewed = this.decision;
    await this.authorize(); await this.createPRs(); await this.reconcile();
    // Reverse-plan's required review must bind to its actual review action, not
    // the later authorization packet. Both actions remain separately counted.
    if (reverse) this.decision = reviewed;
    return this.install();
  }

  /**
   * Reach CONFIGURED_PRECONDITION through the same public interfaces on a
   * separate, retained setup ledger, then verify the realised state before the
   * first measured action. Setup never contributes to the measured ledger.
   */
  async prepareConfigured() {
    const measured = { ledger: this.ledger, stage: this.stage };
    this.setup = true;
    const setupLedger = new ActionLedger({ runId: `${this.runId}-setup`, job: 'J5', mode: 'product',
      path: join(this.config.evidence_dir, `${this.runId}-setup.jsonl`) });
    this.ledger = setupLedger; this.stage = { id: 'configured-precondition', phase: 'cold' };
    try {
      await this.prepareInventory(); await this.ownership(); await this.freeze('setup'); await this.plans();
      await this.authorize(); await this.createPRs(); await this.reconcile(); await this.install();
      await this.publishNext();
    } finally { setupLedger.close(); this.ledger = measured.ledger; this.stage = measured.stage; this.setup = false; }
    this.setupRounds = this.rounds; this.rounds = [];
    const bindings = await this.targets();
    assert.equal(bindings.length, CONFIGURED_PRECONDITION.bindings);
    let installed = 0; let lockless = 0; let open = 0;
    for (const repo of this.repos) {
      for (const pr of this.git.pulls(repo.id)) if (!pr.merged) open += 1;
      if (repo.disposition === 'unavailable') { await assert.rejects(lstat(this.projects.get(repo.id)), { code: 'ENOENT' }); continue; }
      let lock = null;
      try { lock = JSON.parse(await readFile(join(this.projects.get(repo.id), LOCK))); } catch (error) { if (error.code !== 'ENOENT') throw error; }
      if (included(repo)) { assert.equal(lock?.version_id, this.first.id, 'configured_release_not_installed'); installed += 1; }
      else {
        assert.equal(lock, null, 'excluded_repository_has_lock');
        for (const [path, text] of Object.entries(repo.files)) assert.equal(await readFile(join(this.projects.get(repo.id), path), 'utf8'), text);
        lockless += 1;
      }
    }
    const pins = bindings.filter((row) => row.selector?.version === this.first.number).length;
    const realised = { schema: 1, population: this.repos.length, bindings: bindings.length, installed_release: this.first.number,
      installed_repositories: installed, lockless_excluded_with_local_instructions: lockless,
      unavailable: this.repos.filter((repo) => repo.disposition === 'unavailable').length, deliberate_pins: pins,
      open_pull_requests: open, next_release_published: this.version.number, reached_by: CONFIGURED_PRECONDITION.reached_by };
    assert.deepEqual(realised, { ...CONFIGURED_PRECONDITION }, 'configured_precondition_not_realised');
    const unknown = this.repos.find((repo) => repo.disposition === 'unknown_response');
    this.git.armUnknownResponse(unknown.id);
    await this.save('configured-precondition', { evidence_class: 'synthetic fixture', realised,
      setup_ledger: summarizeLedger(setupLedger.events()), setup_campaigns: this.campaigns.slice() });
  }

  async perform(stage, { ledger, signal }) {
    this.stage = stage; this.ledger = ledger; this.signal = signal; this.decision = null;
    signal.throwIfAborted(); let result;
    if (this.cohort === 'configured' && !this.setupRounds.length) {
      assert.equal(stage.id, 'freeze-targets', 'configured_cohort_starts_at_recurring_rollout');
      await this.prepareConfigured(); this.decision = null;
    }
    switch (stage.id) {
      case 'inventory-existing-repositories': result = await this.prepareInventory(); break;
      case 'establish-ownership': result = await this.ownership(); break;
      case 'freeze-targets': result = await this.freeze('rollout'); break;
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
    let source; let value; let raw;
    if (check.id === 'exact-forty-targets') {
      source = 'api'; raw = await this.targets(); value = raw.map((row) => row.repository.repository_id);
    } else if (check.id === 'one-pr-per-target') {
      source = 'git'; raw = this.repos.map((repo) => ({ repository_id: repo.id, pulls: this.git.pulls(repo.id) }));
      assert.equal(this.rounds.length, 3, 'all_rollout_rounds_required');
      for (const [index, repo] of this.repos.entries()) {
        const expected = included(repo) ? [...this.setupRounds, ...this.rounds].map((round) => `futureagi/context/${round.campaign_id}/${this.bindings[index].id}`).sort() : [];
        assert.deepEqual(raw[index].pulls.map((pr) => pr.head.ref).sort(), expected, 'pr_branch_accounting');
      }
      value = { duplicates: raw.reduce((count, row) => count + row.pulls.length - new Set(row.pulls.map((pr) => pr.head.ref)).size, 0) };
    } else if (check.id === 'authorized-base-preimages') {
      source = 'git'; raw = [];
      assert.equal(this.rounds.length, 3, 'all_rollout_rounds_required');
      for (const round of this.rounds) for (const patch of round.plans) {
        const repo = this.repos.find((row) => row.id === patch.repository_id);
        const branch = `futureagi/context/${round.campaign_id}/${patch.binding_id}`;
        const matches = this.git.pulls(repo.id).filter((pr) => pr.head.ref === branch);
        assert.equal(matches.length, 1, 'authorized_pr_unobserved');
        const pr = matches[0];
        const response = await this.git.fetch(`https://api.github.com/repos/synthetic/${repo.name}/git/commits/${pr.head.sha}`);
        assert.equal(response.status, 200); const commit = await response.json();
        assert.deepEqual(commit.parents, [{ sha: patch.plan.base_commit }], 'unauthorized_parent');
        const before = this.git.files(patch.plan.base_commit); const expected = { ...before };
        for (const change of patch.plan.changes) {
          assert.equal(Object.hasOwn(before, change.path) ? hash(before[change.path]) : null, change.before_sha256, 'unauthorized_preimage');
          if (change.action === 'remove') delete expected[change.path];
          else {
            const bytes = Buffer.from(patch.contents[change.path], 'base64');
            assert.equal(hash(bytes), change.after_sha256, 'unauthorized_patch'); expected[change.path] = bytes.toString();
          }
        }
        const files = this.git.files(pr.head.sha);
        assert.deepEqual(files, expected, 'unauthorized_tree');
        raw.push({ repository_id: repo.id, branch, commit, files, plan: patch.plan });
      }
      assert.equal(raw.length, 99, 'authorized_patch_denominator'); value = { unauthorized_effects: 0 };
    } else if (check.id === 'local-policy-preserved') {
      source = 'filesystem'; value = []; raw = [];
      for (const repo of this.repos.filter((repo) => repo.disposition !== 'unavailable')) for (const [path, original] of Object.entries(repo.files)) {
        const actual = await readFile(join(this.projects.get(repo.id), path), 'utf8');
        raw.push({ path: `${repo.id}/${path}`, content: actual });
        // The manifest is the original local policy, not the new managed text.
        // Extract the observed original segment, never substitute expected bytes.
        // Exact merged trees are independently checked above and at installation.
        if (path === 'AGENTS.md' && included(repo)) {
          const start = actual.indexOf(original);
          assert.ok(start >= 0 && actual.indexOf(original, start + original.length) === -1, 'local_segment_missing_or_duplicated');
          value.push({ path: `${repo.id}/${path}`, content: actual.slice(start, start + original.length) });
        } else value.push({ path: `${repo.id}/${path}`, content: actual });
      }
    } else if (check.id === 'pins-retained') {
      // Derive the count from independent API selector, Git PR and filesystem
      // lock/instruction observations; a pin that received a PR, a lock, a
      // changed selector or changed local bytes counts as advanced.
      source = 'filesystem'; const targets = await this.targets(); raw = [];
      for (const repo of this.repos.filter((repo) => repo.disposition === 'pin')) {
        const project = this.projects.get(repo.id);
        const target = targets.find((row) => row.repository.repository_id === repo.id);
        let lockPresent = true;
        try { await lstat(join(project, LOCK)); } catch (error) { if (error.code !== 'ENOENT') throw error; lockPresent = false; }
        const files = Object.fromEntries(await Promise.all(Object.keys(repo.files).map(async (path) => [path, await readFile(join(project, path), 'utf8')])));
        const advanced = target.decision !== 'exclude' || canonical(target.selector) !== canonical({ version: this.first.number })
          || this.git.pulls(repo.id).length > 0 || lockPresent || Object.entries(repo.files).some(([path, text]) => files[path] !== text);
        raw.push({ repository_id: repo.id, decision: target.decision, selector: target.selector, pulls: this.git.pulls(repo.id).length,
          files, lock_present: lockPresent, advanced });
      }
      value = { deliberate_pins: raw.length, advanced_pins: raw.filter((row) => row.advanced).length };
    } else if (check.id === 'stage-accounting') {
      source = 'api'; raw = [];
      const targets = await this.targets(); assert.equal(targets.length, 40);
      for (const binding of this.bindings) {
        const observations = await this.api('GET', `${this.campaign.root}targets/${binding.id}/observations/`);
        assert.equal(observations.installed, null, 'git_must_not_imply_installation');
        assert.equal(observations.instructions_delivered, null, 'git_must_not_imply_delivery');
        raw.push({ binding_id: binding.id, observations });
      }
      assert.equal(this.rounds.length, 3, 'all_rollout_rounds_required');
      for (const round of this.rounds) {
        const version = round.targets[0]?.desired_version_id;
        requireNativeDelivery(round.installs, this.repos.filter(included).map((repo) => repo.id), version);
        for (const row of round.installs) {
          assert.ok(row.installed && row.head !== row.merge, 'merge_install_boundary_missing');
          const bytes = await readFile(join(this.config.evidence_dir, `${this.runId}-native-${round.label}-${row.repository_id}.json`));
          assert.equal(hash(bytes), row.artifact_sha256, 'native_artifact_changed');
          const boundary = JSON.parse(bytes);
          const expectedInstructions = this.git.files(row.merge)['AGENTS.md'];
          assert.equal(boundary.installed_sha256, hash(expectedInstructions), 'native_loaded_wrong_installation');
          assertCodexBoundary({ requests: boundary.requests, events: boundary.process_events, expectedInstructions });
        }
      }
      value = { target_count: targets.length, merge_install_load_distinct: true };
      raw = { api: raw, independently_observed_rounds: this.rounds };
    } else throw new Error('j5_unknown_check');
    // Equality checks compare preregisterable outcomes, not unpredictable UUIDs
    // and timestamps. Hash the complete raw witnesses, not merely the summary.
    return { source, value, artifact_sha256: await this.save(`observation-${check.id}-${++this.serial}`, { raw, value }) };
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
