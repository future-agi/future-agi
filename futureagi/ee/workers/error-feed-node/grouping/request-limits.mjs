// Gemini 3.8 Flash: 1,048,576 context / 65,536 output tokens.
// https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/3-8-flash
// Keep output at F6's 8,192; allow 40,384 tokens of additional headroom.
export const MAX_INPUT_TOKENS = 1_000_000;
// AgentCC's shipped transport default is 10 MiB (local deployment uses 100 MiB).
// This is an HTTP/memory guard, not a token estimate.
export const MAX_REQUEST_BYTES = 10 * 1024 * 1024;
export const MAX_EVIDENCE_BYTES = (MAX_REQUEST_BYTES - 8192) / 2;

function limitError(message, diagnostics) {
  return Object.assign(new Error(message), {diagnostics});
}

export function requestLimits(env = process.env) {
  const values = {
    inputTokens: Number(env.GROUPING_MAX_INPUT_TOKENS ?? MAX_INPUT_TOKENS),
    requestBytes: Number(env.GROUPING_MAX_REQUEST_BYTES ?? MAX_REQUEST_BYTES),
    // Regular (non-introductory) rates; no speculative cache discount.
    inputUsdPerMillion: Number(env.GROUPING_INPUT_USD_PER_MILLION ?? 1.50),
    outputUsdPerMillion: Number(env.GROUPING_OUTPUT_USD_PER_MILLION ?? 7.50),
  };
  if (!Number.isSafeInteger(values.inputTokens) || values.inputTokens < 1
      || values.inputTokens > MAX_INPUT_TOKENS
      || !Number.isSafeInteger(values.requestBytes) || values.requestBytes < 1
      || values.requestBytes > MAX_REQUEST_BYTES
      || ![values.inputUsdPerMillion, values.outputUsdPerMillion]
        .every(value => Number.isFinite(value) && value > 0 && value <= 1000)) {
    throw new Error('Invalid grouping request limits or reservation rates');
  }
  return values;
}

export async function measureRequest({body, config, signal, fetchImpl = fetch,
  limits = requestLimits()}) {
  const requestBytes = Buffer.byteLength(JSON.stringify(body));
  if (requestBytes > limits.requestBytes) {
    throw limitError(`Grouping transport limit: ${requestBytes} bytes > ${limits.requestBytes}`,
      {reason:'transport_limit',request_bytes:requestBytes,request_byte_limit:limits.requestBytes});
  }
  // Match the text-only grouping request's Gemini translation, including its
  // system instruction and structured-output schema. Never use /v1/count_tokens:
  // that endpoint returns a characters-based estimate, not Gemini tokenization.
  const countBody = {
    contents: body.messages.filter(m => m.role !== 'system')
      .map(m => ({role:'user', parts:[{text:m.content}]})),
    systemInstruction: {parts:body.messages.filter(m => m.role === 'system')
      .map(m => ({text:m.content}))},
    generationConfig: {responseMimeType:'application/json',
      responseSchema:body.response_format.json_schema.schema,
      maxOutputTokens:body.max_completion_tokens},
  };
  const url = new URL(config.baseUrl);
  url.pathname = url.pathname.replace(/\/v1\/?$/, '')
    + '/v1beta/models/' + config.model.split('/').map(encodeURIComponent).join('/') + ':countTokens';
  const timeout = AbortSignal.timeout(30000);
  const response = await fetchImpl(url, {method:'POST', redirect:'error',
    headers:{authorization:`Bearer ${config.apiKey}`, 'content-type':'application/json'},
    body:JSON.stringify(countBody), signal:signal ? AbortSignal.any([signal,timeout]) : timeout});
  // AgentCC's OpenAI-compatible route does not expose native countTokens.
  // Each text token consumes at least one UTF-8 byte; add fixed headroom for
  // protocol framing and reserve against this upper bound, not an estimate
  // that could undercharge or silently exceed the model context.
  const fallbackTokens = requestBytes + 8192;
  if (response.status === 404 && fallbackTokens <= limits.inputTokens) {
    await response.body?.cancel();
    return {input_tokens:fallbackTokens,request_bytes:requestBytes,
      input_token_limit:limits.inputTokens,request_byte_limit:limits.requestBytes,
      count_source:'utf8_upper_bound'};
  }
  if (!response.ok) {
    await response.body?.cancel();
    throw limitError(`Grouping token count unavailable: HTTP ${response.status}; inference not sent`,
      {reason:'token_count_unavailable',http_status:response.status,request_bytes:requestBytes});
  }
  const {totalTokens} = await response.json();
  if (!Number.isSafeInteger(totalTokens) || totalTokens < 1) {
    throw new Error('Invalid Gemini token count; inference not sent');
  }
  const measured = {input_tokens:totalTokens, request_bytes:requestBytes,
    input_token_limit:limits.inputTokens, request_byte_limit:limits.requestBytes};
  if (totalTokens > limits.inputTokens) {
    throw limitError(`Grouping token limit: ${totalTokens} tokens > ${limits.inputTokens}; ${requestBytes} bytes`,
      {reason:'token_limit',...measured});
  }
  return measured;
}

export function reservationUsd(inputTokens, outputTokens, floor, limits = requestLimits()) {
  return Math.max(floor, Math.ceil((inputTokens * limits.inputUsdPerMillion
    + outputTokens * limits.outputUsdPerMillion) * 1000) / 1_000_000_000);
}
