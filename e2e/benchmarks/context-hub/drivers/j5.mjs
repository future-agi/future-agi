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
import { assertCodexBoundary, assertQualifiedCodex, CODEX_VERSION, NATIVE_SCOPE } from '../../../lib/skills-r3/j5-native-fixture.mjs';
import { ContextRollout, ownershipPacket } from '../../../../bin/lib/context-rollout.mjs';
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
 * Without an explicit pre-Checkpoint-P measurement opt-in the runner refuses
 * before any write; with it, counts are measured but the claim stays blocked and
 * the external real-GitHub check is reported open, never observed from the fake.
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
// Followers and deliberate pins are installed; pins only at their pinned release.
const adopted = (repo) => included(repo) || repo.disposition === 'pin';
const LOCK = '.futureagi-context.lock.json';

/**
 * Declarative precondition for the already-configured cohort. A configured run
 * reaches it through the same public interfaces on a separate setup ledger and
 * verifies the realised state before its first measured action. It is never
 * relabelled as recurring work, and no compatible lock is seeded.
 */
export const CONFIGURED_PRECONDITION = Object.freeze({
  schema: 1, population: 40, bindings: 40, installed_release: 1, installed_repositories: 37,
  lockless_excluded_with_local_instructions: 2, unavailable: 1, deliberate_pins: 4, pins_installed_at_pinned_release: 4,
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
export function j5Registration({ label, provenance, python, evidenceDir, native, measurementOnly = false }) {
  const cohort = J5_MEASUREMENTS[label];
  assert.ok(cohort, 'unknown_j5_measurement');
  const job = getJob('J5'); const repos = fixtureRepositories();
  const initial = { label, precondition: { ...(cohort === 'cold' ? COLD_PRECONDITION : CONFIGURED_PRECONDITION) } };
  const original = repos.filter((repo) => repo.disposition !== 'unavailable').flatMap((repo) => Object.entries(repo.files)
    .map(([path, text]) => ({ path: `${repo.id}/${path}`, sha256: hash(text), size_bytes: Buffer.byteLength(text) })));
  return { schema: 1, job: 'J5', cohort, evidence_scope: 'synthetic', population: job.population, fault_ids: job.fault_ids,
    // Explicit pre-Checkpoint-P measurement opt-in (runner block); still no receipt and no GitHub observation.
    ...(measurementOnly ? { pre_checkpoint_p_measurement: true } : {}),
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
  // The caller derives the planned population from the fixture: 33 followers,
  // plus the four pins in the round that adopts them at their pinned release.
  assert.ok([33, 37].includes(repositoryIds.length), 'native_delivery_included_population');
  assert.equal(new Set(repositoryIds).size, repositoryIds.length, 'native_delivery_duplicate_target');
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
    this.pinsInstalled = false;
    // Operator configuration: hub origin and workspace scope. The planning
    // credential itself is issued per rollout by the start command.
    this.env = { ...process.env, FI_CONTEXT_ORIGIN: this.hubUrl, FI_CONTEXT_ORGANIZATION_ID: this.headers['X-Organization-Id'],
      FI_CONTEXT_WORKSPACE_ID: this.headers['X-Workspace-Id'] };
    this.repos = fixtureRepositories();
    this.git = new J5GitFixture(this.repos);
    this.rollout = new ContextRollout({ hub: (method, path, data) => this.api(method, path, data), executor: this.executor,
      python: this.config.python, env: this.env });
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
      exceptionItems: repos.filter((repo) => !this.planned(repo)).map((repo) => `repo-${repo.id}`), decision }, effect);
  }

  /**
   * Fixture-derived expectation for the current round, never the product's
   * output: followers always; deliberate pins only in a round at their own
   * pinned release before any has been installed.
   */
  expectedPlanned() {
    return this.repos.filter((repo) => included(repo) || (repo.disposition === 'pin' && !this.pinsInstalled
      && this.version?.number === this.first?.number));
  }

  planned(repo) {
    return this.campaign?.expected ? this.campaign.expected.includes(repo.id) : included(repo);
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

  /**
   * One decision per exposed review unit; the packet hash binds all units of the
   * stage. A unit is a proven-identical group, the held-exception list or one
   * individual exception, so actual review work is counted, never per-click.
   */
  async review(primitive, units, { packet, exposed, preconditions }, effect = async () => {}) {
    const decision = { packet_sha256: hash(canonical(packet)), preconditions_sha256: hash(canonical(preconditions)) };
    const shown = new Set(exposed);
    let result;
    for (const [position, unit] of units.entries()) result = await this.action(primitive, async () => {
      assert.ok(unit.repos.length > 0 && unit.repos.every((repo) => shown.has(repo.id)), 'review_item_not_exposed');
      // The decision's effect, if any, is the last unit's act: it covers the whole reviewed packet.
      return position === units.length - 1 ? effect() : undefined;
    }, { repos: unit.repos, decision });
    this.decision = decision;
    await this.save(`decision-${this.stage.id}-${++this.serial}`, { decision, units: units.map((unit) => ({ kind: unit.kind, key: unit.key,
      repositories: unit.repos.map((repo) => repo.id) })), packet, preconditions });
    return result;
  }

  /** Units of a product packet as fixture repositories; every exposed repository exactly once. */
  packetUnits(packet, repos = this.repos) {
    const byId = new Map(repos.map((repo) => [repo.id, repo]));
    const units = packet.units.map((unit) => ({ kind: unit.kind, key: unit.key, repos: unit.repository_ids.map((id) => byId.get(id)) }));
    const ids = units.flatMap((unit) => unit.repos.map((repo) => repo?.id));
    assert.ok(ids.every(Boolean), 'review_item_unknown');
    assert.equal(ids.length, repos.length, 'review_item_count');
    assert.deepEqual([...new Set(ids)].sort(), repos.map((repo) => repo.id).sort(), 'review_items_not_exactly_once');
    return units;
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
    // Choosing the 40 repositories is the operator's own input (the comparator
    // counts its equivalent, write-selected-repositories-list): counted once.
    await this.action('configuration_edit', () => writeFile(manifest,
      JSON.stringify(this.selected.map(({ repository_id, project }) => ({ repository_id, project })))));
    await mkdir(this.config.evidence_dir, { recursive: true });
    // One product command: inventory without hooks and the grouped ownership packet for the next stage's review.
    const adopted = await this.action('command', () => this.rollout.inventoryOwnership({ manifestPath: manifest }));
    await writeFile(join(this.config.evidence_dir, `${this.runId}${this.setup ? '-setup' : ''}-inventory.json`),
      JSON.stringify({ exitCode: adopted.exitCode, inventory: adopted.inventory }, null, 2), { flag: 'wx', mode: 0o600 });
    const observed = { exitCode: adopted.exitCode, inventory: adopted.inventory };
    this.ownershipReview = adopted.packet;
    const expected = this.repos.map((repo) => ({ id: repo.id, available: repo.disposition !== 'unavailable', lock_sha256: null,
      files: Object.fromEntries(Object.entries(repo.files).filter(([path]) => path !== 'references/local-policy.md').map(([path, text]) => [path, hash(text)])) }));
    assertInventory(observed.inventory, expected); assert.equal(observed.exitCode, 6);
    this.inventory = observed.inventory;
    for (const repo of this.repos.filter((repo) => repo.disposition !== 'unavailable')) {
      for (const [path, text] of Object.entries(repo.files)) assert.equal(await readFile(join(this.projects.get(repo.id), path), 'utf8'), text);
      await assert.rejects(lstat(join(this.projects.get(repo.id), LOCK)), { code: 'ENOENT' });
    }
    this.ledger.fault('no-lock-local-instructions', 'cold-inventory');
    this.ledger.fault('unavailable-target', 'cold-inventory');
    return { source: 'filesystem', value: expected };
  }

  /**
   * One review of the candidate release (create/edit already returned its
   * packet), then one approval bound to exactly that candidate. With a start
   * request the same approval also freezes and plans the rollout of it.
   */
  async publish(reviewed, baseVersion, start = null) {
    await this.action('review', async () => { assert.ok(reviewed?.candidate_digest && reviewed.review_fingerprint, 'release_review_packet_missing'); });
    this.releases += 1;
    if (!start) return this.action('approve', () => this.rollout.publishReviewed({ review: reviewed, baseVersion }));
    return this.action('approve', () => this.rollout.publishAndStart({ review: reviewed, baseVersion, start }));
  }

  /** Edit the next release; with a rollout label, publishing it also starts that rollout (returns the started campaign). */
  async publishNext(label = null) {
    const saved = await this.action('field_edit', () => this.rollout.editDraft({ contextId: this.contextId, fromVersion: this.version.number,
      files: [{ path: 'AGENTS.md', content: `Reviewed managed release ${this.releases + 1}. Preserve all local instructions.\n` }], description: 'Synthetic fixture.' }));
    if (!label) { this.version = await this.publish(saved.review, this.version.number); return null; }
    const { version, started } = await this.publish(saved.review, this.version.number,
      { ...this.startRequest(label), parentCampaignId: this.campaigns.at(-1) ?? null });
    this.version = version;
    return started;
  }

  async ownership() {
    assert.ok(this.projects && this.inventory, 'inventory_required');
    const capability = await this.api('GET', `${ROOT}capabilities/`);
    assert.equal(capability.campaigns_enabled, true, 'enable_campaigns_only_in_test_stack');
    // Independent oracle for the product's grouped ownership packet: every shared
    // instruction file it groups is byte-identical on disk across the group, and
    // every retained personal file it lists is the repository's actual file.
    // The packet came from the counted inventory command (one product step);
    // it must be exactly the grouping of that inventory, then is checked on disk.
    const packet = this.ownershipReview;
    assert.deepEqual(packet, ownershipPacket(this.inventory), 'ownership_packet_not_from_inventory');
    for (const group of packet.groups) {
      for (const member of group.members) {
        const project = this.projects.get(member.repository_id);
        await assert.rejects(lstat(join(project, LOCK)), { code: 'ENOENT' });
        for (const file of [...group.shared, ...member.retained]) assert.equal(hash(await readFile(join(project, file.path))), file.sha256, 'ownership_packet_file_mismatch');
        const listed = new Set([...group.shared, ...member.retained].map((file) => file.path));
        const repo = this.repos.find((row) => row.id === member.repository_id);
        for (const path of Object.keys(repo.files).filter((path) => path !== 'references/local-policy.md')) assert.ok(listed.has(path), 'ownership_packet_hid_instruction');
      }
    }
    assert.deepEqual(packet.held.map((row) => row.repository_id), this.repos.filter((repo) => repo.disposition === 'unavailable').map((repo) => repo.id));
    // Ownership is reviewed once per distinct shared instruction content, once
    // for the held exceptions and individually for anything unproven.
    await this.review('review', this.packetUnits(packet), { packet, exposed: this.repos.map((repo) => repo.id),
      preconditions: { locks: 'absent', selected: this.repos.map((repo) => repo.id) } });
    // Holding a locally modified repository is the operator's own per-repository
    // decision (the inventory cannot see it): each is a counted field edit.
    this.exclusions = {};
    for (const repo of this.repos.filter((row) => row.disposition === 'local_modification')) {
      await this.action('field_edit', async () => {
        this.exclusions[repo.id] = 'Locally modified repository; retain local policy and hold until its owners adopt explicitly.';
      }, { repos: [repo] });
    }
    this.name = `e2e-j5-driver-${randomUUID().slice(0, 12)}`;
    const created = await this.action('configuration_edit', () => this.rollout.createContext({ name: this.name, description: 'Synthetic campaign driver.',
      files: [{ path: 'AGENTS.md', content: 'Reviewed managed release 1. Preserve all local instructions.\n' }] }));
    this.contextId = created.context_id; this.contextRoot = `${ROOT}${this.contextId}/`;
    assert.equal(created.review?.context_id, this.contextId, 'candidate_review_missing');
    this.first = this.version = await this.publish(created.review, null);
    const rows = [];
    for (const repo of this.repos) {
      const row = { name: repo.name, repository_id: repo.id, profile: this.profile, selector: { label: 'production' } };
      // Each deliberate pin is a separate edited selector in the binding manifest.
      if (repo.disposition === 'pin') await this.action('field_edit', async () => { row.selector = { version: this.first.number }; }, { repos: [repo] });
      rows.push(row);
    }
    this.bindings = await this.action('configuration_edit', () => this.batch().bindAll({ contextId: this.contextId, rows }));
    assert.deepEqual(this.bindings.map((row) => row.repository.repository_id), this.repos.map((repo) => repo.id), 'binding_order');
    return { source: 'api', value: { ownership: packet, bindings: this.bindings } };
  }

  startRequest(label, reverse = false) {
    const restoreLocks = reverse ? Object.fromEntries(this.repos.filter(included).map((repo) => [repo.id, join(this.scratch, `${repo.id}-original-lock.json`)])) : {};
    return { contextId: this.contextId, contextName: this.name, campaignName: `${this.name}-${label}`, bindings: this.bindings, version: this.version.number,
      selected: this.selected, exclusions: this.exclusions, mode: reverse ? 'restore' : 'plan', client: this.profile, restoreLocks, outDir: this.scratch };
  }

  /** Adopt a started (or replacement) campaign's frozen membership and plans; the product's output is checked, not trusted. */
  async adoptStarted(label, started) {
    this.campaigns.push(started.campaign_id); this.credentials.push(started.credential_id); this.env = { ...this.rollout.env };
    const root = `${ROOT}campaigns/${started.campaign_id}/`;
    this.campaign = { ...(await this.api('GET', root)), id: started.campaign_id, root, label, patches: new Map(), decisions: [], receipts: new Map(),
      expected: this.expectedPlanned().map((repo) => repo.id), review: started.review, packet: started.packet };
    const targets = await this.targets();
    assert.deepEqual(targets.map((row) => row.binding_id).sort(), this.bindings.map((row) => row.id).sort());
    assert.ok(targets.every((row) => row.desired_version === this.version.number), 'frozen_release_mismatch');
    return targets;
  }

  /** One command freezes all 40 bindings at the release, issues the implied planning credential and plans every target. */
  async freeze(label, reverse = false) {
    assert.equal(this.bindings?.length, 40, 'forty_bindings_required');
    const started = await this.action('command', () => this.rollout.start({ ...this.startRequest(label, reverse), parentCampaignId: this.campaigns.at(-1) ?? null }));
    return { source: 'api', value: await this.adoptStarted(label, started) };
  }

  async plans() {
    const { review, packet } = this.campaign;
    const index = new Map(this.bindings.map((binding, i) => [binding.id, i]));
    // Independent expectation: exactly the deliberate followers are planned and
    // each exception is the product's own classification of the fixture case.
    const expectedKind = { pin: this.version.number === this.first.number ? 'pinned_current' : 'pinned_selector',
      local_modification: 'operator_exclusion', unavailable: 'unavailable' };
    for (const row of review.exceptions) {
      const repo = this.repos[index.get(row.binding_id)];
      assert.equal(row.kind, expectedKind[repo.disposition] ?? 'ungrouped', 'exception_classification');
    }
    assert.deepEqual([...review.patches.keys()].map((id) => this.repos[index.get(id)].id).sort(),
      [...this.campaign.expected].sort(), 'planned_population');
    for (const [bindingId, patch] of review.patches) {
      assert.equal(patch.plan.desired_version_id, this.version.id);
      this.campaign.patches.set(index.get(bindingId), patch);
    }
    this.campaign.decisions = review.decisions;
    await this.observeExceptions(review);
    const targets = await this.targets();
    // The packet is bound to the server's plan digests; check it independently.
    for (const row of targets) assert.equal(packet.decisions.find((item) => item.binding_id === row.binding_id)?.plan_digest, row.plan_digest ?? null, 'packet_plan_digest');
    assert.deepEqual(packet.held.map((row) => row.repository_id).sort(), review.exceptions.filter((row) => row.kind !== 'ungrouped').map((row) => row.repository_id).sort());
    this.campaign.units = this.packetUnits(packet);
    // Matches the comparator's granularity: one review per identical group, one
    // for the held list, and each unproven exception individually.
    await this.review('review', this.campaign.units, { packet, exposed: targets.map((row) => row.repository.repository_id),
      preconditions: { campaign: await this.api('GET', this.campaign.root) } });
    return { source: 'api', value: { targets, packet } };
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
    // An installed pin is observed at its pinned release from its own lock.
    for (const row of review.exceptions.filter((item) => item.kind === 'pinned_current')) {
      const lock = JSON.parse(await readFile(join(this.projects.get(row.repository_id), LOCK), 'utf8'));
      assert.equal(row.observed.lock_version_id, this.first.id, 'pinned_current_observation');
      assert.equal(lock.version_id, this.first.id, 'pin_not_at_pinned_release');
    }
    const unavailable = review.exceptions.filter((row) => row.kind === 'unavailable');
    assert.equal(unavailable.length, 1, 'unavailable_exception');
    assert.equal(unavailable[0].observed.git_status, 404);
    await assert.rejects(lstat(this.projects.get(unavailable[0].repository_id)), { code: 'ENOENT' });
    this.ledger.fault('no-lock-local-instructions', `plan-${this.campaign.label}`);
    this.ledger.fault('unavailable-target', `plan-${this.campaign.label}`);
  }

  /**
   * One approval over the reviewed packet with every exception explicitly
   * acknowledged; once authorized, the same step opens each authorized target's
   * deterministic PR (product path authorizeAndOpen). PR creation is mechanical
   * follow-on work, so the create-prs stage has no further operator command.
   */
  async authorize() {
    const before = await this.api('GET', this.campaign.root);
    const { packet } = this.campaign;
    const acknowledged = [...packet.held, ...packet.individual].map((row) => row.repository_id);
    const indices = [...this.campaign.patches.keys()].sort((a, b) => a - b);
    this.pendingBaseMovement = null;
    if (!this.baseMovement && !this.setup) {
      // Fixture fault, not an operator action: a concurrent contributor advances
      // main after review and before the approval writes. The batch must stop
      // before any write; recovery must review a replacement campaign.
      const repo = this.repos[indices[0]];
      const base = (await (await this.git.fetch(`https://api.github.com/repos/synthetic/${repo.name}/commits/main`)).json()).sha;
      const after = this.git.advanceUnchangedBase(repo.id);
      this.pendingBaseMovement = { repo, before: base, after, writes: this.git.requests.filter((row) => row.method !== 'GET').length };
    }
    let opened;
    await this.review('approve', [{ kind: 'campaign', key: this.campaign.id, repos: this.repos }], { packet,
      exposed: packet.units.flatMap((unit) => unit.repository_ids), preconditions: before },
    async () => { opened = await this.rollout.authorizeAndOpen({ packet, acknowledgedExceptions: acknowledged, targets: this.openTargets(indices) }); return opened; });
    assert.equal(opened.campaign.status, 'authorized', 'campaign_not_authorized');
    this.campaign.opened = { indices, receipts: opened.receipts };
    return { source: 'api', value: await this.targets() };
  }

  openTargets(indices, { key = () => randomUUID() } = {}) {
    return indices.map((index) => ({ bindingId: this.bindings[index].id, repository: `synthetic/${this.repos[index].name}`,
      contents: this.campaign.patches.get(index).contents, idempotencyKey: key(index) }));
  }

  executeTargets(indices, options) {
    return this.batch().executeAll({ campaignId: this.campaign.id, targets: this.openTargets(indices, options) });
  }

  /** Observe the PRs the authorization opened; no operator command (verified against the fake Git host). */
  async createPRs() {
    assert.ok(this.campaign.opened, 'authorized_open_missing');
    const { indices, receipts } = this.campaign.opened;
    if (this.pendingBaseMovement) {
      const { repo, before, after, writes } = this.pendingBaseMovement; this.pendingBaseMovement = null;
      assert.equal(receipts[0].state, 'unknown'); assert.match(receipts[0].reason, /Base moved/);
      assert.ok(receipts.slice(1).every((row) => row.state === 'not_attempted'), 'batch_continued_after_base_movement');
      assert.equal(this.git.requests.filter((row) => row.method !== 'GET').length, writes, 'stale_base_wrote_git');
      assert.ok(this.repos.every((row) => this.git.pulls(row.id).length === this.prCount(row)), 'stale_base_created_pr');
      this.campaign.receipts.set(indices[0], receipts[0]);
      this.baseMovement = { repository_id: repo.id, before, after, campaign_id: this.campaign.id, receipt: receipts[0], recovered: false };
      return { source: 'git', value: this.baseMovement };
    }
    for (const [position, index] of indices.entries()) {
      const unknown = this.rounds.length === 0 && this.repos[index].disposition === 'unknown_response';
      assert.equal(receipts[position].state, unknown ? 'unknown' : 'pr_open');
      this.campaign.receipts.set(index, receipts[position]);
    }
    return { source: 'git', value: this.repos.map((repo) => ({ repository_id: repo.id, pulls: this.git.pulls(repo.id) })) };
  }

  /** Completed PRs for this repository before the current round. */
  prCount(repo) {
    return [...this.setupRounds, ...this.rounds].filter((round) => round.expected.includes(repo.id)).length;
  }

  async reconcile() {
    if (this.baseMovement && !this.baseMovement.recovered) {
      const stale = this.campaign;
      const repo = this.repos.find((row) => row.id === this.baseMovement.repository_id);
      // The operator confirms the moved base and that no instruction file changed in it.
      const actual = await this.action('reconcile', async () => {
        const response = await this.git.fetch(`https://api.github.com/repos/synthetic/${repo.name}/commits/main`);
        assert.equal(response.status, 200); return response.json();
      }, { dimension: 'R', repos: [repo] });
      assert.equal(actual.sha, this.baseMovement.after);
      assert.deepEqual(this.git.files(actual.sha), this.git.files(this.baseMovement.before), 'concurrent_instruction_change_unreviewed');
      this.bases.set(repo.id, actual.sha);
      // One recovery decision: cancel the stale campaign (its uncertain receipt
      // is retained) and plan a replacement linked to it. The stale authorized
      // plan is never edited or retried; the replacement is reviewed and
      // authorized again over all forty targets.
      const replaced = await this.action('recovery_decision', () => this.rollout.replace({ ...this.startRequest(stale.label), campaignId: stale.id,
        reason: 'Concurrent base movement invalidated reviewed ancestry. Cancel before reviewing a replacement campaign.' }));
      assert.equal(replaced.cancelled.status, 'cancelled');
      assert.equal((await this.api('GET', stale.root)).status, 'cancelled');
      const oldTargets = await this.batch().targets(stale.id);
      assert.equal(oldTargets.length, 40, 'cancelled_target_accounting');
      await this.adoptStarted(stale.label, replaced);
      assert.equal((await this.api('GET', this.campaign.root)).parent_campaign_id, stale.id, 'replacement_not_linked');
      this.baseMovement.recovered = true;
      await this.save('base-movement', { ...this.baseMovement, cancelled: replaced.cancelled, targets: oldTargets, duplicate_prs: 0, replacement_campaign_id: this.campaign.id });
      this.ledger.fault('base-movement', 'reviewed-replacement-campaign');
      await this.plans(); await this.authorize(); await this.createPRs();
    }
    for (const [index, receipt] of this.campaign.receipts) {
      if (receipt.state !== 'unknown') continue;
      this.ledger.fault('unknown-pr-response', `campaign-${this.campaigns.length}`);
      // One recovery decision (product path reconcileAndResume): pause, the
      // product looks the target up again by its deterministic branch and tree,
      // and resumes only on an exact PR. The oracle checks no Git write happened.
      const writes = this.git.requests.filter((row) => row.method !== 'GET').length;
      const { observed, campaign } = await this.action('recovery_decision', () => this.rollout.reconcileAndResume({ campaignId: this.campaign.id,
        target: this.openTargets([index])[0], reason: 'Exact deterministic PR and complete tree reconciled.' }));
      assert.equal(observed.state, 'pr_open'); assert.equal(this.git.requests.filter((row) => row.method !== 'GET').length, writes);
      const pulls = this.git.pulls(this.repos[index].id);
      assert.equal(pulls.filter((pr) => pr.number === observed.pull_request_number).length, 1, 'reconciled_pr_unobserved');
      this.campaign.receipts.set(index, observed);
      assert.ok(['authorized', 'running'].includes(campaign.status), 'campaign_not_resumed');
    }
    return { source: 'git', value: [...this.campaign.receipts.values()] };
  }

  async install() {
    const indices = [...this.campaign.patches.keys()].sort((a, b) => a - b);
    const byRepo = new Map(indices.map((index) => [this.repos[index].id, index]));
    const prs = new Map();
    for (const index of indices) {
      const repo = this.repos[index]; const receipt = this.campaign.receipts.get(index);
      assert.equal(receipt.state, 'pr_open');
      const prUrl = `https://api.github.com/repos/synthetic/${repo.name}/pulls/${receipt.pull_request_number}`;
      // Each repository's own review/CI remains a counted handoff.
      prs.set(index, await this.action('handoff', async () => (await this.git.fetch(prUrl)).json(), { dimension: 'H', repos: [repo], target: 'repository-review' }));
    }
    // Per reviewed unit (a proven-identical group or an individual exception):
    // one review of the repository reviewers' results against the authorized PR
    // receipts, then one merge decision (product path mergeReviewed) that
    // re-verifies every head/parent/tree, merges head-guarded, installs only
    // what merged and load-checks only verified installations with the pinned
    // host. Merge, installation and load receipts stay separate per target and
    // the oracle below re-checks each against the Git fixture and filesystem.
    const native = Boolean(this.config.native && !this.setup);
    if (native) await assertQualifiedCodex({ executable: this.config.native.executable, executableSha256: this.config.native.sha256 });
    const host = native ? { executable: this.config.native.executable, sha256: this.config.native.sha256, version: CODEX_VERSION } : null;
    const toTarget = (repo) => {
      const index = byRepo.get(repo.id); const patch = this.campaign.patches.get(index); const receipt = this.campaign.receipts.get(index);
      const encoded = patch.contents['AGENTS.md'];
      if (native) assert.ok(encoded, 'reviewed_instructions_missing');
      return { repository_id: repo.id, repository: `synthetic/${repo.name}`, pull_request_number: receipt.pull_request_number,
        commit: receipt.repository_commit, plan: patch.plan, contents: patch.contents, binding_id: this.bindings[index].id,
        plan_path: patch.path, project: this.projects.get(repo.id),
        expected_instructions: encoded ? Buffer.from(encoded, 'base64').toString('utf8') : null };
    };
    const merges = new Map(); const loads = new Map();
    for (const unit of this.campaign.units) {
      const members = unit.repos.filter((repo) => byRepo.has(repo.id));
      if (!members.length) continue;
      await this.action('review', async () => {
        for (const repo of members) {
          const index = byRepo.get(repo.id); const pr = prs.get(index); const patch = this.campaign.patches.get(index);
          assert.equal(pr.head.sha, this.campaign.receipts.get(index).repository_commit, 'reviewed_head_not_authorized_commit'); assert.equal(pr.merged, false);
          const tree = this.git.files(pr.head.sha);
          for (const change of patch.plan.changes) assert.equal(tree[change.path] === undefined ? null : hash(tree[change.path]), change.after_sha256);
        }
      }, { repos: members });
      const result = await this.action('approve', () => this.rollout.mergeReviewed({ campaignId: this.campaign.id, targets: members.map(toTarget), host }),
        { repos: members });
      assert.deepEqual(result.merges.map((row) => row.repository_id), members.map((repo) => repo.id), 'merged_unit_membership');
      // Per-target receipts: a refused or unknown merge is retained, never counted as merged.
      assert.ok(result.merges.every((row) => row.state === 'merged' && row.merged === true), 'merge_receipt_not_merged');
      assert.ok(result.recorded.length === members.length && result.recorded.every((row) => row.state === 'merged' && row.delivered === false), 'merge_observation');
      assert.ok(result.installed.length === members.length
        && result.installed.every((row) => row.state === 'installed' && row.delivery === 'not_observed' && row.verified === true), 'installation_unverified');
      assert.deepEqual(result.loads.map((row) => row.repository_id), native ? members.map((repo) => repo.id) : [], 'load_check_target_set');
      for (const row of result.merges) {
        const index = byRepo.get(row.repository_id); const repo = this.repos[index];
        const observed = this.git.pulls(repo.id).find((pr) => pr.number === row.pull_request_number);
        assert.equal(observed?.merged, true, 'merge_unobserved'); assert.equal(observed.merge_commit_sha, row.merge_commit);
        assert.notEqual(row.merge_commit, this.campaign.receipts.get(index).repository_commit);
        merges.set(index, { pr: prs.get(index), merge: { merged: true, sha: row.merge_commit } });
      }
      for (const row of result.loads) loads.set(row.repository_id, row);
    }
    assert.equal(merges.size, indices.length, 'planned_target_not_merged');
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
      if (repo.disposition === 'pin') assert.equal(this.version.id, this.first.id, 'pin_installed_beyond_pinned_release');
      const lockBytes = await readFile(join(this.projects.get(repo.id), LOCK));
      assert.equal(JSON.parse(lockBytes).version_id, this.version.id);
      if (this.rounds.length === 0 && this.setupRounds.length === 0) await writeFile(join(this.scratch, `${repo.id}-original-lock.json`), lockBytes);
      this.bases.set(repo.id, merge.sha);
      // A cancelled stale-base campaign created no PR. Count successful rounds,
      // and independently forbid duplicate branches in the retained Git history.
      const pulls = this.git.pulls(repo.id);
      assert.equal(pulls.length, this.prCount(repo) + 1, 'duplicate_pr');
      assert.equal(new Set(pulls.map((row) => row.head.ref)).size, pulls.length, 'duplicate_pr_branch');
      installs.push({ repository_id: repo.id, version_id: this.version.id, pr: pr.number, head: pr.head.sha, merge: merge.sha, hashes,
        installed: true, discovery: 'not_observed', delivery: 'not_observed', source: 'filesystem' });
    }
    if (native) {
      // The merge decision's load receipts are not trusted: each raw capture is
      // re-checked by the oracle against the merged Git tree before it is recorded.
      for (const install of installs) {
        const receipt = loads.get(install.repository_id);
        assert.ok(receipt?.requests && receipt.process_events, `load_check_failed:${receipt?.error ?? 'no_capture'}`);
        assertCodexBoundary({ requests: receipt.requests, events: receipt.process_events, expectedInstructions: this.git.files(install.merge)['AGENTS.md'] });
        const boundary = { evidence_class: 'synthetic fixture', ...receipt, scope: NATIVE_SCOPE };
        Object.assign(install, { discovery: boundary.discovery, delivery: boundary.delivery, source: boundary.source,
          scope: boundary.scope, artifact_sha256: await this.save(`native-${this.campaign.label}-${receipt.repository_id}`, boundary) });
      }
    }
    if (this.campaign.expected.some((id) => this.repos.find((repo) => repo.id === id).disposition === 'pin')) this.pinsInstalled = true;
    await this.checkExcluded();
    const report = { evidence_class: 'synthetic fixture', complete_job: false, label: this.campaign.label, setup: Boolean(this.setup),
      campaign_id: this.campaign.id, version_number: this.version.number, expected: [...this.campaign.expected], targets: await this.targets(), installs, review_units: this.campaign.units.map((unit) => ({ kind: unit.kind,
        repositories: unit.repos.map((repo) => repo.id) })),
      plans: [...this.campaign.patches].map(([index, patch]) => ({ repository_id: this.repos[index].id,
        binding_id: this.bindings[index].id, plan: patch.plan, contents: patch.contents })) };
    this.rounds.push(report); await this.save(`${this.setup ? 'setup-' : ''}${this.campaign.label}`, report);
    return report;
  }

  async checkExcluded() {
    for (const repo of this.repos.filter((repo) => !this.campaign.expected.includes(repo.id))) {
      if (repo.disposition === 'pin' && this.pinsInstalled) {
        // An installed pin held in this round: no new PR, its lock still at the
        // pinned release and its unmanaged local files untouched.
        assert.equal(this.git.pulls(repo.id).length, this.prCount(repo), 'held_pin_written');
        assert.equal(JSON.parse(await readFile(join(this.projects.get(repo.id), LOCK), 'utf8')).version_id, this.first.id, 'held_pin_advanced');
        for (const [path, text] of Object.entries(repo.files)) {
          const actual = await readFile(join(this.projects.get(repo.id), path), 'utf8');
          if (path === 'AGENTS.md') assert.ok(actual.includes(text), 'held_pin_local_segment_lost'); else assert.equal(actual, text);
        }
        continue;
      }
      assert.equal(this.git.pulls(repo.id).length, 0, 'excluded_repository_written');
      if (repo.disposition === 'unavailable') { await assert.rejects(lstat(this.projects.get(repo.id)), { code: 'ENOENT' }); continue; }
      for (const [path, text] of Object.entries(repo.files)) assert.equal(await readFile(join(this.projects.get(repo.id), path), 'utf8'), text);
      await assert.rejects(lstat(join(this.projects.get(repo.id), LOCK)), { code: 'ENOENT' });
    }
  }

  /**
   * Fixture fault, not an operator action: a concurrent local edit to repos[0]'s
   * managed file before the reverse plan. The reverse plan command itself must
   * surface it as that repository's own exception (no separate operator check).
   */
  async injectReverseConflict() {
    const repo = this.repos[0]; const project = this.projects.get(repo.id);
    const lockPath = join(project, LOCK); const lockBytes = await readFile(lockPath);
    const managed = JSON.parse(lockBytes).materialization.files.find((row) => row.ownership === 'managed' && row.destination.endsWith('AGENTS.md'));
    assert.ok(managed, 'managed_instruction_missing');
    const managedPath = join(project, managed.destination); const original = await readFile(managedPath);
    const dirtyBytes = 'Locally modified managed policy; retain for explicit conflict resolution.\n';
    await writeFile(managedPath, dirtyBytes);
    this.conflict = { repo, project, lockPath, lockBytes, managed, managedPath, original, dirtyBytes };
  }

  /**
   * One recovery decision on the surfaced conflict (keep a copy of the local
   * edit, restore the managed bytes), then one counted re-plan command for
   * exactly that target in the same draft campaign (replanResolved). The oracle first checks
   * the refusal changed nothing and that only this repository was held.
   */
  async resolveReverseConflict() {
    const { repo, lockPath, lockBytes, managed, managedPath, original, dirtyBytes } = this.conflict;
    const { review } = this.campaign;
    const conflicts = review.exceptions.filter((row) => row.kind === 'local_conflict');
    assert.deepEqual(conflicts.map((row) => row.repository_id), [repo.id], 'local_conflict_not_surfaced');
    assert.equal(conflicts[0].observed.error, 'local_modifications');
    assert.equal(await readFile(managedPath, 'utf8'), dirtyBytes, 'dirty_local_work_lost');
    assert.deepEqual(await readFile(lockPath), lockBytes, 'dirty_check_mutated_lock');
    assert.ok(![...review.patches.values()].some((patch) => patch.repository_id === repo.id), 'conflicted_target_planned');
    await this.action('recovery_decision', async () => {
      // Export the conflicting bytes before the scripted operator chooses the
      // previous managed content. The export survives scratch-tree teardown.
      await this.save('retained-dirty-local-work', { repository_id: repo.id, path: managed.destination, content: dirtyBytes, sha256: hash(dirtyBytes) });
      await writeFile(managedPath, original);
    }, { repos: [repo] });
    // The operator then invokes the re-plan (replan-resolved): its own counted command.
    const replanned = await this.action('command', () => this.rollout.replanResolved({ campaignId: this.campaign.id, review,
      repositoryIds: [repo.id], request: this.startRequest('reverse', true) }), { repos: [repo] });
    this.campaign.review = replanned.review; this.campaign.packet = replanned.packet;
    this.ledger.fault('dirty-managed-segment', 'reverse-plan-holds-local-conflict');
    return { dirty: 'local_modifications', held_until_resolved: true, lock_unchanged: true, local_work_exported: true };
  }

  /**
   * A new local edit after review makes the reviewed plan stale: applying it in
   * the operator's shell is refused before any write. Uses the campaign's own
   * re-planned restore plan (no separate restore command); the standalone SDK
   * needs the rollout's planning credential exported in that shell: counted.
   */
  async staleReverseCheck() {
    const { repo, project, lockPath, lockBytes } = this.conflict;
    await this.action('configuration_edit', async () => { assert.ok(this.env.FI_CONTEXT_CREDENTIAL, 'planning_credential_missing'); }, { repos: [repo] });
    const binding = this.bindings[this.repos.indexOf(repo)].id;
    const stalePath = this.campaign.review.patches.get(binding)?.path;
    assert.ok(stalePath, 'resolved_target_not_replanned');
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
    return 'stale_plan';
  }

  async update(reverse) {
    assert.ok(this.rounds.length, 'initial_install_required');
    if (reverse) {
      await this.injectReverseConflict(); this.version = this.first; await this.freeze('reverse', true);
      const resolved = await this.resolveReverseConflict();
      this.reverseConflicts = { ...resolved, stale: await this.staleReverseCheck() };
      await this.save('reverse-conflicts', this.reverseConflicts);
    }
    // Publishing the reviewed next release also freezes and plans its rollout (one approval, no repository write).
    else await this.adoptStarted('second-update', await this.publishNext('second-update'));
    await this.plans();
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
    // The setup ledger is the first file a configured run writes; its directory may not exist yet.
    await mkdir(this.config.evidence_dir, { recursive: true });
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
    let installed = 0; let lockless = 0; let open = 0; let pinsInstalled = 0;
    for (const repo of this.repos) {
      for (const pr of this.git.pulls(repo.id)) if (!pr.merged) open += 1;
      if (repo.disposition === 'unavailable') { await assert.rejects(lstat(this.projects.get(repo.id)), { code: 'ENOENT' }); continue; }
      let lock = null;
      try { lock = JSON.parse(await readFile(join(this.projects.get(repo.id), LOCK))); } catch (error) { if (error.code !== 'ENOENT') throw error; }
      if (adopted(repo)) {
        assert.equal(lock?.version_id, this.first.id, 'configured_release_not_installed'); installed += 1;
        if (repo.disposition === 'pin') pinsInstalled += 1;
      }
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
      pins_installed_at_pinned_release: pinsInstalled, open_pull_requests: open, next_release_published: this.version.number, reached_by: CONFIGURED_PRECONDITION.reached_by };
    assert.deepEqual(realised, { ...CONFIGURED_PRECONDITION }, 'configured_precondition_not_realised');
    const unknown = this.repos.find((repo) => repo.disposition === 'unknown_response');
    this.git.armUnknownResponse(unknown.id);
    await this.save('configured-precondition', { evidence_class: 'synthetic fixture', realised,
      setup_ledger: summarizeLedger(setupLedger.events()), setup_campaigns: this.campaigns.slice() });
  }

  async perform(stage, { ledger, signal }) {
    this.stage = stage; this.ledger = ledger; this.signal = signal; this.decision = null; this.rollout.signal = signal;
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
        requireNativeDelivery(report.installs, report.expected, this.version.id);
        result = { source: 'model_boundary', value: report }; break;
      }
      case 'second-update':
      case 'reverse-plan': {
        const report = await this.update(stage.id === 'reverse-plan');
        requireNativeDelivery(report.installs, report.expected, this.version.id);
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
        // Exactly one PR per round that planned this repository (fixture-derived).
        const expected = [...this.setupRounds, ...this.rounds].filter((round) => round.expected.includes(repo.id))
          .map((round) => `futureagi/context/${round.campaign_id}/${this.bindings[index].id}`).sort();
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
      assert.equal(raw.length, this.rounds.reduce((total, round) => total + round.expected.length, 0), 'authorized_patch_denominator');
      value = { unauthorized_effects: 0 };
    } else if (check.id === 'local-policy-preserved') {
      source = 'filesystem'; value = []; raw = [];
      for (const repo of this.repos.filter((repo) => repo.disposition !== 'unavailable')) for (const [path, original] of Object.entries(repo.files)) {
        const actual = await readFile(join(this.projects.get(repo.id), path), 'utf8');
        raw.push({ path: `${repo.id}/${path}`, content: actual });
        // The manifest is the original local policy, not the new managed text.
        // Extract the observed original segment, never substitute expected bytes.
        // Exact merged trees are independently checked above and at installation.
        if (path === 'AGENTS.md' && adopted(repo)) {
          const start = actual.indexOf(original);
          assert.ok(start >= 0 && actual.indexOf(original, start + original.length) === -1, 'local_segment_missing_or_duplicated');
          value.push({ path: `${repo.id}/${path}`, content: actual.slice(start, start + original.length) });
        } else value.push({ path: `${repo.id}/${path}`, content: actual });
      }
    } else if (check.id === 'pins-retained') {
      // A pin is retained only if it was actually installed at its pinned
      // release and survived every later round: exactly one adoption PR at that
      // release, an explicit exclusion in every later round, its lock and its
      // managed bytes still those of the adoption merge, its selector unchanged
      // and its local bytes intact. Counts derive from independent API, Git and
      // filesystem observations; any deviation counts the pin as advanced.
      source = 'filesystem'; const targets = await this.targets(); raw = [];
      const allRounds = [...this.setupRounds, ...this.rounds];
      assert.ok(this.rounds.length === 3, 'all_rollout_rounds_required');
      for (const repo of this.repos.filter((repo) => repo.disposition === 'pin')) {
        const project = this.projects.get(repo.id);
        const target = targets.find((row) => row.repository.repository_id === repo.id);
        const pulls = this.git.pulls(repo.id);
        const adoption = allRounds.filter((round) => round.expected.includes(repo.id));
        const held = allRounds.filter((round) => !round.expected.includes(repo.id))
          .map((round) => ({ label: round.label, version: round.version_number,
            decision: round.targets.find((row) => row.repository.repository_id === repo.id)?.decision }));
        let lock = null;
        try { lock = JSON.parse(await readFile(join(project, LOCK), 'utf8')); } catch (error) { if (error.code !== 'ENOENT') throw error; }
        const files = Object.fromEntries(await Promise.all(Object.keys(repo.files).map(async (path) => [path, await readFile(join(project, path), 'utf8')])));
        const merged = pulls.length === 1 && pulls[0].merged === true ? this.git.files(pulls[0].merge_commit_sha) : null;
        const install = adoption[0]?.installs.find((row) => row.repository_id === repo.id);
        const managedIntact = Boolean(merged) && files['AGENTS.md'] === merged['AGENTS.md'];
        const localIntact = Object.entries(repo.files).every(([path, text]) => (path === 'AGENTS.md'
          ? files[path].indexOf(text) >= 0 && files[path].indexOf(text) === files[path].lastIndexOf(text) : files[path] === text));
        const advanced = adoption.length !== 1 || adoption[0].version_number !== this.first.number || !install
          || install.version_id !== this.first.id || pulls.length !== 1 || pulls[0].head.ref !== `futureagi/context/${adoption[0].campaign_id}/${target.binding_id}`
          || lock?.version_id !== this.first.id || !managedIntact || !localIntact
          || canonical(target.selector) !== canonical({ version: this.first.number })
          || held.length !== allRounds.length - 1 || held.some((row) => row.decision !== 'exclude');
        raw.push({ repository_id: repo.id, selector: target.selector, adoption_rounds: adoption.map((round) => round.label),
          held_rounds: held, pulls: pulls.map((pr) => ({ number: pr.number, ref: pr.head.ref, merged: pr.merged, merge: pr.merge_commit_sha })),
          lock_version_id: lock?.version_id ?? null, managed_intact: managedIntact, local_intact: localIntact, files, advanced });
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
        requireNativeDelivery(round.installs, round.expected, version);
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
    const nativeObserved = this.rounds.length === 3 && this.rounds.every((round) => round.installs.length === round.expected.length
      && round.installs.every((row) => row.source === 'model_boundary' && row.discovery === 'observed' && row.delivery === 'observed'));
    this.git = null; this.executor = null; this.env = {}; this.headers = {};
    await this.save('teardown', { settled_or_cancelled: qualified, credentials_revoked: qualified, fixture_destroyed: qualified,
      complete_job: false, discovery: nativeObserved ? 'observed' : 'not_observed', delivery: nativeObserved ? 'observed' : 'not_observed',
      scope: nativeObserved ? 'codex-0.167.1-linux-root-AGENTS-only' : null });
    return { qualified };
  }
}
