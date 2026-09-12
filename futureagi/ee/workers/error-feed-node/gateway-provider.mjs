import {readFile} from 'node:fs/promises';
import {ChatCompletionsCompatibleProvider} from '@omega/core';

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

function serializedRequestBytes(body) {
  if (typeof body === 'string') return Buffer.byteLength(body);
  if (body instanceof Uint8Array) return body.byteLength;
  if (body instanceof ArrayBuffer) return body.byteLength;
  throw new InputContextBudgetError('Input context request could not be measured');
}

export function createGatewayProvider({baseUrl, model, apiKey, signal, maxCalls = 12,
  maxInputBytesTotal, fetchImpl = fetch}) {
  if (!Number.isInteger(maxCalls) || maxCalls < 1 || maxCalls > 100) throw new Error('Invalid model-call budget');
  if (maxInputBytesTotal !== undefined
      && (!Number.isSafeInteger(maxInputBytesTotal) || maxInputBytesTotal < 1)) throw new Error('Invalid input byte budget');
  const calls = [];
  let requestBytes = 0;
  const transport = new ChatCompletionsCompatibleProvider({
    id: 'agentcc', baseUrl, model, apiKey,
    fetch: async (url, init) => {
      signal?.throwIfAborted();
      if (calls.length >= maxCalls) throw new Error('Model-call budget exhausted');
      const bytes = serializedRequestBytes(init?.body);
      if (maxInputBytesTotal !== undefined && requestBytes + bytes > maxInputBytesTotal) {
        throw new InputContextBudgetError('Input context budget exhausted');
      }
      requestBytes += bytes;
      const call = {call_number: calls.length + 1, requested_model: model, status: 'started',
        cost_microusd: null, cost_status: 'unknown', usage: null};
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
          throw new Error('Gateway request failed with HTTP ' + response.status);
        }
        call.status = 'received';
        return response;
      } catch (error) {
        call.status = signal?.aborted ? 'aborted' : 'error';
        if (error.message.startsWith('Gateway request failed with HTTP ')) throw error;
        throw new Error(signal?.aborted ? 'Gateway request aborted' : 'Gateway transport failed');
      } finally { call.latency_ms = Date.now() - started; }
    }
  });
  // generate only: streaming cost headers cannot be assumed final before the
  // gateway has completed a response. Preserve raw usage details for cached and
  // reasoning tokens without interpreting them as additional billable tokens.
  const provider = {
    id: transport.id, supports: transport.supports,
    async generate(request) {
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
        if (error instanceof InputContextBudgetError) throw error;
        if (/^(Gateway|Model-call)/.test(error.message)) throw error;
        throw new Error('Gateway response could not be processed');
      }
    }
  };
  return {
    provider,
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
