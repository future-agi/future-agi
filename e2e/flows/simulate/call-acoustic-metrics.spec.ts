import { readFile } from 'node:fs/promises';
import type { Locator, Page } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { seedAcousticMetricsCalls } from '../../lib/simulate-seed';

// Browser-side waits: one navigation's first paint, not a stage. The local
// stack slows several-fold beside other workers, so not the 10s expect default.
const UI_READY = 60_000;

// The call detail the Environments call drawer reads
// (frontend/src/utils/axios.js, endpoints.runResultsV3.callDetail).
const callDetailPath = (id: string) => `/simulate/v3/call-executions/${id}/`;

// Pinned from futureagi/ee/voice/services/audio_analysis/constants.py.
const PIPELINE_VERSION = 'audio_metrics_pipeline_v1';
const PITCH_ALGORITHM_VERSION = 'pyin_c2c7_16k_v1';
const SNR_ALGORITHM_VERSION = 'power_quantiles_q90_q10_16k_v1';
const PREPROCESSING_VERSION = 'native_target_decode_canonical_16k_v1';

// Minted for this flow. The drawer prints Hz and dB with one decimal
// (frontend/src/components/VoiceDetailDrawerV2/acousticMetricsLabels.js,
// formatMetricValue).
const ANALYSIS_ID = 'e2e-sim3-seeded-analysis';
const PITCH_HZ = 187.43;
const SNR_DB = 23.64;
// A private storage location planted in the stored row. The read projection
// (futureagi/ee/voice/services/audio_analysis/envelope.py, sanitize_for_api)
// keeps only SOURCE_FIELDS / COVERAGE_FIELDS, so it must never reach the API.
const PRIVATE_OBJECT_KEY = 'call-recordings/e2e-sim3-private/tested-agent.wav';

// The row the analysis worker commits to CallExecution.audio_metrics
// (envelope.py AudioEnvelope + metric_entry). The VQI entry is the one the
// scheduler writes when model weights are configured but no validated
// calibration is (ee/voice/temporal/activities/audio_analysis.py). The stored
// top-level state is deliberately stale ('complete'): the API must derive it.
// Capture origin, transport and provider are null: no recording, no provider.
const STORED_ENVELOPE = {
  schema_version: 1,
  analysis_id: ANALYSIS_ID,
  generation: 0,
  state: 'complete',
  computed_at: '2026-11-07T12:00:20+00:00',
  scheduled_at: '2026-11-07T12:00:00+00:00',
  deadline_at: '2026-11-07T12:30:00+00:00',
  target_role: 'tested_agent',
  pipeline_version: PIPELINE_VERSION,
  source: {
    capture_origin: null,
    transport: null,
    provider: null,
    target_role: 'tested_agent',
    mapping_version: null,
    original_sample_rate_hz: 16000,
    original_channel_count: 1,
    target_sample_count: 320000,
    input_duration_seconds: 20,
    preprocessing_version: PREPROCESSING_VERSION,
    object_key: PRIVATE_OBJECT_KEY,
  },
  metrics: {
    average_pitch_hz: {
      value: PITCH_HZ, unit: 'Hz', state: 'available', reason: null,
      algorithm_version: PITCH_ALGORITHM_VERSION,
      coverage: { analyzed_seconds: 20, voiced_seconds: 12.5, frame_count: 626,
                  voiced_frame_count: 391, object_key: PRIVATE_OBJECT_KEY },
    },
    estimated_snr_db: {
      value: SNR_DB, unit: 'dB', state: 'available', reason: null,
      algorithm_version: SNR_ALGORITHM_VERSION,
      coverage: { analyzed_seconds: 20, active_seconds: 9.1, inactive_seconds: 4.2, frame_count: 623 },
      flags: { noise_floor_floored: false, value_capped: false },
    },
    voice_quality_index: {
      value: null, unit: 'index', state: 'unavailable', reason: 'not_validated',
      algorithm_version: null, coverage: null,
      model_version: null, calibration_id: null, scale_id: null,
    },
  },
};

// What GET call detail must return for that row: the stored fields the
// projection allows, the private key gone, state derived from the metrics
// (envelope.py derive_state: two available + one unavailable = partial).
const EXPECTED_ANALYZED = {
  schema_version: 1,
  analysis_id: ANALYSIS_ID,
  generation: 0,
  state: 'partial',
  computed_at: '2026-11-07T12:00:20+00:00',
  scheduled_at: '2026-11-07T12:00:00+00:00',
  deadline_at: '2026-11-07T12:30:00+00:00',
  target_role: 'tested_agent',
  pipeline_version: PIPELINE_VERSION,
  source: {
    capture_origin: null,
    transport: null,
    provider: null,
    target_role: 'tested_agent',
    mapping_version: null,
    original_sample_rate_hz: 16000,
    original_channel_count: 1,
    target_sample_count: 320000,
    input_duration_seconds: 20,
    preprocessing_version: PREPROCESSING_VERSION,
  },
  metrics: {
    average_pitch_hz: {
      value: 187.43, unit: 'Hz', state: 'available', reason: null,
      algorithm_version: PITCH_ALGORITHM_VERSION,
      coverage: { analyzed_seconds: 20, voiced_seconds: 12.5, frame_count: 626, voiced_frame_count: 391 },
    },
    estimated_snr_db: {
      value: 23.64, unit: 'dB', state: 'available', reason: null,
      algorithm_version: SNR_ALGORITHM_VERSION,
      coverage: { analyzed_seconds: 20, active_seconds: 9.1, inactive_seconds: 4.2, frame_count: 623 },
      flags: { noise_floor_floored: false, value_capped: false },
    },
    voice_quality_index: {
      value: null, unit: 'index', state: 'unavailable', reason: 'not_validated',
      algorithm_version: null, coverage: null,
      model_version: null, calibration_id: null, scale_id: null,
    },
  },
};

// A call whose audio_metrics column is NULL (every call recorded before the
// feature): envelope.py empty_envelope(0) with reason not_analyzed.
const notAnalyzedMetric = (unit: string) => ({
  value: null, unit, state: 'unavailable', reason: 'not_analyzed', algorithm_version: null, coverage: null,
});
const EXPECTED_NOT_ANALYZED = {
  schema_version: 1,
  analysis_id: null,
  generation: 0,
  state: 'not_requested',
  computed_at: null,
  scheduled_at: null,
  deadline_at: null,
  target_role: 'tested_agent',
  pipeline_version: null,
  source: null,
  metrics: {
    average_pitch_hz: notAnalyzedMetric('Hz'),
    estimated_snr_db: { ...notAnalyzedMetric('dB'), flags: null },
    voice_quality_index: { ...notAnalyzedMetric('index'), model_version: null, calibration_id: null, scale_id: null },
  },
};

// Copy pinned from AcousticMetricsSection.jsx and acousticMetricsLabels.js
// (ACOUSTIC_METRICS, REASON_LABELS, STATE_LABELS).
const REGION_NAME = 'Acoustic metrics · Tested agent';
const VQI_NOTE = /Estimated perceptual quality, normalized 0–5\s*Not MOS or a naturalness certification$/;

interface CallDetail { id: string; audio_metrics?: unknown }

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

// The <dd> that follows a <dt> with exactly this label in a details list.
function detail(region: Locator, label: string): Locator {
  return region.locator('dt').filter({ hasText: new RegExp(`^${escapeRegExp(label)}$`) })
    .locator('xpath=following-sibling::dd[1]');
}

// Opens the call drawer from its run page and returns the call-detail body
// the browser received and the acoustic metrics region.
async function openCallAnalytics(page: Page, runUrl: string, callId: string) {
  const detailResponse = page.waitForResponse(
    (r) => r.url().includes(callDetailPath(callId)) && r.request().method() === 'GET' && r.status() === 200,
    { timeout: UI_READY },
  );
  await page.goto(`${runUrl}?rowId=${callId}`);
  const body = (await (await detailResponse).json()) as CallDetail;
  await page.getByRole('tab', { name: 'Call Analytics' }).click({ timeout: UI_READY });
  const region = page.getByRole('region', { name: REGION_NAME });
  await expect(region).toBeVisible({ timeout: UI_READY });
  return { body, region };
}

test('SIM-E2E-003: A completed voice call shows the tested agent\'s acoustic metrics, an uncalibrated VQI and the same envelope in the raw export', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SIM-E2E-003', area: 'simulate',
    userGoal: 'A developer reviewing a completed voice call from an Environments run reads the tested agent\'s average pitch and estimated SNR in Call Analytics, sees the voice quality index marked as not calibrated with its reason rather than a number, can open the capture/version details and measurement limits, and downloads the same metrics in the raw JSON; a call that was never analyzed says so instead of showing numbers. The stored analysis result is seeded (no recording, provider call or analysis worker runs in this harness — see seedAcousticMetricsCalls in e2e/lib/simulate-seed.ts)',
    steps: ['seed one completed Environments run with two voice calls through the backend: one with a stored acoustic-metrics result, one never analyzed',
            'open the analyzed call\'s drawer from its run page and switch to Call Analytics',
            'read the pitch, SNR and voice quality index rows and the status badge',
            'open Capture & version details and Measurement limits',
            'download the raw data',
            'open the never-analyzed call\'s drawer and switch to Call Analytics'],
    backendChecks: ['PG simulate_call_execution holds the seeded result, with its private storage key, on the analyzed call and NULL on the other, both in the actor\'s organization at analysis generation 0',
                    'GET /simulate/v3/call-executions/{id}/ returns the analyzed call\'s audio_metrics exactly: pitch and SNR available with their values, versions and coverage, the voice quality index unavailable with reason not_validated and no value, model or calibration, the derived state partial, and no private storage key',
                    'the same endpoint returns state not_requested with every metric unavailable and reason not_analyzed for the never-analyzed call',
                    'the call-detail response the drawer received carries the same envelope as the API read'],
    browserChecks: ['the acoustic metrics region shows exactly 187.4 for pitch, 23.6 for SNR and "Calibration not validated — Unavailable" for the voice quality index, under a Partial badge',
                    'Capture & version details shows the pipeline version, no calibration, and the not_validated reason code; Measurement limits states VQI is not MOS',
                    'the downloaded raw JSON carries the same audio_metrics as the API, without the private storage key',
                    'the never-analyzed call shows "Not analyzed — Unavailable" on all three rows under an Unavailable badge'],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  // Ten browser steps, each its own UI_READY budget (600s), plus seeding and
  // 60s headroom so a slow run fails on the step that ran out.
  test.setTimeout(720_000);

  const seed = seedAcousticMetricsCalls({
    organizationId: actor.organizationId,
    workspaceId: actor.workspaceId,
    audioMetrics: STORED_ENVELOPE,
  });
  await testInfo.attach('seeded', {
    contentType: 'application/json',
    body: JSON.stringify({ organizationId: actor.organizationId, workspaceId: actor.workspaceId, ...seed }),
  });
  const runUrl = `/dashboard/simulate/environments/${seed.environmentId}/runs/${seed.runTestId}/${seed.testExecutionId}`;

  await test.step('storage: the seeded result is stored on one call and absent on the other', async () => {
    const rows = await probe.pg<{ id: string; audio_metrics: Record<string, any> | null;
                                  audio_analysis_generation: number; organization_id: string }>(
      `SELECT ce.id::text AS id, ce.audio_metrics, ce.audio_analysis_generation, rt.organization_id::text AS organization_id
         FROM simulate_call_execution ce
         JOIN simulate_test_execution te ON te.id = ce.test_execution_id
         JOIN simulate_run_test rt ON rt.id = te.run_test_id
        WHERE ce.id = ANY($1::uuid[])
        ORDER BY ce.id`,
      [[seed.analyzedCallId, seed.notAnalyzedCallId]],
    );
    const byId = Object.fromEntries(rows.map((row) => [row.id, row]));
    expect(Object.keys(byId).sort()).toEqual([seed.analyzedCallId, seed.notAnalyzedCallId].sort());
    for (const row of rows) {
      expect(row.organization_id).toBe(actor.organizationId);
      expect(row.audio_analysis_generation).toBe(0);
    }
    expect(byId[seed.analyzedCallId].audio_metrics?.source?.object_key).toBe(PRIVATE_OBJECT_KEY);
    expect(byId[seed.notAnalyzedCallId].audio_metrics).toBeNull();
  });

  const apiAnalyzed = await test.step('api: the analyzed call\'s envelope is projected exactly, without the private key', async () => {
    const body = await actor.api.get<CallDetail>(callDetailPath(seed.analyzedCallId));
    expect(body.id).toBe(seed.analyzedCallId);
    expect(body.audio_metrics).toEqual(EXPECTED_ANALYZED);
    expect(JSON.stringify(body)).not.toContain(PRIVATE_OBJECT_KEY);
    return body.audio_metrics;
  });

  await test.step('api: the never-analyzed call reports not_requested / not_analyzed', async () => {
    const body = await actor.api.get<CallDetail>(callDetailPath(seed.notAnalyzedCallId));
    expect(body.audio_metrics).toEqual(EXPECTED_NOT_ANALYZED);
  });

  const { body: uiAnalyzed, region } = await test.step('ui: open the analyzed call in Call Analytics', async () => {
    return openCallAnalytics(page, runUrl, seed.analyzedCallId);
  }, { timeout: 3 * UI_READY });

  await test.step('ui: the drawer received the API envelope', async () => {
    expect(uiAnalyzed.audio_metrics).toEqual(apiAnalyzed);
  });

  await test.step('ui: pitch, SNR and an uncalibrated VQI under a Partial badge', async () => {
    await expect(region.getByRole('listitem')).toHaveText([
      /^Average pitch \(Hz\)\s*187\.4$/,
      /^Estimated SNR \(dB\)\s*23\.6$/,
      new RegExp(`^Voice quality index \\(0–5\\)\\s*Calibration not validated — Unavailable\\s*${VQI_NOTE.source}`),
    ], { timeout: UI_READY });
    await expect(region.getByText('Partial', { exact: true })).toBeVisible({ timeout: UI_READY });
    await expect(region.getByText('Capture: — · —', { exact: true })).toBeVisible({ timeout: UI_READY });
  }, { timeout: UI_READY });

  await test.step('ui: capture & version details and measurement limits', async () => {
    await region.getByRole('button', { name: 'Capture & version details' }).click({ timeout: UI_READY });
    await expect(detail(region, 'Pipeline version')).toHaveText(PIPELINE_VERSION, { timeout: UI_READY });
    await expect(detail(region, 'Calibration')).toHaveText('—');
    await expect(detail(region, 'Model version')).toHaveText('—');
    await expect(detail(region, 'Provider')).toHaveText('—');
    await expect(detail(region, 'Voice quality index (0–5) reason code')).toHaveText('not_validated');
    await expect(detail(region, 'Average pitch (Hz) reason code')).toHaveCount(0);
    await region.getByRole('button', { name: 'Measurement limits' }).click({ timeout: UI_READY });
    await expect(region.getByText(
      'VQI is a normalized perceptual prediction, not MOS or a naturalness certification.',
      { exact: true },
    )).toBeVisible({ timeout: UI_READY });
  }, { timeout: UI_READY });

  await test.step('ui: the raw JSON download carries the same audio_metrics', async () => {
    const download = page.waitForEvent('download', { timeout: UI_READY });
    await page.getByRole('button', { name: 'Download raw data', exact: true }).click({ timeout: UI_READY });
    const file = await (await download).path();
    const raw = await readFile(file, 'utf-8');
    await testInfo.attach('raw-export.json', { contentType: 'application/json', body: raw });
    const exported = JSON.parse(raw) as CallDetail;
    expect(exported.id).toBe(seed.analyzedCallId);
    expect(exported.audio_metrics).toEqual(EXPECTED_ANALYZED);
    expect(raw).not.toContain(PRIVATE_OBJECT_KEY);
  }, { timeout: UI_READY });

  await test.step('ui: the never-analyzed call says so on every row', async () => {
    const { body, region: notAnalyzed } = await openCallAnalytics(page, runUrl, seed.notAnalyzedCallId);
    expect(body.audio_metrics).toEqual(EXPECTED_NOT_ANALYZED);
    await expect(notAnalyzed.getByRole('listitem')).toHaveText([
      /^Average pitch \(Hz\)\s*Not analyzed — Unavailable$/,
      /^Estimated SNR \(dB\)\s*Not analyzed — Unavailable$/,
      new RegExp(`^Voice quality index \\(0–5\\)\\s*Not analyzed — Unavailable\\s*${VQI_NOTE.source}`),
    ], { timeout: UI_READY });
    await expect(notAnalyzed.getByText('Unavailable', { exact: true })).toBeVisible({ timeout: UI_READY });
  }, { timeout: 3 * UI_READY });
});
