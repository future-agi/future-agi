import { randomUUID } from 'node:crypto';
import { createServer, type IncomingMessage, type ServerResponse } from 'node:http';
import { compileAttempt, matchesRequest, nativePath, nonStreamBody, validateAttempt,
  type AttemptPlan, type Barrier } from './wire';

// LOCAL fixture control API (not product endpoints). In-process registration and
// release only: no arbitrary-write HTTP control route, subprocess or outbound IO.
// The accepted runtime must start this in the gateway's network namespace, or
// supply an independently reviewed same-job loopback projection. Never broaden
// listen() to 0.0.0.0 or use a provider/production host to solve routing.
// requestId is the consumer's minted correlation ID. Matching the minted input
// marker binds arrivals to it; it is NOT read from a guessed request header.
// Consumers must separately join the real gateway request ID to this scenario
// before querying logs/Redis/ledger. The fixture never manufactures that join.
export interface ScenarioPlan {
  requestId: string; attempts: readonly AttemptPlan[];
  // Generates a fresh LOCAL-only upstream credential. Never accepts real keys.
  syntheticCredential?: boolean;
}
export interface AttemptObservation {
  index: number; target: string; dialect: string; status: number; credentialMatches: boolean | null;
  frames: number; contentFrames: number; partialUsageFrames: number; completeUsageFrames: number;
  waiting: Barrier | null; reached: Barrier | null;
  cancelledAt: Barrier | 'before-output' | 'between-barriers' | null;
  terminal: 'open' | 'finished' | 'provider-disconnected' |
    'client-cancelled' | 'fixture-failed' | 'fixture-closed';
}
/** Writer-side completeness only; the consumer still owes a real client read.
 * Keep the close boundary after the pending barrier is cleared, so an earlier
 * cancellation can never impersonate cancel-after-usage merely by its label.
 */
export function assertAttemptComplete(p: AttemptPlan, row: AttemptObservation): void {
  const terminal = p.outcome === 'disconnect-after-output' ? 'provider-disconnected'
    : p.outcome.startsWith('cancel-') ? 'client-cancelled' : 'finished';
  if (row.terminal !== terminal) throw new Error('FIXTURE_TERMINAL_MISMATCH');
  if (!p.outcome.startsWith('cancel-')) return;
  const boundary = p.outcome === 'cancel-after-usage' ? 'after-usage' : 'after-output';
  if (row.cancelledAt !== boundary || row.reached !== boundary || row.waiting !== null) {
    throw new Error('FIXTURE_CANCEL_BOUNDARY_MISMATCH');
  }
  const frames = compileAttempt(p, 'fixture-verification').filter(e => e.kind === 'frame');
  if (row.status !== 200 || row.frames !== frames.length ||
      row.contentFrames !== frames.filter(e => e.content).length ||
      row.partialUsageFrames !== frames.filter(e => e.usage === 'partial').length ||
      row.completeUsageFrames !== frames.filter(e => e.usage === 'complete').length) {
    throw new Error('FIXTURE_CANCEL_FRAME_MISMATCH');
  }
}
interface ScenarioState {
  id: string; requestId: string; marker: string; plans: AttemptPlan[]; upstreamCredential: string | null;
  attempts: AttemptObservation[]; violations: string[];
  releases: Map<number, { name: Barrier; cancelOnly: boolean; release: () => void }>;
}
export interface ProviderOptions {
  // Fixture dead-man budget, NOT a product settlement SLA. Runner supplies it.
  barrierTimeoutMs: number;
}
const MAX_BODY = 128 * 1024;
async function readBody(req: IncomingMessage): Promise<unknown> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > MAX_BODY) throw new Error('FIXTURE_BODY_TOO_LARGE');
    chunks.push(Buffer.from(chunk));
  }
  try { return JSON.parse(Buffer.concat(chunks).toString('utf8')); }
  catch { throw new Error('FIXTURE_INVALID_JSON'); }
}
function json(res: ServerResponse, status: number, data: unknown): void {
  res.writeHead(status, { 'content-type': 'application/json', 'cache-control': 'no-store' });
  res.end(JSON.stringify(data));
}
function writeFrame(res: ServerResponse, bytes: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const closed = () => { cleanup(); reject(new Error('FIXTURE_CONNECTION_CLOSED')); };
    const cleanup = () => res.off('close', closed);
    res.once('close', closed);
    res.write(bytes, error => { cleanup(); if (error) reject(new Error('FIXTURE_WRITE_FAILED')); else resolve(); });
  });
}
function barrier(state: ScenarioState, row: AttemptObservation, name: Barrier,
  cancelOnly: boolean, res: ServerResponse, timeoutMs: number): Promise<void> {
  return new Promise((resolve, reject) => {
    let done = false;
    const finish = (failed: boolean) => {
      if (done) return;
      done = true; clearTimeout(timer); res.off('close', closed);
      state.releases.delete(row.index); row.waiting = null;
      if (failed) reject(new Error('FIXTURE_BARRIER_CLOSED')); else resolve();
    };
    const closed = () => finish(true);
    const timer = setTimeout(() => {
      state.violations.push('BARRIER_DEADLINE'); row.terminal = 'fixture-failed';
      finish(true); res.destroy();
    }, timeoutMs);
    state.releases.set(row.index, { name, cancelOnly, release: () => finish(false) });
    row.waiting = name; row.reached = name;
    res.once('close', closed);
    if (res.destroyed) finish(true);
  });
}

export async function createMoneyProvider(options: ProviderOptions) {
  if (!Number.isSafeInteger(options.barrierTimeoutMs) || options.barrierTimeoutMs < 1 ||
      options.barrierTimeoutMs > 60_000) throw new Error('INVALID_FIXTURE_DEADLINE');
  const states = new Map<string, ScenarioState>();
  const requests = new Set<Promise<void>>();
  const responses = new Set<ServerResponse>();
  let unscopedRejections = 0;
  let closing = false;
  const handle = async (req: IncomingMessage, res: ServerResponse) => {
    // Local requests only; do not trust Host, forwarded headers, a DNS name or
    // a caller-supplied URL. All recorded identifiers are minted, never from req.
    if (closing || req.socket.remoteAddress !== '127.0.0.1' || req.method !== 'POST') {
      unscopedRejections++; json(res, 403, { error: 'FIXTURE_LOCAL_POST_REQUIRED' }); req.resume(); return;
    }
    const match = /^\/money\/([0-9a-f-]{36})\/([a-z][a-z0-9-]{0,31})(\/.*)$/.exec(req.url ?? '');
    const state = match ? states.get(match[1]) : undefined;
    if (!match || !state) {
      unscopedRejections++; json(res, 404, { error: 'FIXTURE_SCENARIO_ABSENT' }); req.resume(); return;
    }
    const index = state.attempts.length;
    const p = state.plans[index];
    if (!p || match[2] !== p.target || match[3] !== nativePath(p)) {
      state.violations.push('UNEXPECTED_ATTEMPT'); json(res, 409, { error: 'FIXTURE_ATTEMPT_MISMATCH' }); req.resume(); return;
    }
    // Reserve arrival order BEFORE awaiting request bytes, so concurrent requests
    // cannot both consume slot zero. A malformed attempt remains visible.
    const row: AttemptObservation = { index, target: p.target, dialect: p.dialect, status: 0,
      frames: 0, contentFrames: 0, partialUsageFrames: 0, completeUsageFrames: 0,
      credentialMatches: null, waiting: null, reached: null, cancelledAt: null, terminal: 'open' };
    state.attempts.push(row);
    res.once('close', () => {
      if (row.terminal !== 'open') return;
      row.terminal = res.writableFinished ? 'finished' : 'client-cancelled';
      if (row.terminal === 'client-cancelled') {
        // Registered before barrier's close listener clears waiting/release.
        row.cancelledAt = row.waiting ?? (row.contentFrames === 0 ? 'before-output' : 'between-barriers');
      }
    });
    try {
      const data = await readBody(req);
      if (state.upstreamCredential !== null) {
        // Source da4c81cc62: providers/openai/openai.go Authorization;
        // anthropic/anthropic.go x-api-key; gemini/gemini.go x-goog-api-key.
        const name = p.dialect === 'openai' ? 'authorization' : p.dialect === 'anthropic' ? 'x-api-key' : 'x-goog-api-key';
        const expected = p.dialect === 'openai' ? `Bearer ${state.upstreamCredential}` : state.upstreamCredential;
        row.credentialMatches = req.headers[name] === expected;
        if (!row.credentialMatches) {
          state.violations.push('SYNTHETIC_CREDENTIAL_MISMATCH'); row.status = 403; row.terminal = 'fixture-failed';
          json(res, 403, { error: 'FIXTURE_CREDENTIAL_MISMATCH' }); return;
        }
      }
      if (!matchesRequest(p, data, state.marker)) {
        state.violations.push('REQUEST_ANCHOR_MISMATCH'); row.status = 422;
        row.terminal = 'fixture-failed'; json(res, 422, { error: 'FIXTURE_REQUEST_MISMATCH' }); return;
      }
      if (p.outcome === 'http-429' || p.outcome === 'http-503') {
        row.status = p.outcome === 'http-429' ? 429 : 503;
        // Fixed synthetic error; shape pinned to providers' error decoders.
        // No error message, body or header from the incoming request is echoed.
        const error = p.dialect === 'gemini'
          ? { error: { code: row.status, status: row.status === 429 ? 'RESOURCE_EXHAUSTED' : 'UNAVAILABLE', message: 'fixture' } }
          : p.dialect === 'anthropic'
            ? { type: 'error', error: { type: row.status === 429 ? 'rate_limit_error' : 'api_error', message: 'fixture' } }
            : { error: { type: 'server_error', message: 'fixture', code: 'fixture_retryable' } };
        json(res, row.status, error); return;
      }
      row.status = 200;
      if (!p.stream) {
        row.frames = 1; row.contentFrames = 1;
        row.partialUsageFrames = p.usage === 'partial' ? 1 : 0;
        row.completeUsageFrames = p.usage === 'complete' ? 1 : 0;
        json(res, 200, nonStreamBody(p, state.marker)); return;
      }
      res.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' });
      res.flushHeaders();
      for (const event of compileAttempt(p, state.marker)) {
        if (res.destroyed) return;
        if (event.kind === 'frame') {
          await writeFrame(res, event.bytes);
          row.frames++; if (event.content) row.contentFrames++;
          if (event.usage === 'partial') row.partialUsageFrames++;
          if (event.usage === 'complete') row.completeUsageFrames++;
        } else if (event.kind === 'barrier') {
          await barrier(state, row, event.name, event.cancelOnly, res, options.barrierTimeoutMs);
        } else if (event.kind === 'disconnect') {
          row.terminal = 'provider-disconnected'; res.destroy(); return;
        } else {
          // Gemini STOP and EOF share one end() write. The real adapter may close
          // immediately on STOP; no after-terminal release is required. Premature
          // closes still fail unless this response actually finished writing.
          if (event.bytes === undefined) res.end();
          else res.end(event.bytes, () => { row.frames++; });
        }
      }
    } catch {
      // Cancellation is an observation, not an implementation failure. All other
      // failures stay visible in the safe counters; never print received bytes.
      if (row.terminal === 'client-cancelled' || row.terminal === 'fixture-closed') return;
      row.terminal = 'fixture-failed'; state.violations.push('FIXTURE_REQUEST_FAILED');
      if (!res.headersSent && !res.destroyed) { row.status = 400; json(res, 400, { error: 'FIXTURE_REQUEST_FAILED' }); }
      else res.destroy();
    }
  };
  const server = createServer((req, res) => {
    responses.add(res); res.once('close', () => responses.delete(res));
    res.on('error', () => { unscopedRejections++; });
    // Keep errors observed and input-free even if a transport error occurs before
    // route selection. No background rejected promises are allowed.
    const pending = handle(req, res).catch(() => { unscopedRejections++; res.destroy(); });
    requests.add(pending); void pending.then(() => requests.delete(pending));
  });
  server.requestTimeout = 15_000;
  server.headersTimeout = 10_000;
  server.on('clientError', (_error, socket) => { unscopedRejections++; socket.destroy(); });
  await new Promise<void>((resolve, reject) => {
    const failed = () => reject(new Error('FIXTURE_LISTEN_FAILED'));
    server.once('error', failed);
    server.listen(0, '127.0.0.1', () => { server.off('error', failed); resolve(); });
  });
  server.on('error', () => { unscopedRejections++; });
  const address = server.address();
  if (!address || typeof address === 'string' || address.address !== '127.0.0.1') {
    server.close(); throw new Error('FIXTURE_LOOPBACK_BINDING_FAILED');
  }
  const origin = `http://127.0.0.1:${address.port}`;
  return {
    origin,
    register(plan: ScenarioPlan) {
      if (closing || states.size >= 64 || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(plan.requestId) ||
          [...states.values()].some(s => s.requestId === plan.requestId) ||
          (plan.syntheticCredential !== undefined && typeof plan.syntheticCredential !== 'boolean') ||
          !Array.isArray(plan.attempts) || plan.attempts.length < 1 || plan.attempts.length > 16) {
        throw new Error('INVALID_OR_DUPLICATE_SCENARIO');
      }
      plan.attempts.forEach(validateAttempt);
      const state: ScenarioState = { id: randomUUID(), requestId: plan.requestId, marker: `money-${randomUUID()}`,
        plans: structuredClone([...plan.attempts]), attempts: [], violations: [], releases: new Map(),
        upstreamCredential: plan.syntheticCredential === true ? `fixture-${randomUUID()}` : null };
      states.set(state.id, state);
      return {
        id: state.id, requestId: state.requestId, marker: state.marker,
        // VM memory only. Attach snapshot(), never this handle.
        upstreamCredential: state.upstreamCredential,
        baseUrl(target: string) {
          if (!state.plans.some(p => p.target === target)) throw new Error('UNKNOWN_FIXTURE_TARGET');
          return `${origin}/money/${state.id}/${target}`;
        },
        snapshot() {
          return structuredClone({ schema: 'money-provider-observation/v1', fixtureId: state.id,
            requestId: state.requestId, attempts: state.attempts, violations: state.violations });
        },
        release(index: number, name: Barrier) {
          const pending = state.releases.get(index);
          if (!pending || pending.name !== name) throw new Error('BARRIER_NOT_WAITING');
          if (pending.cancelOnly) throw new Error('CLIENT_CANCEL_REQUIRED');
          pending.release();
        },
      };
    },
    health() { return { unscopedRejections, scenarios: states.size, activeResponses: responses.size }; },
    verifyComplete() {
      if (unscopedRejections !== 0 || states.size === 0) throw new Error('FIXTURE_INCOMPLETE_OR_UNSCOPED');
      for (const state of states.values()) {
        if (state.violations.length || state.attempts.length !== state.plans.length || state.releases.size) {
          throw new Error('FIXTURE_ATTEMPT_INVENTORY_INCOMPLETE');
        }
        for (const [index, row] of state.attempts.entries()) {
          assertAttemptComplete(state.plans[index], row);
        }
      }
    },
    async close() {
      if (closing) return;
      closing = true;
      for (const state of states.values()) for (const row of state.attempts) {
        if (row.terminal === 'open') row.terminal = 'fixture-closed';
      }
      const stopped = new Promise<void>((resolve, reject) => server.close(error => {
        if (error) reject(new Error('FIXTURE_CLOSE_FAILED')); else resolve();
      }));
      for (const res of responses) res.destroy();
      server.closeAllConnections();
      await Promise.all([stopped, ...requests]);
    },
  };
}
export type MoneyProvider = Awaited<ReturnType<typeof createMoneyProvider>>;
export type MoneyScenario = ReturnType<MoneyProvider['register']>;
