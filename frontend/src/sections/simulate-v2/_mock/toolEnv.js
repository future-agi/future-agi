import { envChangePatch } from "./versions";
import { gatewayToolStatusOf } from "./gatewayMcp";

/*
  The tool environment (PRD v6, §4.5 and Story 5): each tool runs at a chosen
  fidelity, may carry a fault profile, and tools that touch the same records
  share state within a run.

    stub     — a canned response. Cheapest, fully deterministic.
    replay   — a captured real response, replayed. Deterministic; can go stale.
    stateful — the world's data, read and written; consistent across calls.
    live     — the real tool, inside the sandbox. Non-deterministic; never
               production.

  Defaults come from the tool's side-effect class and are one click to
  change. A tier or fault change is a world edit — a minor environment
  version (change kind "toolbehaviour"), since the contract is unchanged.

  Stored on envState.toolEnv = { [toolName]: { tier, faults, confirmed } };
  faults = { timeoutPct, errorPct, rateLimitPct, latencyMs, malformedPct }.
  A tool not listed takes its default.
*/

/*
  `word` / `label` are the PRD's names (simulated against the world, recorded,
  fixed, live) — what screens show. `short` stays the stored vocabulary: it
  is what a run's pinned tool summary ("5 stateful · 3 replay") was written
  in, so changing it would make every older run read as a different dial.
*/
export const TIERS = [
  { id: "stub", label: "Fixed", word: "Fixed", short: "Stub", note: "A fixed response. Cheapest and fully deterministic." },
  { id: "replay", label: "Recorded", word: "Recorded", short: "Replay", note: "A real response recorded once and played back. Deterministic, but can go stale." },
  { id: "stateful", label: "Simulated against the world", word: "Simulated", short: "Stateful", note: "Answered by the world: reads and writes its seeded data, consistent across every call in a run." },
  { id: "live", label: "Live", word: "Live", short: "Live", note: "The real tool. Not deterministic. Only for read-only tools, and only once you allow it for that tool." },
];
export const tierOf = (id) => TIERS.find((t) => t.id === id) || TIERS[2];

export const FAULT_PRESETS = {
  none: { label: "Clean", faults: null },
  realistic: { label: "Realistic", faults: { timeoutPct: 5, errorPct: 3, rateLimitPct: 0, latencyMs: 400, malformedPct: 0 } },
  adversarial: { label: "Adversarial", faults: { timeoutPct: 20, errorPct: 10, rateLimitPct: 10, latencyMs: 1500, malformedPct: 5, permissionPct: 5, duplicatePct: 5, afterCommitPct: 5 } },
};

const WRITES = /^(book|create|reschedule|update|cancel|delete|send|post|upsert|issue|generate|charge|refund|transfer|escalate|submit)/i;
const READS = /^(get|read|check|list|search|fetch|verify|lookup|find|shipment|status)/i;
const REAL = /^(shell|bash|exec|run_|file_|read_file|write_file|browser|navigate|click|type_|screenshot)/i;
const FRESH = /(price|rate|weather|news|web|fetch_url|stock|availability)/i;

/** read · write · real (needs real execution) · fresh (freshness matters) */
export const sideEffectOf = (tool) => {
  /* An MCP tool says so itself (readOnlyHint) — that wins over its name. */
  if (["read", "write", "fresh", "real"].includes(tool?.effect)) return tool.effect;
  const n = String(tool?.name || "");
  if (REAL.test(n)) return "real";
  if (WRITES.test(n)) return "write";
  if (FRESH.test(n)) return "fresh";
  if (READS.test(n)) return "read";
  return "write";
};

/* Story 5 Step 1: read-only → replay; mutating → stateful; real execution → live. */
const DEFAULT_TIER = { read: "replay", fresh: "replay", write: "stateful", real: "live" };

/*
  Before the dial, a tool could only be marked read-only or writing
  (envState.toolResolutions, "read"/"reads" or "write"/"writes"). A saved
  mark still counts as a choice: writes → stateful, reads → replay.
*/
const LEGACY_TIER = { write: "stateful", writes: "stateful", read: "replay", reads: "replay" };
export const legacyTierOf = (envState, name) => LEGACY_TIER[envState?.toolResolutions?.[name]] || null;

/** One tool's settings, defaults filled in. */
/**
 * Where a tool from an MCP server goes when it's Live: its server, or the
 * Gateway when the server is registered there. It's the target already, so
 * Live never asks for one. Null for any other tool.
 */
export const mcpLiveTargetOf = (tool) => (tool?.via?.kind === "mcp"
  ? (tool.via.gateway ? `Gateway · ${tool.via.label}` : tool.via.host || tool.via.label)
  : null);

export const toolSettingOf = (tool, envState) => {
  const saved = envState?.toolEnv?.[tool.name] || {};
  const effect = sideEffectOf(tool);
  const legacy = saved.tier ? null : legacyTierOf(envState, tool.name);
  /* Nothing is recorded for a tool the world met on an MCP server: it starts Simulated. */
  const defaultTier = tool?.via?.kind === "mcp" && effect !== "real" ? "stateful" : DEFAULT_TIER[effect];
  return {
    name: tool.name,
    effect,
    tier: saved.tier || legacy || defaultTier,
    defaultTier,
    liveTarget: mcpLiveTargetOf(tool),
    faults: saved.faults || null,
    confirmed: !!saved.confirmed || !!legacy,
    safeTarget: saved.safeTarget || null,
    /* Live is allowed per tool, by hand. A read-only tool already on Live
       from before the rule counts as allowed — a saved dial keeps working. */
    liveAllowed: !!saved.liveAllowed || (saved.tier === "live" && (effect === "read" || effect === "fresh")),
    /* From a Gateway server: Live goes through the Gateway, so what it says now decides. */
    viaGateway: !!tool.via?.gateway,
    gatewayBlock: (() => { const g = gatewayToolStatusOf(tool); return g && g.kind !== "deprecated" ? g : null; })(),
  };
};

/* ── live, phase 2: read-only tools, allowed per tool ────────────────────── */

const READ_ONLY = new Set(["read", "fresh"]);

/**
 * Whether a tool may run Live, and why not. Tools that need real execution
 * (shell, browser, files) run live inside the hosted sandbox — that is their
 * default. A read-only tool may go Live once someone allows it for that
 * tool. A tool that writes never runs Live: it stays simulated against the
 * world, recorded or fixed.
 */
export const liveAllowedOf = (setting) => {
  if (setting?.gatewayBlock) return { allowed: false, needsAllow: false, reason: setting.gatewayBlock.tip };
  if (setting?.effect === "real") return { allowed: true, needsAllow: false, reason: null };
  if (READ_ONLY.has(setting?.effect)) {
    return setting.liveAllowed
      ? { allowed: true, needsAllow: false, reason: null }
      : { allowed: false, needsAllow: true, reason: `Allow live calls for ${setting.name} first — it only reads, so it may call the real tool once you allow it.${setting.viaGateway ? " Live calls go through your Gateway, with its rate limits and checks." : ""}` };
  }
  return { allowed: false, needsAllow: false, reason: `${setting?.name || "This tool"} writes data, so it can't run Live — it stays simulated against the world, recorded or fixed.` };
};

/**
 * A saved dial the rule no longer allows — a write tool already on Live.
 * Shown as advice; the dial is left as it is, so runs that pinned it replay
 * unchanged.
 */
export const liveAdvisoryOf = (setting) => {
  if (setting?.tier !== "live") return null;
  /* The Gateway blocked it, dropped it, or can't reach it since it went Live. */
  if (setting.gatewayBlock) {
    return { label: `Live, but ${setting.gatewayBlock.label.toLowerCase()} — switch it`, note: `${setting.gatewayBlock.tip} Switch it to ${tierOf(setting.defaultTier).word} so the next run doesn't call it.`, to: setting.defaultTier };
  }
  return setting.effect === "write"
    ? { label: "Live (not allowed for write tools) — switch it", note: `${setting.name} writes data. Live isn't offered for write tools any more; switch it to ${tierOf(setting.defaultTier).word} so the next run doesn't reach a real system.`, to: setting.defaultTier }
    : null;
};

/** Allow (or stop allowing) Live for one read-only tool — a permission, not a world edit. Taking it back moves a Live tool to its default. */
export const allowLivePatch = (envState, name, on, tool = null) => {
  const all = envState?.toolEnv || {};
  const cur = all[name] || {};
  if (!on && cur.tier === "live") {
    const setting = toolSettingOf(tool || { name }, envState);
    return toolEnvPatch(envState, name, { liveAllowed: false, tier: setting.defaultTier, safeTarget: null }, `${name} set to ${tierOf(setting.defaultTier).word}`);
  }
  return { toolEnv: { ...all, [name]: { ...cur, liveAllowed: !!on } } };
};

/** Every tool in the environment, with its settings. */
export const toolEnvOf = (env, envState) => (env?.tools || []).map((t) => toolSettingOf(t, envState));

/** "8 stateful · 3 replay · 1 live · 2 with faults" — what a run will pin. */
export const toolEnvSummary = (env, envState) => {
  const rows = toolEnvOf(env, envState);
  const counts = TIERS.map((t) => [t.short.toLowerCase(), rows.filter((r) => r.tier === t.id).length]).filter(([, n]) => n);
  const faulty = rows.filter((r) => r.faults).length;
  return [...counts.map(([k, n]) => `${n} ${k}`), faulty ? `${faulty} with faults` : null].filter(Boolean).join(" · ");
};

/** The same counts in the words screens use — "5 simulated · 3 recorded". Display only; runs pin `toolEnvSummary`. */
export const toolEnvSummaryWords = (env, envState) => {
  const rows = toolEnvOf(env, envState);
  const counts = TIERS.map((t) => [t.word.toLowerCase(), rows.filter((r) => r.tier === t.id).length]).filter(([, n]) => n);
  const faulty = rows.filter((r) => r.faults).length;
  return [...counts.map(([k, n]) => `${n} ${k}`), faulty ? `${faulty} with faults` : null].filter(Boolean).join(" · ");
};

/** A short fingerprint a run records, so a comparison can tell the dial moved. */
export const toolEnvPin = (env, envState) => {
  const rows = toolEnvOf(env, envState);
  const s = rows.map((r) => `${r.name}:${r.tier}:${r.faults ? Object.values(r.faults).join("/") : "-"}`).join("|");
  let h = 0;
  for (let i = 0; i < s.length; i += 1) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  return `t${(h % 9000) + 1000}`;
};

/* ── additions: presets, live safety, run inputs, edits ──────────────────── */

/* Roadmap actors.md "Failure profiles": permission denied, missing record,
   timeout, rate limit, malformed response, duplicate event, stale read and
   partial failure — and a timeout before commit apart from one after it,
   because only the second leaves a write the retry has to reuse. */
export const FAULT_FIELDS = [
  { key: "timeoutPct", label: "Timeout before commit", unit: "%", max: 100 },
  { key: "afterCommitPct", label: "Timeout after commit", unit: "%", max: 100 },
  { key: "errorPct", label: "5xx error", unit: "%", max: 100 },
  { key: "rateLimitPct", label: "Rate-limited", unit: "%", max: 100 },
  { key: "permissionPct", label: "Permission denied", unit: "%", max: 100 },
  { key: "missingPct", label: "Missing record", unit: "%", max: 100 },
  { key: "duplicatePct", label: "Duplicate delivery", unit: "%", max: 100 },
  { key: "stalePct", label: "Stale read", unit: "%", max: 100 },
  { key: "partialPct", label: "Partial failure", unit: "%", max: 100 },
  { key: "malformedPct", label: "Malformed", unit: "%", max: 100 },
  { key: "latencyMs", label: "Extra latency", unit: "ms", max: 10000 },
];

const sameFaults = (a, b) => FAULT_FIELDS.every((f) => Number(a?.[f.key] || 0) === Number(b?.[f.key] || 0));

/** Which preset a fault profile is — "none", "realistic", "adversarial" or "custom". */
export const faultPresetOf = (faults) => {
  if (!faults || FAULT_FIELDS.every((f) => !Number(faults[f.key]))) return "none";
  return Object.keys(FAULT_PRESETS).find((k) => FAULT_PRESETS[k].faults && sameFaults(FAULT_PRESETS[k].faults, faults)) || "custom";
};

/** "5% timeout · 3% 5xx · +400ms" — what a fault profile does, in one line. */
export const faultLine = (faults) => {
  if (faultPresetOf(faults) === "none") return "No faults";
  return [
    ...FAULT_FIELDS.filter((f) => f.unit === "%" && Number(faults[f.key])).map((f) => `${faults[f.key]}% ${f.label.toLowerCase()}`),
    faults.latencyMs ? `+${faults.latencyMs}ms` : null,
  ].filter(Boolean).join(" · ");
};

/**
 * Live is the real tool. Where Live is the default (shell, browser, file
 * tools) it runs in the hosted sandbox — that is its target. Anywhere else,
 * choosing Live points the run at a real API, so a run waits until someone
 * names a safe target for it or takes the tier back down (AC-5.10, AC-1.8).
 */
export const needsSafeTarget = (setting) => setting?.tier === "live" && setting.defaultTier !== "live" && !setting.safeTarget && !setting.liveTarget;

/**
 * The fields to save when a tool goes Live: permission given, and an MCP
 * tool's target recorded with it — so readers that only have the tool's name
 * (a run's preflight) know where it goes.
 */
export const liveFieldsOf = (tool) => ({ tier: "live", liveAllowed: true, ...(mcpLiveTargetOf(tool) && { safeTarget: mcpLiveTargetOf(tool) }) });

/** The tools a run cannot start with — Live with no safe target. */
export const unsafeLiveTools = (env, envState) => toolEnvOf(env, envState).filter(needsSafeTarget);

/** Same, from saved settings alone — readable where only envState is at hand. */
export const unsafeLiveToolNames = (envState) => Object.entries(envState?.toolEnv || {})
  .filter(([name]) => needsSafeTarget(toolSettingOf({ name }, envState)))
  .map(([name]) => name);

/** { toolName: faults } for every tool carrying a fault profile — what a run draws faults from. Null when none. */
export const toolFaultsOf = (env, envState) => {
  const rows = toolEnvOf(env, envState).filter((r) => faultPresetOf(r.faults) !== "none");
  return rows.length ? Object.fromEntries(rows.map((r) => [r.name, r.faults])) : null;
};

/**
 * The patch for one tool's change. A tier or fault change is a world edit
 * (minor version, kind "toolbehaviour"); the detail names the tool so the
 * pending cost can count the scenarios that call it.
 */
export const toolEnvPatch = (envState, name, change, detail) => {
  const all = envState?.toolEnv || {};
  return {
    toolEnv: { ...all, [name]: { ...(all[name] || {}), ...change, confirmed: true } },
    ...envChangePatch(envState, "toolbehaviour", detail),
  };
};

/** One preset across every tool — one pending change, not one per tool. */
export const allToolsFaultPatch = (env, envState, presetId) => {
  const faults = FAULT_PRESETS[presetId]?.faults || null;
  const all = { ...(envState?.toolEnv || {}) };
  (env?.tools || []).forEach((t) => { all[t.name] = { ...(all[t.name] || {}), faults }; });
  return {
    toolEnv: all,
    ...envChangePatch(envState, "toolbehaviour", `${FAULT_PRESETS[presetId]?.label || "Clean"} faults on every tool`),
  };
};

/** A Live tool's safe target, confirmed. */
export const safeTargetPatch = (envState, tool, url) =>
  toolEnvPatch(envState, tool, { safeTarget: url }, `${tool} live target set to ${url}`);

/** A Live tool taken back to its default tier. */
export const downgradeTierPatch = (envState, tool, tier) =>
  toolEnvPatch(envState, tool, { tier, safeTarget: null }, `${tool} set to ${tierOf(tier).word}`);
