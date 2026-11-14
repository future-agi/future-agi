import { request, type Page } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import type { TestActor } from '../../lib/provisioning';
import { sendTrace, type SeededTrace } from '../../lib/otlp';
import { POLL, type StateProbe } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// tracer/serializers/project.py ProjectSerializer; LLMTracingView renders the
// CallLogsGrid only for source=simulator, not for an OTLP-created prototype.
const PROJECT = '/tracer/project/';
// tracer/serializers/trace.py; frontend/sections/agents/helper.jsx.
const VOICE_LIST = '/tracer/trace/list_voice_calls/';
const VOICE_DETAIL = '/tracer/trace/voice_call_detail/';
// VoiceCallListQueryBuilder: conversation roots; collector derives their type
// from fi.span.kind. TraceView._build_recording_dict reads these attributes.
const VOICE_SPAN_KIND = 'conversation';
const RECORDING_PREFIX = 'conversation.recording';
// Same-origin, guaranteed absent: a real 404, not a mocked recording response.
const DEAD_RECORDING_URL = `${E2E.appUrl}/e2e-no-such-recording.wav`;
// Existing Observe first-paint budget; unchanged from the original flows.
const UI_READY = 60_000;
const LOADER_COPY = /painting sound waves/i;

type VoiceRow = { id: string; trace_id: string; project_id: string; call_id: string };
type VoiceList = { results: VoiceRow[]; has_more: boolean; query_complete: boolean };
type VoiceDetail = { status: boolean; result: VoiceRow & {
  recording_available: boolean;
  recording: { stereo_url: string | null; mono: {
    combined_url: string | null; customer_url: string | null; assistant_url: string | null;
  } };
} };

async function createVoiceProject(actor: TestActor, projectName: string) {
  // Public create shape also exercised by OBS-E2E-008; no source rewrite in SQL.
  const body = await actor.api.post<{ result: { project_id: string; name: string } }>(PROJECT, {
    name: projectName, model_type: 'GenerativeLLM', trace_type: 'observe', source: 'simulator',
  });
  expect(body.result.name).toBe(projectName);
  expect(body.result.project_id).toMatch(/^[0-9a-f-]{36}$/);
  return body.result.project_id;
}

async function checkSource(probe: StateProbe, actor: TestActor, projectName: string,
  projectId: string, seeded: SeededTrace) {
  const projects = await probe.pg<{ id: string; name: string; organization_id: string;
    workspace_id: string; source: string }>(
    'SELECT id,name,organization_id,workspace_id,source FROM tracer_project WHERE id=$1 AND deleted=false',
    [projectId]);
  expect(projects).toStrictEqual([{ id: projectId, name: projectName,
    organization_id: actor.organizationId, workspace_id: actor.workspaceId, source: 'simulator' }]);
  await expect.poll(async () => probe.ch<{ id: string; project_id: string; org_id: string }>(
    `SELECT id,project_id,org_id FROM spans FINAL
     WHERE trace_id={t:String} AND observation_type='conversation'
       AND parent_span_id='' AND is_deleted=0 ORDER BY id`,
    { t: seeded.traceId }), POLL.SPAN_VISIBLE).toStrictEqual([
      { id: seeded.spanIds[0], project_id: projectId, org_id: actor.organizationId },
    ]);
}

async function openVoiceCall(page: Page, projectId: string, traceId: string, callId: string) {
  const expected = { id: traceId, trace_id: traceId, project_id: projectId, call_id: callId };
  const listResponse = page.waitForResponse(response => {
    const url = new URL(response.url());
    if (url.origin !== new URL(E2E.apiUrl).origin || url.pathname !== VOICE_LIST) return false;
    const req = response.request();
    if (req.method() === 'POST') return req.postDataJSON().project_id === projectId;
    return req.method() === 'GET' && url.searchParams.get('project_id') === projectId;
  }, { timeout: UI_READY });
  await page.goto(`/dashboard/observe/${projectId}/llm-tracing`);
  const listed = await listResponse;
  expect(listed.status()).toBe(200);
  // Voice list uses a top-level results envelope, not probe.apiList's table.
  const body = await listed.json() as VoiceList;
  expect(body.query_complete).toBe(true);
  expect(body.has_more).toBe(false);
  expect(body.results.map(row => ({ id: row.id, trace_id: row.trace_id,
    project_id: row.project_id, call_id: row.call_id }))).toStrictEqual([expected]);
  // helper.jsx hides call_id by default. The observed list above must contain
  // exactly our minted call; require one completed row, then bind the drawer's
  // actual request/response to that trace. Never take an arbitrary first row.
  const row = page.getByRole('row').filter({ hasText: /ended|completed/i });
  await expect(row).toHaveCount(1, { timeout: UI_READY });
  const detailResponse = page.waitForResponse(response => {
    const url = new URL(response.url());
    return url.origin === new URL(E2E.apiUrl).origin && url.pathname === VOICE_DETAIL
      && response.request().method() === 'GET'
      && url.searchParams.get('trace_id') === traceId && url.searchParams.get('project_id') === projectId;
  }, { timeout: UI_READY });
  await row.click({ timeout: UI_READY });
  const detailed = await detailResponse;
  expect(detailed.status()).toBe(200);
  const detail = await detailed.json() as VoiceDetail;
  expect(detail.status).toBe(true);
  expect({ id: detail.result.id, trace_id: detail.result.trace_id,
    project_id: detail.result.project_id, call_id: detail.result.call_id }).toStrictEqual(expected);
  return { list: body, detail };
}

test('OBS-E2E-012: an unreachable recording shows an error, not an endless loader', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'OBS-E2E-012', area: 'observe',
    userGoal: 'A user opens a voice call whose recording cannot be fetched and learns that, instead of watching a spinner forever',
    steps: ['create a scoped voice project through the public API and ingest a call whose recording URLs 404',
      'open Voice Observe and the exact seeded call detail drawer',
      'see "Recording unavailable", with no loader, retry or exposed recording URL'],
    backendChecks: ['the project is source=simulator in the actor organization and workspace',
      'the exact seeded conversation root is live in ClickHouse under that project and organization',
      'the browser voice list and detail select only the seeded call and retain its four recording URLs'],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  test.setTimeout(300_000); // 30s setup + 15s ingestion + 4*60s UI/API + 15s headroom.
  const req = await request.newContext();
  try {
    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const projectName = `e2e-voice-err-${suffix}`, callId = `e2e-voice-err-call-${suffix}`;
    const projectId = await createVoiceProject(actor, projectName);
    const seeded = await sendTrace(req, {
      collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey, secretKey: actor.secretKey,
      projectName, rootName: `e2e.voice-call-${suffix}`,
      resourceAttributes: { project_type: 'observe' },
      rootAttributes: {
        'fi.span.kind': VOICE_SPAN_KIND, 'call.status': 'ended',
        metadata: { call_execution_id: callId },
        // All four exhaust stereo and mono fallback, as TH-7758 did.
        [`${RECORDING_PREFIX}.stereo`]: DEAD_RECORDING_URL,
        [`${RECORDING_PREFIX}.mono.combined`]: DEAD_RECORDING_URL,
        [`${RECORDING_PREFIX}.mono.assistant`]: DEAD_RECORDING_URL,
        [`${RECORDING_PREFIX}.mono.customer`]: DEAD_RECORDING_URL,
      },
    });
    await testInfo.attach('seeded-voice-call', {
      body: JSON.stringify({ ...seeded, projectId, callId }), contentType: 'application/json',
    });
    await checkSource(probe, actor, projectName, projectId, seeded);
    const observed = await openVoiceCall(page, projectId, seeded.traceId, callId);
    await testInfo.attach('voice-api', { body: JSON.stringify(observed), contentType: 'application/json' });
    expect(observed.detail.result.recording_available).toBe(true);
    expect(observed.detail.result.recording).toStrictEqual({ stereo_url: DEAD_RECORDING_URL, mono: {
      combined_url: DEAD_RECORDING_URL, customer_url: DEAD_RECORDING_URL, assistant_url: DEAD_RECORDING_URL,
    } });
    await expect(page.getByText(/recording unavailable/i)).toBeVisible({ timeout: UI_READY });
    await expect(page.getByText(LOADER_COPY)).toBeHidden();
    await expect(page.getByRole('button', { name: /^retry$/i })).toHaveCount(0);
    await expect(page.getByText(DEAD_RECORDING_URL)).toHaveCount(0);
    await testInfo.attach('recording-state', { body: await page.screenshot(), contentType: 'image/png' });
  } finally {
    await req.dispose();
  }
});

test('OBS-E2E-013: a voice call with no recording says so', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'OBS-E2E-013', area: 'observe',
    userGoal: 'A user opens a voice call that has no recording at all and sees a plain explanation',
    steps: ['create a scoped voice project through the public API and ingest a call with no recording attributes',
      'open the exact seeded call detail drawer', 'see "No recording found" without a loader or recording error'],
    backendChecks: ['the project is source=simulator in the actor organization and workspace',
      'the exact seeded conversation root is live in ClickHouse under that project and organization',
      'the browser voice list and detail select only the seeded call, with recording_available false and no recording URLs'],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  test.setTimeout(300_000); // Same ingestion/UI budgets as OBS-E2E-012; no retries.
  const req = await request.newContext();
  try {
    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const projectName = `e2e-voice-none-${suffix}`, callId = `e2e-voice-none-call-${suffix}`;
    const projectId = await createVoiceProject(actor, projectName);
    const seeded = await sendTrace(req, {
      collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey, secretKey: actor.secretKey,
      projectName, rootName: `e2e.voice-call-silent-${suffix}`,
      resourceAttributes: { project_type: 'observe' },
      rootAttributes: { 'fi.span.kind': VOICE_SPAN_KIND, 'call.status': 'ended',
        metadata: { call_execution_id: callId } },
    });
    await testInfo.attach('seeded-voice-call', {
      body: JSON.stringify({ ...seeded, projectId, callId }), contentType: 'application/json',
    });
    await checkSource(probe, actor, projectName, projectId, seeded);
    const observed = await openVoiceCall(page, projectId, seeded.traceId, callId);
    await testInfo.attach('voice-api', { body: JSON.stringify(observed), contentType: 'application/json' });
    expect(observed.detail.result.recording_available).toBe(false);
    expect(observed.detail.result.recording).toStrictEqual({ stereo_url: null, mono: {
      combined_url: null, customer_url: null, assistant_url: null,
    } });
    await expect(page.getByText(/no recording found/i)).toBeVisible({ timeout: UI_READY });
    await expect(page.getByText(LOADER_COPY)).toBeHidden();
    await expect(page.getByText(/recording unavailable/i)).toHaveCount(0);
    await testInfo.attach('recording-state', { body: await page.screenshot(), contentType: 'image/png' });
  } finally {
    await req.dispose();
  }
});
