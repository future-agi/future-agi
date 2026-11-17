// W7-4 remote scorers (W7-SCOPE §8 C1): the pure model of a remote scorer's
// definition, shared by the editor and the mock world (FE never imports
// _mock; the mock imports this, as DecisionJudges/decisionModels.js is).
//
// custom_template.config.remote = {url, auth {kind, token_last4?, oauth?},
//   timeout_ms, retries, retry_on, calls_at_a_time, send, output, mapping}
//
// Bounds are not invented here:
// - timeout_ms 5,000-30,000, default 15,000: the approved webhooks design
//   (webhooks/DESIGN.md:361, "the whole attempt's wall-clock budget").
// - retries 0-3, default 0, waits Retry-After else 2^n s: owner decision 3
//   (W7-SCOPE §11).
// - calls_at_a_time 1-8, default 1: W7-SCOPE §8 C1.
// - "HTTPS only. Private and local addresses are blocked.": the platform
//   rule for every outbound URL (notify/DESIGN.md, webhooks/DESIGN.md).

import { checkScore } from "src/sections/common/Runs/scoreStatus";

const NOT_AN_OBJECT = "score error: the answer is not a JSON object";

export const REMOTE_TIMEOUT_MS = Object.freeze({
  min: 5000,
  max: 30000,
  default: 15000,
});
export const REMOTE_RETRIES = Object.freeze({ min: 0, max: 3, default: 0 });
export const REMOTE_CALLS_AT_A_TIME = Object.freeze({
  min: 1,
  max: 8,
  default: 1,
});

export const RETRY_CLASSES = Object.freeze([
  { value: "429", label: "429 Too many requests" },
  { value: "5xx", label: "5xx server errors" },
  { value: "timeout", label: "No answer in time" },
]);

export const AUTH_KINDS = Object.freeze([
  { value: "hmac", label: "Signed (HMAC-SHA256)" },
  { value: "bearer", label: "Bearer token" },
  { value: "oauth", label: "OAuth client credentials" },
  { value: "none", label: "No auth" },
]);

export const SEND_MODES = Object.freeze([
  { value: "row", label: "The row and the output" },
  { value: "trace", label: "The row, the output and the trace steps" },
]);

export const REMOTE_OUTPUT_TYPES = Object.freeze([
  { value: "score", label: "Score 0 to 1" },
  { value: "pass_fail", label: "Pass / fail" },
  { value: "choice", label: "Choice" },
]);

export const RUN_URL_MESSAGE =
  "Runs call your scorer from Future AGI: use an https URL it can reach. Private and local addresses are blocked.";
export const CALLED_FROM_BROWSER = "Called from your browser";
export const SIGNATURE_HEADER = "X-FutureAGI-Signature";
export const SIGNATURE_LABEL = "X-FutureAGI-Signature (HMAC-SHA256)";

/** The URL parsed, or null. */
export function parseUrl(url) {
  try {
    const u = new URL(String(url ?? "").trim());
    return u.protocol === "http:" || u.protocol === "https:" ? u : null;
  } catch {
    return null;
  }
}

/** The host a URL names (no port), or "" for a URL that does not parse. */
export const hostOf = (url) => parseUrl(url)?.hostname ?? "";

const ipv4Of = (host) => {
  const m = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(host);
  if (!m) return null;
  const parts = m.slice(1).map(Number);
  return parts.every((n) => n <= 255) ? parts : null;
};

// The eight 16-bit groups of an IPv6 literal (the URL parser writes the
// short hex form, `[::ffff:7f00:1]` for `[::ffff:127.0.0.1]`; a dotted IPv4
// tail is read too), or null.
function ipv6Groups(host) {
  let h = host;
  const tail = /(\d{1,3}(?:\.\d{1,3}){3})$/.exec(h);
  if (tail) {
    const v4 = ipv4Of(tail[1]);
    if (!v4) return null;
    const hi = ((v4[0] << 8) | v4[1]).toString(16);
    const lo = ((v4[2] << 8) | v4[3]).toString(16);
    h = `${h.slice(0, -tail[1].length)}${hi}:${lo}`;
  }
  const halves = h.split("::");
  if (halves.length > 2) return null;
  const part = (x) => (x ? x.split(":") : []);
  const head = part(halves[0]);
  const rest = halves.length === 2 ? part(halves[1]) : [];
  const fill = halves.length === 2 ? 8 - head.length - rest.length : 0;
  if (fill < 0) return null;
  const groups = [...head, ...Array(fill).fill("0"), ...rest];
  if (groups.length !== 8 || groups.some((g) => !/^[0-9a-f]{1,4}$/.test(g)))
    return null;
  return groups.map((g) => parseInt(g, 16));
}

const isPrivateV4 = ([a, b]) =>
  a === 0 ||
  a === 10 ||
  a === 127 ||
  (a === 169 && b === 254) ||
  (a === 172 && b >= 16 && b <= 31) ||
  (a === 192 && b === 168) ||
  (a === 100 && b >= 64 && b <= 127);

// The IPv4 address in the last 32 bits of an IPv6 form that reaches it:
// mapped ::ffff:a.b.c.d, translated ::ffff:0:a.b.c.d, NAT64
// 64:ff9b::a.b.c.d or compatible ::a.b.c.d (so :: and ::1 too).
function embeddedV4(g) {
  const zero = (from, to) => g.slice(from, to).every((x) => x === 0);
  const carries =
    (zero(0, 5) && g[5] === 0xffff) ||
    (zero(0, 4) && g[4] === 0xffff && g[5] === 0) ||
    zero(0, 6) ||
    (g[0] === 0x64 && g[1] === 0xff9b && zero(2, 6));
  return carries ? [g[6] >> 8, g[6] & 255, g[7] >> 8, g[7] & 255] : null;
}

/**
 * Whether a host is local or a private, loopback or link-local IP literal
 * (localhost and *.localhost, with or without a trailing dot; 127/8, 10/8,
 * 172.16/12, 192.168/16, 169.254/16, 0/8, 100.64/10; ::, ::1, fc00::/7,
 * fe80::/10, fec0::/10 and the IPv6 forms that carry one of those IPv4
 * addresses, in the URL parser's hex form too).
 */
export function isPrivateHost(host) {
  const h = String(host ?? "")
    .toLowerCase()
    .replace(/^\[|\]$/g, "")
    .replace(/\.+$/, "");
  if (!h) return true;
  if (h === "localhost" || h.endsWith(".localhost")) return true;
  const v4 = ipv4Of(h);
  if (v4) return isPrivateV4(v4);
  if (h.includes(":")) {
    const g = ipv6Groups(h);
    if (!g) return true;
    const mapped = embeddedV4(g);
    if (mapped) return isPrivateV4(mapped);
    return (
      (g[0] & 0xfe00) === 0xfc00 ||
      (g[0] & 0xffc0) === 0xfe80 ||
      (g[0] & 0xffc0) === 0xfec0
    );
  }
  return false;
}

/** Whether a URL is on the author's own machine (Test calls it from the browser). */
export function isBrowserLocalUrl(url) {
  const u = parseUrl(url);
  if (!u) return false;
  const h = u.hostname.replace(/^\[|\]$/g, "").replace(/\.+$/, "");
  return h === "localhost" || h === "127.0.0.1" || h === "::1";
}

/** null when runs may call `url`, else the reason they may not. */
export function runUrlError(url) {
  const u = parseUrl(url);
  if (!u)
    return "Enter the scorer's URL, like https://scorer.example.com/score";
  if (u.protocol !== "https:" || isPrivateHost(u.hostname))
    return RUN_URL_MESSAGE;
  return null;
}

/**
 * How Test on one row calls `url`: {mode: "platform"} for a URL runs may
 * call, {mode: "browser", label} for http(s)://localhost or 127.0.0.1, else
 * {error}.
 */
export function testUrlMode(url) {
  if (!runUrlError(url)) return { mode: "platform" };
  if (isBrowserLocalUrl(url))
    return { mode: "browser", label: CALLED_FROM_BROWSER };
  return { error: runUrlError(url) };
}

const intIn = (v, { min, max }, fallback) => {
  if (v === undefined || v === null || v === "") return { value: fallback };
  const n = Number(v);
  if (!Number.isInteger(n) || n < min || n > max) return { error: true };
  return { value: n };
};

/**
 * Validate a remote scorer definition for saving (both sides).
 * @param {object} input  the editor's form or a create / PATCH body's remote
 * @param {{forTest?: boolean}} [opts]  Test on one row also accepts a URL on
 *   the author's machine
 * @returns {{value: object, errors: Object<string, string>}}
 */
export function validateRemoteDef(input = {}, { forTest = false } = {}) {
  const errors = {};
  const url = String(input.url ?? "").trim();
  const urlErr = forTest ? testUrlMode(url).error : runUrlError(url);
  if (urlErr) errors.url = urlErr;
  const kind = input.auth?.kind ?? "hmac";
  if (!AUTH_KINDS.some((k) => k.value === kind))
    errors.auth = "Pick how Future AGI signs in to your scorer";
  const oauth = input.auth?.oauth ?? null;
  if (kind === "oauth") {
    const tokenUrlErr = forTest
      ? testUrlMode(oauth?.token_url).error
      : runUrlError(oauth?.token_url);
    if (!oauth?.token_url || tokenUrlErr)
      errors.token_url = tokenUrlErr || "Enter the token URL";
    if (!String(oauth?.client_id ?? "").trim())
      errors.client_id = "Enter the client id";
  }
  const t = intIn(
    input.timeout_ms,
    REMOTE_TIMEOUT_MS,
    REMOTE_TIMEOUT_MS.default,
  );
  if (t.error)
    errors.timeout_ms = `Timeout is ${REMOTE_TIMEOUT_MS.min / 1000} to ${REMOTE_TIMEOUT_MS.max / 1000} s`;
  const r = intIn(input.retries, REMOTE_RETRIES, REMOTE_RETRIES.default);
  if (r.error)
    errors.retries = `Retries are ${REMOTE_RETRIES.min} to ${REMOTE_RETRIES.max}`;
  const c = intIn(
    input.calls_at_a_time,
    REMOTE_CALLS_AT_A_TIME,
    REMOTE_CALLS_AT_A_TIME.default,
  );
  if (c.error)
    errors.calls_at_a_time = `Calls at a time are ${REMOTE_CALLS_AT_A_TIME.min} to ${REMOTE_CALLS_AT_A_TIME.max}`;
  const retryOn = [...new Set(input.retry_on ?? ["429", "5xx", "timeout"])];
  if (retryOn.some((x) => !RETRY_CLASSES.some((k) => k.value === x)))
    errors.retry_on = "Retry on 429, 5xx or timeouts only";
  if ((r.value ?? 0) > 0 && !retryOn.length)
    errors.retry_on = "Pick what to retry on, or set retries to 0";
  const send = input.send ?? "row";
  if (!SEND_MODES.some((s) => s.value === send))
    errors.send = "Send the row, or the row and the trace";
  const outType = input.output?.type ?? "score";
  if (!REMOTE_OUTPUT_TYPES.some((o) => o.value === outType))
    errors.output = "Pick the answer type";
  let choices;
  if (outType === "choice") {
    choices = input.output?.choices ?? [];
    const labels = choices.map((x) => String(x.label ?? "").trim());
    if (choices.length < 2) errors.choices = "Add at least two choices";
    else if (labels.some((l) => !l) || new Set(labels).size !== labels.length)
      errors.choices = "Each choice needs its own label";
    else if (
      choices.some(
        (x) =>
          !(Number.isFinite(Number(x.score)) && x.score >= 0 && x.score <= 1),
      )
    )
      errors.choices = "Give each choice a score from 0 to 1, like Yes = 1";
  }
  const mapping = {};
  Object.entries(input.mapping ?? {}).forEach(([k, v]) => {
    const key = String(k).trim();
    if (key) mapping[key] = v == null ? "" : String(v).trim();
  });
  if (Object.keys(mapping).some((k) => !/^[A-Za-z_][A-Za-z0-9_]*$/.test(k)))
    errors.mapping = "Input names use letters, digits and _";
  const value = {
    url,
    auth: {
      kind,
      ...(kind === "oauth"
        ? {
            oauth: {
              token_url: String(oauth?.token_url ?? "").trim(),
              client_id: String(oauth?.client_id ?? "").trim(),
              scope: String(oauth?.scope ?? "").trim(),
            },
          }
        : {}),
    },
    timeout_ms: t.value ?? REMOTE_TIMEOUT_MS.default,
    retries: r.value ?? REMOTE_RETRIES.default,
    retry_on: retryOn,
    calls_at_a_time: c.value ?? REMOTE_CALLS_AT_A_TIME.default,
    send,
    output: {
      type: outType,
      ...(outType === "choice"
        ? {
            choices: (choices ?? []).map((x) => ({
              label: String(x.label).trim(),
              score: Number(x.score),
            })),
          }
        : {}),
    },
    mapping,
  };
  return { value, errors };
}

/**
 * Validate a scorer's answer {score, passed?, reason?, label?} through W1-4's
 * one check (scoreStatus.checkScore), in its wording ("score error: 1.4 >
 * 1"); the answer's shape is this protocol's own. Returns {value, passed,
 * reason, label?} or {error, raw}.
 */
export function validateAnswer(answer, output = {}, threshold = 0.5) {
  const type = output?.type ?? "score";
  const obj = answer && typeof answer === "object" && !Array.isArray(answer);
  if (!obj) return { error: NOT_AN_OBJECT, raw: answer ?? null };
  const has = (k) => answer[k] !== undefined && answer[k] !== null;
  const field =
    type === "choice"
      ? "label"
      : type === "pass_fail" && has("passed")
        ? "passed"
        : "score";
  if (!has(field))
    return {
      error: `score error: no ${field === "label" ? "label" : "score"} in the answer`,
      raw: null,
    };
  const raw = answer[field];
  const choices = Object.fromEntries(
    (output.choices ?? []).map((c) => [c.label, c.score]),
  );
  const check = checkScore(
    raw,
    type === "choice"
      ? { outputType: "choices", choices }
      : { outputType: type },
  );
  if (!check.ok) return { error: check.reason, raw };
  const passed =
    type === "pass_fail"
      ? check.value === 1
      : type === "score" && typeof answer.passed === "boolean"
        ? answer.passed
        : check.value >= threshold;
  const reason = typeof answer.reason === "string" ? answer.reason : "";
  return {
    value: check.value,
    passed,
    reason,
    ...(type === "choice" ? { label: check.label } : {}),
  };
}

/** "•••• 1a2b" for a stored token's last four. */
export const maskedToken = (last4) => (last4 ? `•••• ${last4}` : "");

/** "15 s" for 15000 ms. */
export const secondsText = (ms) => `${Math.round(Number(ms) / 1000)} s`;

/** One try's line: "Try 1 · 503 · 120 ms" or "Try 2 · No answer in 5 s". */
export function tryLine(t = {}) {
  const head = `Try ${t.n}`;
  if (t.status) return `${head} · ${t.status} · ${Math.round(t.ms ?? 0)} ms`;
  return `${head} · ${t.error ?? "no answer"}`;
}

/** The confirm of Score a run: "20 calls to host · billed by your service · 0 Future AGI credits". */
export const scoreRunConfirmText = (calls, host) =>
  `${calls} call${calls === 1 ? "" : "s"} to ${host} · billed by your service · 0 Future AGI credits`;

const UNIT_WORDS = Object.freeze({
  cell: ["cell", "cells"],
  row: ["row", "rows"],
  try: ["try", "tries"],
});

/**
 * What a batch of remote calls came to, failures counted, never called
 * scored: "20 cells scored by host", "0 rows scored, 3 failed: HTTP 503
 * from host" (the drainer's run note and Score a run's toast).
 * @param {{scored: number, failed?: number, unit?: "cell"|"row"|"try",
 *   by?: string|null, error?: string|null}} p
 */
export function scoredText({
  scored,
  failed = 0,
  unit = "cell",
  by = null,
  error = null,
}) {
  const [one, many] = UNIT_WORDS[unit] ?? UNIT_WORDS.cell;
  const head = `${scored} ${scored === 1 ? one : many} scored${by ? ` by ${by}` : ""}`;
  return failed
    ? `${head}, ${failed} failed${error ? `: ${error}` : ""}`
    : head;
}
