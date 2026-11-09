// One JSON line for every answer the gateway gives itself instead of EE's (READINESS 3.13, RC-03:
// each refused attempt "answers ... and logs its reason"). Django logs its own refusals as
// `omega_rollout_refused`; this is the gateway's half.
//
// A line names the method, the route path, the status and the stable error and reason. It names
// the org and workspace only when this gateway verified the signature of the key that carries them,
// so a forged key cannot write someone else's ids into the log. It never holds a credential: no
// header, no query string (a credential in the query is itself a refusal), no body, and the path is
// cut to 512 characters.
import { redactSecrets } from "./channel-token/redact.mjs";

const MAX_PATH = 512;
const MAX_FIELD = 128;

export function stderrSink(line) {
  process.stderr.write(`${redactSecrets(line)}\n`);
}

const text = (value, max = MAX_FIELD) => String(value ?? "").slice(0, max);

/**
 * @param {(line: string) => void} [sink] where each JSON line goes (stderr by default)
 * @returns {(entry: { method?: string, path?: string, status: number, error: string, reason?: string,
 *   claims?: object, verified?: boolean }) => void}
 */
export function createRefusalLog(sink = stderrSink, now = () => new Date().toISOString()) {
  return ({ method, path, status, error, reason, claims, verified }) => {
    const line = {
      event: status === 502 ? "omega_gateway_failed" : "omega_gateway_refused",
      at: now(),
      method: text(method, 16),
      path: text(path, MAX_PATH),
      status,
      error: text(error),
    };
    if (reason) line.reason = text(reason);
    if (verified && claims && typeof claims === "object") {
      if (typeof claims.org_id === "string") line.org_id = text(claims.org_id);
      if (typeof claims.workspace_id === "string") line.workspace_id = text(claims.workspace_id);
    }
    try {
      sink(JSON.stringify(line));
    } catch {
      // A log sink that fails never changes the answer the caller gets.
    }
  };
}
