import { randomBytes } from 'node:crypto';
import { request } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { ApiError } from '../../lib/api-client';
import type { ApiClient } from '../../lib/api-client';
import { sendTrace } from '../../lib/otlp';
import { POLL } from '../../lib/state-probe';
import type { StateProbe } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';
import { provisionActor } from '../../lib/provisioning';
import { admitToProduct } from '../../lib/rollout-seed';

// The Deployments tab's call (SPEC 5.3; frontend sections/omega/shared/observeClient.js).
const ensurePath = (deploymentId: string) => `/tracer/omega/deployments/${deploymentId}/observe/`;
// What Observe -> Alerts and the span table list (endpoints the UI already calls).
const MONITOR_LIST_PATH = '/tracer/user-alerts/list_monitors/';
const SPAN_LIST_PATH = '/tracer/observation-span/list_spans_observe/';

interface ObserveAlert { id: string; kind: string; name: string; metricType: string }
interface ObserveResult {
  projectId: string; projectName: string; alerts: ObserveAlert[];
  alertsDeferred: null | { code: string; message: string };
}
interface Envelope { status: boolean; result: ObserveResult }
interface MonitorRow { id: string; name: string; is_mute: boolean }
interface SpanRow { span_id: string; trace_id: string }

// EE's shapes: `dep-` and 16 hex digits; names match ^[a-z][a-z0-9-]{0,39}$.
const deploymentId = () => `dep-${randomBytes(8).toString('hex')}`;
const deploymentName = (tag: string) => `e2e-${tag}-${randomBytes(3).toString('hex')}`;

async function ensure(api: ApiClient, id: string, name: string): Promise<ObserveResult> {
  const body = await api.post<Envelope>(ensurePath(id), { name });
  expect(body.status).toBe(true);
  return body.result;
}

async function refusal(call: Promise<unknown>): Promise<ApiError> {
  const error = await call.then(() => null, (e: unknown) => e);
  expect(error).toBeInstanceOf(ApiError);
  return error as ApiError;
}

/** One live tracer_project row for the name in the org, and the spans of the trace on it. */
async function expectTraceOn(probe: StateProbe, traceId: string, spanCount: number,
                             projectName: string, organizationId: string): Promise<string> {
  await expect.poll(async () => {
    const rows = await probe.ch<{ n: string }>(
      'SELECT count() AS n FROM spans FINAL WHERE trace_id = {t:String}', { t: traceId });
    return Number(rows[0].n);
  }, POLL.SPAN_VISIBLE).toBe(spanCount);
  const projects = await probe.pg<{ id: string; trace_type: string }>(
    'SELECT id, trace_type FROM tracer_project WHERE name = $1 AND organization_id = $2 AND deleted = false',
    [projectName, organizationId]);
  expect(projects).toHaveLength(1);
  expect(projects[0].trace_type).toBe('observe');
  const spanProjects = await probe.ch<{ project_id: string }>(
    'SELECT DISTINCT project_id FROM spans FINAL WHERE trace_id = {t:String}', { t: traceId });
  expect(spanProjects.map((r) => r.project_id)).toEqual([projects[0].id]);
  return projects[0].id;
}

test('OBS-E2E-060: a deployment\'s Observe project is the one its traces land in, with two default alerts', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'OBS-E2E-060', area: 'observe',
    userGoal: 'A deployed agent\'s traces and its two default alerts are in one Observe project, deploy/<name>, whichever comes first: the Deployments tab\'s ensure or the first trace',
    steps: [
      'ensure before the org is admitted to Deployments: refused as not found',
      'admit the actor\'s org and a second org to Deployments directly through the backend (the rollout\'s staff entry; no operator UI in this harness, see e2e/lib/rollout-seed.ts)',
      'ensure deployment A (POST /tracer/omega/deployments/<id>/observe/), then send its trace through the collector with project_name=deploy/<name>',
      'send deployment B\'s trace first, then ensure it',
      'ensure deployment A again',
      'the second org ensures deployment A\'s id',
      'list the alerts and the spans of A\'s project as Observe does',
      'ensure three more deployments of the actor\'s org (N=3), then send each one\'s trace as the EE relay does: with X-FI-Expected-Organization-Id, -Workspace-Id and -Project-Id naming the project ensure returned',
      'send a trace that asserts another deployment\'s project as its expected target',
    ],
    backendChecks: [
      'the unadmitted ensure is 404 product_not_admitted and writes no tracer_project row',
      'one live tracer_project row per deploy/<name> in the org, trace_type observe, and the CH spans (FINAL) of each trace carry its project_id, which is the projectId ensure returned (both orders)',
      'two live tracer_useralertmonitor rows per project (count_of_errors and daily_tokens_spent), matching the alert ids ensure returned; alertsDeferred is null',
      'one tracer_omega_deployment_observe_link row per deployment id, in the actor\'s workspace, pointing at that project',
      'the second ensure returns the same projectId and alert ids and adds no row',
      'the other org gets 404 with the not-found message, and has no deploy/<name> project',
      'list_monitors?project_id=<A> returns exactly the two defaults, unmuted; list_spans_observe returns both seeded spans',
      'the three deployments get three distinct projects (deploy/<name> each, one link row each); every relay-shaped trace is accepted and its CH spans carry exactly its own deployment\'s project id',
      'the mismatched expected target is refused 403 by the collector and no span of that trace lands anywhere',
    ],
  }),
}, async ({ actor, probe }, testInfo) => {
  // Five SPAN_VISIBLE polls, two org sign-ups and two container execs, past the 120 s default.
  test.setTimeout(360_000);
  const req = await request.newContext();
  const depA = deploymentId();
  const depB = deploymentId();
  const nameA = deploymentName(`w${testInfo.workerIndex}a`);
  const nameB = deploymentName(`w${testInfo.workerIndex}b`);
  const traceAttributes = (id: string, name: string) => ({
    'omega.deployment.id': id, 'omega.deployment.name': name,
    'omega.deployment.environment': 'production', 'omega.run.source': 'api',
  });

  await test.step('rollout wall: not admitted is not found, and writes nothing', async () => {
    const refused = await refusal(actor.api.post(ensurePath(depA), { name: nameA }));
    expect(refused.status).toBe(404);
    expect((refused.body as { code?: string }).code).toBe('product_not_admitted');
    const rows = await probe.pg('SELECT id FROM tracer_project WHERE name = $1', [`deploy/${nameA}`]);
    expect(rows).toHaveLength(0);
  });

  const otherReq = await request.newContext({ baseURL: E2E.apiUrl });
  const other = await provisionActor(otherReq, `w${testInfo.workerIndex}-other`);
  admitToProduct(actor.organizationId, 'deployments');
  admitToProduct(other.organizationId, 'deployments');

  const first = await test.step('A: ensure first, then the trace lands on the ensured project', async () => {
    const result = await ensure(actor.api, depA, nameA);
    expect(result.projectName).toBe(`deploy/${nameA}`);
    expect(result.alerts.map((a) => a.kind)).toEqual(['errors_spike', 'daily_tokens']);
    expect(result.alerts.map((a) => a.metricType)).toEqual(['count_of_errors', 'daily_tokens_spent']);
    expect(result.alertsDeferred).toBeNull();
    const seeded = await sendTrace(req, {
      collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey, secretKey: actor.secretKey,
      projectName: result.projectName, rootName: 'agent.run',
      rootAttributes: traceAttributes(depA, nameA),
    });
    await testInfo.attach('trace-a', { body: JSON.stringify(seeded), contentType: 'application/json' });
    const projectId = await expectTraceOn(probe, seeded.traceId, seeded.spanIds.length,
      result.projectName, actor.organizationId);
    expect(projectId).toBe(result.projectId);
    return { result, seeded };
  });

  await test.step('B: the trace first, then ensure links the collector\'s project', async () => {
    const projectName = `deploy/${nameB}`;
    const seeded = await sendTrace(req, {
      collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey, secretKey: actor.secretKey,
      projectName, rootName: 'agent.run', rootAttributes: traceAttributes(depB, nameB),
    });
    const collectorProject = await expectTraceOn(probe, seeded.traceId, seeded.spanIds.length,
      projectName, actor.organizationId);
    const result = await ensure(actor.api, depB, nameB);
    expect(result.projectId).toBe(collectorProject);
    expect(result.alerts).toHaveLength(2);
    const projects = await probe.pg('SELECT id FROM tracer_project WHERE name = $1 AND deleted = false',
      [projectName]);
    expect(projects).toHaveLength(1);
  });

  await test.step('storage: links, and two monitors per project matching what ensure returned', async () => {
    for (const [id, name] of [[depA, nameA], [depB, nameB]]) {
      const links = await probe.pg<{ project_id: string; workspace_id: string; deployment_name: string }>(
        'SELECT project_id, workspace_id, deployment_name FROM tracer_omega_deployment_observe_link '
        + 'WHERE ee_deployment_id = $1 AND deleted = false', [id]);
      expect(links).toHaveLength(1);
      expect(links[0].workspace_id).toBe(actor.workspaceId);
      expect(links[0].deployment_name).toBe(name);
      const monitors = await probe.pg<{ id: string; metric_type: string }>(
        'SELECT id, metric_type FROM tracer_useralertmonitor WHERE project_id = $1 AND deleted = false '
        + 'ORDER BY metric_type', [links[0].project_id]);
      expect(monitors.map((m) => m.metric_type)).toEqual(['count_of_errors', 'daily_tokens_spent']);
    }
    const monitorsA = await probe.pg<{ id: string }>(
      'SELECT id FROM tracer_useralertmonitor WHERE project_id = $1 AND deleted = false',
      [first.result.projectId]);
    expect(monitorsA.map((m) => m.id).sort()).toEqual(first.result.alerts.map((a) => a.id).sort());
  });

  await test.step('A again: the same project and alerts, nothing added', async () => {
    const again = await ensure(actor.api, depA, nameA);
    expect(again).toEqual(first.result);
    const rows = await probe.pg<{ n: string }>(
      'SELECT count(*) AS n FROM tracer_useralertmonitor WHERE project_id = $1', [first.result.projectId]);
    expect(Number(rows[0].n)).toBe(2);
  });

  await test.step('another org: the deployment id is not found, and nothing is made there', async () => {
    const refused = await refusal(other.api.post(ensurePath(depA), { name: nameA }));
    expect(refused.status).toBe(404);
    expect((refused.body as { message?: string }).message)
      .toBe('This deployment was not found in your workspace.');
    expect(JSON.stringify(refused.body)).not.toContain(first.result.projectId);
    const theirs = await probe.pg('SELECT id FROM tracer_project WHERE name = $1 AND organization_id = $2',
      [`deploy/${nameA}`, other.organizationId]);
    expect(theirs).toHaveLength(0);
  });

  await test.step('API: Observe lists the two defaults and both spans under A\'s project', async () => {
    const monitors = await probe.apiList<MonitorRow>(MONITOR_LIST_PATH, {
      project_id: first.result.projectId, page_number: 0, page_size: 25,
    });
    expect(monitors.map((m) => m.id).sort()).toEqual(first.result.alerts.map((a) => a.id).sort());
    expect(monitors.every((m) => m.is_mute === false)).toBe(true);
    const spans = await probe.apiList<SpanRow>(SPAN_LIST_PATH, {
      project_id: first.result.projectId, page_number: 0, page_size: 25, filters: '[]',
    });
    expect(spans.map((s) => s.span_id).sort()).toEqual([...first.seeded.spanIds].sort());
  });

  await test.step('N of N: three deployments, three projects; relay-shaped traces land only on their own', async () => {
    const trio = [0, 1, 2].map((index) => ({
      id: deploymentId(), name: deploymentName(`w${testInfo.workerIndex}n${index}`),
    }));
    const ensured: ObserveResult[] = [];
    for (const deployment of trio) ensured.push(await ensure(actor.api, deployment.id, deployment.name));
    expect(new Set(ensured.map((result) => result.projectId)).size).toBe(3);
    expect(ensured.map((result) => result.projectName)).toEqual(trio.map((d) => `deploy/${d.name}`));
    for (const [index, deployment] of trio.entries()) {
      const links = await probe.pg<{ project_id: string }>(
        'SELECT project_id FROM tracer_omega_deployment_observe_link WHERE ee_deployment_id = $1 AND deleted = false',
        [deployment.id]);
      expect(links.map((row) => row.project_id)).toEqual([ensured[index].projectId]);
      // What the EE relay sends for this deployment: its stamped attributes and the expected target.
      const seeded = await sendTrace(req, {
        collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey, secretKey: actor.secretKey,
        projectName: ensured[index].projectName, rootName: 'agent.run',
        rootAttributes: traceAttributes(deployment.id, deployment.name),
        headers: expectedTarget(actor.organizationId, actor.workspaceId, ensured[index].projectId),
      });
      const landed = await expectTraceOn(probe, seeded.traceId, seeded.spanIds.length,
        ensured[index].projectName, actor.organizationId);
      expect(landed).toBe(ensured[index].projectId);
    }

    // A trace that names one deployment's project but asserts another's: the collector refuses it.
    const refused = await sendTrace(req, {
      collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey, secretKey: actor.secretKey,
      projectName: ensured[0].projectName, rootName: 'agent.run',
      rootAttributes: traceAttributes(trio[0].id, trio[0].name),
      headers: expectedTarget(actor.organizationId, actor.workspaceId, ensured[1].projectId),
    }).then(() => null, (error: unknown) => String(error));
    expect(refused).toMatch(/^Error: collector 403/);
  });

  await otherReq.dispose();
  await req.dispose();
});

/** The expected-target headers the EE relay sends with every deployment trace (fi-collector expected_target.go). */
function expectedTarget(organizationId: string, workspaceId: string, projectId: string): Record<string, string> {
  return {
    'X-FI-Expected-Organization-Id': organizationId,
    'X-FI-Expected-Workspace-Id': workspaceId,
    'X-FI-Expected-Project-Id': projectId,
  };
}
