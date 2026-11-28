import { createServer } from 'node:http';
import { randomUUID } from 'node:crypto';
import { Barriers } from './controls.mjs';
import { canonical, requireValue, sha256 } from './canonical.mjs';

const MAX_BODY = 1024 * 1024;
const MAX_SCRIPT = 256;

/** H1 fixture: OpenAI-shaped transport only, never a production model registration bypass. */
export class ProgrammableProvider {
  #scripts = new Map();
  #requests = [];
  #server;
  #base;
  barriers = new Barriers();

  register(steps) {
    requireValue(Array.isArray(steps) && steps.length > 0 && steps.length <= MAX_SCRIPT, 'Bounded nonempty script required');
    const copy = structuredClone(steps);
    requireValue(Buffer.byteLength(canonical(copy)) <= 4 * MAX_BODY, 'Script too large');
    for (const step of copy) {
      requireValue(step && typeof step === 'object', 'Invalid step');
      requireValue(['json', 'disconnect', 'raw'].includes(step.kind), 'Invalid response kind');
      requireValue(step.status === undefined || [200, 400, 401, 403, 404, 409, 429, 500, 503].includes(step.status), 'Invalid HTTP status');
      requireValue(step.delay_ms === undefined || Number.isInteger(step.delay_ms) && step.delay_ms >= 0 && step.delay_ms <= 10_000, 'Bounded delay required');
    }
    const capability = randomUUID();
    this.#scripts.set(capability, { steps: copy, position: 0 });
    return { capability, baseURL: () => {
      requireValue(this.#base, 'Provider not listening');
      return `${this.#base}/fixtures/${capability}/v1`;
    } };
  }

  ledger(capability) {
    requireValue(this.#scripts.has(capability), 'Unknown fixture capability');
    return structuredClone(this.#requests.filter((r) => r.fixture === capability));
  }
  remaining(capability) {
    const script = this.#scripts.get(capability);
    requireValue(script, 'Unknown fixture capability');
    return script.steps.length - script.position;
  }

  async listen() {
    requireValue(!this.#server, 'Already listening');
    this.#server = createServer((request, response) => {
      this.#handle(request, response).catch(() => {
        if (!response.headersSent && !response.destroyed) response.writeHead(500).end('{"error":{"code":"fixture_internal_error"}}');
        else response.destroy();
      });
    });
    this.#server.requestTimeout = 15_000;
    this.#server.headersTimeout = 10_000;
    await new Promise((resolve, reject) => {
      this.#server.once('error', reject);
      this.#server.listen(0, '127.0.0.1', resolve);
    });
    this.#base = `http://127.0.0.1:${this.#server.address().port}`;
    return this;
  }

  async #handle(request, response) {
    const route = /^\/fixtures\/([a-f0-9-]{36})\/v1\/chat\/completions$/.exec(request.url ?? '');
    const script = route && this.#scripts.get(route[1]);
    if (request.method !== 'POST' || !script) { response.writeHead(404).end(); return; }
    let size = 0;
    const chunks = [];
    for await (const chunk of request) {
      size += chunk.length;
      if (size > MAX_BODY) { response.writeHead(413).end(); return; }
      chunks.push(chunk);
    }
    const bytes = Buffer.concat(chunks);
    let body;
    try { body = JSON.parse(bytes.toString('utf8')); } catch { response.writeHead(400).end(); return; }
    if (!Array.isArray(body.messages) || typeof body.model !== 'string' || body.stream === true) {
      response.writeHead(400).end('{"error":{"code":"fixture_request_invalid"}}'); return;
    }
    const index = script.position++;
    const entry = { fixture: route[1], sequence: index + 1, request_sha256: sha256(bytes), request_bytes: size,
      received_at: new Date().toISOString(), state: 'received' };
    this.#requests.push(entry);
    const step = script.steps[index];
    if (!step) { entry.state = 'unexpected_request'; response.writeHead(409).end('{"error":{"code":"script_exhausted"}}'); return; }
    // Only a digest may be used as an expectation; no payload or auth is retained.
    if (step.request_sha256 && step.request_sha256 !== entry.request_sha256) {
      entry.state = 'request_mismatch'; response.writeHead(409).end('{"error":{"code":"script_request_mismatch"}}'); return;
    }
    if (step.barrier) await this.barriers.wait(step.barrier);
    if (step.delay_ms) await new Promise((resolve) => setTimeout(resolve, step.delay_ms));
    if (step.kind === 'disconnect') { entry.state = 'disconnected_after_receipt'; response.destroy(); return; }
    const headers = { 'content-type': 'application/json' };
    if (step.retry_after !== undefined) {
      requireValue(Number.isInteger(step.retry_after) && step.retry_after >= 0, 'Invalid retry delay');
      headers['retry-after'] = String(step.retry_after);
    }
    const payload = step.kind === 'raw' ? String(step.body) : JSON.stringify(step.body);
    entry.response_sha256 = sha256(payload);
    entry.state = response.destroyed ? 'client_disconnected' : 'responded';
    response.writeHead(step.status ?? 200, headers).end(payload);
  }

  async close() {
    this.barriers.close();
    if (!this.#server) return;
    this.#server.closeAllConnections();
    await new Promise((resolve, reject) => this.#server.close((error) => error ? reject(error) : resolve()));
  }
}

export function completion(message, { finishReason = 'stop', usage = { prompt_tokens: 10, completion_tokens: 4, total_tokens: 14 } } = {}) {
  return { id: 'fixture-completion', object: 'chat.completion', created: 0, model: 'fixture-model',
    choices: [{ index: 0, message: { role: 'assistant', ...message }, finish_reason: finishReason }],
    ...(usage === null ? {} : { usage }) };
}
