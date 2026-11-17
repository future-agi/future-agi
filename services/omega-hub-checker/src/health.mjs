import { createServer } from "node:http";

/** The registry's checker lease (futureagi/omega_hub/services/checks.py LEASE). */
export const LEASE_MS = 15 * 60_000;

/**
 * Healthy means the worker is making progress:
 *   - idle: the polling loop ran within `healthTimeoutMs` (a wedged loop goes unhealthy);
 *   - busy: the job was claimed, or last heartbeated, within `jobHealthTimeoutMs` (the lease). A
 *     normal check runs for minutes with the loop parked on it, so busy work is judged by the
 *     job's own progress, not by the poll timestamp.
 */
export function healthy(state, now = Date.now()) {
  if (state.stopping) return false;
  if (state.activeJobs > 0) {
    const progressAt = state.lastProgressAt ?? state.lastClaimAt ?? state.startedAt;
    return now - progressAt <= (state.jobHealthTimeoutMs ?? LEASE_MS);
  }
  const lastPollAt = state.lastPollAt ?? state.startedAt;
  return now - lastPollAt <= (state.healthTimeoutMs ?? 60_000);
}

const iso = (value) => (value ? new Date(value).toISOString() : null);

function payload(state) {
  return {
    status: healthy(state) ? "ok" : "unhealthy",
    startedAt: new Date(state.startedAt).toISOString(),
    claims: state.claims,
    completed: state.completed,
    activeJobs: state.activeJobs,
    lastPollAt: iso(state.lastPollAt),
    lastClaimAt: iso(state.lastClaimAt),
    lastProgressAt: iso(state.lastProgressAt),
    lastCompletionAt: iso(state.lastCompletionAt),
  };
}

/** The health listener, not yet listening (tests bind it to an ephemeral port). */
export function createHealthServer(state) {
  return createServer((request, response) => {
    if (request.url === "/healthz") {
      const body = payload(state);
      response.writeHead(body.status === "ok" ? 200 : 503, { "content-type": "application/json", "cache-control": "no-store" });
      response.end(JSON.stringify(body));
      return;
    }
    if (request.url === "/metrics") {
      const since = (value) => (value ? (Date.now() - value) / 1000 : 0);
      const lines = [
        `omega_hub_checker_claims_total ${state.claims}`,
        `omega_hub_checker_completed_total ${state.completed}`,
        `omega_hub_checker_active_jobs ${state.activeJobs}`,
        `omega_hub_checker_seconds_since_last_poll ${since(state.lastPollAt)}`,
        `omega_hub_checker_seconds_since_last_claim ${since(state.lastClaimAt)}`,
        `omega_hub_checker_seconds_since_last_progress ${since(state.lastProgressAt)}`,
      ];
      response.writeHead(200, { "content-type": "text/plain; version=0.0.4", "cache-control": "no-store" });
      response.end(`${lines.join("\n")}\n`);
      return;
    }
    response.writeHead(404, { "content-type": "text/plain" });
    response.end("not found\n");
  });
}

/** Port 0 (the default, OMEGA_HUB_CHECKER_HEALTH_PORT unset) turns the listener off. */
export function startHealthServer(port, state, host = "0.0.0.0") {
  if (!port) return null;
  const server = createHealthServer(state);
  server.listen(port, host);
  return server;
}
