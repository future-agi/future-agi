// Product wire provenance at da4c81cc623f909ce38f53e01b557ee8374d5adb:
// OpenAI: e2e/stack/mock-llm/server.mjs chat completion; internal/models/response.go
// and internal/providers/openai/openai.go usage-only chunk forwarding.
// Anthropic: internal/providers/anthropic/{anthropic.go,translate.go,stream.go}
// endpoint /v1/messages; anthropicResponse/Usage and message_* / content_block_*.
// Gemini: internal/providers/gemini/{translate.go,gemini.go,stream.go,usage.go}
// geminiResponse/UsageMetadata and buildURL/buildNativeURL, alt=sse.
// All internal/ paths above are beneath agentcc-gateway/. These are synthetic
// native provider responses, not catalogue eligibility or ledger authority.
export type Dialect = 'openai' | 'anthropic' | 'gemini';
export type UsageMode = 'complete' | 'absent' | 'partial' | 'delayed' | 'duplicate';
export type Outcome = 'finish' | 'disconnect-after-output' | 'cancel-before-usage' |
  'cancel-after-usage' | 'http-429' | 'http-503';
export type Barrier = 'after-output' | 'after-usage';
export interface AttemptPlan {
  target: string; dialect: Dialect; model: string; stream: boolean;
  usage: UsageMode; inputTokens: number; outputTokens: number; outcome: Outcome;
}
export type WireEvent =
  | { kind: 'frame'; bytes: string; usage: 'none' | 'partial' | 'complete'; content: boolean }
  | { kind: 'barrier'; name: Barrier; cancelOnly: boolean }
  // A terminal Gemini candidate is sent with end(), not write()+a later barrier.
  | { kind: 'disconnect' } | { kind: 'end'; bytes?: string };
const DIALECTS: readonly string[] = ['openai', 'anthropic', 'gemini'];
const USAGES: readonly string[] = ['complete', 'absent', 'partial', 'delayed', 'duplicate'];
const OUTCOMES: readonly string[] = ['finish', 'disconnect-after-output', 'cancel-before-usage',
  'cancel-after-usage', 'http-429', 'http-503'];
export function validateAttempt(p: AttemptPlan): void {
  if (!DIALECTS.includes(p.dialect) || !USAGES.includes(p.usage) || !OUTCOMES.includes(p.outcome) ||
      typeof p.stream !== 'boolean' || !/^[a-z][a-z0-9-]{0,31}$/.test(p.target) ||
      typeof p.model !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(p.model)) {
    throw new Error('INVALID_NATIVE_PLAN');
  }
  for (const count of [p.inputTokens, p.outputTokens]) {
    if (!Number.isSafeInteger(count) || count < 0 || count > 1_000_000) throw new Error('INVALID_USAGE');
  }
  if (!p.stream && (p.usage === 'delayed' || p.usage === 'duplicate' ||
      ['disconnect-after-output', 'cancel-before-usage', 'cancel-after-usage'].includes(p.outcome))) {
    throw new Error('STREAM_REQUIRED');
  }
  if (['disconnect-after-output', 'cancel-before-usage'].includes(p.outcome) && p.usage !== 'absent') {
    throw new Error('PRE_USAGE_FAULT_REQUIRES_ABSENT_USAGE');
  }
  if (p.outcome === 'cancel-after-usage' && !['complete', 'delayed', 'duplicate'].includes(p.usage)) {
    throw new Error('POST_USAGE_CANCEL_REQUIRES_COMPLETE_USAGE');
  }
}
export function nativePath(p: AttemptPlan): string {
  validateAttempt(p);
  if (p.dialect === 'openai') return '/v1/chat/completions';
  if (p.dialect === 'anthropic') return '/v1/messages';
  return `/v1beta/models/${p.model}:${p.stream ? 'streamGenerateContent?alt=sse' : 'generateContent'}`;
}
function usage(p: AttemptPlan, partial = false): Record<string, number> {
  if (p.dialect === 'openai') return { prompt_tokens: p.inputTokens,
    ...(!partial ? { completion_tokens: p.outputTokens, total_tokens: p.inputTokens + p.outputTokens } : {}) };
  if (p.dialect === 'anthropic') return { input_tokens: p.inputTokens,
    ...(!partial ? { output_tokens: p.outputTokens } : {}) };
  return { promptTokenCount: p.inputTokens,
    ...(!partial ? { candidatesTokenCount: p.outputTokens, totalTokenCount: p.inputTokens + p.outputTokens } : {}) };
}
export function nonStreamBody(p: AttemptPlan, marker: string) {
  validateAttempt(p);
  const counters = p.usage === 'absent' ? {} : {
    [p.dialect === 'gemini' ? 'usageMetadata' : 'usage']: usage(p, p.usage === 'partial'),
  };
  if (p.dialect === 'openai') return { id: marker, object: 'chat.completion', model: p.model,
    choices: [{ index: 0, message: { role: 'assistant', content: marker }, finish_reason: 'stop' }], ...counters };
  if (p.dialect === 'anthropic') return { id: marker, type: 'message', role: 'assistant', model: p.model,
    content: [{ type: 'text', text: marker }], stop_reason: 'end_turn', ...counters };
  return { candidates: [{ content: { role: 'model', parts: [{ text: marker }] }, finishReason: 'STOP' }],
    modelVersion: p.model, ...counters };
}
function frame(value: unknown, event: string | undefined, kind: 'none' | 'partial' | 'complete' = 'none',
  content = false): WireEvent {
  return { kind: 'frame', bytes: `${event ? `event: ${event}\n` : ''}data: ${JSON.stringify(value)}\n\n`,
    usage: kind, content };
}
function contentFrames(p: AttemptPlan, marker: string): WireEvent[] {
  if (p.dialect === 'openai') return [frame({ id: marker, object: 'chat.completion.chunk', model: p.model,
    choices: [{ index: 0, delta: { role: 'assistant', content: marker }, finish_reason: null }] }, undefined, 'none', true)];
  if (p.dialect === 'gemini') return [frame({ candidates: [{ content: { role: 'model', parts: [{ text: marker }] } }] },
    undefined, 'none', true)];
  // Partial usage is emitted exactly once on message_start, with no final output count.
  return [frame({ type: 'message_start', message: { id: marker, type: 'message', role: 'assistant',
    model: p.model, content: [], ...(p.usage === 'partial' ? { usage: usage(p, true) } : {}) } },
    'message_start', p.usage === 'partial' ? 'partial' : 'none'),
  frame({ type: 'content_block_start', index: 0, content_block: { type: 'text', text: '' } }, 'content_block_start'),
  frame({ type: 'content_block_delta', index: 0, delta: { type: 'text_delta', text: marker } },
    'content_block_delta', 'none', true),
  frame({ type: 'content_block_stop', index: 0 }, 'content_block_stop')];
}
function usageFrame(p: AttemptPlan, marker: string, partial: boolean): WireEvent {
  const kind = partial ? 'partial' : 'complete';
  if (p.dialect === 'openai') return frame({ id: marker, object: 'chat.completion.chunk', model: p.model,
    choices: [], usage: usage(p, partial) }, undefined, kind);
  if (p.dialect === 'anthropic') return frame({ type: 'message_delta', delta: { stop_reason: 'end_turn' },
    usage: usage(p, partial) }, 'message_delta', kind);
  // da4c81cc62 gemini/stream.go:parseStreamData creates a Choice for an empty
  // candidate; gemini.go:343-362 forwards only Choices and cancels on finishReason.
  // BOTH partial and complete usage must survive forwarding WITHOUT ending the
  // stream before after-usage can be observed/released (or deliberately cancelled).
  // Partial has NO total/output and NO finishReason: usage.go:toUsage must not
  // infer a missing output count as measured zero from a terminal reading.
  return frame({ candidates: [{ content: { role: 'model', parts: [] } }],
    usageMetadata: usage(p, partial) }, undefined, kind);
}
/** Frame schedule only. Barriers are acknowledged by the HTTP writer, NOT a
 * claim that the gateway/browser has consumed bytes or that settlement occurred.
 * The consumer must separately observe its real client chunk before cancelling.
 * 'delayed' holds the SAME live stream; it is not a real late-worker event.
 * 'duplicate' duplicates a provider frame, not an outbox/ledger delivery.
 */
export function compileAttempt(p: AttemptPlan, marker: string): WireEvent[] {
  validateAttempt(p);
  if (!/^[A-Za-z0-9-]{1,160}$/.test(marker)) throw new Error('INVALID_SYNTHETIC_MARKER');
  if (!p.stream || p.outcome.startsWith('http-')) return [];
  const events = contentFrames(p, marker);
  events.push({ kind: 'barrier', name: 'after-output', cancelOnly: p.outcome === 'cancel-before-usage' });
  if (p.outcome === 'disconnect-after-output') return [...events, { kind: 'disconnect' }];
  if (p.outcome === 'cancel-before-usage') return events;
  if (p.usage === 'partial' && p.dialect !== 'anthropic') events.push(usageFrame(p, marker, true));
  if (['complete', 'delayed', 'duplicate'].includes(p.usage)) {
    events.push(usageFrame(p, marker, false));
    if (p.usage === 'duplicate') events.push(usageFrame(p, marker, false));
    events.push({ kind: 'barrier', name: 'after-usage', cancelOnly: p.outcome === 'cancel-after-usage' });
    if (p.outcome === 'cancel-after-usage') return events;
  }
  if (p.dialect === 'openai') {
    events.push(frame({ id: marker, object: 'chat.completion.chunk', model: p.model,
      choices: [{ index: 0, delta: {}, finish_reason: 'stop' }] }, undefined));
    events.push({ kind: 'frame', bytes: 'data: [DONE]\n\n', content: false, usage: 'none' });
  } else if (p.dialect === 'anthropic') {
    if (p.usage === 'absent' || p.usage === 'partial') events.push(frame({ type: 'message_delta',
      delta: { stop_reason: 'end_turn' } }, 'message_delta'));
    events.push(frame({ type: 'message_stop' }, 'message_stop'));
  }
  // Gemini's real canonical adapter closes its upstream immediately on STOP
  // (gemini.go:343-362 at the prep pin). End the HTTP response WITH that final
  // candidate, after all barriers. No close is reclassified as success: provider.ts
  // still requires writableFinished. A cancel-after-usage plan returns above and
  // never sends STOP. No usage on STOP, so partial evidence stays partial.
  events.push(p.dialect === 'gemini' ? { kind: 'end',
    bytes: `data: ${JSON.stringify({ candidates: [{ content: { role: 'model', parts: [] }, finishReason: 'STOP' }] })}\n\n`,
  } : { kind: 'end' });
  return events;
}

/** In-memory validation only. Never retain/return incoming headers or bodies.
 * Request shapes: OpenAI/Anthropic messages and Gemini native contents are pinned
 * to the same provider translation sources as above. Synthetic marker equality
 * proves this fixture was reached by the intended request, not a prior call.
 */
export function matchesRequest(p: AttemptPlan, value: unknown, marker: string): boolean {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const b = value as Record<string, unknown>;
  if (p.dialect === 'gemini') {
    const contents = b.contents as { role?: string; parts?: { text?: string }[] }[] | undefined;
    return Array.isArray(contents) && contents.length === 1 && contents[0]?.role === 'user' &&
      Array.isArray(contents[0]?.parts) && contents[0].parts.length === 1 && contents[0].parts[0]?.text === marker;
  }
  const messages = b.messages as { role?: string; content?: unknown }[] | undefined;
  if (b.stream !== undefined && typeof b.stream !== 'boolean') return false;
  if (b.model !== p.model || (b.stream === true) !== p.stream || !Array.isArray(messages) ||
      messages.length !== 1 || messages[0]?.role !== 'user') return false;
  const content = messages[0].content;
  if (content === marker) return true;
  // anthropic/translate.go:translateMessage emits a single text block for a
  // plain OpenAI message. Native Messages may supply the exact string instead.
  return p.dialect === 'anthropic' && Array.isArray(content) && content.length === 1 &&
    content[0]?.type === 'text' && content[0]?.text === marker;
}
