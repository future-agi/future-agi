import { mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { ActionLedger, summarizeLedger } from '../../lib/skills-r3/ledger.mjs';
import { assertObservation, OracleFailure } from '../../lib/skills-r3/oracles.mjs';
import { COMMIT, SHA256, canonical, digest, exactSet, requireValue, token } from '../../lib/skills-r3/canonical.mjs';
import { JOBS, getJob } from './jobs.mjs';

export function validateProvenance(value) {
  requireValue(value && ['gcp', 'e2b'].includes(value.environment), 'Approved cloud provenance required');
  requireValue(value.environment !== 'gcp' || value.project === 'fagi-omega-staging', 'Wrong GCP project');
  for (const key of ['product_base', 'product_head', 'product_tree', 'sdk_commit']) requireValue(COMMIT.test(value[key]), `Missing real ${key}`);
  for (const key of ['fixture_hash', 'runtime_digest']) requireValue(SHA256.test(value[key]), `Missing ${key}`);
  for (const key of ['package_hashes', 'lockfile_hashes']) requireValue(Array.isArray(value[key]) && value[key].length > 0 && value[key].every((hash) => SHA256.test(hash)), `Missing ${key}`);
  requireValue(typeof value.dirty === 'boolean', 'Dirty/final provenance must be explicit');
}

function validateRegistration(job, registration) {
  requireValue(registration?.schema === 1 && registration.job === job.id, 'Preregistration required');
  requireValue(['cold', 'configured'].includes(registration.cohort), 'Choose cold/configured cohort');
  requireValue(['synthetic', 'customer'].includes(registration.evidence_scope), 'Declare input provenance');
  for (const key of ['customer_input_hash', 'initial_conditions_hash', 'safety_requirements_hash', 'complete_job_boundary_hash']) requireValue(SHA256.test(registration[key]), `Missing ${key}`);
  requireValue(canonical(registration.population) === canonical(job.population), 'Population narrowed from complete job');
  exactSet(Object.keys(registration.expected_observations ?? {}), job.checks.map((c) => c.id), 'preregistered checks');
  exactSet(registration.fault_ids, job.fault_ids, 'preregistered faults');
  requireValue(Object.values(registration.expected_observations).every((value) => value !== undefined && value !== null), 'Unobserved expectations cannot pass');
}

export function comparisonContract(job, registration) {
  return { schema: 1, job: job.id, cohort: registration.cohort,
    input: registration.customer_input_hash, initial_conditions: registration.initial_conditions_hash,
    safety: registration.safety_requirements_hash, boundary: registration.complete_job_boundary_hash,
    population: structuredClone(registration.population) };
}

function validateStageCoverage(stage, events) {
  const completed = new Set(events.filter((event) => event.type === 'end' && event.status === 'completed').map((event) => event.start_sequence));
  const receipt = events.find((event) => event.type === 'stage' && event.step === stage.id);
  const actions = events.filter((event) => event.type === 'start' && event.dimension === 'A' && completed.has(event.sequence)
    && (event.step === stage.id || receipt?.action_sequences.includes(event.sequence)));
  requireValue(receipt || actions.length > 0, `Uninstrumented stage ${stage.id}`);
  requireValue(!receipt || receipt.phase === stage.phase, 'Stage receipt phase differs');
  // A mechanical stage may be automatic; an actual human review or authorization may not.
  for (const primitive of stage.human_actions ?? []) requireValue(actions.some((action) => action.primitive === primitive), `Missing human ${primitive} for ${stage.id}`);
}

function validateAccounting(job, cohort, events) {
  const stages = job.steps.filter((stage) => cohort === 'cold' || stage.phase !== 'cold');
  for (const event of events.filter((item) => item.type === 'start' || item.type === 'stage')) {
    const stage = stages.find((item) => item.id === event.step);
    requireValue(stage && event.phase === stage.phase, 'Action/stage is outside the preregistered cohort or phase');
  }
  for (const stage of stages) validateStageCoverage(stage, events);
}

/** Performs ordered complete-job stages; never infers A from the number of stages. */
export async function runJourney({ jobId, runId, driver, registration, outputDir, deadlineMs = 1_800_000 }) {
  const job = getJob(jobId);
  token(runId, 'run');
  requireValue(Number.isInteger(deadlineMs) && deadlineMs > 0 && deadlineMs <= 3_600_000, 'Explicit bounded deadline required');
  if (!driver) return unavailableJob(job, 'F12 public-interface driver and qualified topology are unavailable');
  validateRegistration(job, registration);
  validateProvenance(driver.provenance);
  requireValue(['product', 'competitor', 'selftest'].includes(driver.mode), 'Invalid driver mode');
  requireValue(typeof driver.perform === 'function' && typeof driver.observe === 'function' && typeof driver.teardown === 'function', 'Complete driver required');
  if (job.phase === 'E' && !SHA256.test(registration.checkpoint_P_receipt)) return unavailableJob(job, 'Campaign execution is deferred until Checkpoint P');
  await mkdir(outputDir, { recursive: true });
  const ledger = new ActionLedger({ runId, job: jobId, mode: driver.mode, path: join(outputDir, `${runId}.jsonl`) });
  const controller = new AbortController();
  let timeout;
  const expired = new Promise((_, reject) => {
    timeout = setTimeout(() => { controller.abort(); reject(new Error('journey_deadline')); }, deadlineMs);
  });
  const report = { schema: 1, job: jobId, run_id: runId, mode: driver.mode, cohort: registration.cohort,
    evidence_scope: registration.evidence_scope, status: 'running', provenance: structuredClone(driver.provenance),
    preregistration_hash: digest(registration), comparison_contract: comparisonContract(job, registration),
    comparison_identity: digest(comparisonContract(job, registration)),
    stages: [], checks: [], fault_ids: job.fault_ids, teardown: 'unknown', observations_complete: false,
    claim: 'Not qualified', error_code: null };
  ledger.registration({ comparisonIdentity: report.comparison_identity, preregistrationHash: report.preregistration_hash });
  const work = async () => {
    for (const stage of job.steps) {
      if (registration.cohort === 'configured' && stage.phase === 'cold') continue;
      if (controller.signal.aborted) throw new Error('journey_deadline');
      const entry = { id: stage.id, status: 'running' };
      report.stages.push(entry);
      try {
        await driver.perform(structuredClone(stage), { ledger, signal: controller.signal });
        if (controller.signal.aborted) throw new Error('journey_deadline');
        validateStageCoverage(stage, ledger.events());
        entry.status = 'completed';
      } catch (error) { entry.status = 'failed'; throw error; }
    }
    for (const check of job.checks) {
      if (controller.signal.aborted) throw new Error('journey_deadline');
      const observation = await driver.observe(structuredClone(check), { signal: controller.signal });
      if (controller.signal.aborted) throw new Error('journey_deadline');
      try {
        assertObservation(check, registration.expected_observations[check.id], observation);
        report.checks.push({ id: check.id, status: 'pass', source: observation.source, artifact_sha256: observation.artifact_sha256 });
        ledger.outcome(check.id, 'pass', observation.artifact_sha256);
      } catch (error) {
        report.checks.push({ id: check.id, status: 'fail' });
        throw error;
      }
    }
    validateAccounting(job, registration.cohort, ledger.events());
    exactSet([...new Set(ledger.events().filter((e) => e.type === 'fault').map((e) => e.id))], job.fault_ids, 'exercised faults');
    report.observations_complete = true;
    report.status = 'completed';
  };
  try { await Promise.race([work(), expired]); }
  catch (error) {
    report.status = 'failed';
    report.error_code = error instanceof OracleFailure ? `oracle:${error.checkId}` : 'journey_failed';
  } finally {
    clearTimeout(timeout);
    controller.abort();
    // Teardown must return confirmed resource destruction, not just a successful cancel request.
    // It has its own deadline and leaves uncertainty visible instead of declaring a green timeout.
    let teardownTimer;
    try {
      const result = await Promise.race([driver.teardown(), new Promise((_, reject) => {
        teardownTimer = setTimeout(() => reject(new Error('teardown_deadline')), 30_000);
      })]);
      report.teardown = result?.qualified === true ? 'confirmed' : 'unknown';
    } catch { report.teardown = 'unknown'; }
    finally { clearTimeout(teardownTimer); }
    ledger.close();
    report.events = ledger.events();
    report.measurement = summarizeLedger(report.events);
    if (report.teardown !== 'confirmed' || !report.measurement.integrity.complete) report.status = 'failed';
    report.claim = report.status === 'completed' && driver.mode !== 'selftest'
      ? 'Observed action counts only; competitive and human study gates remain separate' : 'Not qualified';
    // Exclusive report creation protects slow/failed runs from accidental replacement.
    await writeFile(join(outputDir, `${runId}.json`), `${JSON.stringify(report, null, 2)}\n`, { flag: 'wx', mode: 0o600 });
  }
  return report;
}

export function unavailableJob(job, reason) {
  return { schema: 1, job: job.id, phase: job.phase, status: 'not_measured', reason,
    operator_actions: null, screen_transitions: null, handoffs: null, reconciliations: null,
    action_ratio: null, ten_x_actions: null, human_time_study: 'not_conducted', claim: 'Not qualified',
    ...(job.id === 'J5' ? { components: Object.fromEntries(['cold_adoption', 'recurring_rollout', 'recovery']
      .map((name) => [name, { status: 'not_measured', action_ratio: null, ten_x_actions: null }])) } : {}) };
}

export function compareActionRuns(ours, competitor, review) {
  const notMeasured = (reason) => ({ status: 'not_measured', reason, action_ratio: null, ten_x_actions: null });
  if (ours?.status !== 'completed' || competitor?.status !== 'completed') return notMeasured('Both complete jobs must finish, including failed attempts/recovery');
  if (ours.mode !== 'product' || competitor.mode !== 'competitor' || ours.evidence_scope !== 'customer' || competitor.evidence_scope !== 'customer') return notMeasured('Synthetic fixtures and documentation are not customer comparisons');
  if (ours.comparison_identity !== competitor.comparison_identity) return notMeasured('Job inputs, initial conditions, cohort, population, safety or boundary differ');
  if (!review || review.reviewer_id === review.author_id || !review.reviewer_id || !review.author_id || !SHA256.test(review.strongest_configured_route_hash) || !SHA256.test(review.receipt_sha256)) return notMeasured('Independent comparator competence/access review is missing');
  if (!ours.observations_complete || !competitor.observations_complete || ours.teardown !== 'confirmed' || competitor.teardown !== 'confirmed') return notMeasured('Complete independently verified outcomes required');
  if (ours.provenance?.dirty || competitor.provenance?.dirty) return notMeasured('Dirty iteration is not final qualification');
  let own; let other;
  try {
    const validateRun = (run) => {
      validateProvenance(run.provenance);
      const job = getJob(run.job);
      requireValue(['cold', 'configured'].includes(run.cohort), 'Cohort missing');
      const contract = run.comparison_contract;
      requireValue(contract?.schema === 1 && contract.job === job.id && contract.cohort === run.cohort, 'Comparison contract missing');
      exactSet(Object.keys(contract), ['schema', 'job', 'cohort', 'input', 'initial_conditions', 'safety', 'boundary', 'population'], 'comparison contract');
      for (const key of ['input', 'initial_conditions', 'safety', 'boundary']) requireValue(SHA256.test(contract[key]), 'Comparison input missing');
      requireValue(canonical(contract.population) === canonical(job.population) && digest(contract) === run.comparison_identity, 'Comparison identity differs');
      const binding = run.events[0];
      requireValue(binding?.type === 'registration' && binding.comparison_identity === run.comparison_identity
        && binding.preregistration_hash === run.preregistration_hash, 'Reviewer-bound registration differs');
      const stages = job.steps.filter((stage) => run.cohort === 'cold' || stage.phase !== 'cold');
      exactSet(run.stages.filter((stage) => stage.status === 'completed').map((stage) => stage.id), stages.map((stage) => stage.id), 'complete stage coverage');
      exactSet(run.checks.filter((check) => check.status === 'pass').map((check) => check.id), job.checks.map((check) => check.id), 'complete check coverage');
      requireValue(run.events.every((event) => event.mode === run.mode && event.job === run.job && event.run_id === run.run_id), 'Ledger provenance mismatch');
      exactSet([...new Set(run.events.filter((event) => event.type === 'fault').map((event) => event.id))], job.fault_ids, 'fault coverage');
      const outcomes = run.events.filter((event) => event.type === 'outcome');
      exactSet(outcomes.map((event) => event.id), job.checks.map((check) => check.id), 'outcome ledger');
      requireValue(outcomes.every((event) => event.status === 'pass'), 'Failed/unknown outcome');
      validateAccounting(job, run.cohort, run.events);
      for (const check of run.checks) {
        const definition = job.checks.find((item) => item.id === check.id);
        requireValue(definition.sources.includes(check.source) && SHA256.test(check.artifact_sha256), 'Invalid independent evidence');
        requireValue(outcomes.find((event) => event.id === check.id).artifact_hash === check.artifact_sha256, 'Evidence identity differs');
      }
      return summarizeLedger(run.events);
    };
    own = validateRun(ours); other = validateRun(competitor);
    requireValue(review.ours_ledger_hash === own.integrity.hash && review.competitor_ledger_hash === other.integrity.hash, 'Reviewer receipt not bound to measured ledgers');
  } catch { return notMeasured('Missing, changed or invalid complete-job evidence'); }
  if (!own.integrity.complete || !other.integrity.complete) return notMeasured('Incomplete journal');
  const compareCounts = (a, b) => a.A === 0 || b.A === 0
    ? { ...notMeasured('Zero action baseline'), ours: a, competitor: b }
    : { status: 'measured_actions_only', ours: a, competitor: b, action_ratio: b.A / a.A, ten_x_actions: 10 * a.A <= b.A };
  const caveat = { claim: 'Action-count comparison only, not a human-time or overall product advantage',
    human_study_required: true, product_acceptance: 'blocked_pending_human_time_success_comprehension_and_cost' };
  if (ours.job === 'J5') {
    // These are three distinct complete-job measurements, never a pooled action ratio.
    const components = Object.fromEntries([['cold_adoption', 'cold'], ['recurring_rollout', 'recurring'], ['recovery', 'recovery']]
      .map(([name, phase]) => [name, phase === 'cold' && ours.cohort === 'configured'
        ? notMeasured('Cold adoption is outside the configured cohort') : compareCounts(own.phases[phase], other.phases[phase])]));
    const complete = Object.values(components).every((value) => value.status === 'measured_actions_only');
    return { status: complete ? 'measured_actions_only' : 'partially_measured_actions_only', components,
      ours: own.counts, competitor: other.counts, action_ratio: null,
      ten_x_actions: complete ? Object.values(components).every((value) => value.ten_x_actions) : null, ...caveat };
  }
  return { ...compareCounts(own.counts, other.counts), ...caveat };
}

export function baselineReport(documentedBaselines) {
  return { schema: 1, protocol: 'context-hub-complete-jobs-r3.2', generated_at: new Date().toISOString(),
    evidence_kind: 'documentation_and_unavailable_product_runs', documented_baselines: documentedBaselines,
    jobs: JOBS.map((job) => unavailableJob(job, job.phase === 'E'
      ? 'E-only campaign; Checkpoint P and authorized real-GitHub conformance not supplied'
      : 'F12b consumption/auth interfaces exist; complete authoring/review/native/session public-interface journeys, released packages, customer environment and independent comparator access remain unavailable')),
    checkpoint_P: 'blocked', general_10x_launch: 'not_established', human_study: 'not_conducted' };
}
