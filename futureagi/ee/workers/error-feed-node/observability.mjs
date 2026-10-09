import {AsyncLocalStorage} from 'node:async_hooks';
import {readFile} from 'node:fs/promises';
import {controlFailureDetails} from './control-client.mjs';

const active = new AsyncLocalStorage();
let telemetry;
const diagnostic = () => process.stderr.write('{"event":"omega_observability_unavailable"}\n');
let lastSpanDiagnostic = -Infinity;
const safely = operation => {
  try { return operation(); } catch {
    if (Date.now() - lastSpanDiagnostic >= 60_000) {
      lastSpanDiagnostic = Date.now();
      diagnostic();
    }
    return undefined;
  }
};

// Explicit parent contexts avoid changing Omega's or another SDK's global provider.
export function configureObservability(value) { telemetry = value; }

export async function startObservability(env = process.env, load = () => import('@future-agi/error-feed-telemetry')) {
  if (env.OMEGA_OBSERVABILITY_ENABLED !== 'true') return;
  try {
    const secret = async name => env[name + '_FILE']
      ? (await readFile(env[name + '_FILE'], 'utf8')).trim() : env[name];
    const apiKey = await secret('FI_API_KEY'), secretKey = await secret('FI_SECRET_KEY');
    if (!apiKey || !secretKey || /[\r\n]/.test(apiKey + secretKey) || !env.FI_PROJECT_NAME?.trim()) {
      throw new Error('Incomplete observability configuration');
    }
    if (env.FI_BASE_URL) {
      const url = new URL(env.FI_BASE_URL);
      if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) {
        throw new Error('Invalid collector URL');
      }
    }
    const sdk = await load();
    const provider = sdk.register({projectName: env.FI_PROJECT_NAME, projectType: sdk.ProjectType.OBSERVE,
      // fi-core 1.0.0 does not attach its batch processor to OTel 2.x.
      batch: false, setGlobalTracerProvider: false, verbose: false,
      headers: {'x-api-key': apiKey, 'x-secret-key': secretKey},
      ...(env.FI_BASE_URL ? {endpoint: env.FI_BASE_URL} : {})});
    configureObservability({provider, tracer: provider.getTracer('omega-error-feed'),
      captureContent: env.OMEGA_OBSERVABILITY_CAPTURE_CONTENT === 'true',
      rootContext: sdk.ROOT_CONTEXT, withParent: (context, span) => sdk.trace.setSpan(context, span)});
  } catch { diagnostic(); }
}

export async function stopObservability() {
  const current = telemetry;
  telemetry = undefined;
  if (!current) return;
  let timer;
  try {
    await Promise.race([current.provider.shutdown(), new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error('Telemetry shutdown timeout')), 5000);
    })]);
  } catch { diagnostic(); }
  finally { clearTimeout(timer); }
}

export function claimAttributes(claim) {
  const attributes = Object.fromEntries(['organization_id', 'organization_name', 'workspace_id', 'project_id', 'project_name', 'job_id', 'attempt_id',
    'feature_attempt_id', 'trace_id', 'engine_version', 'policy_version', 'generation']
    .filter(key => ['string', 'number'].includes(typeof claim[key]))
    .map(key => ['error_feed.' + key, claim[key]]));
  // The customer organization is the user of our internal Error Feed service.
  if (typeof claim.organization_id === 'string' && claim.organization_id.trim()) {
    attributes['user.id'] = claim.organization_id;
    if (typeof claim.organization_name === 'string' && claim.organization_name.trim()) {
      attributes['user.name'] = claim.organization_name;
    }
  }
  return attributes;
}

// Select work inputs explicitly: never serialize an authenticated control claim.
export function claimInput(claim) {
  return Object.fromEntries(['organization_id', 'organization_name', 'project_id', 'project_name',
    'trace_id', 'job_id', 'attempt_id', 'feature_attempt_id', 'read_cutoff', 'memory', 'limits',
    'snapshot', 'pending_snapshots', 'pending_ids', 'policy_version']
    .filter(key => claim[key] !== undefined).map(key => [key, claim[key]]));
}

const MAX_CONTENT_BYTES = 32768;
export function spanContent(direction, value) {
  if (!telemetry?.captureContent || !['input', 'output'].includes(direction) || value === undefined) return;
  safely(() => {
    const serialized = JSON.stringify(typeof value === 'function' ? value() : value, (key, item) =>
      /^(?:authorization|api[_-]?key|secret[_-]?key|password|lease_token|access_token|refresh_token)$/i.test(key)
        ? '[REDACTED]' : item);
    if (serialized === undefined) return;
    const bytes = Buffer.from(serialized);
    const truncated = bytes.length > MAX_CONTENT_BYTES;
    // Preserve valid JSON and count the escaped preview against the byte bound too.
    let content = serialized, previewBytes = MAX_CONTENT_BYTES - 1024;
    while (Buffer.byteLength(content) > MAX_CONTENT_BYTES) {
      content = JSON.stringify({truncated: true,
        preview: new TextDecoder().decode(bytes.subarray(0, previewBytes))});
      previewBytes = Math.floor(previewBytes / 2);
    }
    spanAttributes({[direction + '.value']: content, [direction + '.mime_type']: 'application/json',
      ['error_feed.' + direction + '.truncated']: truncated});
  });
}

export function spanAttributes(attributes) {
  const span = active.getStore()?.span;
  if (span) safely(() => span.setAttributes(Object.fromEntries(Object.entries(attributes)
    .filter(([, value]) => typeof value === 'string' || typeof value === 'boolean'
      || typeof value === 'number' && Number.isFinite(value)))));
}

export async function observe(name, kind, attributes, run, {root = false, input, output = false} = {}) {
  const current = telemetry;
  if (!current) return run();
  const parent = root ? undefined : active.getStore();
  const context = parent?.context ?? current.rootContext;
  const identity = {...parent?.identity, ...Object.fromEntries(Object.entries(attributes)
    .filter(([key]) => ['user.id', 'user.name', 'error_feed.organization_id', 'error_feed.organization_name',
      'error_feed.project_id', 'error_feed.project_name'].includes(key)))};
  const sessionId = root ? (attributes['error_feed.job_id'] || attributes['error_feed.attempt_id']
    || attributes['error_feed.feature_attempt_id']) : parent?.sessionId;
  const span = safely(() => current.tracer.startSpan(name, {
    attributes: {'fi.span.kind': kind, ...attributes, ...identity,
      ...(sessionId ? {'session.id': 'error-feed:' + sessionId} : {})},
  }, context));
  if (!span) return run();
  const childContext = safely(() => current.withParent(context, span)) ?? context;
  const scope = {span, context: childContext, identity, sessionId, executionFailed: false};
  try {
    return await active.run(scope, async () => {
      if (current.captureContent && input !== undefined) safely(() => spanContent('input',
        typeof input === 'function' ? input() : input));
      const result = await run();
      if (current.captureContent && output) safely(() => spanContent('output',
        typeof output === 'function' ? output(result) : result));
      // A returned failed investigation is not a thrown exception. Never let
      // OK overwrite its ERROR status (OTel gives OK higher precedence).
      if (!scope.executionFailed) safely(() => span.setStatus({code: 1}));
      return result;
    });
  } catch (error) {
    // The control client supplies only bounded, safe diagnostics. Attach them
    // directly: active.run has exited here, so there is no active span scope.
    const details = controlFailureDetails(error);
    safely(() => span.setAttributes(Object.fromEntries(Object.entries(details)
      .map(([key, value]) => ['error_feed.control.' + key.replace(/^control_/, ''), value]))));
    // Exception messages, stacks, prompts, recordings and evidence may contain customer data.
    safely(() => span.setStatus({code: 2, message: 'operation_failed'}));
    throw error;
  } finally { safely(() => span.end()); }
}

export function executionResult(result) {
  spanAttributes({'error_feed.execution_status': result.execution_status,
    'error_feed.outcome': result.outcome, 'error_feed.finding_count': result.findings?.length});
  if (result.execution_status === 'failed') {
    const scope = active.getStore();
    if (scope) {
      scope.executionFailed = true;
      safely(() => scope.span.setStatus({code: 2, message: 'execution_failed'}));
    }
  }
  return result;
}

export function observeAgent(omega, id, ...args) {
  return observe('error_feed.agent.' + id, 'AGENT', {'gen_ai.agent.id': id}, () => omega.runJson(id, ...args),
    {input: () => args[0], output: result => result.value});
}

export function observeTool(definition) {
  const execute = definition.execute;
  return {...definition, execute: (...args) => observe('error_feed.tool.' + definition.name,
    'TOOL', {'tool.name': definition.name}, () => execute(...args), {input: () => args[0], output: true})};
}

export function modelAttributes(call) {
  if (!call) return;
  spanAttributes({'gen_ai.request.model': call.requested_model, 'gen_ai.response.model': call.routed_model,
    // Observe prioritizes llm.model_name; retain the requested alias separately.
    'llm.model_name': call.routed_model || call.requested_model,
    'gen_ai.provider.name': call.provider, 'gen_ai.usage.input_tokens': call.usage?.prompt_tokens,
    'gen_ai.usage.output_tokens': call.usage?.completion_tokens, 'gen_ai.usage.total_tokens': call.usage?.total_tokens,
    'gen_ai.usage.cache_read_tokens': call.usage?.prompt_tokens_details?.cached_tokens,
    'gen_ai.usage.output_tokens.reasoning': call.usage?.completion_tokens_details?.reasoning_tokens,
    'error_feed.gateway.request_id': call.gateway_request_id, 'error_feed.gateway.cache_status': call.cache_status,
    'error_feed.gateway.cost_status': call.cost_status,
    // Use AgentCC's reported charge instead of repricing tokens in Observe.
    // Preserve explicit zero; an absent receipt must remain unknown.
    'gen_ai.cost.total': call.cost_microusd == null ? undefined : call.cost_microusd / 1e6,
    'error_feed.gateway.cost_usd': call.cost_microusd == null ? undefined : call.cost_microusd / 1e6,
    'error_feed.gateway.http_status': call.http_status, 'error_feed.gateway.call_number': call.call_number});
}
