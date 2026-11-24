import { createHash, randomUUID } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { crc32, deflateSync } from 'node:zlib';
import { request, type APIRequestContext, type Locator, type Response } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import type { TestActor } from '../../lib/provisioning';
import { sendOtlpExport, type OtlpKeyValue, type OtlpSpan } from '../../lib/otlp';
import { POLL } from '../../lib/state-probe';
import { assertReadyAttachmentOccurrences, assertImmutableAttachmentFacts, readAttachmentOccurrences,
  readAttachmentRefs, readAttachmentRoots, readAttachmentMessages, readAttachmentSessions,
  type AttachmentSeedFacts, type AttachmentRow } from '../../lib/attachment-probe';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// The attachments stack (FI_ATTACHMENTS_*, the S3 double, the https origin and
// the sandboxed worker) is in docker-compose.e2e.yml only.
test.skip(process.env.E2E_STACK === 'standalone', 'Attachment storage and the https media origin are Distributed-only');
// E2E.attachmentsAppUrl is served with Caddy's internal CA (e2e/stack/attachments/Caddyfile).
test.use({ ignoreHTTPSErrors: true, viewport: {width: 1440, height: 900} });

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
// TraceSessionView.destroy supports collector-only sessions (unlike the PG-only
// trace DELETE). The target below is read from CH, never inferred from a trace id.
const SESSION = (session: string) => `/tracer/trace-session/${session}/`;
// One-sided diagnostic: the deletion assertion remains unchanged when skipped.
const SKIP_DELETE = process.env.E2E_ATTACHMENTS_SKIP_DELETE === '1';
// New one-sided diagnostics (original perturb.py anchors and SKIP_DELETE stay intact).
// Change only an expected fact or only an input below, never both. None is runtime proof until run.
const DIAGNOSTIC = process.env.E2E_ATTACHMENTS_066_MUTATION ?? '';
const DIAGNOSTICS = ['', 'pg-digest', 'pg-length', 'pg-parent', 'image-uri', 'pdf-uri',
  'body-digest', 'authorized-occurrence-negative'];
// A fresh scope starts at deletion generation 0 (tracer/models/attachment.py AttachmentScope).
const DELETION_GENERATION = 0;
// tracer/urls.py "v1/attachment-media/<media_session_id>/<occurrence_id>/<grant_id>"
// (F01 traceAI #675: the gateway every player must use; legacy recording URLs and
// the old two-identity route never go through it).
const GATEWAY_PREFIX = '/tracer/v1/attachment-media/';
// F01 MediaGatewayPath: two canonical non-nil UUIDs and an opaque grant token,
// bounded to 230 characters. Validate the whole route before reading its parts.
const gatewayIds = (path: string) => {
  expect(path.length).toBeLessThanOrEqual(230);
  expect(path.startsWith(GATEWAY_PREFIX)).toBe(true);
  const ids = path.slice(GATEWAY_PREFIX.length).split('/');
  expect(ids).toHaveLength(3);
  for (const id of ids.slice(0, 2)) {
    expect(id).toMatch(/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/);
    expect(id).not.toBe('00000000-0000-0000-0000-000000000000');
  }
  expect(ids[2]).toMatch(/^[a-zA-Z0-9_-]{1,128}$/);
  return ids;
};
// playback.py SESSION_HEADER: the non-secret handle the browser's own exchange
// returned. API replays of resolve must name it with the same bearer and org.
const MEDIA_SESSION_HEADER = 'x-attachment-media-session';
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
// The LiveKit call's recording: synthetic tones, not speech. 48 kHz mono Ogg/Opus from
// ffmpeg/libopus (as tone.ogg), 7 s: amplitude-modulated bursts at 0.5–1.7, 2.5–4.2 and
// 4.5–6.2 s with digital silence between; decodes to 336,000 frames (media_worker
// duration_ms = 7000). Pipecat keeps the 0.2 s tone as the no-peaks control.
const LIVEKIT_OGG = readFileSync(new URL('../../fixtures/attachments/livekit-call.ogg', import.meta.url));
const DURATION_MS = { livekit: 7000, pipecat: 200 } as const;
// The seeded LiveKit call, in ms after it starts. The transcript text is seeded with the
// trace; it is not recognised from the tones. Each turn states its call offset (which the
// Voice transcript seeks by) and its wall clock (which tool spans share).
const TURNS = [
  { role: 'user', content: 'Is my order on its way?', start: 500, end: 1700 },
  { role: 'assistant', speaker_name: 'Fulfillment', content: "I'm checking your order.", start: 2500, end: 4200 },
  { role: 'assistant', speaker_name: 'Fulfillment', content: 'It shipped today.', start: 4500, end: 6200 },
] as const;
// traceAI's LiveKit exporter keeps LiveKit's span name and carries the tool's
// name in attributes (traceai_livekit exporters/base_exporter.py).
const LOOKUP = { name: 'lookup_order', span: 'function_tool', start: 3000, end: 4000 } as const;
const INVENTORY = { name: 'check_inventory', start: 1000, end: 1400 } as const;
// RecordingSeekBar WAVEFORM_BARS and SLOT.
const WAVEFORM_BARS = 96, BAR_SLOT = 3;
// Ended, and played at least `min` ms in total: the sum of the played ranges,
// not where the last one ends.
const endedAfter = (node: HTMLAudioElement, min: number) => {
  let ms = 0;
  for (let i = 0; i < node.played.length; i++) ms += (node.played.end(i) - node.played.start(i)) * 1000;
  return [node.ended, Math.round(ms) >= min];
};
const UI_READY = 60_000;

type Media = { name: string; bytes: Buffer; mime: string; modality: string; direction: 'input' | 'output' | 'mixed';
  purpose: string; placement?: { message_index: number; part_index: number; role: string } };
type Seeded = Media & { attachmentId: string; occurrenceId: string; uploadId: string; parentSpanId: string;
  facts: Readonly<AttachmentSeedFacts> };
type SpanAttachment = { reference: { occurrence_id: string; attachment_id: string; mime_type: string; modality: string;
  direction: string; purpose?: string; message_index?: number; part_index?: number; role?: string }; status: string };
// CH trace_detail.py:_parse_content returns parsed input, not a carrier-helper projection.
type DetailEntry = { observation_span: { id: string; name: string; input: unknown; output?: unknown; attachments?: SpanAttachment[] }; children: DetailEntry[] };

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
    // Freeze the independent oracle BEFORE any upload request. Never derive expected
    // bytes/hash/parent/placement from a serializer, read-back, carrier or mutable request.
    const facts: Readonly<AttachmentSeedFacts> = Object.freeze({
      occurrence: ids.occurrence_id, attachment: ids.attachment_id,
      organization_id: actor.organizationId, workspace_id: actor.workspaceId, project_id: projectId,
      capture_session: session.capture_session_id, correlation_nonce: session.correlation_nonce, writer_kind: 'api_key',
      trace_id: dashed(traceHex), span_id: item.parentSpanId, direction: item.direction, purpose: item.purpose,
      message_index: item.placement?.message_index ?? null, part_index: item.placement?.part_index ?? null,
      // F01 UploadRequest has indices but no role/channel; contract.py:PLACEMENT_FIELDS
      // keeps those reference-only fields NULL on upload-backed occurrences. The OTLP
      // carrier still has its original user role; do not turn that into a PG oracle.
      role: null, mime_type: item.mime, verified_mime_type: item.mime,
      verified_byte_length: String(item.bytes.length), verified_sha256: sha256(item.bytes),
    });
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
    seeded.push({ ...item, facts, attachmentId: ids.attachment_id, occurrenceId: ids.occurrence_id, uploadId: created.upload_id });
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

const isResolve = (projectId: string) => (response: Response) =>
  new URL(response.url()).pathname === F01.resolve(projectId) && response.request().method() === 'POST';
/** The browser's own resolve credentials: bearer, org, workspace and its media-session handle. */
const browserResolveHeaders = async (response: Response, origin: string) => {
  const sent = await response.request().allHeaders();
  expect(response.status()).toBe(200);
  expect(sent.authorization).toMatch(/^Bearer \S+$/);
  expect(sent.origin).toBe(origin);
  expect(sent[MEDIA_SESSION_HEADER]).toMatch(/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/);
  expect(sent[MEDIA_SESSION_HEADER]).not.toBe('00000000-0000-0000-0000-000000000000');
  const grants = await response.json() as { items: Grant[] };
  for (const grant of grants.items.filter(item => item.status === 'ready')) {
    expect(gatewayIds(grant.gateway_path!).slice(0, 2))
      .toStrictEqual([sent[MEDIA_SESSION_HEADER], grant.occurrence_id]);
  }
  const headers: Record<string, string> = { Origin: origin, 'Content-Type': 'application/json' };
  for (const key of ['authorization', 'x-organization-id', 'x-workspace-id', MEDIA_SESSION_HEADER])
    if (sent[key]) headers[key] = sent[key];
  return headers;
};
const dashed = (hex32: string) =>
  `${hex32.slice(0, 8)}-${hex32.slice(8, 12)}-${hex32.slice(12, 16)}-${hex32.slice(16, 20)}-${hex32.slice(20)}`;
type Grant = { occurrence_id: string; status: string; gateway_path?: string; expires_at?: string };
type VoiceDetail = { result: { recording_attachment?: { occurrence_id: string; state: string;
  parent: { trace_id: string; span_id: string }; manifest?: { duration_ms: number; layout: string; codec: string; container: string } } } };

test('OBS-E2E-066: a recorded voice call and a traced image play and render on the trace for a signed-in user', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'OBS-E2E-066', area: 'observe',
    userGoal: 'A developer opens SDK-shaped LiveKit and Pipecat recordings in trace and Voice players, follows a call\'s tool use beside its speech, sees an image where the model saw it, and deletes the LiveKit session to revoke its media without affecting Pipecat',
    steps: [
      'create a voice project and an observe project (SDK source, not simulator) and opt both into attachments (operator step, test-image endpoint)',
      'with the org API key, bootstrap an F01 capture session per trace and upload a LiveKit and a Pipecat Ogg/Opus call recording, a WAV, a placed PNG and a PDF (initialize, PUT, complete)',
      'send through the collector a 7 s LiveKit call (untyped job_entrypoint root with lk.job_id and three timed turns, agent_session child carrying the recording, a nested lookup_order tool span), a Pipecat call (CONVERSATION root carrying the recording, a check_inventory tool span, no transcript) and the model trace',
      'on the https app origin, open the LiveKit trace, select agent_session and open Conversation: see lookup_order between the turns around it, seek by clicking turns while paused, select lookup_order to open its span on the right without moving the recording, and play to the native end',
      'open the Pipecat trace and play the root\'s recording to the end with peaks unavailable, with check_inventory listed under it as background activity',
      'revisit the LiveKit recording after its derivative is ready: see tall waveform bars for the tones and none for silence, move the paused media by clicking turns, and watch the waveform while real audio plays',
      'open the model trace, select the model span, switch to Chat and see the PNG inside user message 2',
      'play the WAV beside turn.audio in Attributes, and download the PDF, which has no inline preview',
      'open each call on the dedicated Voice full-page route and play its Attachment recording to the end',
      'delete only the LiveKit CH session through the supported session API',
    ],
    backendChecks: [
      'PG: exactly the five seeded occurrences have their own scope, capture binding, trace/span and placement, ready Attachment-owned verified MIME/length/sha256 equal to the pre-upload ledger, and complete immutable version and storage binding',
      'CH spans FINAL: each parent carries exactly the seeded refs, both voice roots remain parentless conversation roots, and stored input plus trace-detail input retain image/PDF fi-attachment occurrence URIs independently of placement and carrier construction',
      'API: voice detail projects each call\'s recording as ready on its own parent span with a mixed Ogg/Opus manifest of 7,000 ms (LiveKit) and 200 ms (Pipecat), and the trace detail projects exactly the seeded attachments as ready and carries the tool span\'s seeded input and output, which Conversation never renders',
      'API: the viewer gateway serves all five originals with fixture length/MIME/sha256, recording Range/HEAD and renewal preserve exact bytes, anonymous media is denied and resolve without credentials is 401; a known authorized attachment UUID used as an occurrence is not_found',
      'API: the waveform uses the selected peaks grant, its actual gateway bytes and their dimension header; the unavailable-peaks slider remains keyboard operable',
      'API: every media request the page made went through the gateway for exactly the three loaded audio occurrences (zero legacy requests)',
      'API and PG: deleting the CH-only LiveKit session returns 204, immediately denies the unchanged unexpired cookie/grant and tombstones its occurrence while complete immutable facts survive; Pipecat remains playable with unchanged bytes. Physical erasure is separate storage qualification',
    ],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  test.setTimeout(960_000);
  // 90s seed + 2*60s ASYNC_JOB + 15s SPAN_VISIBLE + 2*60s voice projection
  // + 6*60s UI_READY + 3*40s direct API + 60s for three 7 s playbacks + 75s headroom = 960s.
  // No suite change/retries.
  expect(DIAGNOSTICS).toContain(DIAGNOSTIC);
  const api = await request.newContext({ baseURL: E2E.apiUrl });
  const anonymous = await request.newContext({ baseURL: E2E.attachmentsAppUrl, ignoreHTTPSErrors: true });
  // Every media fetch the page makes; the last step asserts they all used the gateway.
  const media: string[] = [];
  page.on('request', r => { if (r.resourceType() === 'media') media.push(new URL(r.url()).pathname); });
  try {
    const { suffix, voiceProject, traceProject, livekitTraceId, pipecatTraceId, traceId,
      livekitRoot, livekitSession, pipecatRoot, llmSpan, livekitRecording, pipecatRecording,
      image, audio, document, traced, userText, expectedMessageInput,
      lookupSpan, inventorySpan, toolCanaries } = await test.step('seed: original synthetic F01 media and SDK-shaped trees', async () => {
      const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
      const voiceName = `e2e-att-voice-${suffix}`, traceName = `e2e-att-trace-${suffix}`;
      const jobId = `e2e-att-job-${suffix}`, userText = `e2e-att-user-${suffix}`;
      // fi-collector clickhouse25exporter/converter.go maps session.id to a
      // project-scoped deterministic trace_session_id. Calls must not share it.
      const livekitSessionName = `e2e-att-lk-${suffix}`, pipecatSessionName = `e2e-att-pc-${suffix}`;
      const voiceProject = await createProject(actor, voiceName);
      const traceProject = await createProject(actor, traceName);
      const livekitTrace = hex(16), pipecatTrace = hex(16), trace = hex(16);
      const livekitRoot = hex(8), livekitSession = hex(8), pipecatRoot = hex(8), traceRoot = hex(8), llmSpan = hex(8);
      const lookupSpan = hex(8), inventorySpan = hex(8);
      // Tool payloads minted for this run: present in span detail, never in Conversation.
      const toolCanaries = [`e2e-att-tool-in-${suffix}`, `e2e-att-tool-out-${suffix}`] as const;
      const tone = 300 + (Date.now() % 500);
      const colour: [number, number, number] = [Date.now() % 256, 40, 200];
      const recordingMedia = (bytes: Buffer) => ({ bytes, mime: 'audio/ogg', modality: 'audio', direction: 'mixed' as const,
        purpose: 'conversation_recording' });

      const livekit = await upload(api, actor, voiceName, voiceProject, livekitTrace, [
        { name: 'livekit-recording', ...recordingMedia(LIVEKIT_OGG), parentSpanId: livekitSession },
      ]);
      const pipecat = await upload(api, actor, voiceName, voiceProject, pipecatTrace, [
        { name: 'pipecat-recording', ...recordingMedia(RECORDING_OGG), parentSpanId: pipecatRoot },
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
      // The LiveKit call runs 7 s and ended 3 s ago; offsets are ms after it starts.
      const callStart = Date.now() - 10_000;
      const callNs = (ms: number) => String(BigInt(callStart + ms) * 1_000_000n);
      const timed = (base: OtlpSpan, from: number, to: number): OtlpSpan =>
        ({ ...base, startTimeUnixNano: callNs(from), endTimeUnixNano: callNs(to) });
      expect(image.attachmentId).not.toBe(image.occurrenceId);
      expect(document.attachmentId).not.toBe(document.occurrenceId);
      // Literal expected content from minted identities, not messages/carriers/placement output.
      const expectedMessageInput = [
        { role: 'system', content: 'Describe what the user shows you.' },
        { role: 'user', content: [{ type: 'text', text: userText },
          { type: 'image_url', image_url: { url: `fi-attachment:${image.occurrenceId}` } },
          { type: 'file', file: { file_id: `fi-attachment:${document.occurrenceId}` } }] },
      ];
      const messages = JSON.stringify([
        { role: 'system', content: 'Describe what the user shows you.' },
        { role: 'user', content: [{ type: 'text', text: userText },
          { type: 'image_url', image_url: { url: `fi-attachment:${DIAGNOSTIC === 'image-uri' ? image.attachmentId : image.occurrenceId}` } },
          { type: 'file', file: { file_id: `fi-attachment:${DIAGNOSTIC === 'pdf-uri' ? document.attachmentId : document.occurrenceId}` } }] },
      ]);
      const cfg = { collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey, secretKey: actor.secretKey };
      await sendOtlpExport(api, cfg, { resourceSpans: [{ resource: resource(voiceName), scopeSpans: [{
        scope: { name: 'livekit-agents' }, spans: [
          // No fi.span.kind: the LiveKit SDK marks no root; the collector maps it.
          // Voice detail reads the call transcript from the conversation root.
          timed(span(livekitTrace, livekitRoot, LIVEKIT_ROOT, [str(LIVEKIT_JOB_ID, jobId), str('session.id', livekitSessionName),
            str('fi.conversation.transcript', JSON.stringify(TURNS.map(({ start, end, ...turn }) => ({ ...turn,
              start_time_seconds: start / 1000, end_time_seconds: end / 1000,
              timestamp: new Date(callStart + start).toISOString() }))))]), 0, DURATION_MS.livekit),
          timed(span(livekitTrace, livekitSession, LIVEKIT_SESSION,
            [str('session.id', livekitSessionName), ...carriers(livekit.session, [livekitRecording])], livekitRoot),
            0, DURATION_MS.livekit),
          // A tool call while the agent says it is checking the order.
          timed(span(livekitTrace, lookupSpan, LOOKUP.span, [str('fi.span.kind', 'TOOL'),
            str('gen_ai.tool.name', LOOKUP.name), str('lk.function_tool.name', LOOKUP.name),
            str('lk.function_tool.id', `call-${suffix}`),
            str('session.id', livekitSessionName), str('input.value', JSON.stringify({ order: toolCanaries[0] })),
            str('output.value', JSON.stringify({ status: toolCanaries[1] }))], livekitSession), LOOKUP.start, LOOKUP.end),
        ] }, {
        scope: { name: 'pipecat' }, spans: [
          span(pipecatTrace, pipecatRoot, PIPECAT_ROOT, [str('fi.span.kind', PIPECAT_KIND), str('gen_ai.span.kind', PIPECAT_KIND),
            str('session.id', pipecatSessionName), ...carriers(pipecat.session, [pipecatRecording])]),
          // Today's real shape: a traced call with no transcript, only its spans.
          { ...span(pipecatTrace, inventorySpan, INVENTORY.name, [str('fi.span.kind', 'tool'),
            str('session.id', pipecatSessionName)], pipecatRoot),
            startTimeUnixNano: String(start + BigInt(INVENTORY.start) * 1_000_000n),
            endTimeUnixNano: String(start + BigInt(INVENTORY.end) * 1_000_000n) },
        ] }] }] });
      await sendOtlpExport(api, cfg, { resourceSpans: [{ resource: resource(traceName), scopeSpans: [{
        scope: { name: 'e2e-attachments' }, spans: [
          span(trace, traceRoot, `e2e.att-agent-${suffix}`, [str('fi.span.kind', 'agent')]),
          span(trace, llmSpan, `e2e.att-llm-${suffix}`, [str('fi.span.kind', 'llm'), str('input.value', messages),
            str('input.mime_type', 'application/json'), str('output.value', `e2e-att-answer-${suffix}`),
            str('turn.audio', `fi-attachment:${audio.occurrenceId}`), ...carriers(traced.session, [image, audio, document])], traceRoot),
        ] }] }] });
      const livekitTraceId = dashed(livekitTrace), pipecatTraceId = dashed(pipecatTrace), traceId = dashed(trace);
      const voiceSeeded = [livekitRecording, pipecatRecording];
      await testInfo.attach('seeded', { contentType: 'application/json', body: JSON.stringify({
        voiceProject, traceProject, livekitTraceId, pipecatTraceId, traceId, livekitRoot, livekitSession, pipecatRoot,
        traceRoot, llmSpan, jobId, livekitSessionName, pipecatSessionName, lookupSpan, inventorySpan,
        callStart: new Date(callStart).toISOString(), turns: TURNS, lookup: LOOKUP, inventory: INVENTORY,
        occurrences: [...voiceSeeded, ...traced.seeded].map(({ bytes, ...rest }) => ({ ...rest, sha256: sha256(bytes), size: bytes.length })),
      }) });

      return { suffix, voiceProject, traceProject, livekitTraceId, pipecatTraceId, traceId,
        livekitRoot, livekitSession, pipecatRoot, llmSpan, livekitRecording, pipecatRecording,
        image, audio, document, traced, userText, expectedMessageInput, lookupSpan, inventorySpan, toolCanaries };
    }, { timeout: 90_000 });
    const allSeeded = [livekitRecording, pipecatRecording, ...traced.seeded];
    const readAll = async () => [
      ...await readAttachmentOccurrences(probe, { organizationId: actor.organizationId,
        workspaceId: actor.workspaceId, projectId: voiceProject }, [livekitRecording.occurrenceId, pipecatRecording.occurrenceId]),
      ...await readAttachmentOccurrences(probe, { organizationId: actor.organizationId,
        workspaceId: actor.workspaceId, projectId: traceProject }, traced.seeded.map(item => item.occurrenceId)),
    ];
    // Expected-only diagnostics: the input fixture and actual stored rows stay untouched.
    const expectedFacts = allSeeded.map(item => ({ ...item.facts }));
    if (DIAGNOSTIC === 'pg-digest') expectedFacts[0].verified_sha256 = '0'.repeat(64);
    if (DIAGNOSTIC === 'pg-length') expectedFacts[0].verified_byte_length = String(livekitRecording.bytes.length + 1);
    if (DIAGNOSTIC === 'pg-parent') expectedFacts[0].span_id = livekitRoot;
    let beforeDeletion: AttachmentRow[] = [];
    await test.step('storage: PG occurrences bound and ready with complete independent verified facts', async () => {
      await expect(async () => {
        const rows = await readAll();
        assertReadyAttachmentOccurrences(rows, expectedFacts);
        beforeDeletion = rows;
      }).toPass(POLL.ASYNC_JOB);
      // Only fingerprints of server-assigned locators/versions are attached, never raw storage keys.
      await testInfo.attach('verified-facts', { contentType: 'application/json', body: JSON.stringify(beforeDeletion.map(row => ({
        occurrence: row.occurrence, attachment: row.attachment, captureSession: row.capture_session,
        trace: row.trace_id, span: row.span_id, bytes: row.verified_byte_length, sha256: row.verified_sha256,
        mime: row.verified_mime_type, bindingId: row.storage_binding_id, bindingFingerprint: row.binding_fingerprint,
        versionFingerprint: sha256(Buffer.from(row.verified_storage_version)), keyFingerprint: sha256(Buffer.from(row.verified_storage_key)),
      }))) });
    });

    await test.step('storage: CH spans FINAL carry the refs; both voice roots are conversation roots', async () => {
      const refs = async (projectId: string, traceKey: string) => (await readAttachmentRefs(probe, projectId, traceKey))
        .map(row => ({ id: row.id, occurrences: (JSON.parse(row.refs) as { occurrence_id: string }[]).map(r => r.occurrence_id).sort() }));
      const roots = (traceKey: string) => readAttachmentRoots(probe, voiceProject, traceKey);
      const storedMessages = async () => (await readAttachmentMessages(probe, traceProject, traceId, llmSpan))
        .map(row => ({ id: row.id, input: JSON.parse(row.input), input_attribute: JSON.parse(row.input_attribute) }));
      // Collector refs and roots share one SPAN_VISIBLE budget, retaining exact sets.
      await expect.poll(async () => [
        await refs(voiceProject, livekitTraceId), await refs(voiceProject, pipecatTraceId), await refs(traceProject, traceId),
        [...await roots(livekitTraceId), ...await roots(pipecatTraceId)], await storedMessages(),
      ], POLL.SPAN_VISIBLE).toStrictEqual([
        [{ id: livekitSession, occurrences: [livekitRecording.occurrenceId] }],
        [{ id: pipecatRoot, occurrences: [pipecatRecording.occurrenceId] }],
        [{ id: llmSpan, occurrences: traced.seeded.map(item => item.occurrenceId).sort() }],
        [{ id: livekitRoot, name: LIVEKIT_ROOT, observation_type: CONVERSATION },
          { id: pipecatRoot, name: PIPECAT_ROOT, observation_type: CONVERSATION }],
        [{ id: llmSpan, input: expectedMessageInput, input_attribute: expectedMessageInput }],
      ]);
    });

    await test.step('API: voice detail projects each call\'s recording on its own parent span', async () => {
      const projection = async (voiceTraceId: string) => (await actor.api.get<VoiceDetail>(VOICE_DETAIL,
        { trace_id: voiceTraceId, project_id: voiceProject })).result.recording_attachment ?? null;
      const ready = (item: Seeded, voiceTraceId: string, durationMs: number) => ({
        occurrence_id: item.occurrenceId, state: 'ready', parent: { trace_id: voiceTraceId, span_id: item.parentSpanId },
        manifest: expect.objectContaining({ duration_ms: durationMs, layout: 'mixed', codec: 'opus', container: 'ogg' }),
      });
      await expect.poll(() => projection(livekitTraceId), { timeout: UI_READY })
        .toEqual(ready(livekitRecording, livekitTraceId, DURATION_MS.livekit));
      await expect.poll(() => projection(pipecatTraceId), { timeout: UI_READY })
        .toEqual(ready(pipecatRecording, pipecatTraceId, DURATION_MS.pipecat));
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
    const snapshot = async (name: string) => {
      const path = testInfo.outputPath(`${name}.png`);
      await page.screenshot({path});
      await testInfo.attach(name, {path, contentType:'image/png'});
    };
    const exactResolve = (project:string, item:Seeded, traceKey:string, representation:string) => (r:Response) => {
      if (!isResolve(project)(r)) return false;
      const items = r.request().postDataJSON()?.items;
      return items?.length===1 && items[0].occurrence_id===item.occurrenceId && items[0].representation===representation
        && items[0].parent?.trace_id.replaceAll('-','')===traceKey.replaceAll('-','') && items[0].parent?.span_id===item.parentSpanId;
    };
    // Spoken turns and the tool row, in Conversation's own list order.
    const conversationRows = /^(Is my order on its way\?|I'm checking your order\.|It shipped today\.|lookup_order)$/;
    const playRecording = async (drawer: Locator, item: Seeded, name: string) => {
      const live = item===livekitRecording, traceKey = live?livekitTraceId:pipecatTraceId;
      const durationMs = live ? DURATION_MS.livekit : DURATION_MS.pipecat;
      await expect(drawer.getByRole('button',{name:'Load audio: Mixed recording'})).toHaveCount(0);
      await expect(drawer.getByLabel('Span attachments')).toHaveCount(0);
      // Exercise an optional derivative outage without intercepting any playable grant or audio byte.
      const fallbackRoute = F01.resolve(voiceProject);
      if(!live) await page.route(`**${fallbackRoute}`,route=>route.request().postDataJSON()?.items?.every((i:{representation:string})=>i.representation==='peaks') ? route.abort() : route.continue());
      const resolved = page.waitForResponse(exactResolve(voiceProject,item,traceKey,'playable'), { timeout: UI_READY });
      await drawer.getByRole('button',{name:'Conversation',exact:true}).click();
      const response = await resolved;
      const grants = await response.json() as { items: Grant[] };
      expect(grants.items.map(g => [g.occurrence_id, g.status])).toStrictEqual([[item.occurrenceId, 'ready']]);
      const panel=drawer.getByRole('region',{name:'Conversation',exact:true});
      const play=panel.getByRole('button',{name:'Play recording',exact:true});
      const player=panel.locator('audio');
      await expect(player).toHaveCount(1);
      const slider=panel.getByRole('slider',{name:'Recording position'});
      if(live){
        // The tool sits between the turns around it on the clock spans and turns share.
        await expect(panel.getByText(conversationRows))
          .toHaveText([TURNS[0].content, TURNS[1].content, LOOKUP.name, TURNS[2].content]);
        const tool = panel.getByRole('button', { exact: true,
          name: `View span ${LOOKUP.name}, tool call, from 0:03.0 to 0:04.0 after call start` });
        await expect(tool).toHaveText(`Tool call${LOOKUP.name}0:03.0–0:04.0`);
        // Clicking a transcript turn seeks the recording to that turn, as in Voice Observability.
        await panel.getByText(TURNS[2].content,{exact:true}).click();
        await expect(slider).toHaveValue('4.5');
        await expect(play).toBeVisible();
        await panel.getByText(TURNS[1].content,{exact:true}).click();
        await expect(slider).toHaveValue('2.5');
        // The tool row opens its own span on the right and leaves the recording where it is.
        await expect(drawer.getByText(livekitSession, { exact: true })).toBeVisible();
        await tool.click();
        await expect(drawer.getByText(lookupSpan, { exact: true })).toBeVisible();
        await expect(drawer.getByText(livekitSession, { exact: true })).toHaveCount(0);
        await expect(panel).toBeVisible();
        await expect(slider).toHaveValue('2.5');
        // Its payload stays in span detail (asserted on the API above), never in Conversation.
        const conversation = await panel.textContent() ?? '';
        for (const canary of toolCanaries) expect(conversation).not.toContain(canary);
        await snapshot('conversation');
      } else {
        await expect(panel.getByTestId('recording-waveform')).toHaveCount(0);
        // No transcript: the call's tool still shows, timed from the call start.
        await expect(panel.getByTestId('call-activity-row')).toHaveCount(1);
        await expect(panel.getByRole('region', { name: 'Background activity', exact: true }).getByRole('button', { exact: true,
          name: `View span ${INVENTORY.name}, tool call, from 0:01.0 to 0:01.4 after call start` })).toBeVisible();
        await snapshot('seek-bar');
      }
      await slider.focus(); await slider.press('End'); await expect(slider).toHaveValue(String(durationMs / 1000));
      await slider.press('Home'); await expect(slider).toHaveValue('0');
      await play.click();
      await expect.poll(() => player.evaluate((node: HTMLAudioElement) => node.readyState >= 1), { timeout: UI_READY }).toBe(true);
      // Native end; a long call must have played close to its whole length.
      const minPlayedMs = live ? durationMs - 300 : 150;
      await expect.poll(() => player.evaluate(endedAfter, minPlayedMs), { timeout: UI_READY }).toStrictEqual([true, true]);
      await testInfo.attach(name, { body: await page.screenshot(), contentType: 'image/png' });
      if(!live) await page.unroute(`**${fallbackRoute}`);
      const headers = await browserResolveHeaders(response, E2E.attachmentsAppUrl);
      // The grant names exactly the session this resolve selected.
      expect(gatewayIds(grants.items[0].gateway_path!)[0]).toBe(headers[MEDIA_SESSION_HEADER]);
      return { grant: grants.items[0], headers };
    };

    let livekitGrant: Grant = { occurrence_id: '', status: '' };
    let viewerHeaders: Record<string, string> = {};
    await test.step('UI: the LiveKit call recording plays to its end, with its tool call between the turns', async () => {
      const { spans, drawer } = await openTrace(voiceProject, livekitTraceId);
      expect(spans.filter(s => s.attachments?.length).map(s => ({ id: s.id, name: s.name,
        attachments: s.attachments!.map(a => [a.reference.occurrence_id, a.status]) })))
        .toStrictEqual([{ id: livekitSession, name: LIVEKIT_SESSION, attachments: [[livekitRecording.occurrenceId, 'ready']] }]);
      // Positive control for the payload check in Conversation: span detail carries both.
      const lookup = spans.filter(s => s.id === lookupSpan);
      expect(lookup.map(s => s.name)).toStrictEqual([LOOKUP.span]);
      const payload = JSON.stringify([lookup[0].input, lookup[0].output]);
      for (const canary of toolCanaries) expect(payload).toContain(canary);
      await drawer.getByText(LIVEKIT_SESSION, { exact: true }).first().click({ timeout: UI_READY });
      ({ grant: livekitGrant, headers: viewerHeaders } = await playRecording(drawer, livekitRecording, 'livekit-player'));
    }, { timeout: UI_READY + 30_000 });

    const viewer = page.context().request;
    const gateway = (path: string) => `${E2E.attachmentsAppUrl}${path}`;
    const resolveAs = (headers: Record<string, string>, item: Seeded, traceKey: string) =>
      viewer.post(gateway(F01.resolve(voiceProject)), { headers, data: { items: [{ occurrence_id: item.occurrenceId,
        parent: { trace_id: traceKey, span_id: item.parentSpanId }, representation: 'original' }] } });
    await test.step('API: the gateway serves the whole recording, Range, HEAD and a renewed grant; no session is denied', async () => {
      const path = livekitGrant.gateway_path!;
      expect(gatewayIds(path).slice(0, 2))
        .toStrictEqual([viewerHeaders[MEDIA_SESSION_HEADER], livekitRecording.occurrenceId]);
      const whole = await viewer.get(gateway(path));
      expect(whole.status()).toBe(200);
      expect(whole.headers()['content-type']).toBe('audio/ogg');
      expect(sha256(Buffer.from(await whole.body()))).toBe(sha256(LIVEKIT_OGG));
      const ranged = await viewer.get(gateway(path), { headers: { Range: 'bytes=0-3' } });
      expect(ranged.status()).toBe(206);
      expect(ranged.headers()['content-range']).toBe(`bytes 0-3/${LIVEKIT_OGG.length}`);
      expect(Buffer.from(await ranged.body()).equals(LIVEKIT_OGG.subarray(0, 4))).toBe(true);
      // Renewal: a fresh resolve mints a new grant for the same occurrence, and the
      // player's next Range continues the same byte timeline from where it left off.
      const renewal = await resolveAs(viewerHeaders, livekitRecording, livekitTraceId);
      expect(renewal.status(), await renewal.text()).toBe(200);
      const [renewed] = (await renewal.json() as { items: Grant[] }).items;
      expect([renewed.occurrence_id, renewed.status]).toStrictEqual([livekitRecording.occurrenceId, 'ready']);
      expect(renewed.gateway_path).not.toBe(path);
      const renewedPath = renewed.gateway_path!;
      expect(gatewayIds(renewedPath).slice(0, 2))
        .toStrictEqual([viewerHeaders[MEDIA_SESSION_HEADER], livekitRecording.occurrenceId]);
      const middle = Math.floor(LIVEKIT_OGG.length / 2);
      const resumed = await viewer.get(gateway(renewedPath), { headers: { Range: `bytes=${middle}-` } });
      expect(resumed.status()).toBe(206);
      expect(resumed.headers()['content-range']).toBe(`bytes ${middle}-${LIVEKIT_OGG.length - 1}/${LIVEKIT_OGG.length}`);
      expect(Buffer.from(await resumed.body()).equals(LIVEKIT_OGG.subarray(middle))).toBe(true);
      const head = await viewer.head(gateway(renewedPath));
      expect([head.status(), head.headers()['content-type'], head.headers()['content-length']])
        .toStrictEqual([200, 'audio/ogg', String(LIVEKIT_OGG.length)]);
      const denied = await anonymous.get(path, { headers: { Range: 'bytes=0-3' } });
      expect(denied.status()).toBe(404);
      const unauthenticated = await anonymous.post(F01.resolve(voiceProject), {
        headers: { Origin: E2E.attachmentsAppUrl, 'Content-Type': 'application/json' },
        data: { items: [{ occurrence_id: livekitRecording.occurrenceId,
          parent: { trace_id: livekitTraceId, span_id: livekitSession }, representation: 'original' }] } });
      expect(unauthenticated.status()).toBe(401);
    }, { timeout: 40_000 });

    await test.step('UI: the Pipecat call recording on its root plays to its end', async () => {
      const { spans, drawer } = await openTrace(voiceProject, pipecatTraceId);
      expect(spans.filter(s => s.attachments?.length).map(s => ({ id: s.id, name: s.name,
        attachments: s.attachments!.map(a => [a.reference.occurrence_id, a.status]) })))
        .toStrictEqual([{ id: pipecatRoot, name: PIPECAT_ROOT, attachments: [[pipecatRecording.occurrenceId, 'ready']] }]);
      await playRecording(drawer, pipecatRecording, 'pipecat-player');
    }, { timeout: UI_READY });

    await test.step('UI: enhance the same transport with the worker’s genuine peaks after ordinary playback',async()=>{
        const item=livekitRecording, traceKey=livekitTraceId;
        await expect.poll(()=>probe.pg<{state:string}>(`SELECT d.state FROM tracer_attachmentrecordingderivative d JOIN tracer_attachment a ON a.id=d.attachment_id WHERE a.client_id=$1 AND d.representation='peaks'`,[item.attachmentId]),POLL.ASYNC_JOB).toEqual([{state:'ready'}]);
        const {drawer}=await openTrace(voiceProject,traceKey);
        await drawer.getByText(LIVEKIT_SESSION,{exact:true}).first().click();
        const panel=drawer.getByRole('region',{name:'Conversation',exact:true});
        const slider=panel.getByRole('slider',{name:'Recording position'}), player=panel.locator('audio');
        const play=panel.getByRole('button',{name:'Play recording',exact:true});
        const peakGrant=page.waitForResponse(exactResolve(voiceProject,item,traceKey,'peaks'));
        const peakBytes=page.waitForResponse(r=>new URL(r.url()).pathname.includes(`/${item.occurrenceId}/`) && r.headers()['content-type']==='application/octet-stream');
        await drawer.getByRole('button',{name:'Conversation',exact:true}).click();
        const [peak]=((await (await peakGrant).json()) as {items:Grant[]}).items;
        const bytes=await peakBytes;
        expect(new URL(bytes.url()).pathname).toBe(peak.gateway_path);
        const descriptor=JSON.parse(bytes.headers()['x-attachment-peaks']);
        expect(descriptor.v).toBe(1);
        expect((await bytes.body()).length).toBe(4*descriptor.channels*descriptor.levels.reduce((sum:number,l:{buckets:number})=>sum+l.buckets,0));
        await expect(panel.getByTestId('recording-waveform')).toBeVisible();
        // The waveform shows the recording's shape: tall bars for the bursts, nothing for silence.
        const bars = await panel.getByTestId('recording-waveform').evaluate(svg =>
          [...svg.querySelectorAll('rect')].map(r => [Number(r.getAttribute('x')), Number(r.getAttribute('height'))]));
        expect(bars.length).toBeLessThan(WAVEFORM_BARS);
        // Bar i covers [i, i + 1) slots of the call; judge whole slots kept clear of the
        // codec's short tails at each tone edge.
        const slotMs = DURATION_MS.livekit / WAVEFORM_BARS;
        const slot = (x: number) => Math.floor(x / BAR_SLOT);
        const inside = (i: number, from: number, to: number) => i * slotMs >= from && (i + 1) * slotMs <= to;
        // The silence between the first two tones (1.7–2.5 s) draws nothing once the
        // first tone's decoded tail (to about 1.9 s) has died away.
        expect(bars.filter(([x]) => inside(slot(x), 2100, 2400))).toStrictEqual([]);
        // Inside the 2.5–4.2 s tone every slot has a bar well above the 2 px floor.
        const toneSlots = Array.from({ length: WAVEFORM_BARS }, (_, i) => i).filter(i => inside(i, 2700, 4000));
        expect(toneSlots.length).toBeGreaterThan(10);
        expect(bars.filter(([x, h]) => inside(slot(x), 2700, 4000) && h >= 4).map(([x]) => slot(x))).toStrictEqual(toneSlots);
        // The loudest bar reaches full height (26 of 30).
        expect(Math.max(...bars.map(([, h]) => h))).toBeGreaterThan(25);
        await slider.focus(); await slider.press('ArrowRight'); expect(Number(await slider.inputValue())).toBeGreaterThan(0);
        await slider.press('Home'); await expect(slider).toHaveValue('0');
        // A turn click moves the media itself, not just the slider: load, pause, then seek back.
        await play.click();
        await expect.poll(() => player.evaluate((n:HTMLAudioElement)=>n.readyState>=1 && n.currentTime>0), { timeout: UI_READY }).toBe(true);
        await panel.getByRole('button',{name:'Pause recording',exact:true}).click();
        for (const turn of [TURNS[2], TURNS[1]]) {
          await panel.getByText(turn.content,{exact:true}).click();
          await expect(slider).toHaveValue(String(turn.start / 1000));
          await expect.poll(() => player.evaluate((n:HTMLAudioElement, target:number) =>
            n.paused && !n.seeking && Math.abs(n.currentTime - target) <= 0.05, turn.start / 1000), { timeout: UI_READY }).toBe(true);
        }
        await snapshot('livekit-turn-seek');
        // Slow only the screenshot replay; the duration proof above uses normal speed.
        await player.evaluate((n:HTMLAudioElement)=>{n.defaultPlaybackRate=0.25;n.playbackRate=0.25;});
        // Resumes from the last turn clicked, so require real progress past it.
        const beforeReplay = await player.evaluate((n:HTMLAudioElement)=>n.currentTime);
        await play.click();
        await expect(panel.getByRole('button',{name:'Pause recording'})).toBeVisible();
        await expect.poll(()=>player.evaluate((n:HTMLAudioElement, from:number)=>!n.paused && !n.ended && n.currentTime>from+0.05, beforeReplay),{intervals:[15,25]}).toBe(true);
        await snapshot('waveform-playing');
    },{timeout:POLL.ASYNC_JOB.timeout + UI_READY});

    let traceViewerHeaders: Record<string, string> = {};
    await test.step('UI: the model span shows the image in user message 2', async () => {
      const resolved = page.waitForResponse(isResolve(traceProject), { timeout: UI_READY });
      const { spans, drawer } = await openTrace(traceProject, traceId);
      // Inspect actual trace-detail content before rendering. Placement can still render
      // an image when a URI is wrong; that is not a substitute for this exact assertion.
      expect(spans.filter(s => s.id === llmSpan).map(s => s.input)).toStrictEqual([expectedMessageInput]);
      expect(spans.filter(s => s.id === llmSpan).flatMap(s => s.attachments ?? []).map(a => ({
        occurrence: a.reference.occurrence_id, message: a.reference.message_index ?? null,
        part: a.reference.part_index ?? null, role: a.reference.role ?? null,
      })).sort((a, b) => a.occurrence.localeCompare(b.occurrence)))
        .toStrictEqual(traced.seeded.map(item => ({ occurrence: item.occurrenceId,
          message: item.facts.message_index, part: item.facts.part_index, role: item.facts.role,
        })).sort((a, b) => a.occurrence.localeCompare(b.occurrence)));
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
      traceViewerHeaders = await browserResolveHeaders(await resolved, E2E.attachmentsAppUrl);
      await snapshot('inline-image');
    }, { timeout: UI_READY });

    await test.step('UI: the span lists exactly the WAV and the download-only PDF', async () => {
      const drawer = page.locator('.MuiDrawer-paper:visible').filter({ has: page.getByText(traceId, { exact: true }) });
      const listed = drawer.getByLabel('Span attachments');
      // The WAV lives only in Attributes. The remaining attachment list contains just the PDF.
      await expect(listed.getByRole('button')).toHaveText(['Download'], { timeout: UI_READY });
      await expect(listed.getByText('Preview unavailable · download to inspect', { exact: true })).toHaveCount(1);
      await expect(listed.getByRole('img')).toHaveCount(0);
      await expect(drawer.locator('iframe, embed, object')).toHaveCount(0);
      const resolved=page.waitForResponse(exactResolve(traceProject,audio,traceId,'playable'));
      const clip=drawer.getByRole('button',{name:'Play clip: turn.audio',exact:true});
      await expect(clip).toHaveCount(1);
      await clip.scrollIntoViewIfNeeded(); await snapshot('attribute-clip');
      await clip.click();
      expect((await (await resolved).json()).items.map((g:Grant)=>[g.occurrence_id,g.status])).toStrictEqual([[audio.occurrenceId,'ready']]);
      const player = drawer.getByLabel('Clip: turn.audio').and(drawer.locator('audio'));
      await expect(listed.locator('audio')).toHaveCount(0);
      await expect(player).toHaveCount(1);
      await expect.poll(() => player.evaluate((node: HTMLAudioElement) => [node.readyState >= 1, Math.round(node.duration * 10)]),
        { timeout: UI_READY }).toStrictEqual([true, 5]);
      await expect.poll(()=>player.evaluate((n:HTMLAudioElement)=>n.played.length>0 && n.played.end(n.played.length-1)>0)).toBe(true);
      const downloading = page.waitForEvent('download', { timeout: UI_READY });
      await listed.getByRole('button', { name: 'Download Input · message 2 · part 3 PDF' }).click();
      const download = await downloading;
      // The gateway's `Content-Disposition: attachment; filename="document.pdf"`
      // (tracer/services/attachments/playback.py SERVED) wins over the link's name.
      expect(download.suggestedFilename()).toBe('document.pdf');
      const saved = await download.path();
      expect(sha256(readFileSync(saved!))).toBe(sha256(document.bytes));
      await testInfo.attach('span-attachments', { body: await page.screenshot(), contentType: 'image/png' });
    }, { timeout: UI_READY });

    await test.step('API: all five originals match the seed ledger; attachment UUID is not an occurrence', async () => {
      for (const item of allSeeded) {
        const headers = item.facts.project_id === voiceProject ? viewerHeaders : traceViewerHeaders;
        const resolved = await viewer.post(gateway(F01.resolve(item.facts.project_id)), { headers, data: { items: [{
          occurrence_id: item.occurrenceId, parent: { trace_id: item.facts.trace_id, span_id: item.facts.span_id },
          representation: 'original',
        }] } });
        expect(resolved.status()).toBe(200);
        const grants = (await resolved.json() as { items: Grant[] }).items;
        expect(grants.map(g => [g.occurrence_id, g.status])).toStrictEqual([[item.occurrenceId, 'ready']]);
        expect(gatewayIds(grants[0].gateway_path!).slice(0, 2)).toStrictEqual([headers[MEDIA_SESSION_HEADER], item.occurrenceId]);
        const original = await viewer.get(gateway(grants[0].gateway_path!));
        const bytes = Buffer.from(await original.body());
        expect([original.status(), original.headers()['content-type'], original.headers()['content-length'], bytes.length])
          .toStrictEqual([200, item.facts.verified_mime_type, item.facts.verified_byte_length, item.bytes.length]);
        expect(sha256(bytes)).toBe(DIAGNOSTIC === 'body-digest' && item === image ? '0'.repeat(64) : item.facts.verified_sha256);
      }
      // Known authorized asset, same real viewer/session/scope/parent. Only the identity
      // namespace is wrong; a random unknown UUID would not detect attachment-ID fallback.
      const wrongId = DIAGNOSTIC === 'authorized-occurrence-negative' ? image.occurrenceId : image.attachmentId;
      const wrongOccurrence = await viewer.post(gateway(F01.resolve(traceProject)), { headers: traceViewerHeaders, data: { items: [{
        occurrence_id: wrongId, parent: { trace_id: traceId, span_id: llmSpan }, representation: 'original',
      }] } });
      expect(wrongOccurrence.status()).toBe(200);
      const denied = (await wrongOccurrence.json() as { items: Grant[] }).items;
      expect(denied.map(g => [g.occurrence_id, g.status])).toStrictEqual([[wrongId, 'not_found']]);
      expect(denied[0].gateway_path).toBeUndefined();
    }, { timeout: 40_000 });

    // dashboard.jsx VoiceFullPage -> VoiceDetailDrawerV2 -> AudioPlayerCustom
    // selects RecordingAttachmentPlayer from the authorized recording_attachment.
    // SDK projects do not need a simulator-only Voice list to open this route.
    const voiceHeaders: Record<string, Record<string, string>> = {};
    for (const [callId, item, label] of [
      [livekitTraceId, livekitRecording, 'livekit'], [pipecatTraceId, pipecatRecording, 'pipecat'],
    ] as const) {
      const minPlayedMs = label === 'livekit' ? DURATION_MS.livekit - 300 : 150;
      await test.step(`UI: ${label} Attachment recording plays on the dedicated Voice page`, async () => {
        const detail = page.waitForResponse(r => new URL(r.url()).pathname === VOICE_DETAIL
          && new URL(r.url()).searchParams.get('trace_id') === callId, { timeout: UI_READY });
        const resolved = page.waitForResponse(isResolve(voiceProject), { timeout: UI_READY });
        await page.goto(`${E2E.attachmentsAppUrl}/dashboard/observe/${voiceProject}/voice/${callId}`);
        expect((await detail).status()).toBe(200);
        const recording = page.getByLabel('Attachment recording', { exact: true });
        await expect(recording).toBeVisible({ timeout: UI_READY });
        const resolvedResponse = await resolved;
        const projection = await resolvedResponse.json() as { items: Grant[] };
        expect(projection.items.map(g => [g.occurrence_id, g.status]))
          .toStrictEqual([[item.occurrenceId, 'ready']]);
        // Capture this operation's exact identity, not whichever exchange completed
        // last. The later Pipecat page must not invalidate LiveKit's matching grant.
        voiceHeaders[label] = await browserResolveHeaders(resolvedResponse, E2E.attachmentsAppUrl);
        const player = recording.locator('audio');
        await expect(player).toHaveCount(1, { timeout: UI_READY });
        // StreamingPlayback deliberately does not fetch media until Play.
        await recording.getByRole('button', { name: 'Play recording', exact: true }).click({ timeout: UI_READY });
        await expect.poll(() => player.evaluate((node: HTMLAudioElement) => node.readyState >= 1),
          { timeout: UI_READY }).toBe(true);
        await expect.poll(() => player.evaluate(endedAfter, minPlayedMs), { timeout: UI_READY })
          .toStrictEqual([true, true]);
        await testInfo.attach(`${label}-voice-player`, { body: await page.screenshot(), contentType: 'image/png' });
      }, { timeout: UI_READY + 15_000 });
    }

    await test.step('API: deleting the LiveKit session denies its media but preserves Pipecat', async () => {
      const sessions = (traceKey: string) => readAttachmentSessions(probe, voiceProject, traceKey);
      const livekitSessions = await sessions(livekitTraceId), pipecatSessions = await sessions(pipecatTraceId);
      expect(livekitSessions).toHaveLength(1);
      expect(pipecatSessions).toHaveLength(1);
      const livekitSessionId = livekitSessions[0].session_id, pipecatSessionId = pipecatSessions[0].session_id;
      expect(livekitSessionId).toMatch(/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/);
      expect(pipecatSessionId).toMatch(/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/);
      expect(livekitSessionId).not.toBe(pipecatSessionId);
      expect(await probe.pg<{ id: string }>('SELECT id::text FROM tracer_trace WHERE id = $1', [livekitTraceId]))
        .toStrictEqual([]);
      await testInfo.attach('deleted-session', { contentType: 'application/json', body: JSON.stringify({
        livekitSessionId, pipecatSessionId, skipDelete: SKIP_DELETE, pgTraceRows: 0,
      }) });
      // Renew using the exact handle/bearer/scope captured from LiveKit's Voice
      // resolve, even though Pipecat subsequently loaded another page. Keep that
      // operation's matching cookie and unexpired grant unchanged across deletion.
      const deletionHeaders = voiceHeaders.livekit;
      const fresh = await resolveAs(deletionHeaders, livekitRecording, livekitTraceId);
      expect(fresh.status()).toBe(200);
      const [current] = (await fresh.json() as { items: Grant[] }).items;
      expect([current.occurrence_id, current.status]).toStrictEqual([livekitRecording.occurrenceId, 'ready']);
      const path = current.gateway_path!;
      expect(gatewayIds(path).slice(0, 2))
        .toStrictEqual([deletionHeaders[MEDIA_SESSION_HEADER], livekitRecording.occurrenceId]);
      const beforeRange = await viewer.get(gateway(path), { headers: { Range: 'bytes=0-3' } });
      const beforeHead = await viewer.head(gateway(path));
      expect([beforeRange.status(), beforeHead.status()]).toStrictEqual([206, 200]);
      const grantExpiry = Date.parse(current.expires_at!);
      // playback.py grants live for 60 s. Denial must precede their expiry;
      // only the independent PG cleanup poll may use the full ASYNC_JOB budget.
      expect(grantExpiry - Date.now()).toBeGreaterThan(5_000);
      // playback.py COOKIE_NAME, scoped to this session's own gateway path. Never
      // attach or print the credential value.
      const sessionCookie = async () => (await page.context().cookies(gateway(path)))
        .find(cookie => cookie.name === 'fi_media_session_v2')?.value;
      const cookieBefore = await sessionCookie();
      expect(Boolean(cookieBefore)).toBe(true);
      if (!SKIP_DELETE) {
        // The attachment gateway proxies media routes, not the general session API.
        const deleted = await viewer.delete(`${E2E.apiUrl}${SESSION(livekitSessionId)}`, { headers: deletionHeaders });
        expect(deleted.status(), await deleted.text()).toBe(204);
      }
      // Immediate read denial is separate from the bounded tombstone cleanup poll.
      const denied = await resolveAs(deletionHeaders, livekitRecording, livekitTraceId);
      expect(denied.status()).toBe(200);
      expect((await denied.json() as { items: Grant[] }).items.map(g => [g.occurrence_id, g.status]))
        .toStrictEqual([[livekitRecording.occurrenceId, 'not_found']]);
      expect((await viewer.get(gateway(path), { headers: { Range: 'bytes=0-3' } })).status()).toBe(404);
      expect((await viewer.head(gateway(path))).status()).toBe(404);
      expect(Date.now()).toBeLessThan(grantExpiry);
      expect((await sessionCookie()) === cookieBefore, 'Deletion must not exchange the media session').toBe(true);
      await expect(async () => {
        const after = await readAll();
        expect(after.map(row => [row.occurrence, row.deleted]).sort())
          .toStrictEqual(allSeeded.map(item => [item.occurrenceId, item === livekitRecording]).sort());
        assertImmutableAttachmentFacts(beforeDeletion, after);
        assertReadyAttachmentOccurrences(after.filter(row => row.occurrence !== livekitRecording.occurrenceId),
          allSeeded.filter(item => item !== livekitRecording).map(item => item.facts));
      }).toPass(POLL.ASYNC_JOB);
      const survivor = await resolveAs(voiceHeaders.pipecat, pipecatRecording, pipecatTraceId);
      expect(survivor.status()).toBe(200);
      const [remaining] = (await survivor.json() as { items: Grant[] }).items;
      expect([remaining.occurrence_id, remaining.status]).toStrictEqual([pipecatRecording.occurrenceId, 'ready']);
      expect(gatewayIds(remaining.gateway_path!).slice(0, 2))
        .toStrictEqual([voiceHeaders.pipecat[MEDIA_SESSION_HEADER], pipecatRecording.occurrenceId]);
      const survivorBytes = await viewer.get(gateway(remaining.gateway_path!));
      expect(survivorBytes.status()).toBe(200);
      expect(sha256(Buffer.from(await survivorBytes.body()))).toBe(sha256(RECORDING_OGG));
      expect(survivorBytes.headers()['content-length']).toBe(pipecatRecording.facts.verified_byte_length);
    }, { timeout: 40_000 + POLL.ASYNC_JOB.timeout });

    await test.step('API: every media request the page made went through the gateway (zero legacy requests)', async () => {
      expect(media.every(path => path.startsWith(GATEWAY_PREFIX)), JSON.stringify(media)).toBe(true);
      expect([...new Set(media.map(path => gatewayIds(path)[1]))].sort())
        .toStrictEqual([livekitRecording.occurrenceId, pipecatRecording.occurrenceId, audio.occurrenceId].sort());
    });
  } finally {
    await api.dispose();
    await anonymous.dispose();
  }
});
