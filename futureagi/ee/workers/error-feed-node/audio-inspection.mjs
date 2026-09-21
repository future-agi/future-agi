import {open} from 'node:fs/promises';
import {tool} from '@omega/core';

const maxAudioBytes = 8 * 1024 * 1024;
const recordingKeys = ['gen_ai.voice.recording.url', 'conversation.recording.mono.combined'];

function recordingUrl(row) {
  let raw, attributes;
  try {
    raw = typeof row.span_attributes_raw === 'string'
      ? JSON.parse(row.span_attributes_raw) : row.span_attributes_raw ?? {};
    attributes = typeof row.span_attributes === 'string'
      ? JSON.parse(row.span_attributes) : row.span_attributes ?? {};
  } catch {return null;}
  let extra;
  try {extra = typeof row.attributes_extra === 'string' ? JSON.parse(row.attributes_extra) : row.attributes_extra ?? {};}
  catch {extra = {};}
  for (const source of [row.attrs_string, row.span_attr_str, extra, raw, attributes]) {
    for (const key of recordingKeys) {
      if (typeof source?.[key] === 'string' && source[key]) return source[key];
    }
  }
  return null;
}

export function createStorageRecordingResolver({allowedOrigins, fetchImpl = fetch}) {
  const origins = new Set(allowedOrigins);
  if (!origins.size || [...origins].some(value => {
    try {const url = new URL(value); return url.origin !== value || url.protocol !== 'https:';}
    catch {return true;}
  })) throw new Error('Invalid audio storage origin allowlist');
  return async ({store, span_id: spanId, signal}) => {
    signal?.throwIfAborted();
    const span = store.index.get(spanId);
    if (!span || span.bytes > 1024 * 1024) return null;
    const file = await open(store.path, 'r');
    let row;
    try {
      const bytes = Buffer.alloc(span.bytes);
      let read = 0;
      while (read < bytes.length) {
        const count = (await file.read(bytes, read, bytes.length - read, span.offset + read)).bytesRead;
        if (!count) throw new Error('Evidence file changed');
        read += count;
      }
      row = JSON.parse(bytes.toString('utf8'));
    } finally {await file.close();}
    const rawUrl = recordingUrl(row);
    if (!rawUrl) return null;
    let url;
    try {url = new URL(rawUrl);} catch {return null;}
    if (!origins.has(url.origin) || url.username || url.password || url.hash) return null;
    const response = await fetchImpl(url, {method: 'HEAD', signal, redirect: 'error'});
    if (!response.ok) {await response.body?.cancel(); return null;}
    const contentType = response.headers.get('content-type')?.split(';')[0]?.trim();
    const format = /\.mp3$/i.test(url.pathname) || contentType === 'audio/mpeg' ? 'mp3'
      : /\.wav$/i.test(url.pathname) || ['audio/wav', 'audio/x-wav'].includes(contentType) ? 'wav' : null;
    const size = Number(response.headers.get('content-length'));
    if (!format || !Number.isSafeInteger(size) || size < 1 || size > maxAudioBytes) {
      await response.body?.cancel(); return null;
    }
    await response.body?.cancel();
    return {url: url.href, format};
  };
}

// The resolver is trusted worker code. It must prove the recording belongs to
// the claimed trace before returning bytes; no model-supplied URL reaches it.
export function createAudioInspectionTool({claim, store, gateway, resolveRecording, callsRemaining,
  canSpendOutput = () => true, onModelUsage = () => {}, maxResultBytes = 8000, signal}) {
  if (typeof resolveRecording !== 'function') throw new Error('Audio resolver required');
  let inspections = 0;
  const inspect = tool({name: 'inspect_audio',
    description: 'Ask one focused question about audio attached to a recorded span. The answer is a fallible model observation, not independently verified task outcome. Read the span separately for a citation.',
    inputSchema: {type: 'object', additionalProperties: false, required: ['span_id', 'question'], properties: {
      span_id: {type: 'string', minLength: 1, maxLength: 64},
      question: {type: 'string', minLength: 8, maxLength: 1000},
    }},
    async execute({span_id: spanId, question}) {
      signal?.throwIfAborted();
      const span = store.index.get(spanId);
      if (!span) throw new Error('Audio span outside claimed trace');
      if (++inspections > 1 || callsRemaining() < 2 || !canSpendOutput()) {
        return {status: 'unavailable', reason: 'audio_or_model_budget'};
      }
      const recording = await resolveRecording({claim, store, span_id: spanId, signal});
      if (!recording) return {status: 'unavailable', reason: 'no_trusted_recording'};
      const {url, format} = recording;
      const {observation, request_id: requestId, model_used: modelUsed} =
        await gateway.inspectAudio({url, format, question});
      onModelUsage(gateway.accounting?.().calls.at(-1)?.usage?.completion_tokens ?? 1200);
      const statements = observation.observations.filter(item => item && typeof item.statement === 'string'
        && item.statement.length > 0).slice(0, 5);
      const entries = statements.map(item => {
        const start = Number.isFinite(item.start_seconds) && item.start_seconds >= 0 ? item.start_seconds : null;
        const end = Number.isFinite(item.end_seconds) && item.end_seconds >= (start ?? 0) ? item.end_seconds : null;
        return {statement: item.statement.slice(0, 500), start_seconds: start,
          end_seconds: end, speaker: item.speaker ?? null, confidence: item.confidence ?? null};
      });
      const result = {status: 'observed', answer: observation.answer.slice(0, 1000), observations: entries,
        metrics: observation.metrics.filter(item => item && typeof item === 'object').slice(0, 5).map(item => ({
          name: String(item.name ?? '').slice(0, 100), value: String(item.value ?? '').slice(0, 100),
          unit: String(item.unit ?? '').slice(0, 30), method: String(item.method ?? '').slice(0, 200)})),
        uncertainty: String(observation.uncertainty ?? '').slice(0, 500),
        gateway_request_id: requestId,
        model_used: modelUsed, source_span_id: spanId, source: 'audio_model_observation'};
      if (Buffer.byteLength(JSON.stringify(result)) > maxResultBytes) {
        return {status: 'unavailable', reason: 'audio_result_exceeded_budget'};
      }
      return result;
    }});
  return {tool: inspect};
}
