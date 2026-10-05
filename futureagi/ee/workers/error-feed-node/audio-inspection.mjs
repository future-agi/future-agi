import {open} from 'node:fs/promises';
import {tool} from '@future-agi/omega-runtime';

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

export function createStorageRecordingResolver({allowedOrigins}) {
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
    const format = /\.mp3$/i.test(url.pathname) ? 'mp3'
      : /\.wav$/i.test(url.pathname) ? 'wav' : null;
    if (!format) return null;
    return {url: url.href, format};
  };
}

function audioExcerpt(turn, question, result) {
  const lines = result.observations.map(item => {
    const when = item.start_seconds === null ? '' : `${item.start_seconds}-${item.end_seconds ?? '?'}s `;
    const confidence = item.confidence == null ? '' : ` (confidence ${item.confidence})`;
    return `${when}${item.speaker ?? 'unknown speaker'}: ${item.statement}${confidence}`;
  });
  const metrics = result.metrics.filter(item => item.name || item.value).map(item =>
    `Metric ${item.name}: ${item.value}${item.unit ? ` ${item.unit}` : ''}${item.method ? ` (method: ${item.method})` : ''}`);
  return [`Audio model observation of the recording (gateway request ${result.gateway_request_id ?? 'unknown'}, turn ${turn}).`,
    `Q: ${question}`, `A: ${result.answer}`, ...lines, ...metrics,
    ...(result.uncertainty ? [`Uncertainty: ${result.uncertainty}`] : [])].join('\n').slice(0, 8000);
}

// The resolver reads a URL only from the claimed trace; no model-supplied URL reaches it.
// Each recording keeps one conversation per investigation; every answered turn is a citable receipt.
// Controller and children leave the last turn to the verifier, and only answered turns count.
export function createAudioInspectionTool({claim, store, gateway, resolveRecording, callsRemaining,
  phase = () => 'controller', maxTurns = 4, canSpendOutput = () => true, onModelUsage = () => {},
  maxResultBytes = 8000, signal}) {
  if (typeof resolveRecording !== 'function') throw new Error('Audio resolver required');
  const conversations = new Map();
  const receipts = new Map();
  const inspect = tool({name: 'inspect_audio',
    description: 'Talk to an audio-native model that hears the recording attached to a span. Ask, follow up or challenge an earlier answer; it remembers this investigation\'s conversation about that recording. Each answer is a citable audio receipt (evidence_id audio:...), a fallible model observation rather than span text.',
    inputSchema: {type: 'object', additionalProperties: false, required: ['span_id', 'message'], properties: {
      span_id: {type: 'string', minLength: 1, maxLength: 64},
      message: {type: 'string', minLength: 2, maxLength: 1000},
    }},
    async execute({span_id: spanId, message}) {
      signal?.throwIfAborted();
      const span = store.index.get(spanId);
      if (!span) throw new Error('Audio span outside claimed trace');
      if (receipts.size >= (phase() === 'verifier' ? maxTurns : maxTurns - 1)) {
        return {status: 'unavailable', reason: 'audio_turn_cap'};
      }
      if (callsRemaining() < 2 || !canSpendOutput()) return {status: 'unavailable', reason: 'model_call_budget'};
      let conversation = conversations.get(spanId);
      if (!conversation) {
        const recording = await resolveRecording({claim, store, span_id: spanId, signal});
        if (!recording) return {status: 'unavailable', reason: 'no_trusted_recording'};
        conversation = {url: recording.url, format: recording.format, history: []};
        conversations.set(spanId, conversation);
      }
      const {url, format, history} = conversation;
      const {observation, request_id: requestId, model_used: modelUsed} =
        await gateway.inspectAudio({url, format, history, question: message});
      onModelUsage(gateway.accounting?.().calls.at(-1)?.usage?.completion_tokens ?? 1200);
      const statements = observation.observations.filter(item => item && typeof item.statement === 'string'
        && item.statement.length > 0).slice(0, 5);
      const entries = statements.map(item => {
        const start = Number.isFinite(item.start_seconds) && item.start_seconds >= 0 ? item.start_seconds : null;
        const end = Number.isFinite(item.end_seconds) && item.end_seconds >= (start ?? 0) ? item.end_seconds : null;
        return {statement: item.statement.slice(0, 500), start_seconds: start,
          end_seconds: end, speaker: item.speaker ?? null, confidence: item.confidence ?? null};
      });
      const turn = history.length + 1;
      const evidenceId = `audio:${spanId}:${turn}`;
      const result = {status: 'observed', evidence_id: evidenceId, answer: observation.answer.slice(0, 1000), observations: entries,
        metrics: observation.metrics.filter(item => item && typeof item === 'object').slice(0, 5).map(item => ({
          name: String(item.name ?? '').slice(0, 100), value: String(item.value ?? '').slice(0, 100),
          unit: String(item.unit ?? '').slice(0, 30), method: String(item.method ?? '').slice(0, 200)})),
        uncertainty: String(observation.uncertainty ?? '').slice(0, 500),
        gateway_request_id: requestId,
        model_used: modelUsed, source_span_id: spanId, source: 'audio_model_observation'};
      if (Buffer.byteLength(JSON.stringify(result)) > maxResultBytes) {
        return {status: 'unavailable', reason: 'audio_result_exceeded_budget'};
      }
      history.push({question: message, answer: JSON.stringify(observation)});
      receipts.set(evidenceId, {evidence_id: evidenceId, span_id: spanId, parent_span_id: span.parent_span_id || null,
        excerpt: audioExcerpt(turn, message, result)});
      return result;
    }});
  return {tool: inspect, receipts: () => [...receipts.values()]};
}
