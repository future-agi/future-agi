import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { JOBS } from '../../benchmarks/context-hub/jobs.mjs';
import { baselineReport, compareActionRuns, comparisonContract, runJourney, validateProvenance } from '../../benchmarks/context-hub/runner.mjs';
import { extractDocumentedBaselines } from '../../benchmarks/context-hub/flow-audit.mjs';
import { assertObservation, OracleFailure } from '../../lib/skills-r3/oracles.mjs';
import { digest, sha256 } from '../../lib/skills-r3/canonical.mjs';
import { ActionLedger, summarizeLedger } from '../../lib/skills-r3/ledger.mjs';

const hash = 'a'.repeat(64);
const commit = 'b'.repeat(40);
const provenance = { product_base: commit, product_head: commit, product_tree: commit, sdk_commit: commit,
  fixture_hash: hash, runtime_digest: hash, package_hashes: [hash], lockfile_hashes: [hash], dirty: false,
  environment: 'gcp', project: 'fagi-omega-staging' };
function sample(check) {
  if (check.kind === 'manifest') return { expected: [{ path: 'AGENTS.md', sha256: sha256('synthetic'), size_bytes: 9 }], value: [{ path: 'AGENTS.md', content: 'synthetic' }], mutated: [{ path: 'AGENTS.md', content: 'different' }] };
  if (check.kind === 'matrix') return { expected: [{ case_id: 'case-1', arm: 'candidate', repeat: 0, status: 'pass' }], value: [{ case_id: 'case-1', arm: 'candidate', repeat: 0, status: 'pass' }], mutated: [] };
  if (check.kind === 'exact_set') return { expected: ['one', 'two'], value: ['two', 'one'], mutated: ['one'] };
  return { expected: { identity: 'synthetic-approved', count: 1 }, value: { identity: 'synthetic-approved', count: 1 }, mutated: { identity: 'wrong', count: 2 } };
}
function registration(job) {
  return { schema: 1, job: job.id, cohort: 'cold', evidence_scope: 'synthetic', customer_input_hash: hash,
    initial_conditions_hash: hash, safety_requirements_hash: hash, complete_job_boundary_hash: hash,
    population: job.population, fault_ids: job.fault_ids, checkpoint_P_receipt: hash,
    expected_observations: Object.fromEntries(job.checks.map((c) => [c.id, sample(c).expected])) };
}
function driverFor(job, { fail = false } = {}) {
  return { mode: 'selftest', provenance,
    async perform(stage, { ledger }) {
      await ledger.measure({ dimension: 'A', primitive: stage.human_actions[0] ?? 'command', actor: 'synthetic-operator', step: stage.id,
        phase: stage.phase, target: 'synthetic-target', items: ['synthetic-item'] }, () => {});
      if (stage.id === job.steps[0].id) for (const fault of job.fault_ids) ledger.fault(fault, `scenario-${fault}`);
      if (fail) throw new Error('secret-bearing failure must not be printed');
    },
    async observe(check) { return { source: check.sources[0], artifact_sha256: hash, value: sample(check).value }; },
    async teardown() { return { qualified: true }; },
  };
}

// Harness-level one-sided fallibility proof for EVERY declared oracle, not product E2E coverage.
for (const job of JOBS) for (const check of job.checks) {
  test(`${job.id}/${check.id}: green, mutate observed side only, exact failure, restored green`, () => {
    const { expected, value, mutated } = sample(check);
    const observation = { source: check.sources[0], artifact_sha256: hash, value };
    assertObservation(check, expected, observation);
    assert.throws(() => assertObservation(check, expected, { ...observation, value: mutated }),
      (error) => error instanceof OracleFailure && error.checkId === check.id);
    assertObservation(check, expected, observation);
    assert.throws(() => assertObservation(check, expected, { ...observation, source: 'product_toast' }), OracleFailure);
  });
}

for (const job of JOBS) {
  test(`${job.id}: complete scripted orchestration selftest retains honest scope`, async () => {
    const root = await mkdtemp(join(tmpdir(), 'l13-job-'));
    try {
      const result = await runJourney({ jobId: job.id, runId: `selftest-${job.id}`, driver: driverFor(job), registration: registration(job), outputDir: root });
      assert.equal(result.status, 'completed');
      assert.equal(result.claim, 'Not qualified');
      assert.equal(result.stages.length, job.steps.length);
      assert.equal(result.checks.length, job.checks.length);
      assert.equal(result.measurement.counts.A, job.steps.length, 'only this synthetic driver uses one primitive per step');
      assert.equal(compareActionRuns(result, result, {}).ten_x_actions, null);
      const persisted = JSON.parse(await readFile(join(root, `selftest-${job.id}.json`), 'utf8'));
      assert.equal(persisted.mode, 'selftest');
      await assert.rejects(runJourney({ jobId: job.id, runId: `selftest-${job.id}`, driver: driverFor(job), registration: registration(job), outputDir: root }), /EEXIST/);
    } finally { await rm(root, { recursive: true }); }
  });
}

test('missing drivers and deferred expansion cannot become green measurements', async () => {
  const report = baselineReport({ flows: [] });
  assert.equal(report.jobs.length, 6);
  assert.ok(report.jobs.every((j) => j.operator_actions === null && j.ten_x_actions === null));
  assert.equal(report.general_10x_launch, 'not_established');
  assert.deepEqual(Object.keys(report.jobs.find((job) => job.job === 'J5').components), ['cold_adoption', 'recurring_rollout', 'recovery']);
  const noDriver = await runJourney({ jobId: 'J1', runId: 'missing' });
  assert.equal(noDriver.status, 'not_measured');
  const job = JOBS.find((j) => j.id === 'J5');
  const reg = registration(job); delete reg.checkpoint_P_receipt;
  const deferred = await runJourney({ jobId: 'J5', runId: 'deferred', driver: driverFor(job), registration: reg });
  assert.equal(deferred.status, 'not_measured');
});

test('failed actions, incomplete outcomes and teardown uncertainty remain retained failures', async () => {
  const root = await mkdtemp(join(tmpdir(), 'l13-failure-'));
  try {
    const job = JOBS[0];
    const result = await runJourney({ jobId: job.id, runId: 'failed', registration: registration(job), driver: driverFor(job, { fail: true }), outputDir: root });
    assert.equal(result.status, 'failed');
    assert.equal(result.measurement.counts.A, 1);
    assert.ok(!JSON.stringify(result).includes('secret-bearing'));
    const driver = driverFor(job); driver.teardown = async () => ({ qualified: false });
    const uncertain = await runJourney({ jobId: job.id, runId: 'uncertain', registration: registration(job), driver, outputDir: root });
    assert.equal(uncertain.status, 'failed'); assert.equal(uncertain.teardown, 'unknown');
  } finally { await rm(root, { recursive: true }); }
});

test('unrecorded faults and uninstrumented stages fail rather than inventing counts', async () => {
  const root = await mkdtemp(join(tmpdir(), 'l13-accounting-'));
  try {
    const job = JOBS[0];
    const driver = driverFor(job); driver.perform = async () => {};
    const result = await runJourney({ jobId: job.id, runId: 'uninstrumented', registration: registration(job), driver, outputDir: root });
    assert.equal(result.status, 'failed'); assert.equal(result.measurement.counts.A, 0);
    const reg = registration(job); reg.population = { library_members: 1 };
    await assert.rejects(runJourney({ jobId: job.id, runId: 'narrow', registration: reg, driver, outputDir: root }), /Population narrowed/);
  } finally { await rm(root, { recursive: true }); }
});

test('complete-job comparisons use verified ledgers, refuse zero, selftest, dirty and mismatched cohorts', async () => {
  async function run(mode, count) {
    const ledger = new ActionLedger({ runId: `compare-${mode}`, job: 'J1', mode });
    const contract = comparisonContract(JOBS[0], registration(JOBS[0]));
    ledger.registration({ comparisonIdentity: digest(contract), preregistrationHash: hash });
    for (const stage of JOBS[0].steps) for (let i = 0; i < count; i++) await ledger.measure({ dimension: 'A', primitive: stage.human_actions[0] ?? 'command', actor: 'operator', step: stage.id, phase: stage.phase, target: 'test', items: ['item-1'] }, () => {});
    for (const fault of JOBS[0].fault_ids) ledger.fault(fault, `scenario-${fault}`);
    for (const check of JOBS[0].checks) ledger.outcome(check.id, 'pass', hash);
    return { status: 'completed', mode, job: 'J1', run_id: `compare-${mode}`, cohort: 'cold', evidence_scope: 'customer',
      comparison_contract: contract, comparison_identity: digest(contract), preregistration_hash: hash,
      stages: JOBS[0].steps.map((stage) => ({ id: stage.id, status: 'completed' })),
      checks: JOBS[0].checks.map((check) => ({ id: check.id, status: 'pass', source: check.sources[0], artifact_sha256: hash })),
      observations_complete: true, teardown: 'confirmed', provenance, events: ledger.events() };
  }
  const ours = await run('product', 1); const other = await run('competitor', 10);
  const review = { reviewer_id: 'independent', author_id: 'author', strongest_configured_route_hash: hash, receipt_sha256: hash,
    ours_ledger_hash: summarizeLedger(ours.events).integrity.hash, competitor_ledger_hash: summarizeLedger(other.events).integrity.hash };
  const result = compareActionRuns(ours, other, review);
  assert.equal(result.ten_x_actions, true); assert.equal(result.action_ratio, 10);
  assert.equal(result.human_study_required, true);
  assert.equal(compareActionRuns(ours, { ...other, comparison_identity: 'different' }, review).ten_x_actions, null);
  assert.equal(compareActionRuns({ ...ours, provenance: { ...provenance, dirty: true } }, other, review).ten_x_actions, null);
  assert.equal(compareActionRuns(ours, other, { ...review, reviewer_id: 'author' }).ten_x_actions, null);
  assert.equal(compareActionRuns(await run('product', 0), other, review).ten_x_actions, null);
  assert.equal(compareActionRuns({ ...ours, checks: [] }, other, review).ten_x_actions, null);
  assert.equal(compareActionRuns({ ...ours, stages: [] }, other, review).ten_x_actions, null);
  assert.equal(compareActionRuns(ours, other, { ...review, ours_ledger_hash: 'c'.repeat(64) }).ten_x_actions, null);
  const forged = structuredClone(ours); forged.events[0].mode = 'selftest';
  assert.equal(compareActionRuns(forged, other, review).ten_x_actions, null);
});

test('documentation extraction pins exact source bullets and citations, never a comparative denominator', () => {
  const fakeAudit = Object.fromEntries(['flows:1', 'flows:2', 'flows:4', 'flows:5'].map((family) => [family, {
    flows: Array.from({ length: 60 }, (_, i) => ({ name: `Synthetic ${i}`, competitor_steps: ['one documented compound step', 'second'], sources: ['https://docs.example.invalid/fixture', '/private/source'] })),
  }]));
  const result = extractDocumentedBaselines(JSON.stringify(fakeAudit));
  assert.ok(result.flows.every((flow) => flow.documented_step_count === 2 && flow.measured_operator_actions === null));
  assert.ok(result.jobs.every((job) => job.best_applicable_measured_actions === null));
  assert.ok(result.flows.every((flow) => flow.citations.length === 1));
  assert.throws(() => extractDocumentedBaselines('{}'), /Missing audited flow/);
});

test('provenance rejects Mac, unknown packages and synthetic checkout markers', () => {
  validateProvenance(provenance);
  assert.throws(() => validateProvenance({ ...provenance, environment: 'mac' }));
  assert.throws(() => validateProvenance({ ...provenance, package_hashes: [] }));
  assert.throws(() => validateProvenance({ ...provenance, product_head: 'gitify' }));
});
