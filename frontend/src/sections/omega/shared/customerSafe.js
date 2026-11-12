/**
 * The COPY1 check (CP COPY1, READINESS L07): words and shapes a customer must
 * never read. A server message that contains one is replaced by Future AGI
 * copy (omegaReasons.js), so no operator text, internal address, daemon name
 * or system error reaches a page through a server's own words.
 *
 * The list is at least the one the tests and the CP scrape judge pages by
 * (__tests__/omegaReasons.test.js COPY1), plus the internal shapes a raw
 * upstream failure carries.
 */
const FORBIDDEN = [
  /api[\s_-]?key/i,
  /omega server/i,
  /\boperators?\b/i,
  /\bbindings?\b/i,
  /colima/i,
  /container host/i,
  /coming soon/i,
  /control plane/i,
  /host[\s_-]?assertion/i,
  /\bassertions?\b/i,
  /omega[\s_-]?squad/i,
  /\bdelegat/i,
  /\bjwks\b/i,
  /environment variable/i,
  /\benv var/i,
  // Internal addresses and processes.
  /\blocalhost\b/i,
  /\b(?:\d{1,3}\.){3}\d{1,3}\b/, // an IPv4 address
  /\[[0-9a-f:]*:[0-9a-f:]*\]/i, // a bracketed IPv6 address such as [::1]
  /\b[a-z][a-z0-9+.-]*:\/\/[^\s/]+:\d{2,5}\b/i, // a URL with an explicit port
  /\.svc(?:\.cluster\.local)?\b|\.internal\b/i,
  /\bdaemon\b/i,
  // A container command line, which servers write in lower case ("docker run
  // failed (exit 125): ...", "Could not run docker: ..."); prose about a
  // customer's Dockerfile or "Docker build" is not one.
  /\bdocker (?:run|start|stop|rm|rmi|inspect|image|pull|push|build|exec|create|kill|logs|ps|wait|network|volume|compose)\b/,
  /\b(?:[Cc]ould not|[Cc]annot|[Cc]an't|[Ff]ailed to) run docker\b/,
  // System errors and stack frames: Node and libc error codes, a failed
  // spawn ("spawn docker ENOENT"), a stack frame.
  /\bE(?:ACCES|ADDRINUSE|ADDRNOTAVAIL|AGAIN|AI_AGAIN|BADF|BUSY|CONN[A-Z]+|EXIST|HOSTDOWN|HOSTUNREACH|ISDIR|MFILE|NAMETOOLONG|NETDOWN|NETRESET|NETUNREACH|NFILE|NOBUFS|NOENT|NOEXEC|NOMEM|NOSPC|NOTCONN|NOTDIR|NOTEMPTY|NOTFOUND|NOTSOCK|NOTSUP|NXIO|PERM|PIPE|PROTO|ROFS|SPIPE|TIMEDOUT|TXTBSY|XDEV)\b/,
  /\bspawn \S+ E[A-Z]{3,}\b/,
  /\bat [\w$.<>]+ \([^)]*:\d+:\d+\)/,
];

/**
 * An environment-variable name such as OMEGA_HOST_ASSERTIONS. A message that
 * carries one is operator text, unless its code says the names in it are the
 * customer's own (`names`).
 */
const ENV_VAR_NAME = /\b[A-Z][A-Z0-9]*_[A-Z0-9_]{2,}\b/;
const ENV_VAR_NAMES = new RegExp(ENV_VAR_NAME.source, "g");
/** Variables only an operator sets: never allowed, whoever's message it is. */
const OPERATOR_VAR = /\b(?:OMEGA|FA|FUTUREAGI|FUTURE_AGI)_[A-Z0-9_]{2,}\b/;

/**
 * True when `text` is safe to show a customer as-is.
 *
 * `names`: the server's code says every upper-case name in its message is one
 * the customer chose (EE `secret_unavailable` names a deployment's secret
 * reference, such as DATABASE_URL or OPENAI_API_KEY, which the Deployments tab
 * already shows as data). Those names are then data, not words: the prose
 * around them must still pass every check, and an operator's variable
 * (OMEGA_*, FA_*) still fails it.
 */
export function isCustomerSafe(text, { names = false } = {}) {
  if (typeof text !== "string" || text.trim().length === 0) return false;
  if (OPERATOR_VAR.test(text)) return false;
  if (!names && ENV_VAR_NAME.test(text)) return false;
  const prose = names ? text.replace(ENV_VAR_NAMES, "NAME") : text;
  return !FORBIDDEN.some((pattern) => pattern.test(prose));
}
