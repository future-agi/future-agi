// CX-WALL (X-1): no channel token is ever written to a gateway log line.
//
// The access line carries the route template only and the refusal line the path without its query
// (trace.mjs, refusal-log.mjs), so no token reaches either by design. This is the last guard on the
// sinks themselves: any `cct_` token value and any `...token=<value>` pair is replaced before a line
// is written, so a future log call that prints a URL cannot leak one. EE's sink applies the same rule.
const TOKEN_VALUE = /cct_[A-Za-z0-9_-]{43}/gu;
const TOKEN_PAIR = /([A-Za-z_]*token)=([^&\s"'#\\]+)/giu;

export const REDACTED = "[redacted]";

export function redactSecrets(line) {
  return String(line).replace(TOKEN_VALUE, `cct_${REDACTED}`).replace(TOKEN_PAIR, (_match, key) => `${key}=${REDACTED}`);
}
