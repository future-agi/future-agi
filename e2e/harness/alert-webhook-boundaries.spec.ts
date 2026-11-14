import { test, expect } from '../lib/scope-actors';
import { E2E } from '../lib/env';

// Both helpers use public login/invitation APIs, not force-authentication.
const PATH = '/tracer/user-alerts/';

test('alert webhook API preserves actor scope and refuses invalid destinations', async ({ scopeActors: actors, scopeProbe: probe, request }, testInfo) => {
  test.setTimeout(180_000);
  const owner = actors.ownerA;
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const name = `e2e-webhook-api-${suffix}`;
  const url = `http://93.184.216.34/${name}`;
  const project = await actors.send<{ result: { project_id: string } }>(owner, 'POST', '/tracer/project/', {
    name, trace_type: 'observe', model_type: 'GenerativeLLM',
  });
  expect(project.status).toBe(200);
  const body = { project: project.body.result.project_id, name, metric_type: 'count_of_errors',
    threshold_type: 'static', threshold_operator: 'greater_than', critical_threshold_value: 999999,
    alert_frequency: 60, is_mute: true, webhook_url: url, filters: {} };
  const created = await actors.send(owner, 'POST', PATH, body);
  expect(created.status).toBe(200);
  expect(created.body).toEqual({ status: true, result: `${name} alert created successfully` });
  const rows = await probe.pg<{ id: string }>('SELECT id FROM tracer_useralertmonitor WHERE name=$1 AND organization_id=$2', [name, owner.organizationId]);
  expect(rows).toHaveLength(1);
  const id = rows[0].id;
  const target = `${PATH}${id}/`;
  const receipts: unknown[] = [{ case: 'owner-create', response: created }];
  const snapshot = () => probe.pg<{ row: Record<string, unknown> }>(
    'SELECT to_jsonb(m) AS row FROM tracer_useralertmonitor m WHERE id=$1', [id]);
  const initial = await snapshot();
  const read = await actors.send<{ result: Record<string, unknown> }>(owner, 'GET', `${target}details/`);
  expect(read.status).toBe(200);
  expect(read.body.result.webhook_url).toBe(url);
  for (const [label, actor, expected] of [
    ['cross-org', actors.ownerB, 400],
    ['cross-workspace', actors.withWorkspace(owner, actors.emptyWorkspace.id), 400],
    ['viewer', actors.viewer, 403],
  ] as const) {
    const response = await actors.send(actor, 'PATCH', target, { webhook_url: `${url}-${label}` });
    receipts.push({ case: label, response });
    expect(response.status).toBe(expected);
    expect(response.body).toMatchObject({ status: false, detail: expected === 403
      ? 'Write access denied to this workspace' : 'Unable to update alert. Please try again.' });
    expect(JSON.stringify(response.body)).not.toContain(url);
    expect(await snapshot()).toEqual(initial);
  }
  const anonymous = await request.patch(`${E2E.apiUrl}${target}`, { data: { webhook_url: `${url}-anonymous` } });
  const anonymousBody = await anonymous.json();
  receipts.push({ case: 'anonymous', status: anonymous.status(), body: anonymousBody });
  expect(anonymous.status()).toBe(401);
  expect(anonymousBody).toMatchObject({ detail: 'Authentication credentials were not provided.' });
  expect(await snapshot()).toEqual(initial);
  for (const invalid of ['http://127.0.0.1:9/hook', 'http://10.0.0.1/hook', 'http://169.254.169.254/hook',
    'http://[::1]/hook', 'https://user:password@93.184.216.34/hook', 'ftp://93.184.216.34/hook', 'http://93.184.216.34:0/hook']) {
    const before = await snapshot();
    const response = await actors.send(owner, 'PATCH', target, { webhook_url: invalid });
    receipts.push({ case: 'invalid', input: invalid, response });
    expect(response.status).toBe(400);
    expect(response.body).toMatchObject({ status: false });
    expect(await snapshot()).toEqual(before);
  }
  // A non-viewer workspace member can modify an in-scope monitor.
  const member = await actors.send(actors.member, 'PATCH', target, { webhook_url: `${url}-member` });
  receipts.push({ case: 'member-update', response: member });
  expect(member.status).toBe(200);
  const final = await actors.send<{ result: Record<string, unknown> }>(owner, 'GET', `${target}details/`);
  expect(final.body.result.webhook_url).toBe(`${url}-member`);
  expect(final.body.result.is_mute).toBe(true);
  await testInfo.attach('api-matrix', { contentType: 'application/json', body: JSON.stringify({ id, receipts }) });
});
