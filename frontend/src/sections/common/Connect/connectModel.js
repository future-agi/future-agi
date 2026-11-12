// The live connection model (R4 integration; R4-SPEC §4, D21). Pure: the state
// machine of a live-connection session, timers, the access rules of a
// connected machine and what the worker accepts. Copy lives in
// connectCopy.js (status lines, trouble notices, the run strip). Time is
// always passed in (`now`), never read here, so timers never drift: every
// number on screen is computed from timestamps.
//
// Everything here is Preview: the SDK has no connect module yet (R4-SPEC V12).

/** Every state of a connection (frozen; R4-SPEC §4). */
export const CONNECT_STATES = Object.freeze([
  "idle",
  "waiting",
  "connecting",
  "connected",
  "running",
  "stopped",
  "reloaded",
  "stale",
  "offline",
  "version_mismatch",
  "firewall",
  "token_expired",
  "busy",
  "not_allowed",
  "waiting_local_confirm",
  "error",
]);

/** States that show a TroubleNotice (one next step each). */
export const TROUBLE_STATES = Object.freeze([
  "firewall",
  "version_mismatch",
  "token_expired",
  "stale",
  "offline",
  "busy",
  "not_allowed",
  "waiting_local_confirm",
  "error",
]);

/** States in which the agent is reachable (heartbeat line, Try on 1 row). */
export const LIVE_STATES = Object.freeze([
  "connected",
  "running",
  "stopped",
  "reloaded",
]);

/** States in which no agent has registered on this token. */
const NOT_CONNECTED = Object.freeze([
  "idle",
  "waiting",
  "connecting",
  "firewall",
  "version_mismatch",
  "token_expired",
]);

/** The prototype's `Simulate this command` scenarios, in menu order. */
export const SCENARIOS = Object.freeze([
  { id: "ok", label: "Works" },
  { id: "firewall", label: "Firewall blocks it" },
  { id: "version_mismatch", label: "Old SDK" },
  { id: "token_expired", label: "Token expired" },
]);

const SCENARIO_ALIASES = Object.freeze({ old_sdk: "version_mismatch" });

/** A scenario id (CONTRACT.md spells Old SDK `old_sdk`); unknown → `ok`. */
export function scenarioId(id) {
  const v = SCENARIO_ALIASES[id] ?? id;
  return SCENARIOS.some((s) => s.id === v) ? v : "ok";
}

/** Heartbeats every 5 s; stale after 30 s without one; offline after 10 min. */
export const HEARTBEAT_MS = 5000;
export const STALE_AFTER_MS = 30_000;
export const OFFLINE_AFTER_MS = 10 * 60_000;
/** The troubleshooting line shows after a minute in `waiting`. */
export const STUCK_AFTER_MS = 60_000;
/** Tokens live ten minutes (user- and workspace-scoped). */
export const TOKEN_TTL_MS = 10 * 60_000;
/** The first-result goal: under two minutes from opening the drawer. */
export const FIRST_RESULT_GOAL_MS = 2 * 60_000;

const num = (v) => (Number.isFinite(Number(v)) ? Number(v) : null);
const toMs = (t) => {
  if (t == null || t === "") return null;
  if (t instanceof Date) return t.getTime();
  if (typeof t === "number") return Number.isFinite(t) ? t : null;
  const p = Date.parse(t);
  return Number.isFinite(p) ? p : null;
};

/** Milliseconds from `from` (ISO / ms) to `now`; never negative; null when unknown. */
export function elapsedMs(from, now) {
  const a = toMs(from);
  const b = toMs(now);
  if (a == null || b == null) return null;
  return Math.max(0, b - a);
}

/**
 * A timer `m:ss` (`0:12`, `9:12`, `12:05`): whole seconds, floored, so a
 * timer that ticks once a second never jumps or repeats.
 */
export function fmtClock(ms) {
  const n = num(ms);
  const total = n == null ? 0 : Math.max(0, Math.floor(n / 1000));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

/** Seconds left on a token (`Expires in 9:12`); 0 once expired. */
export function tokenLeftMs(expiresAt, now) {
  const e = toMs(expiresAt);
  const b = toMs(now);
  if (e == null || b == null) return 0;
  return Math.max(0, e - b);
}

/** `fic_live_…7Q2k`: tokens are masked on screen and copied in full. */
export function maskToken(token) {
  const t = String(token ?? "");
  if (!t) return "";
  const m = /^(fic_[a-z]+_)/.exec(t);
  const tail = t.slice(-4);
  return `${m ? m[1] : ""}…${tail}`;
}

// ------------------------------------------------------------ the session

/**
 * The state a session is in after its played events (R4-SPEC §4 table).
 * Events `{type, at_ms}`; types: `socket_open`, `socket_open_failed`,
 * `fallback`, `failed`, `rejected` (+ `reason: "version"|"token"`),
 * `registered`, `heartbeat`, `try_started`, `try_done`, `stopped`,
 * `resumed`, `reload`, `local_confirm_needed`, `local_confirmed`, `error`.
 * @param {{type: string, reason?: string}[]} events  in time order
 * @param {{issued?: boolean}} [opts]  a token exists (else `idle`)
 */
export function stateFromEvents(events, { issued = true } = {}) {
  let state = issued ? "waiting" : "idle";
  (events ?? []).forEach((e) => {
    state = transition(state, e);
  });
  return state;
}

/** One step of the state machine; unknown events keep the state. */
export function transition(state, event) {
  const type = event?.type;
  switch (type) {
    case "token_issued":
      return "waiting";
    case "socket_open":
    case "fallback":
      return "connecting";
    case "socket_open_failed":
      return state === "idle" ? "idle" : "connecting";
    case "failed":
      return "firewall";
    case "rejected":
      if (event.reason === "token") return "token_expired";
      return "version_mismatch";
    case "registered":
      return "connected";
    case "heartbeat":
      return state === "stale" || state === "offline" ? "connected" : state;
    case "heartbeat_missed":
      return LIVE_STATES.includes(state) ? "stale" : state;
    case "went_offline":
      return "offline";
    case "try_started":
    case "resumed":
      return "running";
    case "try_done":
      return "connected";
    case "stopped":
      return "stopped";
    case "reload":
      return "reloaded";
    case "busy":
      return "busy";
    case "not_allowed":
      return "not_allowed";
    case "local_confirm_needed":
      return "waiting_local_confirm";
    case "local_confirmed":
      return "running";
    case "error":
      return "error";
    default:
      return state;
  }
}

/** Is a heartbeat this old stale / offline? (`null` = live or unknown). */
export function heartbeatHealth(ageMs) {
  const a = num(ageMs);
  if (a == null) return null;
  if (a >= OFFLINE_AFTER_MS) return "offline";
  if (a >= STALE_AFTER_MS) return "stale";
  return null;
}

/**
 * What happens next: the three-item checklist under the status line
 * (`Agent connected` · `Parameters and tools read (4 · 7)` · `First
 * result`), ticking itself.
 * @param {{state: string, agent?: object|null, firstResult?: object|null}} s
 */
export function checklistOf({ state, agent, firstResult } = {}) {
  const connected = Boolean(agent) && !NOT_CONNECTED.includes(state);
  const params = agent?.params_schema?.length ?? 0;
  const tools = toolCountOf(agent);
  return [
    { id: "connected", label: "Agent connected", done: connected },
    {
      id: "read",
      label: connected
        ? `Parameters and tools read (${params} · ${tools})`
        : "Parameters and tools read",
      done: connected,
    },
    { id: "first", label: "First result", done: Boolean(firstResult) },
  ];
}

/** Tools an agent can call: its own plus every MCP server's. */
export const toolCountOf = (agent) =>
  (agent?.tools?.length ?? 0) +
  (agent?.mcp_servers ?? []).reduce((n, m) => n + (Number(m.tools) || 0), 0);

/** Under the two-minute goal? */
export const firstResultFast = (ms) =>
  num(ms) != null && Number(ms) < FIRST_RESULT_GOAL_MS;

// ------------------------------------------------------------ access (D21)

/**
 * Who may do what on a machine (R4-SPEC D21). The mock answers `you`,
 * `may_use`, `can_manage`; these rules are the same ones, for the UI and the
 * mock's tests.
 * @param {object} machine  ConnectedMachine (+ `you`, `may_use`)
 * @param {{userId?: string, readOnly?: boolean}} [viewer]
 */
export function accessOf(machine, { userId, readOnly = false } = {}) {
  if (!machine)
    return { isOwner: false, mayUse: false, canToggle: false, reason: null };
  const isOwner =
    machine.you === true || (userId != null && machine.owner?.id === userId);
  const mayUse =
    machine.may_use != null
      ? Boolean(machine.may_use)
      : isOwner || Boolean(machine.allow_teammates);
  const ownerName = machine.owner?.name ?? "the owner";
  let reason = null;
  if (!isOwner) reason = `Only ${ownerName} can change this`;
  else if (readOnly) reason = "Viewers can't change runners";
  return { isOwner, mayUse, canToggle: isOwner && !readOnly, reason };
}

/** The first name of a person (`Ravi Menon` → `Ravi`). */
export const firstName = (name) => String(name ?? "").split(/\s+/)[0] || "";

/**
 * What the worker accepts from the UI: only the parameters the agent
 * declared, inside their range (R4-SPEC D21). Everything else is refused.
 * @param {object[]} schema  ParamSchema[]
 * @param {Object<string, any>} values
 * @returns {{accepted: Object<string, any>, refused: {name, reason}[]}}
 */
export function checkParams(schema, values) {
  const byName = new Map((schema ?? []).map((p) => [p.name, p]));
  const accepted = {};
  const refused = [];
  Object.entries(values ?? {}).forEach(([name, value]) => {
    const p = byName.get(name);
    if (!p) {
      refused.push({ name, reason: "not declared" });
      return;
    }
    const why = valueProblem(p, value);
    if (why) refused.push({ name, reason: why });
    else accepted[name] = value;
  });
  return { accepted, refused };
}

function valueProblem(p, value) {
  if (["int", "float"].includes(p.type)) {
    const n = num(value);
    if (n == null) return "not a number";
    if (p.type === "int" && !Number.isInteger(n)) return "not a whole number";
    if (p.min != null && n < p.min) return "below the declared range";
    if (p.max != null && n > p.max) return "above the declared range";
    return null;
  }
  if (p.type === "bool")
    return typeof value === "boolean" ? null : "not true or false";
  if ((p.type === "enum" || p.type === "model") && p.options?.length)
    return p.options.includes(value) ? null : "not one of the declared options";
  if (typeof value !== "string") return "not text";
  return null;
}

const PERMISSION_ORDER = ["read_only", "edit", "full_auto"];

/**
 * Does a harness recipe change widen permissions? (`Full auto`, a new MCP
 * server, a custom command). Such reruns wait for a confirmation on the
 * machine unless it was started with `--allow-recipe-changes`.
 * @param {{permissions?: string, mcp_servers?: {name}[], command?: string}} next
 * @param {{permissions?: string, mcp_servers?: {name}[], command?: string}} current
 * @returns {{widens: boolean, reasons: string[]}}
 */
export function recipeWidens(next = {}, current = {}) {
  const reasons = [];
  const a = PERMISSION_ORDER.indexOf(current.permissions ?? "edit");
  const b = PERMISSION_ORDER.indexOf(next.permissions ?? current.permissions);
  if (b > a && next.permissions === "full_auto") reasons.push("full_auto");
  else if (b > a) reasons.push("permissions");
  const had = new Set((current.mcp_servers ?? []).map((s) => s.name));
  if ((next.mcp_servers ?? []).some((s) => !had.has(s.name)))
    reasons.push("mcp_server");
  if (next.command && next.command !== current.command)
    reasons.push("custom_command");
  return { widens: reasons.length > 0, reasons };
}

/** Does a rerun need a local confirmation on this machine? */
export const needsLocalConfirm = (machine, widening) =>
  Boolean(widening?.widens) && !machine?.allow_recipe_changes;

// ------------------------------------------------------------ live runs

/** Tries that count toward the pass rate (stopped tries never count). */
export function countedTries(lines) {
  return (lines ?? []).filter((l) => l.passed === true || l.passed === false);
}

/** Do the tries of one run all carry the run's sha? (a run never mixes code) */
export function oneShaPerRun(lines, sha) {
  return (lines ?? []).every((l) => !l.code_sha || l.code_sha === sha);
}

/** The fields a handshake declares (what `Show handshake` prints). */
export const HANDSHAKE_FIELDS = Object.freeze([
  "id",
  "name",
  "kind",
  "entry_id",
  "entry",
  "framework_version",
  "runtime",
  "modalities",
  "params_schema",
  "tools",
  "side_effect_tools",
  "mcp_servers",
  "models",
  "concurrency",
  "watch",
  "git",
  "capture",
  "livekit_url",
  "endpoint_url",
]);

/** A copy without null / undefined values at any depth. */
export function dropEmpty(value) {
  if (Array.isArray(value))
    return value.filter((v) => v !== null && v !== undefined).map(dropEmpty);
  if (value && typeof value === "object") {
    const out = {};
    Object.entries(value).forEach(([k, v]) => {
      if (v !== null && v !== undefined) out[k] = dropEmpty(v);
    });
    return out;
  }
  return value;
}

/** The handshake record of an agent: declared fields only, no empty values at any depth. */
export function handshakeRecord(agent) {
  if (!agent) return null;
  const out = {};
  HANDSHAKE_FIELDS.forEach((k) => {
    if (agent[k] !== null && agent[k] !== undefined) out[k] = agent[k];
  });
  return dropEmpty(out);
}
