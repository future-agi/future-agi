import { createHash, randomUUID } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { crc32, deflateSync } from 'node:zlib';
import { request, type APIRequestContext, type Page, type Response } from '@playwright/test';
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
// VoiceCallListQueryBuilder roots and view_enrichment.RECORDING_PURPOSE.
const VOICE_SPAN_KIND = 'conversation';
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

async function createProject(actor: TestActor, name: string, source?: 'simulator') {
  const body = await actor.api.post<{ result: { project_id: string; name: string } }>(PROJECT, {
    name, model_type: 'GenerativeLLM', trace_type: 'observe', ...(source ? { source } : {}),
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

test('OBS-E2E-066: a recorded voice call and a traced image play and render on the trace for a signed-in user', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'OBS-E2E-066', area: 'observe',
    userGoal: 'A developer whose SDK uploaded a call recording and a model\'s image, audio and PDF inputs opens the call and the trace, plays the audio and sees the image where the model saw it',
    steps: [
      'create a voice project and an observe project and opt both into attachments (operator step, test-image endpoint)',
      'with the org API key, bootstrap an F01 capture session per project and upload an Ogg/Opus call recording, a WAV, a placed PNG and a PDF (initialize, PUT, complete)',
      'send the parent spans through the collector carrying fi.attachment refs',
      'on the https app origin, open the voice call and play its recording to the end',
      'open the trace, select the model span, switch to Chat and see the PNG inside user message 2',
      'load the WAV, and download the PDF, which has no inline preview',
    ],
    backendChecks: [
      'PG: each seeded occurrence is bound to its exact trace/span, not deleted, with its placement, its attachment ready and its blob sha256 equal to the uploaded bytes',
      'CH spans FINAL: each parent span carries exactly the seeded fi.attachment.refs',
      'API: the trace detail and voice detail project exactly the seeded attachments as ready, and the voice manifest is a 200 ms mixed Ogg/Opus recording',
      'API: a gateway Range request with the viewer\'s media session answers 206 with the validated type and the first bytes of the upload',
      'API: the same gateway URL without the media session is denied, and resolve without credentials answers 401',
    ],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  // Seeding ~30 s; binding (15 s schedule) + validate drain (15 s schedule)
  // inside one ASYNC_JOB (60 s) per project; SPAN_VISIBLE 15 s; six UI_READY
  // steps; 60 s headroom.
  test.setTimeout(30_000 + 2 * POLL.ASYNC_JOB.timeout + POLL.SPAN_VISIBLE.timeout + 6 * UI_READY + 60_000);
  const api = await request.newContext({ baseURL: E2E.apiUrl });
  const anonymous = await request.newContext({ baseURL: E2E.attachmentsAppUrl, ignoreHTTPSErrors: true });
  try {
    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const voiceName = `e2e-att-voice-${suffix}`, traceName = `e2e-att-trace-${suffix}`;
    const callId = `e2e-att-call-${suffix}`, userText = `e2e-att-user-${suffix}`;
    const voiceProject = await createProject(actor, voiceName, 'simulator');
    const traceProject = await createProject(actor, traceName);
    const voiceTrace = hex(16), trace = hex(16);
    const voiceRoot = hex(8), traceRoot = hex(8), llmSpan = hex(8);
    const tone = 300 + (Date.now() % 500);
    const colour: [number, number, number] = [Date.now() % 256, 40, 200];

    const voice = await upload(api, actor, voiceName, voiceProject, voiceTrace, [
      { name: 'recording', bytes: RECORDING_OGG, mime: 'audio/ogg', modality: 'audio', direction: 'mixed',
        purpose: 'conversation_recording', parentSpanId: voiceRoot },
    ]);
    const traced = await upload(api, actor, traceName, traceProject, trace, [
      { name: 'image', bytes: png(colour), mime: 'image/png', modality: 'image', direction: 'input',
        purpose: 'model_input', placement: { message_index: 1, part_index: 1, role: 'user' }, parentSpanId: llmSpan },
      { name: 'audio', bytes: wav(tone), mime: 'audio/wav', modality: 'audio', direction: 'input',
        purpose: 'turn_recording', parentSpanId: llmSpan },
      { name: 'pdf', bytes: pdf(`e2e-att-${suffix}`), mime: 'application/pdf', modality: 'document', direction: 'input',
        purpose: 'document', placement: { message_index: 1, part_index: 2, role: 'user' }, parentSpanId: llmSpan },
    ]);
    const [recording] = voice.seeded;
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
      scope: { name: 'e2e-attachments' }, spans: [span(voiceTrace, voiceRoot, `e2e.att-call-${suffix}`, [
        str('fi.span.kind', VOICE_SPAN_KIND), str('call.status', 'ended'),
        { key: 'metadata', value: { kvlistValue: { values: [str('call_execution_id', callId)] } } },
        ...carriers(voice.session, [recording]),
      ])] }] }] });
    await sendOtlpExport(api, cfg, { resourceSpans: [{ resource: resource(traceName), scopeSpans: [{
      scope: { name: 'e2e-attachments' }, spans: [
        span(trace, traceRoot, `e2e.att-agent-${suffix}`, [str('fi.span.kind', 'agent')]),
        span(trace, llmSpan, `e2e.att-llm-${suffix}`, [str('fi.span.kind', 'llm'), str('input.value', messages),
          str('input.mime_type', 'application/json'), str('output.value', `e2e-att-answer-${suffix}`),
          ...carriers(traced.session, [image, audio, document])], traceRoot),
      ] }] }] });
    const voiceTraceId = `${voiceTrace.slice(0, 8)}-${voiceTrace.slice(8, 12)}-${voiceTrace.slice(12, 16)}-${voiceTrace.slice(16, 20)}-${voiceTrace.slice(20)}`;
    const traceId = `${trace.slice(0, 8)}-${trace.slice(8, 12)}-${trace.slice(12, 16)}-${trace.slice(16, 20)}-${trace.slice(20)}`;
    await testInfo.attach('seeded', { contentType: 'application/json', body: JSON.stringify({
      voiceProject, traceProject, voiceTraceId, traceId, voiceRoot, traceRoot, llmSpan, callId,
      occurrences: [...voice.seeded, ...traced.seeded].map(({ bytes, ...rest }) => ({ ...rest, sha256: sha256(bytes), size: bytes.length })),
    }) });

    await test.step('storage: PG occurrences bound and ready with placement', async () => {
      await expect.poll(() => readyOccurrences(probe, voiceProject, voice.seeded), POLL.ASYNC_JOB)
        .toStrictEqual(expectedOccurrences(voice.seeded, voiceTraceId));
      await expect.poll(() => readyOccurrences(probe, traceProject, traced.seeded), POLL.ASYNC_JOB)
        .toStrictEqual(expectedOccurrences(traced.seeded, traceId));
    });

    await test.step('storage: CH spans FINAL carry the refs', async () => {
      const refs = async (traceKey: string) => (await probe.ch<{ id: string; refs: string }>(
        `SELECT id, attrs_string['fi.attachment.refs'] AS refs FROM spans FINAL
          WHERE trace_id={t:String} AND is_deleted=0 AND refs != '' ORDER BY id`, { t: traceKey }))
        .map(row => ({ id: row.id, occurrences: (JSON.parse(row.refs) as { occurrence_id: string }[]).map(r => r.occurrence_id).sort() }));
      await expect.poll(() => refs(voiceTraceId), POLL.SPAN_VISIBLE)
        .toStrictEqual([{ id: voiceRoot, occurrences: [recording.occurrenceId] }]);
      await expect.poll(() => refs(traceId), POLL.SPAN_VISIBLE)
        .toStrictEqual([{ id: llmSpan, occurrences: traced.seeded.map(item => item.occurrenceId).sort() }]);
    });

    let recordingGateway = '';
    await test.step('UI: the voice call recording plays to its end', async () => {
      const detail = page.waitForResponse(r => new URL(r.url()).pathname === VOICE_DETAIL
        && new URL(r.url()).searchParams.get('trace_id') === voiceTraceId, { timeout: UI_READY });
      const resolved = page.waitForResponse(isResolve(voiceProject), { timeout: UI_READY });
      await page.goto(`${E2E.attachmentsAppUrl}/dashboard/observe/${voiceProject}/llm-tracing`);
      await page.getByRole('row').filter({ hasText: /ended|completed/i }).click({ timeout: UI_READY });
      const body = await (await detail).json() as { result: { recording_attachment: {
        occurrence_id: string; state: string; parent: { trace_id: string; span_id: string };
        manifest: { duration_ms: number; layout: string; codec: string; container: string } } } };
      expect(body.result.recording_attachment).toMatchObject({
        occurrence_id: recording.occurrenceId, state: 'ready', parent: { trace_id: voiceTraceId, span_id: voiceRoot },
        manifest: { duration_ms: 200, layout: 'mixed', codec: 'opus', container: 'ogg' },
      });
      const grants = await (await resolved).json() as { items: { occurrence_id: string; status: string; gateway_path: string }[] };
      expect(grants.items.map(item => [item.occurrence_id, item.status])).toStrictEqual([[recording.occurrenceId, 'ready']]);
      recordingGateway = grants.items[0].gateway_path;
      const player = page.getByLabel('Attachment recording');
      await player.getByRole('button', { name: 'Play recording' }).click({ timeout: UI_READY });
      const element = player.locator('audio');
      await expect(element).toHaveCount(1);
      await expect.poll(() => element.evaluate((node: HTMLAudioElement) =>
        node.played.length > 0 ? Math.round(node.played.end(node.played.length - 1) * 1000) : 0), { timeout: UI_READY })
        .toBeGreaterThanOrEqual(150);
      await testInfo.attach('voice-player', { body: await page.screenshot(), contentType: 'image/png' });
    });

    await test.step('API: gateway Range answers 206 with the validated type; no session is denied', async () => {
      const ranged = await page.context().request.get(`${E2E.attachmentsAppUrl}${recordingGateway}`, {
        headers: { Range: 'bytes=0-3' } });
      expect(ranged.status()).toBe(206);
      expect(ranged.headers()['content-type']).toBe('audio/ogg');
      expect(ranged.headers()['content-range']).toBe(`bytes 0-3/${RECORDING_OGG.length}`);
      expect(Buffer.from(await ranged.body()).equals(RECORDING_OGG.subarray(0, 4))).toBe(true);
      const denied = await anonymous.get(recordingGateway, { headers: { Range: 'bytes=0-3' } });
      expect(denied.status()).toBe(404);
      const unauthenticated = await anonymous.post(F01.resolve(voiceProject), {
        headers: { Origin: E2E.attachmentsAppUrl, 'Content-Type': 'application/json' },
        data: { items: [{ occurrence_id: recording.occurrenceId,
          parent: { trace_id: voiceTraceId, span_id: voiceRoot }, representation: 'original' }] } });
      expect(unauthenticated.status()).toBe(401);
    });

    await test.step('UI: the model span shows the image in user message 2', async () => {
      const detail = page.waitForResponse(r => new URL(r.url()).pathname === TRACE_DETAIL(traceId)
        && r.request().method() === 'GET', { timeout: UI_READY });
      await page.goto(`${E2E.attachmentsAppUrl}/dashboard/observe/${traceProject}/llm-tracing?tab=traces`);
      await page.locator(`.clean-data-table:visible .ag-row[row-id="${traceId}"] [col-id="trace_name"]`).click({ timeout: UI_READY });
      const body = await (await detail).json() as { result: { observation_spans: DetailEntry[] } };
      const spans = flatten(body.result.observation_spans);
      expect(spans.filter(s => s.attachments?.length).map(s => ({ id: s.id,
        attachments: s.attachments!.map(a => [a.reference.occurrence_id, a.status]).sort() })))
        .toStrictEqual([{ id: llmSpan, attachments: traced.seeded.map(item => [item.occurrenceId, 'ready']).sort() }]);
      const drawer = page.locator('.MuiDrawer-paper:visible').filter({ has: page.getByText(traceId, { exact: true }) });
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
  } finally {
    await api.dispose();
    await anonymous.dispose();
  }
});
