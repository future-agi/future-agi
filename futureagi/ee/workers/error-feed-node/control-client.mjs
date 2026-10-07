import {CONTROL_CONFLICT_REASONS} from './grouping/control-conflict-reasons.mjs';

const failures = new WeakMap();
export function controlFailureDetails(error) {
  return failures.get(error) ?? {};
}

async function conflictDetails(response) {
  if (response.status !== 409) {
    await response.body?.cancel();
    return {};
  }
  try {
    let bytes = 0;
    const chunks = [];
    for await (const chunk of response.body ?? []) {
      bytes += chunk.length;
      if (bytes > 16 * 1024) return {};
      chunks.push(chunk);
    }
    const body = JSON.parse(Buffer.concat(chunks).toString('utf8'));
    // Only exact, static backend conflict reasons may leave the control client.
    if (body.code !== 'grouping_conflict' || !CONTROL_CONFLICT_REASONS.has(body.detail)) return {};
    return {backend_code: body.code, backend_reason: body.detail};
  } catch {
    return {};
  }
}

// Only trusted worker code can use this client. It is never an Omega tool.
export function createControlClient({baseUrl, token, fetchImpl = fetch}) {
  const base = new URL(baseUrl);
  if (!['http:', 'https:'].includes(base.protocol) || base.username || base.password || base.search || base.hash
      || !token || /[\r\n]/.test(token)) throw new Error('Invalid control-plane configuration');
  return async function request(path, body, {method = 'POST', signal} = {}) {
    const grouping = /^\/grouping\/(?:feature-claims|claims|outbox)\/$|^\/grouping\/outbox\/[a-f0-9-]{36}\/ack\/$|^\/grouping\/feature-attempts\/[a-f0-9-]{36}\/(?:complete\/)?$|^\/grouping\/attempts\/[a-f0-9-]{36}\/(?:checkpoint\/|reserve\/|settle\/|publish\/)?$|^\/grouping\/severity\/claims\/$|^\/grouping\/severity\/attempts\/[a-f0-9-]{36}\/(?:reserve\/|settle\/|publish\/)?$/.test(path);
    if (!grouping && !/^\/(?:notifications|claims|reports)\/$|^\/attempts\/[a-f0-9-]{36}\/(?:simulation-evidence\/)?$/.test(path)) throw new Error('Unsupported control operation');
    const payload = JSON.stringify(body);
    const limit = grouping ? 8 * 1024 * 1024 : 2 * 1024 * 1024;
    if (Buffer.byteLength(payload) > limit) throw new Error('Control request exceeded limit');
    const endpoint = new URL(base.href.replace(/\/$/, '') + '/tracer/internal/error-feed-v2' + path);
    const boundedSignal = signal ? AbortSignal.any([signal, AbortSignal.timeout(15000)]) : AbortSignal.timeout(15000);
    const started = performance.now();
    const details = () => ({control_path: path, http_method: method,
      duration_ms: Math.round(performance.now() - started), request_bytes: Buffer.byteLength(payload)});
    let response;
    try {
      response = await fetchImpl(endpoint, {method, signal: boundedSignal, redirect: 'error',
        headers: {Authorization: 'Bearer ' + token, 'Content-Type': 'application/json'}, body: payload});
    } catch (error) {
      if (error && typeof error === 'object') failures.set(error, {...details(),
        failure_code: boundedSignal.aborted ? (signal?.aborted ? 'control_cancelled' : 'control_timeout') : 'control_network_error'});
      throw error;
    }
    if (!response.ok) {
      const error = new Error('Control request failed');
      error.status = response.status;
      const backend = await conflictDetails(response);
      failures.set(error, {...details(), failure_code: 'control_http_error', http_status: response.status,
        ...backend});
      throw error;
    }
    let bytes = 0;
    const chunks = [];
    for await (const chunk of response.body) {
      bytes += chunk.length;
      if (bytes > limit) throw new Error('Control response exceeded limit');
      chunks.push(chunk);
    }
    return JSON.parse(Buffer.concat(chunks).toString('utf8'));
  };
}
