import { request, type Page } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { sendTrace } from '../../lib/otlp';
import { POLL, type StateProbe } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// A voice call is a root span with observation_type 'conversation'
// (query_builders/voice_call_list.py: `WHERE parent_span_id IS NULL AND
// observation_type = 'conversation'`), which the collector derives from
// fi.span.kind.
const VOICE_SPAN_KIND = 'conversation';
const RECORDING_PREFIX = 'conversation.recording';

// Same-origin and guaranteed absent, so the browser's fetch fails with a plain
// 404 rather than a CORS result that would differ between environments. The
// real defect (TH-7758) is a 403 on a third-party bucket; what the player sees
// in both cases is identical — a track that errors instead of becoming ready.
const DEAD_RECORDING_URL = `${E2E.appUrl}/e2e-no-such-recording.wav`;

// The local stack slows several-fold under parallel specs; sized off the other
// observe flows rather than the 10s expect default.
const UI_READY = 60_000;

const LOADER_COPY = /painting sound waves/i;

async function resolveProjectId(
  probe: StateProbe,
  projectName: string,
  organizationId: string,
) {
  const projects = await probe.pg<{ id: string }>(
    'SELECT id FROM tracer_project WHERE name = $1 AND organization_id = $2',
    [projectName, organizationId]);
  expect(projects).toHaveLength(1);
  return projects[0].id;
}

/** A project whose spans are conversations opens directly on the voice-call
 *  grid — there is no separate Voice tab. Rows lead with a timestamp, so the
 *  row is addressed by the status cell instead. */
async function openVoiceCall(page: Page, projectId: string) {
  await page.goto(`/dashboard/observe/${projectId}/llm-tracing`);
  await page
    .locator('tr, [role=row]')
    .filter({ hasText: /ended|completed/i })
    .first()
    .click({ timeout: UI_READY });
}

test('OBS-E2E-010: an unreachable recording shows an error, not an endless loader', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'OBS-E2E-010', area: 'observe',
    userGoal: 'A user opens a voice call whose recording cannot be fetched and learns that, instead of watching a spinner forever',
    steps: ['seed a voice call whose recording URLs 404',
            'open Voice Observe', 'open the call detail drawer',
            'see "Recording unavailable", with no retry offered'],
    backendChecks: ['the seeded conversation span is queryable in CH',
                    'voice_call_detail returns the recording URLs unchanged'],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  test.setTimeout(300_000);
  const req = await request.newContext();
  const projectName = `e2e-voice-err-${testInfo.workerIndex}-${Date.now().toString(36)}`;

  const seeded = await sendTrace(req, {
    collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey,
    secretKey: actor.secretKey, projectName, rootName: 'e2e.voice-call',
    rootAttributes: {
      'fi.span.kind': VOICE_SPAN_KIND,
      'call.status': 'ended',
      // All four, so the player exhausts the stereo path AND the mono
      // fallback — the exact shape TH-7758 arrived with.
      [`${RECORDING_PREFIX}.stereo`]: DEAD_RECORDING_URL,
      [`${RECORDING_PREFIX}.mono.combined`]: DEAD_RECORDING_URL,
      [`${RECORDING_PREFIX}.mono.assistant`]: DEAD_RECORDING_URL,
      [`${RECORDING_PREFIX}.mono.customer`]: DEAD_RECORDING_URL,
    },
  });
  await testInfo.attach('seeded-voice-call', {
    body: JSON.stringify(seeded), contentType: 'application/json',
  });

  await test.step('storage: the conversation span is queryable', async () => {
    await expect.poll(async () => {
      const rows = await probe.ch<{ n: string }>(
        `SELECT count() AS n FROM spans FINAL
         WHERE trace_id = {t:String} AND observation_type = 'conversation'`,
        { t: seeded.traceId });
      return Number(rows[0].n);
    }, POLL.SPAN_VISIBLE).toBe(1);
  });

  const projectId = await resolveProjectId(probe, projectName, actor.organizationId);

  await test.step('the drawer reports the failure instead of spinning', async () => {
    await openVoiceCall(page, projectId);

    // The assertion that would have failed before this fix: the loader is
    // replaced, not merely joined, by a terminal state.
    await expect(page.getByText(/recording unavailable/i))
      .toBeVisible({ timeout: UI_READY });
    await expect(page.getByText(LOADER_COPY)).toBeHidden();

    // A refused source will be refused again, so this variant deliberately
    // offers no retry — that is the distinction from "Audio failed to load",
    // which does offer one.
    await expect(page.getByRole('button', { name: /^retry$/i })).toHaveCount(0);

    // The failure message carries the recording URL; it must not reach the DOM.
    await expect(page.getByText(DEAD_RECORDING_URL)).toHaveCount(0);
  });
});

test('OBS-E2E-011: a voice call with no recording says so', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'OBS-E2E-011', area: 'observe',
    userGoal: 'A user opens a voice call that has no recording at all and sees a plain explanation',
    steps: ['seed a voice call with no recording attributes',
            'open the call detail drawer', 'see "No recording found"'],
    backendChecks: ['voice_call_detail reports recording_available false'],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  test.setTimeout(300_000);
  const req = await request.newContext();
  const projectName = `e2e-voice-none-${testInfo.workerIndex}-${Date.now().toString(36)}`;

  const seeded = await sendTrace(req, {
    collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey,
    secretKey: actor.secretKey, projectName, rootName: 'e2e.voice-call-silent',
    rootAttributes: { 'fi.span.kind': VOICE_SPAN_KIND, 'call.status': 'ended' },
  });

  await expect.poll(async () => {
    const rows = await probe.ch<{ n: string }>(
      `SELECT count() AS n FROM spans FINAL
       WHERE trace_id = {t:String} AND observation_type = 'conversation'`,
      { t: seeded.traceId });
    return Number(rows[0].n);
  }, POLL.SPAN_VISIBLE).toBe(1);

  const projectId = await resolveProjectId(probe, projectName, actor.organizationId);
  await openVoiceCall(page, projectId);

  await expect(page.getByText(/no recording found/i)).toBeVisible({ timeout: UI_READY });
  await expect(page.getByText(LOADER_COPY)).toBeHidden();
  await expect(page.getByText(/recording unavailable/i)).toHaveCount(0);
});
