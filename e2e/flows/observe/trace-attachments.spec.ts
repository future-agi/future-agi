import { createHash, randomUUID } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { crc32, deflateSync } from 'node:zlib';
import { request, type APIRequestContext, type Locator, type Response } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import type { TestActor } from '../../lib/provisioning';
import { sendOtlpExport, type OtlpKeyValue, type OtlpSpan } from '../../lib/otlp';
import { POLL, type StateProbe } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// The attachments stack (FI_ATTACHMENTS_*, the S3 double, the https origin and
// the sandboxed worker) is in docker-compose.e2e.yml only.
test.skip(process.env.E2E_STACK === 'standalone', 'Attachment storage and the https media origin are Distributed-only');
// E2E.attachmentsAppUrl is served with Caddy's internal CA (e2e/stack/attachments/Caddyfile).
test.use({ ignoreHTTPSErrors: true });

// tracer/urls.py (F01 routes); bodies are tracer/services/attachments/f01/contract.schema.json
// BootstrapRequest, UploadRequest and CompleteRequest.
const F01 = {
  session: '/tracer/v1/attachment-sessions',
  uploads: (project: string) => `/tracer/v1/projects/${project}/attachments/uploads`,
  content: (project: string, upload: string) => `/tracer/v1/projects/${project}/attachments/uploads/${upload}/content`,
  complete: (project: string, upload: string) => `/tracer/v1/projects/${project}/attachments/uploads/${upload}/complete`,
  resolve: (project: string) => `/tracer/v1/projects/${project}/attachments/resolve`,
  mediaSession: '/tracer/v1/attachment-media-sessions',
};
// tracer/serializers/project.py ProjectSerializer (also OBS-E2E-012).
const PROJECT = '/tracer/project/';
// e2e/stack/backend/e2e_attachment_scope.py: the operator opt-in; no product path exists.
const SCOPE_OPT_IN = '/tracer/e2e/attachment-scope/';
// TraceDetailDrawerV2 and CallLogsGrid read these (frontend/src/api/contracts).
const TRACE_DETAIL = (trace: string) => `/tracer/trace/${trace}/`;
const VOICE_DETAIL = '/tracer/trace/voice_call_detail/';
// A fresh scope starts at deletion generation 0 (tracer/models/attachment.py AttachmentScope).
const DELETION_GENERATION = 0;
// tracer/urls.py "v1/attachment-media/<occurrence_id>/<grant_id>" (the gateway every
// player must use; legacy recording URLs never go through it).
const GATEWAY_PREFIX = '/tracer/v1/attachment-media/';
// LiveKit roots as fi-collector clickhouse25exporter/converter.go liveKitJobRoot (and
// tracer/utils/otel.py resolve_observation_type) map them: an untyped, parentless
// job_entrypoint carrying lk.job_id is the conversation root; traceai_livekit binds
// the session recording on its agent_session child (view_enrichment.enrich_voice_detail).
const LIVEKIT_ROOT = 'job_entrypoint', LIVEKIT_SESSION = 'agent_session', LIVEKIT_JOB_ID = 'lk.job_id';
// Pipecat: TurnTraceObserver's root span is named "conversation"; traceai_pipecat
// attachments.mark_conversation_root sets both kinds to CONVERSATION and the
// recording binds to that root (python/voice-tests/test_pipecat_attachments.py).
const PIPECAT_ROOT = 'conversation', PIPECAT_KIND = 'CONVERSATION';
// The collector's lowercased observation_type for both roots (VoiceCallListQueryBuilder).
const CONVERSATION = 'conversation';
// Validated by the sandbox probe in tracer/tests/test_attachment_media_types.py
// (48 kHz mono Opus, 0.2 s); the only committed Ogg/Opus the probe is known to accept.
const RECORDING_OGG = readFileSync(new URL('../../../futureagi/tracer/tests/fixtures/attachments/tone.ogg', import.meta.url));
const UI_READY = 60_000;

type Media = { name: string; bytes: Buffer; mime: string; modality: string; direction: 'input' | 'output' | 'mixed';
  purpose: string; placement?: { message_index: number; part_index: number; role: string } };
type Seeded = Media & { attachmentId: string; occurrenceId: string; uploadId: string; parentSpanId: string };
type SpanAttachment = { reference: { occurrence_id: string; attachment_id: string; mime_type: string; modality: string;
  direction: string; purpose?: string; message_index?: number; part_index?: number; role?: string }; status: string };
type DetailEntry = { observation_span: { id: string; name: string; attachments?: SpanAttachment[] }; children: DetailEntry[] };

const sha256 = (bytes: Buffer) => createHash('sha256').update(bytes).digest('hex');
const hex = (bytes: number) => randomUUID().replaceAll('-', '').slice(0, bytes * 2);

/** 16 kHz mono PCM, 0.5 s; the tone frequency is minted so the bytes are this run's. */
function wav(frequency: number): Buffer {
  const rate = 16_000, frames = rate / 2, data = Buffer.alloc(frames * 2);
  for (let i = 0; i < frames; i += 1) data.writeInt16LE(Math.round(8000 * Math.sin(2 * Math.PI * frequency * i / rate)), i * 2);
  const header = Buffer.alloc(44);
  header.write('RIFF', 0); header.writeUInt32LE(36 + data.length, 4); header.write('WAVE', 8);
  header.write('fmt ', 12); header.writeUInt32LE(16, 16); header.writeUInt16LE(1, 20); header.writeUInt16LE(1, 22);
  header.writeUInt32LE(rate, 24); header.writeUInt32LE(rate * 2, 28); header.writeUInt16LE(2, 32); header.writeUInt16LE(16, 34);
  header.write('data', 36); header.writeUInt32LE(data.length, 40);
  return Buffer.concat([header, data]);
}

/** An 8x6 RGB PNG of one minted colour. */
function png(rgb: [number, number, number]): Buffer {
  const chunk = (type: string, body: Buffer) => {
    const len = Buffer.alloc(4); len.writeUInt32BE(body.length);
    const typed = Buffer.concat([Buffer.from(type, 'ascii'), body]);
    const crc = Buffer.alloc(4); crc.writeUInt32BE(crc32(typed) >>> 0);
    return Buffer.concat([len, typed, crc]);
  };
  const ihdr = Buffer.alloc(13); ihdr.writeUInt32BE(8, 0); ihdr.writeUInt32BE(6, 4); ihdr.set([8, 2, 0, 0, 0], 8);
  const row = Buffer.concat([Buffer.from([0]), ...Array.from({ length: 8 }, () => Buffer.from(rgb))]);
  return Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), chunk('IHDR', ihdr),
    chunk('IDAT', deflateSync(Buffer.concat(Array.from({ length: 6 }, () => row)))), chunk('IEND', Buffer.alloc(0))]);
}

/** A one-page PDF carrying a minted comment, with a correct xref table. */
function pdf(marker: string): Buffer {
  const objects = ['<</Type/Catalog/Pages 2 0 R>>', '<</Type/Pages/Kids[3 0 R]/Count 1>>',
    '<</Type/Page/Parent 2 0 R/MediaBox[0 0 72 72]>>'];
  let body = `%PDF-1.4\n% ${marker}\n`;
  const offsets = objects.map((object, i) => {
    const offset = Buffer.byteLength(body);
    body += `${i + 1} 0 obj\n${object}\nendobj\n`;
    return offset;
  });
  const xref = Buffer.byteLength(body);
  body += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
  body += offsets.map(o => `${String(o).padStart(10, '0')} 00000 n \n`).join('');
  body += `trailer\n<</Size ${objects.length + 1}/Root 1 0 R>>\nstartxref\n${xref}\n%%EOF\n`;
  return Buffer.from(body, 'latin1');
}

/** An SDK-style observe project (default source, never 'simulator'). */
async function createProject(actor: TestActor, name: string) {
  const body = await actor.api.post<{ result: { project_id: string; name: string } }>(PROJECT, {
    name, model_type: 'GenerativeLLM', trace_type: 'observe',
  });
  expect(body.result.name).toBe(name);
  const optIn = await actor.api.post<{ project_id: string; deletion_generation: number }>(SCOPE_OPT_IN,
    { project_id: body.result.project_id });
  expect(optIn).toMatchObject({ project_id: body.result.project_id, deletion_generation: DELETION_GENERATION });
  return body.result.project_id;
}

/** F01 bootstrap -> initialize -> PUT -> complete, each with the actor's org API key. */
async function upload(api: APIRequestContext, actor: TestActor, projectName: string, projectId: string,
  traceHex: string, media: (Media & { parentSpanId: string })[]) {
  const keys = { 'X-Api-Key': actor.apiKey, 'X-Secret-Key': actor.secretKey };
  const session = { capture_session_id: randomUUID(), correlation_nonce: randomUUID() };
  const boot = await api.post(F01.session, {
    headers: { ...keys, 'Idempotency-Key': randomUUID() },
    data: { schema_versions: [1], project_name: projectName, project_type: 'observe', ...session },
  });
  expect(boot.status(), await boot.text()).toBe(200);
  const booted = await boot.json() as { scope: { project_id: string }; policy: { version: string; enabled: boolean } };
  expect(booted.scope.project_id).toBe(projectId);
  expect(booted.policy.enabled).toBe(true);
  const seeded: Seeded[] = [];
  for (const item of media) {
    const ids = { attachment_id: randomUUID(), occurrence_id: randomUUID() };
    const init = await api.post(F01.uploads(projectId), {
      headers: { ...keys, 'Idempotency-Key': randomUUID() },
      data: {
        schema_version: 1, ...ids,
        association: { ...session, parent: { trace_id: traceHex, span_id: item.parentSpanId } },
        policy_version: booted.policy.version, deletion_generation: DELETION_GENERATION,
        mime_type: item.mime, modality: item.modality, direction: item.direction, source_kind: 'Bytes',
        purpose: item.purpose, declared_bytes: item.bytes.length,
        ...(item.placement ? { message_index: item.placement.message_index, part_index: item.placement.part_index } : {}),
      },
    });
    expect(init.status(), await init.text()).toBe(201);
    const created = await init.json() as { upload_id: string; write_capability: string; state: string };
    expect(created.state).toBe('pending_upload');
    const bearer = { Authorization: `Bearer ${created.write_capability}` };
    const put = await api.put(F01.content(projectId, created.upload_id), {
      headers: { ...bearer, 'Content-Type': 'application/octet-stream' }, data: item.bytes,
    });
    expect(put.status(), await put.text()).toBe(200);
    const done = await api.post(F01.complete(projectId, created.upload_id), {
      headers: { ...bearer, 'Idempotency-Key': randomUUID() },
      data: { policy_version: booted.policy.version, deletion_generation: DELETION_GENERATION },
    });
    expect(done.status(), await done.text()).toBe(202);
    seeded.push({ ...item, attachmentId: ids.attachment_id, occurrenceId: ids.occurrence_id, uploadId: created.upload_id });
  }
  return { session, seeded };
}

const str = (key: string, value: string): OtlpKeyValue => ({ key, value: { stringValue: value } });
/** F01 SpanAttributes: schema_version is an OTLP int (fi-collector normalization/attachments.go). */
function carriers(session: { capture_session_id: string; correlation_nonce: string }, items: Seeded[]): OtlpKeyValue[] {
  return [
    { key: 'fi.attachment.schema_version', value: { intValue: '1' } },
    str('fi.attachment.refs', JSON.stringify(items.map(item => ({
      attachment_id: item.attachmentId, occurrence_id: item.occurrenceId, direction: item.direction,
      modality: item.modality, mime_type: item.mime, purpose: item.purpose, ...(item.placement ?? {}),
    })))),
    str('fi.attachment.capture_session_id', session.capture_session_id),
    str('fi.attachment.correlation_nonce', session.correlation_nonce),
  ];
}

function flatten(entries: DetailEntry[]): DetailEntry['observation_span'][] {
  return entries.flatMap(entry => [entry.observation_span, ...flatten(entry.children ?? [])]);
}

async function readyOccurrences(probe: StateProbe, projectId: string, items: Seeded[]) {
  return probe.pg<{ occurrence: string; attachment: string; trace_id: string; span_id: string; bound: boolean;
    deleted: boolean; message_index: number | null; part_index: number | null; state: string; mime_type: string;
    blob_sha256: string | null }>(
    `SELECT o.client_id::text AS occurrence, a.client_id::text AS attachment, o.trace_id::text AS trace_id, o.span_id,
            o.bound_at IS NOT NULL AS bound, o.deleted_at IS NOT NULL AS deleted, o.message_index, o.part_index,
            a.state, a.mime_type, b.sha256 AS blob_sha256
       FROM tracer_attachmentoccurrence o
       JOIN tracer_attachmentscope s ON s.id = o.scope_id
       JOIN tracer_attachment a ON a.id = o.attachment_id
       LEFT JOIN tracer_attachmentblob b ON b.attachment_id = a.id
      WHERE s.project_id = $1 AND o.client_id = ANY($2::uuid[])
      ORDER BY o.client_id`,
    [projectId, items.map(item => item.occurrenceId)]);
}

function expectedOccurrences(items: Seeded[], traceId: string) {
  return items.map(item => ({
    occurrence: item.occurrenceId, attachment: item.attachmentId, trace_id: traceId, span_id: item.parentSpanId,
    bound: true, deleted: false, message_index: item.placement?.message_index ?? null,
    part_index: item.placement?.part_index ?? null, state: 'ready', mime_type: item.mime, blob_sha256: sha256(item.bytes),
  })).sort((a, b) => a.occurrence.localeCompare(b.occurrence));
}

const isResolve = (projectId: string) => (response: Response) =>
  new URL(response.url()).pathname === F01.resolve(projectId) && response.request().method() === 'POST';
const dashed = (hex32: string) =>
  `${hex32.slice(0, 8)}-${hex32.slice(8, 12)}-${hex32.slice(12, 16)}-${hex32.slice(16, 20)}-${hex32.slice(20)}`;
type Grant = { occurrence_id: string; status: string; gateway_path?: string; expires_at?: string };
type VoiceDetail = { result: { recording_attachment?: { occurrence_id: string; state: string;
  parent: { trace_id: string; span_id: string }; manifest?: { duration_ms: number; layout: string; codec: string; container: string } } } };

test('OBS-E2E-066: a recorded voice call and a traced image play and render on the trace for a signed-in user', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'OBS-E2E-066', area: 'observe',
    userGoal: 'A developer whose LiveKit and Pipecat agents uploaded call recordings, and whose model saw an image, audio and a PDF, opens each trace, plays the recordings and sees the image where the model saw it',
    steps: [
      'create a voice project and an observe project (SDK source, not simulator) and opt both into attachments (operator step, test-image endpoint)',
      'with the org API key, bootstrap an F01 capture session per trace and upload a LiveKit and a Pipecat Ogg/Opus call recording, a WAV, a placed PNG and a PDF (initialize, PUT, complete)',
      'send through the collector a LiveKit call (untyped job_entrypoint root with lk.job_id, agent_session child carrying the recording), a Pipecat call (CONVERSATION root carrying the recording) and the model trace',
      'on the https app origin, open the LiveKit trace, select agent_session, load its recording and play it to the end',
      'open the Pipecat trace and play the root\'s recording to the end',
      'open the model trace, select the model span, switch to Chat and see the PNG inside user message 2',
      'load the WAV, and download the PDF, which has no inline preview',
    ],
    backendChecks: [
      'PG: each seeded occurrence is bound to its exact trace/span, not deleted, with its placement, its attachment ready and its blob sha256 equal to the uploaded bytes',
      'CH spans FINAL: each parent span carries exactly the seeded fi.attachment.refs, and both voice roots (the untyped LiveKit job_entrypoint and the Pipecat root) are stored as parentless conversation roots',
      'API: voice detail projects each call\'s recording as ready on its own parent span with a 200 ms mixed Ogg/Opus manifest, and the trace detail projects exactly the seeded attachments as ready',
      'API: with the viewer\'s media session the gateway serves the whole recording byte-identical (sha256), answers Range with 206 and HEAD with the full length, and a renewed grant continues the same bytes; without the session it is denied and resolve without credentials answers 401',
      'API: every media request the page made went through the gateway for exactly the three loaded audio occurrences (zero legacy requests)',
    ],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  // Approved plan budget (CONTRACT-STATUS "OBS-E2E-061 revised plan"): seeding, two
  // ASYNC_JOB binds, SPAN_VISIBLE and the UI_READY steps fit with headroom.
  test.setTimeout(600_000);
  const api = await request.newContext({ baseURL: E2E.apiUrl });
  const anonymous = await request.newContext({ baseURL: E2E.attachmentsAppUrl, ignoreHTTPSErrors: true });
  // Every media fetch the page makes; the last step asserts they all used the gateway.
  const media: string[] = [];
  page.on('request', r => { if (r.resourceType() === 'media') media.push(new URL(r.url()).pathname); });
  try {
    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const voiceName = `e2e-att-voice-${suffix}`, traceName = `e2e-att-trace-${suffix}`;
    const jobId = `e2e-att-job-${suffix}`, userText = `e2e-att-user-${suffix}`;
    const voiceProject = await createProject(actor, voiceName);
    const traceProject = await createProject(actor, traceName);
    const livekitTrace = hex(16), pipecatTrace = hex(16), trace = hex(16);
    const livekitRoot = hex(8), livekitSession = hex(8), pipecatRoot = hex(8), traceRoot = hex(8), llmSpan = hex(8);
    const tone = 300 + (Date.now() % 500);
    const colour: [number, number, number] = [Date.now() % 256, 40, 200];
    const recordingMedia = { bytes: RECORDING_OGG, mime: 'audio/ogg', modality: 'audio', direction: 'mixed' as const,
      purpose: 'conversation_recording' };

    const livekit = await upload(api, actor, voiceName, voiceProject, livekitTrace, [
      { name: 'livekit-recording', ...recordingMedia, parentSpanId: livekitSession },
    ]);
    const pipecat = await upload(api, actor, voiceName, voiceProject, pipecatTrace, [
      { name: 'pipecat-recording', ...recordingMedia, parentSpanId: pipecatRoot },
    ]);
    const traced = await upload(api, actor, traceName, traceProject, trace, [
      { name: 'image', bytes: png(colour), mime: 'image/png', modality: 'image', direction: 'input',
        purpose: 'model_input', placement: { message_index: 1, part_index: 1, role: 'user' }, parentSpanId: llmSpan },
      { name: 'audio', bytes: wav(tone), mime: 'audio/wav', modality: 'audio', direction: 'input',
        purpose: 'turn_recording', parentSpanId: llmSpan },
      { name: 'pdf', bytes: pdf(`e2e-att-${suffix}`), mime: 'application/pdf', modality: 'document', direction: 'input',
        purpose: 'document', placement: { message_index: 1, part_index: 2, role: 'user' }, parentSpanId: llmSpan },
    ]);
    const [livekitRecording] = livekit.seeded;
    const [pipecatRecording] = pipecat.seeded;
    const [image, audio, document] = traced.seeded;

    const start = BigInt(Date.now() - 5_000) * 1_000_000n, end = start + 3_000_000_000n;
    const span = (traceId: string, spanId: string, name: string, attributes: OtlpKeyValue[], parentSpanId?: string): OtlpSpan => ({
      traceId, spanId, name, kind: 1, startTimeUnixNano: String(start), endTimeUnixNano: String(end),
      ...(parentSpanId ? { parentSpanId } : {}), attributes,
    });
    const resource = (projectName: string) => ({ attributes: [str('project_name', projectName), str('project_type', 'observe')] });
    const messages = JSON.stringify([
      { role: 'system', content: 'Describe what the user shows you.' },
      { role: 'user', content: [{ type: 'text', text: userText },
        { type: 'image_url', image_url: { url: `fi-attachment:${image.attachmentId}` } },
        { type: 'file', file: { file_id: `fi-attachment:${document.attachmentId}` } }] },
    ]);
    const cfg = { collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey, secretKey: actor.secretKey };
    await sendOtlpExport(api, cfg, { resourceSpans: [{ resource: resource(voiceName), scopeSpans: [{
      scope: { name: 'livekit-agents' }, spans: [
        // No fi.span.kind: the LiveKit SDK marks no root; the collector maps it.
        span(livekitTrace, livekitRoot, LIVEKIT_ROOT, [str(LIVEKIT_JOB_ID, jobId)]),
        span(livekitTrace, livekitSession, LIVEKIT_SESSION, [...carriers(livekit.session, [livekitRecording])], livekitRoot),
      ] }, {
      scope: { name: 'pipecat' }, spans: [
        span(pipecatTrace, pipecatRoot, PIPECAT_ROOT, [str('fi.span.kind', PIPECAT_KIND), str('gen_ai.span.kind', PIPECAT_KIND),
          ...carriers(pipecat.session, [pipecatRecording])]),
      ] }] }] });
    await sendOtlpExport(api, cfg, { resourceSpans: [{ resource: resource(traceName), scopeSpans: [{
      scope: { name: 'e2e-attachments' }, spans: [
        span(trace, traceRoot, `e2e.att-agent-${suffix}`, [str('fi.span.kind', 'agent')]),
        span(trace, llmSpan, `e2e.att-llm-${suffix}`, [str('fi.span.kind', 'llm'), str('input.value', messages),
          str('input.mime_type', 'application/json'), str('output.value', `e2e-att-answer-${suffix}`),
          ...carriers(traced.session, [image, audio, document])], traceRoot),
      ] }] }] });
    const livekitTraceId = dashed(livekitTrace), pipecatTraceId = dashed(pipecatTrace), traceId = dashed(trace);
    const voiceSeeded = [livekitRecording, pipecatRecording];
    await testInfo.attach('seeded', { contentType: 'application/json', body: JSON.stringify({
      voiceProject, traceProject, livekitTraceId, pipecatTraceId, traceId, livekitRoot, livekitSession, pipecatRoot,
      traceRoot, llmSpan, jobId,
      occurrences: [...voiceSeeded, ...traced.seeded].map(({ bytes, ...rest }) => ({ ...rest, sha256: sha256(bytes), size: bytes.length })),
    }) });

    await test.step('storage: PG occurrences bound and ready with placement', async () => {
      await expect.poll(async () => [
        ...await readyOccurrences(probe, voiceProject, [livekitRecording]),
        ...await readyOccurrences(probe, voiceProject, [pipecatRecording]),
      ], POLL.ASYNC_JOB).toStrictEqual([
        ...expectedOccurrences([livekitRecording], livekitTraceId), ...expectedOccurrences([pipecatRecording], pipecatTraceId)]);
      await expect.poll(() => readyOccurrences(probe, traceProject, traced.seeded), POLL.ASYNC_JOB)
        .toStrictEqual(expectedOccurrences(traced.seeded, traceId));
    });

    await test.step('storage: CH spans FINAL carry the refs; both voice roots are conversation roots', async () => {
      const refs = async (traceKey: string) => (await probe.ch<{ id: string; refs: string }>(
        `SELECT id, attrs_string['fi.attachment.refs'] AS refs FROM spans FINAL
          WHERE trace_id={t:String} AND is_deleted=0 AND refs != '' ORDER BY id`, { t: traceKey }))
        .map(row => ({ id: row.id, occurrences: (JSON.parse(row.refs) as { occurrence_id: string }[]).map(r => r.occurrence_id).sort() }));
      const roots = (traceKey: string) => probe.ch<{ id: string; name: string; observation_type: string }>(
        `SELECT id, name, observation_type FROM spans FINAL
          WHERE trace_id={t:String} AND is_deleted=0 AND (parent_span_id IS NULL OR parent_span_id = '') ORDER BY id`, { t: traceKey });
      await expect.poll(() => refs(livekitTraceId), POLL.SPAN_VISIBLE)
        .toStrictEqual([{ id: livekitSession, occurrences: [livekitRecording.occurrenceId] }]);
      await expect.poll(() => refs(pipecatTraceId), POLL.SPAN_VISIBLE)
        .toStrictEqual([{ id: pipecatRoot, occurrences: [pipecatRecording.occurrenceId] }]);
      await expect.poll(() => refs(traceId), POLL.SPAN_VISIBLE)
        .toStrictEqual([{ id: llmSpan, occurrences: traced.seeded.map(item => item.occurrenceId).sort() }]);
      await expect.poll(async () => [...await roots(livekitTraceId), ...await roots(pipecatTraceId)], POLL.SPAN_VISIBLE)
        .toStrictEqual([{ id: livekitRoot, name: LIVEKIT_ROOT, observation_type: CONVERSATION },
          { id: pipecatRoot, name: PIPECAT_ROOT, observation_type: CONVERSATION }]);
    });

    await test.step('API: voice detail projects each call\'s recording on its own parent span', async () => {
      const projection = async (voiceTraceId: string) => (await actor.api.get<VoiceDetail>(VOICE_DETAIL,
        { trace_id: voiceTraceId, project_id: voiceProject })).result.recording_attachment ?? null;
      const ready = (item: Seeded, voiceTraceId: string) => ({
        occurrence_id: item.occurrenceId, state: 'ready', parent: { trace_id: voiceTraceId, span_id: item.parentSpanId },
        manifest: expect.objectContaining({ duration_ms: 200, layout: 'mixed', codec: 'opus', container: 'ogg' }),
      });
      await expect.poll(() => projection(livekitTraceId), { timeout: UI_READY }).toEqual(ready(livekitRecording, livekitTraceId));
      await expect.poll(() => projection(pipecatTraceId), { timeout: UI_READY }).toEqual(ready(pipecatRecording, pipecatTraceId));
    });

    const tracesTab = (projectId: string) => `${E2E.attachmentsAppUrl}/dashboard/observe/${projectId}/llm-tracing?tab=traces`;
    const openTrace = async (projectId: string, id: string) => {
      const detail = page.waitForResponse(r => new URL(r.url()).pathname === TRACE_DETAIL(id)
        && r.request().method() === 'GET', { timeout: UI_READY });
      await page.goto(tracesTab(projectId));
      await page.locator(`.clean-data-table:visible .ag-row[row-id="${id}"] [col-id="trace_name"]`).click({ timeout: UI_READY });
      const body = await (await detail).json() as { result: { observation_spans: DetailEntry[] } };
      return { spans: flatten(body.result.observation_spans),
        drawer: page.locator('.MuiDrawer-paper:visible').filter({ has: page.getByText(id, { exact: true }) }) };
    };
    /** Load the span's recording and play it to the end; returns the browser's grant and resolve headers. */
    const playRecording = async (drawer: Locator, item: Seeded, name: string) => {
      const listed = drawer.getByLabel('Span attachments');
      const resolved = page.waitForResponse(isResolve(voiceProject), { timeout: UI_READY });
      await listed.getByRole('button', { name: 'Load audio: Mixed recording' }).click({ timeout: UI_READY });
      const response = await resolved;
      const grants = await response.json() as { items: Grant[] };
      expect(grants.items.map(g => [g.occurrence_id, g.status])).toStrictEqual([[item.occurrenceId, 'ready']]);
      const player = listed.getByLabel('Mixed recording').and(listed.locator('audio'));
      await expect(player).toHaveCount(1);
      await expect.poll(() => player.evaluate((node: HTMLAudioElement) => node.readyState >= 1), { timeout: UI_READY }).toBe(true);
      await player.evaluate((node: HTMLAudioElement) => node.play());
      await expect.poll(() => player.evaluate((node: HTMLAudioElement) =>
        [node.ended, node.played.length > 0 ? Math.round(node.played.end(node.played.length - 1) * 1000) >= 150 : false]),
      { timeout: UI_READY }).toStrictEqual([true, true]);
      await testInfo.attach(name, { body: await page.screenshot(), contentType: 'image/png' });
      const sent = await response.request().allHeaders();
      const headers: Record<string, string> = { Origin: E2E.attachmentsAppUrl, 'Content-Type': 'application/json' };
      for (const key of ['authorization', 'x-organization-id', 'x-workspace-id']) if (sent[key]) headers[key] = sent[key];
      return { grant: grants.items[0], headers };
    };

    let livekitGrant: Grant = { occurrence_id: '', status: '' };
    let viewerHeaders: Record<string, string> = {};
    await test.step('UI: the LiveKit call recording on agent_session plays to its end', async () => {
      const { spans, drawer } = await openTrace(voiceProject, livekitTraceId);
      expect(spans.filter(s => s.attachments?.length).map(s => ({ id: s.id, name: s.name,
        attachments: s.attachments!.map(a => [a.reference.occurrence_id, a.status]) })))
        .toStrictEqual([{ id: livekitSession, name: LIVEKIT_SESSION, attachments: [[livekitRecording.occurrenceId, 'ready']] }]);
      await drawer.getByText(LIVEKIT_SESSION, { exact: true }).first().click({ timeout: UI_READY });
      ({ grant: livekitGrant, headers: viewerHeaders } = await playRecording(drawer, livekitRecording, 'livekit-player'));
    });

    const viewer = page.context().request;
    const gateway = (path: string) => `${E2E.attachmentsAppUrl}${path}`;
    const resolveAs = (headers: Record<string, string>, item: Seeded, traceKey: string) =>
      viewer.post(gateway(F01.resolve(voiceProject)), { headers, data: { items: [{ occurrence_id: item.occurrenceId,
        parent: { trace_id: traceKey, span_id: item.parentSpanId }, representation: 'original' }] } });
    await test.step('API: the gateway serves the whole recording, Range, HEAD and a renewed grant; no session is denied', async () => {
      const path = livekitGrant.gateway_path!;
      expect(path.startsWith(`${GATEWAY_PREFIX}${livekitRecording.occurrenceId}/`)).toBe(true);
      const whole = await viewer.get(gateway(path));
      expect(whole.status()).toBe(200);
      expect(whole.headers()['content-type']).toBe('audio/ogg');
      expect(sha256(Buffer.from(await whole.body()))).toBe(sha256(RECORDING_OGG));
      const ranged = await viewer.get(gateway(path), { headers: { Range: 'bytes=0-3' } });
      expect(ranged.status()).toBe(206);
      expect(ranged.headers()['content-range']).toBe(`bytes 0-3/${RECORDING_OGG.length}`);
      expect(Buffer.from(await ranged.body()).equals(RECORDING_OGG.subarray(0, 4))).toBe(true);
      // Renewal: a fresh resolve mints a new grant for the same occurrence, and the
      // player's next Range continues the same byte timeline from where it left off.
      const renewal = await resolveAs(viewerHeaders, livekitRecording, livekitTraceId);
      expect(renewal.status(), await renewal.text()).toBe(200);
      const [renewed] = (await renewal.json() as { items: Grant[] }).items;
      expect([renewed.occurrence_id, renewed.status]).toStrictEqual([livekitRecording.occurrenceId, 'ready']);
      expect(renewed.gateway_path).not.toBe(path);
      const renewedPath = renewed.gateway_path!;
      const middle = Math.floor(RECORDING_OGG.length / 2);
      const resumed = await viewer.get(gateway(renewedPath), { headers: { Range: `bytes=${middle}-` } });
      expect(resumed.status()).toBe(206);
      expect(resumed.headers()['content-range']).toBe(`bytes ${middle}-${RECORDING_OGG.length - 1}/${RECORDING_OGG.length}`);
      expect(Buffer.from(await resumed.body()).equals(RECORDING_OGG.subarray(middle))).toBe(true);
      const head = await viewer.head(gateway(renewedPath));
      expect([head.status(), head.headers()['content-type'], head.headers()['content-length']])
        .toStrictEqual([200, 'audio/ogg', String(RECORDING_OGG.length)]);
      const denied = await anonymous.get(path, { headers: { Range: 'bytes=0-3' } });
      expect(denied.status()).toBe(404);
      const unauthenticated = await anonymous.post(F01.resolve(voiceProject), {
        headers: { Origin: E2E.attachmentsAppUrl, 'Content-Type': 'application/json' },
        data: { items: [{ occurrence_id: livekitRecording.occurrenceId,
          parent: { trace_id: livekitTraceId, span_id: livekitSession }, representation: 'original' }] } });
      expect(unauthenticated.status()).toBe(401);
    });

    await test.step('UI: the Pipecat call recording on its root plays to its end', async () => {
      const { spans, drawer } = await openTrace(voiceProject, pipecatTraceId);
      expect(spans.filter(s => s.attachments?.length).map(s => ({ id: s.id, name: s.name,
        attachments: s.attachments!.map(a => [a.reference.occurrence_id, a.status]) })))
        .toStrictEqual([{ id: pipecatRoot, name: PIPECAT_ROOT, attachments: [[pipecatRecording.occurrenceId, 'ready']] }]);
      await playRecording(drawer, pipecatRecording, 'pipecat-player');
    });

    await test.step('UI: the model span shows the image in user message 2', async () => {
      const { spans, drawer } = await openTrace(traceProject, traceId);
      expect(spans.filter(s => s.attachments?.length).map(s => ({ id: s.id,
        attachments: s.attachments!.map(a => [a.reference.occurrence_id, a.status]).sort() })))
        .toStrictEqual([{ id: llmSpan, attachments: traced.seeded.map(item => [item.occurrenceId, 'ready']).sort() }]);
      await drawer.getByText(`e2e.att-llm-${suffix}`, { exact: true }).first().click({ timeout: UI_READY });
      await drawer.getByRole('tab', { name: 'Chat', exact: true }).click({ timeout: UI_READY });
      const message = drawer.locator('div').filter({ hasText: userText }).filter({ has: page.getByLabel('Message images') }).last();
      const inline = message.getByLabel('Message images').getByRole('img');
      await expect(inline).toHaveCount(1, { timeout: UI_READY });
      await expect(inline).toHaveAttribute('alt', 'Input · message 2 · part 2 image');
      await expect.poll(() => inline.evaluate((img: HTMLImageElement) => [img.complete, img.naturalWidth, img.naturalHeight]),
        { timeout: UI_READY }).toStrictEqual([true, 8, 6]);
      await expect(drawer.getByLabel('Message images').getByRole('img')).toHaveCount(1);
      await testInfo.attach('inline-image', { body: await page.screenshot(), contentType: 'image/png' });
    });

    await test.step('UI: the span lists exactly the WAV and the download-only PDF', async () => {
      const drawer = page.locator('.MuiDrawer-paper:visible').filter({ has: page.getByText(traceId, { exact: true }) });
      const listed = drawer.getByLabel('Span attachments');
      // groupByPosition: message 2 (the PDF) sorts before the unplaced WAV.
      await expect(listed.getByRole('button')).toHaveText(['Download', 'Load audio'], { timeout: UI_READY });
      await expect(listed.getByText('Preview unavailable · download to inspect', { exact: true })).toHaveCount(1);
      await expect(listed.getByRole('img')).toHaveCount(0);
      await expect(drawer.locator('iframe, embed, object')).toHaveCount(0);
      await listed.getByRole('button', { name: 'Load audio: Input audio' }).click();
      const player = listed.locator('audio');
      await expect(player).toHaveCount(1);
      await expect.poll(() => player.evaluate((node: HTMLAudioElement) => [node.readyState >= 1, Math.round(node.duration * 10)]),
        { timeout: UI_READY }).toStrictEqual([true, 5]);
      const downloading = page.waitForEvent('download', { timeout: UI_READY });
      await listed.getByRole('button', { name: 'Download Input · message 2 · part 3 PDF' }).click();
      const download = await downloading;
      // The gateway's `Content-Disposition: attachment; filename="document.pdf"`
      // (tracer/services/attachments/playback.py SERVED) wins over the link's name.
      expect(download.suggestedFilename()).toBe('document.pdf');
      const saved = await download.path();
      expect(sha256(readFileSync(saved!))).toBe(sha256(document.bytes));
      await testInfo.attach('span-attachments', { body: await page.screenshot(), contentType: 'image/png' });
    });

    await test.step('API: every media request the page made went through the gateway (zero legacy requests)', async () => {
      expect(media.every(path => path.startsWith(GATEWAY_PREFIX)), JSON.stringify(media)).toBe(true);
      expect([...new Set(media.map(path => path.slice(GATEWAY_PREFIX.length).split('/')[0]))].sort())
        .toStrictEqual([livekitRecording.occurrenceId, pipecatRecording.occurrenceId, audio.occurrenceId].sort());
    });
    // NOT COVERED (CONTRACT-STATUS 2026-11-14 e2e-obs-061 fix): the approved plan's deletion
    // check. Collector-ingested traces have no PG tracer_trace row, so DELETE /tracer/trace/{id}/
    // answers 404 and nothing tombstones the call's media; the product fix is owed in #3945.
  } finally {
    await api.dispose();
    await anonymous.dispose();
  }
});
