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
    preregistration_hash: digest(registration), comparison_identity: digest({ job: jobId, cohort: registration.cohort,
      input: registration.customer_input_hash, safety: registration.safety_requirements_hash,
      boundary: registration.complete_job_boundary_hash, population: registration.population }),
    stages: [], checks: [], fault_ids: job.fault_ids, teardown: 'unknown', observations_complete: false,
    claim: 'Not qualified', error_code: null };
  const work = async () => {
    for (const stage of job.steps) {
      if (registration.cohort === 'configured' && stage.phase === 'cold') continue;
      if (controller.signal.aborted) throw new Error('journey_deadline');
      const entry = { id: stage.id, status: 'running' };
      report.stages.push(entry);
      try {
        await driver.perform(structuredClone(stage), { ledger, signal: controller.signal });
        if (controller.signal.aborted) throw new Error('journey_deadline');
        const events = ledger.events();
        requireValue(events.some((e) => e.type === 'start' && e.dimension === 'A' && e.step === stage.id), `Uninstrumented stage ${stage.id}`);
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
    action_ratio: null, ten_x_actions: null, human_time_study: 'not_conducted', claim: 'Not qualified' };
}

export function compareActionRuns(ours, competitor, review) {
  const notMeasured = (reason) => ({ status: 'not_measured', reason, action_ratio: null, ten_x_actions: null });
  if (ours?.status !== 'completed' || competitor?.status !== 'completed') return notMeasured('Both complete jobs must finish, including failed attempts/recovery');
  if (ours.mode !== 'product' || competitor.mode !== 'competitor' || ours.evidence_scope !== 'customer' || competitor.evidence_scope !== 'customer') return notMeasured('Synthetic fixtures and documentation are not customer comparisons');
  if (ours.comparison_identity !== competitor.comparison_identity) return notMeasured('Job inputs, cohort, population, safety or boundary differ');
  if (!review || review.reviewer_id === review.author_id || !review.reviewer_id || !review.author_id || !SHA256.test(review.strongest_configured_route_hash) || !SHA256.test(review.receipt_sha256)) return notMeasured('Independent comparator competence/access review is missing');
  if (!ours.observations_complete || !competitor.observations_complete || ours.teardown !== 'confirmed' || competitor.teardown !== 'confirmed') return notMeasured('Complete independently verified outcomes required');
  if (ours.provenance?.dirty || competitor.provenance?.dirty) return notMeasured('Dirty iteration is not final qualification');
  let own; let other;
  try {
    const validateRun = (run) => {
      validateProvenance(run.provenance);
      const job = getJob(run.job);
      requireValue(['cold', 'configured'].includes(run.cohort), 'Cohort missing');
      const stages = job.steps.filter((stage) => run.cohort === 'cold' || stage.phase !== 'cold');
      exactSet(run.stages.filter((stage) => stage.status === 'completed').map((stage) => stage.id), stages.map((stage) => stage.id), 'complete stage coverage');
      exactSet(run.checks.filter((check) => check.status === 'pass').map((check) => check.id), job.checks.map((check) => check.id), 'complete check coverage');
      requireValue(run.events.every((event) => event.mode === run.mode && event.job === run.job && event.run_id === run.run_id), 'Ledger provenance mismatch');
      exactSet([...new Set(run.events.filter((event) => event.type === 'fault').map((event) => event.id))], job.fault_ids, 'fault coverage');
      const outcomes = run.events.filter((event) => event.type === 'outcome');
      exactSet(outcomes.map((event) => event.id), job.checks.map((check) => check.id), 'outcome ledger');
      requireValue(outcomes.every((event) => event.status === 'pass'), 'Failed/unknown outcome');
      for (const stage of stages) requireValue(run.events.some((event) => event.type === 'start' && event.dimension === 'A' && event.step === stage.id), 'Uninstrumented stage');
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
  const A = own.counts.A; const B = other.counts.A;
  if (A === 0 || B === 0) return notMeasured('Zero action baseline');
  return { status: 'measured_actions_only', ours: own.counts, competitor: other.counts,
    action_ratio: B / A, ten_x_actions: 10 * A <= B,
    claim: 'Action-count comparison only, not a human-time or overall product advantage',
    human_study_required: true, product_acceptance: 'blocked_pending_human_time_success_comprehension_and_cost' };
}

export function baselineReport(documentedBaselines) {
  return { schema: 1, protocol: 'context-hub-complete-jobs-r3.1', generated_at: new Date().toISOString(),
    evidence_kind: 'documentation_and_unavailable_product_runs', documented_baselines: documentedBaselines,
    jobs: JOBS.map((job) => unavailableJob(job, job.phase === 'E'
      ? 'E-only campaign; Checkpoint P and authorized real-GitHub conformance not supplied'
      : 'F12 interfaces, qualified public-interface driver, customer environment and independent comparator access not supplied')),
    checkpoint_P: 'blocked', general_10x_launch: 'not_established', human_study: 'not_conducted' };
}
