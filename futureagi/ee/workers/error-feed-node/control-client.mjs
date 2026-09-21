// Only trusted worker code can use this client. It is never an Omega tool.
export function createControlClient({baseUrl, token, fetchImpl = fetch}) {
  const base = new URL(baseUrl);
  if (!['http:', 'https:'].includes(base.protocol) || base.username || base.password || base.search || base.hash
      || !token || /[\r\n]/.test(token)) throw new Error('Invalid control-plane configuration');
  return async function request(path, body, {method = 'POST', signal} = {}) {
    const grouping = /^\/grouping\/(?:feature-claims|claims|outbox)\/$|^\/grouping\/outbox\/[a-f0-9-]{36}\/ack\/$|^\/grouping\/feature-attempts\/[a-f0-9-]{36}\/(?:complete\/)?$|^\/grouping\/attempts\/[a-f0-9-]{36}\/(?:checkpoint\/|reserve\/|settle\/|publish\/)?$/.test(path);
    if (!grouping && !/^\/(?:notifications|claims|reports)\/$|^\/attempts\/[a-f0-9-]{36}\/$/.test(path)) throw new Error('Unsupported control operation');
    const payload = JSON.stringify(body);
    const limit = grouping ? 8 * 1024 * 1024 : 2 * 1024 * 1024;
    if (Buffer.byteLength(payload) > limit) throw new Error('Control request exceeded limit');
    const endpoint = new URL(base.href.replace(/\/$/, '') + '/tracer/internal/error-feed-v2' + path);
    const boundedSignal = signal ? AbortSignal.any([signal, AbortSignal.timeout(15000)]) : AbortSignal.timeout(15000);
    const response = await fetchImpl(endpoint, {method, signal: boundedSignal, redirect: 'error',
      headers: {Authorization: 'Bearer ' + token, 'Content-Type': 'application/json'}, body: payload});
    if (!response.ok) {
      await response.body?.cancel();
      const error = new Error('Control request failed');
      error.status = response.status;
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
