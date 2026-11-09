import { randomBytes, randomUUID } from 'node:crypto';
import { request } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { sendTrace } from '../../lib/otlp';
import { POLL } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// ProjectSerializer and ProjectView.create accept source='simulator' and return
// result.project_id/name. LLMTracingView uses projectDetail.source, NOT span kind,
// to select the voice grid. OBS-E2E-008 exercises this same public creation path.
const PROJECT = '/tracer/project/';
// sections/agents/helper.jsx: useCallLogs uses readQuery; useVoiceCallDetail
// sends trace_id and project_id. The list has a top-level results envelope.
const VOICE_LIST = '/tracer/trace/list_voice_calls/';
const VOICE_DETAIL = '/tracer/trace/voice_call_detail/';
// TraceView._build_recording_dict and _recording_available define these keys
// and preserve absent URLs as null. The collector derives observation_type from
// fi.span.kind; voice lists select conversation roots, not ordinary trace rows.
const RECORDING_PREFIX = 'conversation.recording';
// The shared Observe first-paint budget; retain the existing 300s test ceiling.
const UI_READY = 60_000;
// AudioPlayerCustom / MultiTrackAudioPlayer loading copy.
const LOADER_COPY = /painting sound waves/i;

type Recording = {
  mono: { combined_url: string | null; assistant_url: string | null; customer_url: string | null };
  stereo_url: string | null;
};
type VoiceList = {
  results: { id: string; trace_id: string; project_id: string; call_id: string; status: string }[];
  count: number; has_more: boolean; count_is_lower_bound: boolean;
  next_cursor: string | null; query_complete: boolean; query_status: string;
};
type VoiceDetail = {
  status: boolean;
  result: {
    id: string; trace_id: string; project_id: string;
    recording_available: boolean; recording: Recording;
    observation_span: { id: string; observation_type: string }[];
  };
};

const flows = [
  {
    id: 'OBS-E2E-012',
    title: 'an unreachable recording shows an error, not an endless loader',
    userGoal: 'A user opens a voice call whose recording cannot be fetched and learns that, instead of watching a spinner forever',
    prefix: 'err', hasRecording: true,
    steps: ['create a voice project and seed a call whose four recording URLs 404',
      'open Voice Observe and identify the exact seeded call by Call ID',
      'open the call detail drawer and observe a real recording HTTP 404',
      'see "Recording unavailable", with no loader, retry or exposed recording URL'],
    recordingCheck: 'browser and independent voice_call_detail reads return the exact seeded call and all four recording URLs unchanged with recording_available true',
  },
  {
    id: 'OBS-E2E-013',
    title: 'a voice call with no recording says so',
    userGoal: 'A user opens a voice call that has no recording at all and sees a plain explanation',
    prefix: 'none', hasRecording: false,
    steps: ['create a voice project and seed a call with no recording attributes',
      'open Voice Observe and identify the exact seeded call by Call ID',
      'open the call detail drawer',
      'see "No recording found", with no loader or recording-unavailable error'],
    recordingCheck: 'browser and independent voice_call_detail reads return the exact seeded call with recording_available false and all four recording URLs null',
  },
] as const;

for (const flow of flows) {
  test(`${flow.id}: ${flow.title}`, {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: flow.id, area: 'observe', userGoal: flow.userGoal,
      steps: [...flow.steps],
      backendChecks: [
        'the public-created project retains its exact ID/name, actor organization/workspace and simulator source in PG',
        'the exact conversation root and recording attributes are queryable in spans FINAL with the scoped curated trace present',
        'the browser voice-list response and visible Call ID/Status cells identify exactly the seeded completed call',
        flow.recordingCheck,
      ],
    }),
  }, async ({ page, actor, probe }, testInfo) => {
    test.setTimeout(300_000);
    // SPAN_VISIBLE (15s) + four UI_READY steps (240s) + 45s seed/API headroom.
    page.setDefaultTimeout(UI_READY);
    const req = await request.newContext();
    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const projectName = `e2e-voice-${flow.prefix}-${suffix}`;
    const callId = `${projectName}-call`;
    // nginx.conf /assets/ serves files without the SPA fallback. A missing root
    // /e2e-no-such-recording.wav instead returns index.html, not a reliable 404.
    const deadRecordingUrl = `${E2E.appUrl}/assets/${projectName}-missing.wav`;
    const recordingAttributes: Record<string, string> = flow.hasRecording ? {
      [`${RECORDING_PREFIX}.stereo`]: deadRecordingUrl,
      [`${RECORDING_PREFIX}.mono.combined`]: deadRecordingUrl,
      [`${RECORDING_PREFIX}.mono.assistant`]: deadRecordingUrl,
      [`${RECORDING_PREFIX}.mono.customer`]: deadRecordingUrl,
    } : {};
    const expectedRecording: Recording = {
      mono: {
        combined_url: flow.hasRecording ? deadRecordingUrl : null,
        assistant_url: flow.hasRecording ? deadRecordingUrl : null,
        customer_url: flow.hasRecording ? deadRecordingUrl : null,
      },
      stereo_url: flow.hasRecording ? deadRecordingUrl : null,
    };

    try {
      const created = await actor.api.post<{ result: { project_id: string; name: string } }>(PROJECT, {
        name: projectName, model_type: 'GenerativeLLM', trace_type: 'observe', source: 'simulator',
      });
      expect(created.result.name).toBe(projectName);
      const projectId = created.result.project_id;
      expect(projectId).toMatch(/^[0-9a-f-]{36}$/);
      const identities = {
        projectName, traceId: randomUUID(),
        spanIds: [randomBytes(8).toString('hex'), randomBytes(8).toString('hex')],
      };
      await testInfo.attach('seeded-voice-call', {
        body: JSON.stringify({ ...identities, projectId, callId,
          organizationId: actor.organizationId, workspaceId: actor.workspaceId,
          recordingAvailable: flow.hasRecording, recording: expectedRecording }),
        contentType: 'application/json',
      });
      const seeded = await sendTrace(req, {
        collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey, secretKey: actor.secretKey,
        projectName, rootName: `${projectName}.conversation`,
        traceId: identities.traceId, rootSpanId: identities.spanIds[0], childSpanId: identities.spanIds[1],
        resourceAttributes: { project_type: 'observe' },
        rootAttributes: {
          'fi.span.kind': 'conversation', 'call.status': 'ended',
          // voice_call_list derives call_id from root metadata.call_execution_id.
          metadata: { call_execution_id: callId },
          ...recordingAttributes,
        },
      });
      expect(seeded).toStrictEqual(identities);

      await test.step('storage: the public-created project is a scoped voice project', async () => {
        const projects = await probe.pg<{
          id: string; name: string; organization_id: string; workspace_id: string; source: string;
        }>('SELECT id,name,organization_id,workspace_id,source FROM tracer_project WHERE id=$1 AND deleted=false', [projectId]);
        expect(projects).toStrictEqual([{
          id: projectId, name: projectName, organization_id: actor.organizationId,
          workspace_id: actor.workspaceId, source: 'simulator',
        }]);
      });
      await test.step('storage: the exact conversation and recording attributes arrived', async () => {
        await expect.poll(async () => {
          const roots = await probe.ch<{
            id: string; trace_id: string; project_id: string; org_id: string;
            observation_type: string; attrs_string: Record<string, string>;
          }>(`SELECT id,trace_id,project_id,org_id,observation_type,attrs_string FROM spans FINAL
              WHERE trace_id={t:String} AND project_id={p:UUID} AND parent_span_id=''`,
          { t: seeded.traceId, p: projectId });
          const traces = await probe.ch<{ id: string; project_id: string; is_deleted: number }>(
            'SELECT id,project_id,is_deleted FROM traces FINAL WHERE id={t:UUID} AND project_id={p:UUID}',
            { t: seeded.traceId, p: projectId });
          return {
            roots: roots.map(({ attrs_string, ...identity }) => ({
              ...identity, call_status: attrs_string['call.status'],
              recording_attributes: Object.fromEntries(Object.entries(attrs_string)
                .filter(([key]) => key.startsWith(`${RECORDING_PREFIX}.`))),
            })),
            traces,
          };
        }, POLL.SPAN_VISIBLE).toStrictEqual({
          roots: [{ id: seeded.spanIds[0], trace_id: seeded.traceId, project_id: projectId,
            org_id: actor.organizationId, observation_type: 'conversation', call_status: 'ended',
            recording_attributes: recordingAttributes }],
          traces: [{ id: seeded.traceId, project_id: projectId, is_deleted: 0 }],
        });
      });

      await test.step('UI/API: the voice list contains exactly the seeded call', async () => {
        const [response] = await Promise.all([
          page.waitForResponse(response => {
            const url = new URL(response.url());
            if (url.origin !== new URL(E2E.apiUrl).origin || url.pathname !== VOICE_LIST) return false;
            const sent = response.request();
            const query = sent.method() === 'POST' ? sent.postDataJSON() : Object.fromEntries(url.searchParams);
            return query.project_id === projectId;
          }, { timeout: UI_READY }),
          page.goto(`/dashboard/observe/${projectId}/llm-tracing?selectedTab=trace`, { waitUntil: 'domcontentloaded' }),
        ]);
        expect(response.status()).toBe(200);
        const body = await response.json() as VoiceList;
        expect(body).toMatchObject({ count: 1, has_more: false, next_cursor: null,
          count_is_lower_bound: false, query_complete: true, query_status: 'complete' });
        const calls = body.results.map(({ id, trace_id, project_id, call_id, status }) =>
          ({ id, trace_id, project_id, call_id, status }));
        await testInfo.attach('voice-list-identities', {
          body: JSON.stringify(calls), contentType: 'application/json',
        });
        expect(calls).toStrictEqual([{ id: seeded.traceId, trace_id: seeded.traceId,
          project_id: projectId, call_id: callId, status: 'completed' }]);
      }, { timeout: UI_READY });

      // CallLogsGrid has no trace-based getRowId. Reveal its real Call ID column
      // through Display instead of clicking an arbitrary status or row index.
      const callCells = page.locator('.clean-data-table:visible .ag-row [col-id="call_id"]');
      await test.step('UI: show the exact call identity before opening it', async () => {
        await page.getByRole('button', { name: 'Display', exact: true }).click();
        await page.getByText('View columns', { exact: true }).click();
        const dropdown = page.locator('.MuiPopover-paper:visible')
          .filter({ has: page.getByPlaceholder('Search', { exact: true }) });
        const callIdCheckbox = dropdown.getByText('Call ID', { exact: true }).locator('..').getByRole('checkbox');
        // A cold page briefly exposes Trace columns until project source loads.
        await expect(callIdCheckbox).toBeVisible({ timeout: UI_READY });
        await expect(dropdown.getByText('Trace Name', { exact: true })).toHaveCount(0, { timeout: UI_READY });
        const all = dropdown.getByRole('checkbox', { name: 'Toggle Select all', exact: true });
        await all.check();
        await all.uncheck();
        await expect(dropdown.getByRole('checkbox', { checked: true })).toHaveCount(0, { timeout: UI_READY });
        await callIdCheckbox.check();
        await dropdown.getByText('Status', { exact: true }).locator('..').getByRole('checkbox').check();
        await expect(dropdown.getByRole('checkbox', { checked: true })).toHaveCount(2, { timeout: UI_READY });
        await page.keyboard.press('Escape');
        await expect(page.getByPlaceholder('Search', { exact: true })).toBeHidden({ timeout: UI_READY });
        await page.keyboard.press('Escape');
        await expect(page.getByText('View columns', { exact: true })).toBeHidden({ timeout: UI_READY });
        await expect(callCells).toHaveText([callId], { timeout: UI_READY });
        await expect(page.locator('.clean-data-table:visible .ag-row [col-id="status"]'))
          .toHaveText(['Completed'], { timeout: UI_READY });
      }, { timeout: UI_READY });

      const detailMetadata = (body: VoiceDetail) => ({
        id: body.result.id, trace_id: body.result.trace_id, project_id: body.result.project_id,
        conversation_span_ids: body.result.observation_span
          .filter(span => span.observation_type === 'conversation').map(span => span.id),
        recording_available: body.result.recording_available, recording: body.result.recording,
      });
      const expectedDetail = {
        id: seeded.traceId, trace_id: seeded.traceId, project_id: projectId,
        conversation_span_ids: [seeded.spanIds[0]],
        recording_available: flow.hasRecording, recording: expectedRecording,
      };
      await test.step('UI/API: open the seeded call and inspect real recording metadata', async () => {
        const [response, recordingResponse] = await Promise.all([
          page.waitForResponse(response => {
            const url = new URL(response.url());
            return response.request().method() === 'GET' && url.origin === new URL(E2E.apiUrl).origin
              && url.pathname === VOICE_DETAIL && url.searchParams.get('trace_id') === seeded.traceId
              && url.searchParams.get('project_id') === projectId;
          }, { timeout: UI_READY }),
          flow.hasRecording
            ? page.waitForResponse(response => response.url() === deadRecordingUrl
              && response.request().method() === 'GET', { timeout: UI_READY })
            : Promise.resolve(undefined),
          callCells.filter({ hasText: callId }).click(),
        ]);
        expect(response.status()).toBe(200);
        const body = await response.json() as VoiceDetail;
        const metadata = detailMetadata(body);
        await testInfo.attach('browser-voice-recording', {
          body: JSON.stringify({ ...metadata, recordingHttpStatus: recordingResponse?.status() }),
          contentType: 'application/json',
        });
        expect(body.status).toBe(true);
        expect(metadata).toStrictEqual(expectedDetail);
        if (flow.hasRecording) expect(recordingResponse!.status()).toBe(404);
      }, { timeout: UI_READY });

      await test.step('UI: recording settles to the explicit terminal state', async () => {
        const drawer = page.locator('.MuiDrawer-paper:visible');
        await expect(drawer).toHaveCount(1, { timeout: UI_READY });
        if (flow.hasRecording) {
          await expect(drawer.getByText('Recording unavailable', { exact: true })).toBeVisible({ timeout: UI_READY });
          await expect(drawer.getByText(/no recording found/i)).toHaveCount(0, { timeout: UI_READY });
          await expect(page.getByRole('button', { name: /^retry$/i })).toHaveCount(0, { timeout: UI_READY });
          await expect(page.getByText(deadRecordingUrl)).toHaveCount(0, { timeout: UI_READY });
        } else {
          await expect(drawer.getByText(/no recording found/i)).toBeVisible({ timeout: UI_READY });
          await expect(page.getByText(/recording unavailable/i)).toHaveCount(0, { timeout: UI_READY });
        }
        await expect(page.getByText(LOADER_COPY)).toBeHidden({ timeout: UI_READY });
      }, { timeout: UI_READY });

      await test.step('API: independent detail read preserves the recording contract', async () => {
        const body = await actor.api.get<VoiceDetail>(VOICE_DETAIL, { trace_id: seeded.traceId, project_id: projectId });
        const metadata = detailMetadata(body);
        await testInfo.attach('api-voice-recording', {
          body: JSON.stringify(metadata), contentType: 'application/json',
        });
        expect(body.status).toBe(true);
        expect(metadata).toStrictEqual(expectedDetail);
      });
    } finally {
      await req.dispose();
    }
  });
}
