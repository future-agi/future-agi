import {readFile} from 'node:fs/promises';
import {setTimeout as sleep} from 'node:timers/promises';
import {ChatCompletionsCompatibleProvider} from '@future-agi/omega-runtime';

// AgentCC owns rates, aliases and tenant pricing. Its response header is USD
// with six decimals; absent metadata must never become a free invocation.
export function parseGatewayCost(value) {
  if (typeof value !== 'string' || !/^\d+(?:\.\d{1,6})?$/.test(value)) return null;
  const [whole, fraction = ''] = value.split('.');
  const micros = Number(whole) * 1_000_000 + Number(fraction.padEnd(6, '0'));
  return Number.isSafeInteger(micros) ? micros : null;
}

export async function gatewayConfig(env = process.env) {
  const baseUrl = env.AGENTCC_BASE_URL;
  const model = env.OMEGA_MODEL_ID;
  if (!baseUrl || !model) throw new Error('AGENTCC_BASE_URL and OMEGA_MODEL_ID are required');
  const url = new URL(baseUrl);
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) {
    throw new Error('Gateway URL must be HTTP(S) without embedded credentials or query');
  }
  if (!url.pathname.replace(/\/$/, '').endsWith('/v1')) throw new Error('Gateway base URL must end in /v1');
  const apiKey = env.AGENTCC_API_KEY_FILE
    ? (await readFile(env.AGENTCC_API_KEY_FILE, 'utf8')).trim()
    : env.AGENTCC_API_KEY;
  if (!apiKey || /[\r\n]/.test(apiKey)) throw new Error('A valid gateway API key or secret file is required');
  return {baseUrl, model, apiKey};
}

class InputContextBudgetError extends Error {}

class GatewayHttpError extends Error {
  constructor(status, retryAfter) {
    super('Gateway request failed with HTTP ' + status);
    this.status = status;
    const value = retryAfter?.trim();
    const milliseconds = /^\d+(?:\.\d+)?$/.test(value ?? '')
      ? Number(value) * 1000 : value ? Date.parse(value) - Date.now() : 0;
    this.retryAfterMs = Number.isNaN(milliseconds) ? 0 : Math.max(0, milliseconds);
  }
}

function serializedRequestBytes(body) {
  if (typeof body === 'string') return Buffer.byteLength(body);
  if (body instanceof Uint8Array) return body.byteLength;
  if (body instanceof ArrayBuffer) return body.byteLength;
  throw new InputContextBudgetError('Input context request could not be measured');
}

export function createGatewayProvider({baseUrl, model, apiKey, signal, maxCalls = 12,
  maxInputBytesTotal, fetchImpl = fetch, retrySleep = sleep, retryRandom = Math.random}) {
  if (!Number.isInteger(maxCalls) || maxCalls < 1 || maxCalls > 100) throw new Error('Invalid model-call budget');
  if (maxInputBytesTotal !== undefined
      && (!Number.isSafeInteger(maxInputBytesTotal) || maxInputBytesTotal < 1)) throw new Error('Invalid input byte budget');
  const calls = [];
  let requestBytes = 0;
  const trackedFetch = async (url, init) => {
      signal?.throwIfAborted();
      if (calls.length >= maxCalls) throw new Error('Model-call budget exhausted');
      const bytes = serializedRequestBytes(init?.body);
      if (maxInputBytesTotal !== undefined && requestBytes + bytes > maxInputBytesTotal) {
        throw new InputContextBudgetError('Input context budget exhausted');
      }
      requestBytes += bytes;
      const call = {call_number: calls.length + 1, requested_model: model, status: 'started',
        cost_microusd: null, cost_status: 'unknown', usage: null, request_bytes: bytes};
      calls.push(call);
      const started = Date.now();
      try {
        const response = await fetchImpl(url, {...init, signal, redirect: 'error'});
        call.http_status = response.status;
        call.provider = response.headers.get('x-agentcc-provider');
        call.routed_model = response.headers.get('x-agentcc-model-used');
        call.gateway_request_id = response.headers.get('x-request-id');
        call.cache_status = response.headers.get('x-agentcc-cache');
        call.cost_microusd = parseGatewayCost(response.headers.get('x-agentcc-cost'));
        call.cost_status = call.cost_microusd === null ? 'unknown' : 'reported';
        if (!response.ok) {
          await response.body?.cancel();
          // Do not surface upstream response bodies: they can echo credentials or customer data.
          throw new GatewayHttpError(response.status, response.headers.get('retry-after'));
        }
        call.status = 'received';
        return response;
      } catch (error) {
        call.status = signal?.aborted ? 'aborted' : 'error';
        if (error.message.startsWith('Gateway request failed with HTTP ')) throw error;
        throw new Error(signal?.aborted ? 'Gateway request aborted' : 'Gateway transport failed');
      } finally { call.latency_ms = Date.now() - started; }
    };
  const transport = new ChatCompletionsCompatibleProvider({
    id: 'agentcc', baseUrl, model, apiKey, fetch: trackedFetch
  });
  // generate only: streaming cost headers cannot be assumed final before the
  // gateway has completed a response. Preserve raw usage details for cached and
  // reasoning tokens without interpreting them as additional billable tokens.
  const provider = {
    id: transport.id, supports: transport.supports,
    async generate(request) {
      const firstCall = calls.length + 1;
      let waitedMs = 0;
      // Retry only explicit rejections. An ambiguous timeout or 5xx may
      // already have completed upstream and must not be replayed blindly.
      for (let retry = 0; ; retry++) {
        const before = calls.length;
        try {
          const response = await transport.generate(request);
          const call = calls[before];
          if (call) {
            call.status = 'completed';
            call.usage = response.raw?.usage ?? null;
            call.response_id = response.raw?.id ?? null;
          }
          return response;
        } catch (error) {
          const call = calls[before];
          if (call && call.status === 'received') call.status = 'invalid_response';
          if (error instanceof GatewayHttpError && error.status === 429 && retry < 2 && !signal?.aborted) {
            const waitMs = Math.ceil(Math.max(error.retryAfterMs, 1000 * 2 ** retry) + retryRandom() * 1000);
            if (calls.length < maxCalls && waitedMs + waitMs <= 30000
                && (maxInputBytesTotal === undefined || requestBytes + call.request_bytes <= maxInputBytesTotal)) {
              call.retry_delay_ms = waitMs;
              waitedMs += waitMs;
              try { await retrySleep(waitMs, undefined, {signal}); }
              catch { throw new Error('Gateway request aborted'); }
              continue;
            }
          }
          if (error instanceof InputContextBudgetError) throw error;
          if (/^(Gateway|Model-call)/.test(error.message)) throw error;
          throw new Error('Gateway response could not be processed');
        } finally {
          if (retry && calls[before]) calls[before].retry_of = firstCall;
        }
      }
    }
  };
  return {
    provider,
    async inspectAudio({url, format, question}) {
      if (typeof url !== 'string' || !url.startsWith('https://') || url.length > 2048
          || !['wav', 'mp3'].includes(format) || typeof question !== 'string'
          || !question.trim() || question.length > 1000) throw new Error('Invalid audio inspection input');
      const body = JSON.stringify({model, max_tokens: 1200, messages: [{role: 'user', content: [
        {type: 'text', text: `Answer only this question about the attached recording: ${question}\nReturn JSON with answer, observations (each with start_seconds, end_seconds, speaker, statement, confidence), metrics (name, value, unit, method), and uncertainty. Do not invent timestamps or measurements. Use null when unavailable.`},
        {type: 'file', file: {file_id: url, format: format === 'wav' ? 'audio/wav' : 'audio/mpeg'}},
      ]}]});
      const response = await trackedFetch(baseUrl.replace(/\/$/, '') + '/chat/completions', {
        method: 'POST', headers: {Authorization: `Bearer ${apiKey}`, 'Content-Type': 'application/json',
          'Cache-Control': 'no-store', 'X-AgentCC-Cache': 'skip'}, body,
      });
      const call = calls.at(-1);
      try {
        const raw = await response.json();
        const content = raw.choices?.[0]?.message?.content;
        if (typeof content !== 'string' || content.length > 16000) throw new Error('Invalid audio model response');
        const parsed = JSON.parse(content.trim().replace(/^```(?:json)?\s*/i, '').replace(/```$/, '').trim());
        if (!parsed || typeof parsed.answer !== 'string' || parsed.answer.length > 8000
            || !Array.isArray(parsed.observations) || parsed.observations.length > 30
            || !Array.isArray(parsed.metrics) || parsed.metrics.length > 30) throw new Error('Invalid audio observation');
        call.status = 'completed';
        call.usage = raw.usage ?? null;
        call.response_id = raw.id ?? null;
        return {observation: parsed, request_id: call.gateway_request_id,
          model_used: call.routed_model ?? model};
      } catch {
        call.status = 'invalid_response';
        throw new Error('Audio gateway response could not be processed');
      }
    },
    accounting() {
      const unknown = calls.filter(c => c.cost_microusd === null).length;
      const knownMicros = calls.reduce((sum, c) => sum + (c.cost_microusd ?? 0), 0);
      return {source: 'agentcc-response-headers', currency: 'USD', model_calls: calls.length,
        request_bytes: requestBytes,
        known_cost_usd: knownMicros / 1_000_000,
        cost_usd: unknown ? null : knownMicros / 1_000_000,
        cost_status: unknown ? 'incomplete' : 'reported',
        unknown_cost_calls: unknown, calls: structuredClone(calls)};
    }
  };
}
