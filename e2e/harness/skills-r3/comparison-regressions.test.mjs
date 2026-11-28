import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { getJob } from '../../benchmarks/context-hub/jobs.mjs';
import { compareActionRuns, runJourney } from '../../benchmarks/context-hub/runner.mjs';
import { summarizeLedger } from '../../lib/skills-r3/ledger.mjs';
import { digest } from '../../lib/skills-r3/canonical.mjs';
import { main } from '../../benchmarks/context-hub/cli.mjs';

// Synthetic observations exercise comparator mechanics, not customer/competitor measurements.
const hash = 'a'.repeat(64);
const decision = { packet_sha256: hash, preconditions_sha256: hash };
const provenance = { product_base: 'b'.repeat(40), product_head: 'b'.repeat(40), product_tree: 'b'.repeat(40),
  sdk_commit: 'b'.repeat(40), fixture_hash: hash, runtime_digest: hash, package_hashes: [hash],
  lockfile_hashes: [hash], dirty: false, environment: 'gcp', project: 'fagi-omega-staging' };
const sample = (check) => check.kind === 'manifest' || check.kind === 'matrix' || check.kind === 'exact_set' ? [] : { exact: true };
function registration(job, overrides = {}) {
  return { schema: 1, job: job.id, cohort: 'configured', evidence_scope: 'customer', customer_input_hash: hash,
    initial_conditions_hash: hash, safety_requirements_hash: hash, complete_job_boundary_hash: hash,
    population: job.population, fault_ids: job.fault_ids, checkpoint_P_receipt: hash,
    expected_observations: Object.fromEntries(job.checks.map((check) => [check.id, sample(check)])), ...overrides };
}
function driver(job, mode, { counts = {}, coverage = false } = {}) {
  let first = true;
  return { mode, provenance,
    async perform(stage, { ledger }) {
      if (first) for (const id of job.fault_ids) ledger.fault(id, `scenario-${id}`);
      first = false;
      const sequences = [];
      for (let i = 0; i < (counts[stage.id] ?? 1); i++) {
        sequences.push(ledger.events().length + 1);
        const primitive = stage.human_actions?.[i] ?? 'command';
        await ledger.measure({ dimension: 'A', primitive, actor: 'operator', step: stage.id, phase: stage.phase,
          target: 'synthetic-target', items: ['synthetic-item'], decision }, () => {});
      }
      if (coverage || stage.human_actions.length) ledger.stage({ step: stage.id, phase: stage.phase, source: 'api', artifactHash: hash, actionSequences: sequences, decision });
    },
    async observe(check) { return { source: check.sources[0], artifact_sha256: hash, value: sample(check) }; },
    async teardown() { return { qualified: true }; },
  };
}
function review(ours, competitor) {
  return { reviewer_id: 'independent', author_id: 'author', strongest_configured_route_hash: hash, receipt_sha256: hash,
    ours_ledger_hash: summarizeLedger(ours.events).integrity.hash, competitor_ledger_hash: summarizeLedger(competitor.events).integrity.hash };
}
async function withRuns(jobId, callback) {
  const root = await mkdtemp(join(tmpdir(), 'l13-regression-'));
  const job = getJob(jobId);
  try { await callback(job, async (mode, options = {}, overrides = {}) => runJourney({ jobId, runId: `${mode}-${options.id ?? 'run'}`,
    driver: driver(job, mode, options), registration: registration(job, overrides), outputDir: root })); }
  finally { await rm(root, { recursive: true, force: true }); }
}

test('TEST-003 / TENX-01: comparison identity includes actual preregistered initial conditions', async () => {
  await withRuns('J1', async (job, run) => {
    const counts = Object.fromEntries(job.steps.map((stage) => [stage.id, 10]));
    const ours = await run('product');
    const other = await run('competitor', { counts }, { initial_conditions_hash: 'c'.repeat(64) });
    assert.equal(ours.status, 'completed'); assert.equal(other.status, 'completed');
    assert.notEqual(ours.comparison_identity, other.comparison_identity);
    assert.equal(compareActionRuns(ours, other, review(ours, other)).status, 'not_measured');
    const relabeled = structuredClone(other);
    relabeled.comparison_contract.initial_conditions = ours.comparison_contract.initial_conditions;
    relabeled.comparison_identity = digest(relabeled.comparison_contract);
    assert.equal(compareActionRuns(ours, relabeled, review(ours, other)).status, 'not_measured', 'report relabeling cannot change the preregistration inside the reviewed ledger');
  });
});

test('TENX-02: a 10x aggregate cannot hide 10x worse recovery', async () => {
  await withRuns('J5', async (job, run) => {
    const ownCounts = Object.fromEntries(job.steps.map((stage) => [stage.id, stage.phase === 'recovery' ? 10 : 1]));
    const otherCounts = Object.fromEntries(job.steps.map((stage) => [stage.id, stage.phase === 'recurring' ? 43 : 1]));
    const ours = await run('product', { counts: ownCounts });
    const other = await run('competitor', { counts: otherCounts });
    assert.equal(ours.status, 'completed'); assert.equal(other.status, 'completed');
    const result = compareActionRuns(ours, other, review(ours, other));
    assert.equal(result.components.recurring_rollout.ten_x_actions, true);
    assert.equal(result.components.recovery.ten_x_actions, false);
    assert.equal(result.components.recovery.action_ratio, 0.1);
    assert.equal(result.components.cold_adoption.status, 'not_measured');
    assert.notEqual(result.ten_x_actions, true);
    assert.equal(result.action_ratio, null, 'J5 has no pooled headline');
  });
});

test('TENX-03: one command covers multiple mechanical stages and automatic evidence costs no action', async () => {
  const root = await mkdtemp(join(tmpdir(), 'l13-batched-'));
  try {
    const job = getJob('J5');
    const d = driver(job, 'competitor', { coverage: true });
    const original = d.perform;
    let shared;
    d.perform = async (stage, context) => {
      const { ledger } = context;
      if (stage.id === 'second-update') {
        ledger.stage({ step: stage.id, phase: stage.phase, source: 'git', artifactHash: hash, actionSequences: [shared] });
      } else if (stage.id === 'reconcile-before-retry') {
        ledger.stage({ step: stage.id, phase: stage.phase, source: 'git', artifactHash: hash, actionSequences: [] });
      } else {
        if (stage.id === 'create-prs') shared = ledger.events().length + 1;
        await original(stage, context);
      }
    };
    const result = await runJourney({ jobId: job.id, runId: 'batch', driver: d, registration: registration(job), outputDir: root });
    assert.equal(result.status, 'completed');
    assert.equal(result.measurement.counts.A, 6, '8 configured stages, two covered without new operator actions');
    assert.equal(result.stages.length, 8);
    // Comparator checks must also accept coverage without per-stage A. Use a fresh
    // valid product run rather than relabeling the tamper-evident ledger.
    const own = await runJourney({ jobId: job.id, runId: 'own', driver: driver(job, 'product'), registration: registration(job), outputDir: root });
    const comparison = compareActionRuns(own, result, review(own, result));
    assert.equal(comparison.components.recurring_rollout.status, 'measured_actions_only');
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('J5 cold comparison requires all three components; opposing phase results never pool', async () => {
  await withRuns('J5', async (job, run) => {
    const ours = await run('product', {}, { cohort: 'cold' });
    const tenfold = Object.fromEntries(job.steps.map((stage) => [stage.id, 10]));
    const other = await run('competitor', { counts: tenfold }, { cohort: 'cold' });
    const result = compareActionRuns(ours, other, review(ours, other));
    assert.equal(result.status, 'measured_actions_only'); assert.equal(result.ten_x_actions, true);
    assert.equal(result.action_ratio, null);
    assert.deepEqual(Object.keys(result.components), ['cold_adoption', 'recurring_rollout', 'recovery']);
    assert.ok(Object.values(result.components).every((component) => component.action_ratio === 10));
    const recoveryWorse = await run('competitor', { id: 'worse', counts: { ...tenfold, 'reconcile-before-retry': 1, 'reverse-plan': 1 } }, { cohort: 'cold' });
    assert.equal(compareActionRuns(ours, recoveryWorse, review(ours, recoveryWorse)).ten_x_actions, false);
    const incomplete = structuredClone(other); incomplete.stages = incomplete.stages.filter((stage) => stage.id !== 'reverse-plan');
    assert.equal(compareActionRuns(ours, incomplete, review(ours, other)).status, 'not_measured');
  });
});

test('automation cannot replace required human decisions or move recovery actions into recurring work', async () => {
  const root = await mkdtemp(join(tmpdir(), 'l13-human-'));
  try {
    const job = getJob('J5');
    for (const bad of ['human', 'phase']) {
      const d = driver(job, 'product', { coverage: true }); const original = d.perform;
      d.perform = async (stage, context) => {
        if (stage.id === 'authorize-exact-patches' && bad === 'human') {
          context.ledger.stage({ step: stage.id, phase: stage.phase, source: 'api', artifactHash: hash, actionSequences: [] });
        } else await original(bad === 'phase' && stage.id === 'reverse-plan' ? { ...stage, phase: 'recurring' } : stage, context);
      };
      const result = await runJourney({ jobId: job.id, runId: bad, driver: d, registration: registration(job), outputDir: root });
      assert.equal(result.status, 'failed');
    }
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('comparison CLI saves machine-readable components and fails unmeasured or below-bar gates', async () => {
  const root = await mkdtemp(join(tmpdir(), 'l13-cli-'));
  try {
    await withRuns('J5', async (job, run) => {
      const ours = await run('product'); const other = await run('competitor');
      const inputs = [ours, other, review(ours, other)];
      const paths = inputs.map((_, i) => join(root, `${i}.json`));
      await Promise.all(inputs.map((value, i) => writeFile(paths[i], JSON.stringify(value))));
      const output = join(root, 'comparison.json');
      assert.equal(await main(['compare', ...paths, output]), 1);
      const result = JSON.parse(await readFile(output, 'utf8'));
      assert.equal(result.job, 'J5'); assert.equal(result.ten_x_actions, null);
      assert.equal(result.components.recovery.ten_x_actions, false);
      await assert.rejects(main(['compare', ...paths, output]), /EEXIST/, 'failed comparisons must not be overwritten');
    });
  } finally { await rm(root, { recursive: true, force: true }); }
});

for (const [later, originalStage] of [['renewed-review', 'review-packet'], ['authorized-release', 'record-decision']]) {
  test(`EVIDENCE-01: ${later} cannot reuse ${originalStage} in a journey or imported comparison`, async () => {
    const root = await mkdtemp(join(tmpdir(), 'l13-decision-'));
    const job = getJob('J4');
    try {
      const d = driver(job, 'product', { coverage: true });
      const perform = d.perform;
      d.perform = async (stage, context) => {
        if (stage.id !== later) return perform(stage, context);
        const original = context.ledger.events().find((event) => event.type === 'start' && event.step === originalStage);
        context.ledger.stage({ step: stage.id, phase: stage.phase, source: 'api', artifactHash: hash,
          actionSequences: [original.sequence], decision });
      };
      const rejected = await runJourney({ jobId: 'J4', runId: 'reused', driver: d,
        registration: registration(job), outputDir: root });
      assert.equal(rejected.status, 'failed');
      assert.equal(rejected.stages.at(-1).id, later);
      const run = (mode) => runJourney({ jobId: 'J4', runId: mode, driver: driver(job, mode, { coverage: true }),
        registration: registration(job), outputDir: root });
      const ours = await run('product'), other = await run('competitor');
      assert.equal(ours.status, 'completed'); assert.equal(other.status, 'completed');
      assert.equal(compareActionRuns(ours, other, review(ours, other)).action_ratio, 1);
      const forged = structuredClone(ours);
      const original = forged.events.find((event) => event.type === 'start' && event.step === originalStage);
      const removed = forged.events.find((event) => event.type === 'start' && event.step === later);
      const receipt = forged.events.find((event) => event.type === 'stage' && event.step === later);
      receipt.action_sequences = [original.sequence];
      forged.events = forged.events.filter((event) => event.sequence !== removed.sequence && event.start_sequence !== removed.sequence);
      const indexes = new Map(forged.events.map((event, index) => [event.sequence, index + 1]));
      let previous = '0'.repeat(64);
      for (const event of forged.events) {
        event.sequence = indexes.get(event.sequence);
        if (event.start_sequence) event.start_sequence = indexes.get(event.start_sequence);
        if (event.action_sequences) event.action_sequences = event.action_sequences.map((sequence) => indexes.get(sequence));
        event.previous = previous;
        const { hash: ignored, ...body } = event;
        event.hash = digest(body); previous = event.hash;
      }
      assert.equal(compareActionRuns(forged, other, review(forged, other)).ten_x_actions, null);
    } finally { await rm(root, { recursive: true, force: true }); }
  });
}

test('human decisions must bind the independently observed packet and preconditions', async () => {
  const root = await mkdtemp(join(tmpdir(), 'l13-packet-'));
  const job = getJob('J4');
  try {
    for (const field of ['packet_sha256', 'preconditions_sha256']) {
      const d = driver(job, 'product');
      const perform = d.perform;
      d.perform = async (stage, context) => {
        if (stage.id !== 'renewed-review') return perform(stage, context);
        await context.ledger.measure({ dimension: 'A', primitive: 'review', actor: 'operator', step: stage.id,
          phase: stage.phase, target: 'packet', items: ['item'], decision }, () => {});
        context.ledger.stage({ step: stage.id, phase: stage.phase, source: 'api', artifactHash: hash,
          decision: { ...decision, [field]: 'c'.repeat(64) } });
      };
      const result = await runJourney({ jobId: 'J4', runId: field, driver: d, registration: registration(job), outputDir: root });
      assert.equal(result.status, 'failed');
      assert.equal(result.stages.at(-1).id, 'renewed-review');
    }
  } finally { await rm(root, { recursive: true, force: true }); }
});
