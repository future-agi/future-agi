import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { getJob } from '../../benchmarks/context-hub/jobs.mjs';
import { PRE_CHECKPOINT_P, compareActionRuns, runJourney } from '../../benchmarks/context-hub/runner.mjs';
import { sha256 } from '../../lib/skills-r3/canonical.mjs';

// The J5 pre-Checkpoint-P measurement-only mode measures counts but never
// produces a supported claim, a Checkpoint P receipt or a GitHub observation.
const hash = 'a'.repeat(64);
const commit = 'b'.repeat(40);
const decision = { packet_sha256: hash, preconditions_sha256: hash };
const provenance = { product_base: commit, product_head: commit, product_tree: commit, sdk_commit: commit,
  fixture_hash: hash, runtime_digest: hash, package_hashes: [hash], lockfile_hashes: [hash], dirty: false,
  environment: 'gcp', project: 'fagi-omega-staging' };
function sample(check) {
  if (check.kind === 'manifest') return { expected: [{ path: 'AGENTS.md', sha256: sha256('synthetic'), size_bytes: 9 }], value: [{ path: 'AGENTS.md', content: 'synthetic' }] };
  if (check.kind === 'exact_set') return { expected: ['one', 'two'], value: ['two', 'one'] };
  return { expected: { identity: 'synthetic-approved', count: 1 }, value: { identity: 'synthetic-approved', count: 1 } };
}
function registration(job, extra = {}) {
  return { schema: 1, job: job.id, cohort: 'cold', evidence_scope: 'synthetic', customer_input_hash: hash,
    initial_conditions_hash: hash, safety_requirements_hash: hash, complete_job_boundary_hash: hash,
    population: job.population, fault_ids: job.fault_ids,
    expected_observations: Object.fromEntries(job.checks.map((c) => [c.id, sample(c).expected])), ...extra };
}
function driverFor(job, observed) {
  let faulted = false;
  return { mode: 'product', provenance,
    async perform(stage, { ledger }) {
      await ledger.measure({ dimension: 'A', primitive: stage.human_actions[0] ?? 'command', actor: 'synthetic-operator', step: stage.id,
        phase: stage.phase, target: 'synthetic-target', items: ['synthetic-item'], decision }, () => {});
      ledger.stage({ step: stage.id, phase: stage.phase, source: 'api', artifactHash: hash, decision });
      // The configured cohort skips cold stages, so faults belong to the first performed stage.
      if (!faulted) { faulted = true; for (const fault of job.fault_ids) ledger.fault(fault, `scenario-${fault}`); }
    },
    async observe(check) {
      observed.push(check.id);
      if (check.id === 'real-github-conformance') throw new Error('external_real_github_conformance_not_supplied');
      return { source: check.sources[0], artifact_sha256: hash, value: sample(check).value };
    },
    async teardown() { return { qualified: true }; },
  };
}
async function withDir(fn) {
  const root = await mkdtemp(join(tmpdir(), 'j5-measure-'));
  try { return await fn(root); } finally { await rm(root, { recursive: true }); }
}
const flag = { [PRE_CHECKPOINT_P.flag]: true };

test('without the explicit opt-in J5 still refuses before any action with null counts', async () => {
  const job = getJob('J5'); const observed = [];
  const result = await runJourney({ jobId: 'J5', runId: 'no-flag', driver: driverFor(job, observed), registration: registration(job) });
  assert.equal(result.status, 'not_measured');
  assert.equal(result.reason, 'Campaign execution is deferred until Checkpoint P');
  assert.equal(result.operator_actions, null); assert.equal(result.ten_x_actions, null);
  assert.deepEqual(observed, []);
});

test('measurement-only J5 records real counts, leaves real-GitHub open and blocks every claim', async () => withDir(async (root) => {
  const job = getJob('J5'); const observed = [];
  const result = await runJourney({ jobId: 'J5', runId: 'measured', driver: driverFor(job, observed), registration: registration(job, flag), outputDir: root });
  assert.equal(result.status, 'completed');
  assert.equal(result.evidence_class, 'synthetic fixture + fake Git server');
  assert.equal(result.claim_status, 'blocked_pending_checkpoint_p_and_real_github');
  assert.match(result.claim, /^Pre-Checkpoint-P measurement/);
  assert.ok(!observed.includes('real-github-conformance'), 'the external check is never observed from the driver');
  const github = result.checks.find((check) => check.id === 'real-github-conformance');
  assert.equal(github.status, 'external_open');
  assert.equal(result.observations_complete, false, 'an open external check never makes observations complete');
  assert.ok(!result.events.some((event) => event.type === 'outcome' && event.id === 'real-github-conformance'));
  assert.equal(result.checks.filter((check) => check.status === 'pass').length, job.checks.length - 1);
  assert.equal(result.measurement.counts.A, job.steps.length);
  assert.deepEqual(Object.keys(result.components), ['cold_adoption', 'recurring_rollout', 'recovery']);
  assert.equal(result.components.cold_adoption.counts.A, job.steps.filter((stage) => stage.phase === 'cold').length);
  assert.ok(Object.values(result.components).every((row) => row.action_ratio === null && row.ten_x_actions === null));
  const persisted = JSON.parse(await readFile(join(root, 'measured.json'), 'utf8'));
  assert.equal(persisted.claim_status, result.claim_status);
}));

test('a configured measurement never reports a cold-adoption component', async () => withDir(async (root) => {
  const job = getJob('J5');
  const result = await runJourney({ jobId: 'J5', runId: 'configured', driver: driverFor(job, []),
    registration: registration(job, { ...flag, cohort: 'configured' }), outputDir: root });
  assert.equal(result.status, 'completed');
  assert.equal(result.components.cold_adoption.status, 'not_measured');
  assert.equal(result.components.recurring_rollout.status, 'pre_checkpoint_p_measurement');
}));

test('the opt-in is refused for customer scope, another job, a non-true value or alongside a receipt', async () => {
  const j5 = getJob('J5');
  await assert.rejects(runJourney({ jobId: 'J5', runId: 'customer', driver: driverFor(j5, []),
    registration: registration(j5, { ...flag, evidence_scope: 'customer' }) }), /limited to the synthetic fixture/);
  await assert.rejects(runJourney({ jobId: 'J5', runId: 'truthy', driver: driverFor(j5, []),
    registration: registration(j5, { [PRE_CHECKPOINT_P.flag]: 'yes' }) }), /must be explicit/);
  await assert.rejects(runJourney({ jobId: 'J5', runId: 'receipt', driver: driverFor(j5, []),
    registration: registration(j5, { ...flag, checkpoint_P_receipt: hash }) }), /never accompanies a Checkpoint P receipt/);
  const j4 = getJob('J4');
  await assert.rejects(runJourney({ jobId: 'J4', runId: 'other-job', driver: driverFor(j4, []),
    registration: registration(j4, flag) }), /limited to J5/);
});

test('compare refuses any pre-Checkpoint-P report, including a relabelled one', async () => withDir(async (root) => {
  const job = getJob('J5');
  const ours = await runJourney({ jobId: 'J5', runId: 'ours', driver: driverFor(job, []), registration: registration(job, flag), outputDir: root });
  const review = { reviewer_id: 'r', author_id: 'a', strongest_configured_route_hash: hash, receipt_sha256: hash };
  const blocked = compareActionRuns(ours, { ...ours, mode: 'competitor' }, review);
  assert.equal(blocked.ten_x_actions, null); assert.equal(blocked.action_ratio, null);
  assert.equal(blocked.claim_status, 'blocked_pending_checkpoint_p_and_real_github');
  // Stripping the labels cannot launder the run: the open external check still blocks.
  const stripped = { ...ours, evidence_scope: 'customer' };
  delete stripped.claim_status; delete stripped.evidence_class; delete stripped.measurement_mode;
  const relabelled = compareActionRuns(stripped, { ...stripped, mode: 'competitor' }, review);
  assert.equal(relabelled.ten_x_actions, null);
  assert.match(relabelled.reason, /Pre-Checkpoint-P/);
}));

test('J5 documents its own bounded journey deadline; other jobs keep the default', () => {
  assert.equal(getJob('J5').integration.journey_deadline_ms, 3_600_000);
  for (const id of ['J1', 'J2', 'J3', 'J4', 'J6']) assert.equal(getJob(id).integration.journey_deadline_ms, undefined);
});
