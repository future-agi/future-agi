import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { seedHarnessEnvironment } from '../../lib/simulate-seed';

// The readiness screen is TH-8877; until it ships this flow drives the API it will
// read. Paths from futureagi/simulate/views/harness_environment.py
// (HarnessEnvironmentViewSet: capabilities, capabilities/recheck, run).
const ENVIRONMENTS = '/simulate/api/harness-environments';

// The measured rows, in catalogue order, and what a certificate with every check
// passed says of each once the contract routes issue_refund to production; cancel
// reads the agent's last Stop, and a seeded agent was never stopped
// (futureagi/simulate/services/harness_readiness/rules.py;
// test_harness_readiness.py::TestDoneWhen pins the same report).
const MEASURED = {
  start: 'supported',
  message: 'supported',
  tool_discovery: 'supported',
  tool_routing: 'blocked',
  state: 'supported',
  reset: 'supported',
  cancel: 'untested',
};
const COUNTS = { blocked: 1, stale: 0, untested: 9, partial: 0, unavailable: 1, supported: 5 };

interface Ref { id: string; label: string }
interface Row { id: string; status: string; measured: boolean; reason_code: string | null;
                fix: { target: string } | null }
interface Validation { state: string; job_id: string | null; purpose: string | null;
                      error: { code: string; reason: string } | null }
// The run gate's refusal body (futureagi/simulate/services/harness_versioning/validation.py,
// require_pair_runnable).
interface Refusal { error: string; message: string; retryable: boolean; retry_after?: number;
                    check?: { job_id: string; deadline_at: string };
                    reason_code?: string; reason?: string }
interface Report {
  pairing: { agent_version: Ref; environment_version: Ref; suite_version: Ref; evidence: string };
  validation: Validation;
  answer: { kind: string; worst: string };
  rows: Row[];
  counts: Record<string, number>;
}

test('SIM-E2E-003: a pairing reads its readiness, a re-check validates it, and a run waits for the check', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SIM-E2E-003', area: 'simulate',
    userGoal: 'A developer reads whether an agent, environment and suite pairing can run and why, '
      + 'asks for an unchecked pairing to be checked, and is told to wait rather than run it while the check runs',
    steps: ['seed a built, certified Environment whose contract routes issue_refund to production, '
              + 'and a built one whose pairing was never certified (e2e/lib/harness_environment_seed.py)',
            'read the certified pairing\'s readiness (GET capabilities)',
            'read the uncertified pairing\'s readiness',
            'ask for it to be checked again (POST capabilities/recheck)',
            'read its readiness while the check runs',
            'try to run a scenario on that pairing'],
    backendChecks: ['the certified pairing names the seeded default versions, reads its evidence from the pair certificate, '
                      + 'and its measured rows are exactly five Supported and tool routing Blocked as production_only, answer blocked',
                    'the uncertified pairing reads not_run and unchecked',
                    'recheck answers 202 with a validating recheck validation, and the report then reads validating with that job',
                    'the validation is a queued certify-only job of this Environment pinned to its pending bundle, '
                      + 'and the default agent did not move',
                    'the run answers 409 pair_validating, retryable, with when to retry and the check it waits on, '
                      + 'and creates no run job'],
  }),
}, async ({ actor, probe }, testInfo) => {
  // Two seeds through the backend container (Django start-up each) and six API calls.
  test.setTimeout(120_000);
  const scope = { organizationId: actor.organizationId, workspaceId: actor.workspaceId };
  const certified = seedHarnessEnvironment({ ...scope, productionOnly: 'issue_refund' });
  const unchecked = seedHarnessEnvironment({ ...scope, certified: false });
  await testInfo.attach('seeded', {
    contentType: 'application/json',
    body: JSON.stringify({ ...scope, certified, unchecked }),
  });

  await test.step('api: the certified pairing reads its readiness', async () => {
    const report = await actor.api.get<Report>(`${ENVIRONMENTS}/${certified.environmentId}/capabilities/`);
    expect(report.pairing).toEqual({
      agent_version: expect.objectContaining({ id: certified.agentVersionId }),
      environment_version: expect.objectContaining({ id: certified.environmentVersionId }),
      suite_version: expect.objectContaining({ id: certified.suiteVersionId }),
      evidence: 'pair_certificate',
      // The seed stores no reading of the agent version's tools.
      agent_tools: { read: false, count: 0 },
      evidence_unreadable: false,
    });
    expect(report.validation.state).toBe('ready');
    expect(Object.fromEntries(report.rows.filter((row) => row.measured).map((row) => [row.id, row.status])))
      .toEqual(MEASURED);
    const routing = report.rows.find((row) => row.id === 'tool_routing');
    expect([routing?.reason_code, routing?.fix?.target]).toEqual(['production_only', 'issue_refund']);
    expect(report.answer).toMatchObject({ kind: 'blocked', worst: 'blocked' });
    expect(report.counts).toEqual(COUNTS);
  });

  const capabilities = `${ENVIRONMENTS}/${unchecked.environmentId}/capabilities/`;
  await test.step('api: the uncertified pairing has not been checked', async () => {
    const report = await actor.api.get<Report>(capabilities);
    expect([report.validation.state, report.answer.kind]).toEqual(['not_run', 'unchecked']);
  });

  const recheck = await test.step('api: a re-check starts a validation', async () => {
    const response = await actor.api.request<{ validation: Validation }>('post', `${capabilities}recheck/`, { data: {} });
    expect(response.status).toBe(202);
    expect(response.body.validation).toMatchObject({ state: 'validating', purpose: 'recheck' });
    expect(response.body.validation.job_id).toMatch(/^[0-9a-f-]{36}$/);
    return response.body.validation;
  });
  await testInfo.attach('validation', { contentType: 'application/json', body: JSON.stringify(recheck) });

  await test.step('api: the report reads validating with that job', async () => {
    const report = await actor.api.get<Report>(capabilities);
    expect([report.validation.state, report.validation.job_id, report.answer.kind])
      .toEqual(['validating', recheck.job_id, 'validating']);
  });

  await test.step('storage: a queued certify-only job checks the pending bundle; the default did not move', async () => {
    const [job] = await probe.pg<{ state: string; environment_id: string; agent_version_id: string;
                                   certify_only: string; purpose: string; bundle_state: string }>(
      `SELECT j.state, j.environment_id::text, j.agent_version_id::text,
              j.payload->'metadata'->>'certify_only' AS certify_only,
              j.payload->'metadata'->>'validation_purpose' AS purpose, b.state AS bundle_state
         FROM simulate_hosted_harness_job j
         JOIN simulate_harness_assembled_bundle b ON b.id = j.assembled_bundle_id
        WHERE j.id = $1`, [recheck.job_id]);
    expect(job).toEqual({ state: 'queued', environment_id: unchecked.environmentId,
      agent_version_id: unchecked.agentVersionId, certify_only: 'true', purpose: 'recheck', bundle_state: 'pending' });
    const [environment] = await probe.pg<{ agent_version_id: string }>(
      'SELECT agent_version_id::text FROM simulate_hosted_harness_job WHERE id = $1', [unchecked.environmentId]);
    expect(environment.agent_version_id).toBe(unchecked.agentVersionId);
  });

  await test.step('api: a run of the pairing waits for the check', async () => {
    const response = await actor.api.request<Refusal>('post', `${ENVIRONMENTS}/${unchecked.environmentId}/run/`, {
      data: { scenario_ids: unchecked.scenarioKeys.slice(0, 1), trials: 1 },
      headers: { 'Idempotency-Key': `e2e-sim3-${testInfo.workerIndex}-${Date.now().toString(36)}` },
    });
    expect([response.status, response.body.error, response.body.retryable]).toEqual([409, 'pair_validating', true]);
    expect(response.body.retry_after).toBeGreaterThan(0);
    expect(response.body.check?.job_id).toBe(recheck.job_id);
    const [runs] = await probe.pg<{ n: string }>(
      `SELECT count(*) AS n FROM simulate_hosted_harness_job
        WHERE environment_id = $1 AND (payload->'metadata'->>'certify_only') IS DISTINCT FROM 'true'`,
      [unchecked.environmentId]);
    expect(Number(runs.n)).toBe(0);
  });
});

test('SIM-E2E-006: a pairing whose check failed refuses runs with a plain reason, and a re-check starts afresh', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SIM-E2E-006', area: 'simulate',
    userGoal: 'A developer whose agent failed its check with an environment is told why when a run is refused, '
      + 'without internal detail, and can check the pairing again',
    steps: ['seed a built Environment whose pairing\'s last check failed on issue_refund '
              + '(e2e/lib/harness_environment_seed.py, failed_check)',
            'read the pairing\'s readiness (GET capabilities)',
            'try to run a scenario on that pairing',
            'ask for it to be checked again (POST capabilities/recheck)'],
    backendChecks: ['the report reads validation failed with a plain reason naming issue_refund, answer blocked, '
                      + 'and tool routing Blocked on the failed action',
                    'the run answers 409 pair_failed, not retryable, with the reason code and a reason naming issue_refund, '
                      + 'carries no internal text, and creates no run job',
                    'recheck answers 202 with a new validating job, and the bundle is pending again'],
  }),
}, async ({ actor, probe }, testInfo) => {
  // One seed through the backend container (Django start-up) and three API calls.
  test.setTimeout(90_000);
  const scope = { organizationId: actor.organizationId, workspaceId: actor.workspaceId };
  const failed = seedHarnessEnvironment({ ...scope, certified: false, failedCheck: 'issue_refund' });
  await testInfo.attach('seeded', { contentType: 'application/json', body: JSON.stringify({ ...scope, failed }) });
  const capabilities = `${ENVIRONMENTS}/${failed.environmentId}/capabilities/`;

  await test.step('api: the report says the check failed and why', async () => {
    const report = await actor.api.get<Report>(capabilities);
    expect(report.validation.state).toBe('failed');
    expect(report.validation.error?.reason).toContain('issue_refund');
    expect(report.answer.kind).toBe('blocked');
    const routing = report.rows.find((row) => row.id === 'tool_routing');
    expect([routing?.status, routing?.reason_code]).toEqual(['blocked', 'action_failed']);
  });

  await test.step('api: a run is refused with the reason, and no run job is made', async () => {
    const response = await actor.api.request<Refusal>('post', `${ENVIRONMENTS}/${failed.environmentId}/run/`, {
      data: { scenario_ids: failed.scenarioKeys.slice(0, 1), trials: 1 },
      headers: { 'Idempotency-Key': `e2e-sim6-${testInfo.workerIndex}-${Date.now().toString(36)}` },
    });
    expect([response.status, response.body.error, response.body.retryable]).toEqual([409, 'pair_failed', false]);
    expect(response.body.reason_code).toBe('authoring_runtime_validation_failed');
    expect(response.body.reason).toContain('issue_refund');
    expect(response.body.message).not.toContain('Traceback');
    const [runs] = await probe.pg<{ n: string }>(
      'SELECT count(*) AS n FROM simulate_hosted_harness_job WHERE environment_id = $1', [failed.environmentId]);
    expect(Number(runs.n)).toBe(0);
  });

  await test.step('api: a re-check starts a fresh check of the reset bundle', async () => {
    const response = await actor.api.request<{ validation: Validation }>('post', `${capabilities}recheck/`, { data: {} });
    expect(response.status).toBe(202);
    expect(response.body.validation).toMatchObject({ state: 'validating', purpose: 'recheck' });
    const [bundle] = await probe.pg<{ state: string; checks: string }>(
      `SELECT b.state,
              (SELECT count(*) FROM simulate_hosted_harness_job j
                WHERE j.assembled_bundle_id = b.id AND j.payload->'metadata'->>'certify_only' = 'true')::text AS checks
         FROM simulate_hosted_harness_job j0
         JOIN simulate_harness_assembled_bundle b ON b.id = j0.assembled_bundle_id
        WHERE j0.id = $1`, [response.body.validation.job_id]);
    expect(bundle).toEqual({ state: 'pending', checks: '1' });
  });
});
