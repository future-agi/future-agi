/*
  Seed 19 (RSI PRD exit criteria): Error Feed issues that each reach a
  reviewed proposal with nothing linked by hand.

    ISS-4536 → Airline rebooking agent (repo) — a code change, Ready for
               review, its draft PR one click away
    ISS-4402 → Customer Support Line (Vapi squad) — a prompt change shipped
               as a Vapi copy, released, and seen again: Reopened, so Fix
               again is the next step
    ISS-4366 → Customer Support Line — released over 30 days ago and not
               seen since: Resolved
    ISS-4431 → Customer Support Line — released a few days ago: Watching
    ISS-4529 → Guest booking line (Retell, live numbers pinned) — shipped as
               a Retell draft version
    ISS-4478 → Customer Support Line — No safe improvement, and says why

  The three Customer Support Line releases are numbered and dated in the
  same order (Resolved's first), so its version history reads true.

  Each record is written exactly as Start fix writes one (issueFixRecord),
  with the seeded `compatCheck: 0` and a fixed search seed, so it replays
  identically. Applied to a first-run state and, once, to a stored copy —
  never over an issue someone already ran a fix for, never twice.
*/
import { getEnvironment } from "./environments";
import { DEFAULT_LIMITS, OPT_STATUS, improvementOutcome } from "./optimizationRuns";
import { budgetsWithin } from "./improvementStart";
import { envAgentKey } from "./agentRegistry";
import { agentPinPatch, agentVersions, currentAgentVersion } from "./versions";
import { improvementRunName } from "./optimizer";
import { issueFixRecord, issueScenarioDrafts } from "../errorFeed/issueFixModel";
import { draftVersionRecord, draftVersionOf } from "../agents/versionRelease";
import { releasePatch } from "./releaseFix";

const hoursAgo = (n) => new Date(Date.now() - n * 3600000).toISOString();

/* The issues as the feed held them when Fix was pressed — the fields a fix
   reads. (The feed's own copy is src/api/errorFeed/mockAdapter.js.) */
export const SEEDED_FIX_ISSUES = {
  "ISS-4536": {
    cluster_id: "ISS-4536", project_id: "airline-rebooking", severity: "critical", evidence_state: "confirmed", trace_count: 47, fix_layer: "tools",
    error: { name: "Rebooking summary leaves out a passenger after a segment is cancelled", type: "ToolResultDropped" },
    why_shown: "47 chats where lookup_booking returned every passenger but the summary total named only some; matched successful rebookings list them all.",
  },
  "ISS-4402": {
    cluster_id: "ISS-4402", project_id: "customer-support-line-agent", severity: "high", evidence_state: "confirmed", trace_count: 84, fix_layer: "prompt",
    error: { name: "Cancellation flow proceeds without reading policy exceptions", type: "PolicyBypassError" },
    why_shown: "84 calls where the agent confirmed a cancellation straight away without reading the policy exceptions — notice periods, kept benefits, price-match promises — and in most no cancel_order call followed. Matched cancellations check the exceptions before calling cancel_order.",
  },
  "ISS-4366": {
    cluster_id: "ISS-4366", project_id: "customer-support-line-agent", severity: "high", evidence_state: "confirmed", trace_count: 26, fix_layer: "prompt",
    error: { name: "Refund issued without checking return eligibility", type: "SkippedVerification" },
    why_shown: "26 calls where issue_refund ran with no check_return_eligibility call before it; 9 of those orders were outside the return window. Matched refunds check eligibility first.",
  },
  "ISS-4431": {
    cluster_id: "ISS-4431", project_id: "customer-support-line-agent", severity: "high", evidence_state: "confirmed", trace_count: 33, fix_layer: "prompt",
    error: { name: "New delivery address confirmed after the parcel was dispatched", type: "UnsupportedClaim" },
    why_shown: "33 calls where the agent confirmed a new delivery address although shipment_status already showed the parcel dispatched; update_shipping_address failed and the caller was never told. Matched calls check shipment_status first.",
  },
  "ISS-4529": {
    cluster_id: "ISS-4529", project_id: "guest-booking-line", severity: "high", evidence_state: "confirmed", trace_count: 92, fix_layer: "orchestration",
    error: { name: "Corrected booking time is not passed to the booking tool", type: "ToolArgumentMismatch" },
    why_shown: "64 calls where the guest corrected the time and modify_booking still received the first one; matched successful bookings do not show the pattern.",
  },
  "ISS-4520": {
    cluster_id: "ISS-4520", project_id: "returns-refunds-line-agent", severity: "high", evidence_state: "unresolved", trace_count: 9, fix_layer: "prompt",
    error: { name: "Agent restates dollar figure with a different value in same turn", type: "SelfContradiction" },
    why_shown: "9 traces where the TTS transcript and tool arguments disagree on the dollar amount in the same turn. Mechanism unclear — insufficient evidence to confirm.",
  },
  "ISS-4478": {
    cluster_id: "ISS-4478", project_id: "customer-support-line-agent", severity: "high", evidence_state: "emerging", trace_count: 31, fix_layer: "data",
    error: { name: "Order-status responses cite stale inventory after warehouse switch", type: "StaleCacheReadError" },
    why_shown: "31 occurrences where the cited inventory count contradicts the fresh warehouse read that arrives seconds later.",
  },
};

/*
  Which fix, where, and how it shipped. `seed` is the search's seed — fixed,
  so the record is the same on every load; `expect` is the ending it was
  chosen for, checked when the seed is built. `releasedAfter` (hours after
  it finished) is when a person marked the shipped draft released — the
  Error Feed's watch runs from then. `at` is hours ago.
*/
export const SEEDED_FIXES = [
  { as: "ef-code", issueId: "ISS-4536", envId: "env-chat-travel", seed: "ef-ISS-4536-20-11", expect: "ready", at: 20, minutes: 52 },
  { as: "ef-resolved", issueId: "ISS-4366", envId: "env-voice-support", seed: "ef-ISS-4366", expect: "ready", at: 34 * 24, minutes: 46, ship: { where: "Vapi", kind: "copy" }, releasedAfter: 20 },
  { as: "ef-watching", issueId: "ISS-4431", envId: "env-voice-support", seed: "ef-ISS-4431", expect: "ready", at: 4 * 24 + 6, minutes: 41, ship: { where: "Vapi", kind: "copy" }, releasedAfter: 22 },
  { as: "ef-prompt", issueId: "ISS-4402", envId: "env-voice-support", seed: "ef-ISS-4402-19-6", expect: "ready", at: 30, minutes: 47, also: ["env-voice-after-hours"], ship: { where: "Vapi", kind: "copy" }, releasedAfter: 1 },
  { as: "ef-retell", issueId: "ISS-4529", envId: "env-guest-booking", seed: "ef-ISS-4529-20", expect: "ready", at: 26, minutes: 44, ship: { where: "Retell", kind: "draft" } },
  { as: "ef-no-safe", issueId: "ISS-4478", envId: "env-voice-support", seed: "ef-ISS-4478-19", expect: "no-fix", at: 14, minutes: 51 },
];

const nextId = (records) => `OPT-${String(records.reduce((m, o) => Math.max(m, Number(String(o.id).replace(/\D/g, "")) || 0), 0) + 1).padStart(5, "0")}`;

/* The Retell agent's live numbers are pinned to a released version — a
   Retell draft would otherwise take real calls on the newest version. */
export const SEEDED_RETELL_VALUES = (at) => ({
  retellNumbersPinned: { at, version: "v4" },
  observability: { on: true, since: at, project: "guest-booking-line" },
  liveTestConsent: { at, replayFlag: false },
});

/* The Airline agent reports into its own Observe project — where
   ISS-4536 came from. Set after every seeded improvement is built, so none
   of them changes. */
export const withAirlineTracing = (byEnv, at) => {
  const es = byEnv["env-chat-travel"];
  const a = es?.agent;
  if (!a || a.definition?.repoUrl !== "https://github.com/skyline-air/rebooking-agent" || a.values?.observability) return byEnv;
  return { ...byEnv, "env-chat-travel": { ...es, agent: { ...a, values: { ...(a.values || {}), observability: { on: true, since: at, project: "airline-rebooking" } } } } };
};

/**
 * One seeded fix on its environment: the record, and — for one that shipped
 * a draft — the draft and the Draft agent version it recorded.
 */
export const seededFixFor = (cfg, env, es, agentLimits = {}) => {
  const issue = SEEDED_FIX_ISSUES[cfg.issueId];
  if (!issue || !env || !es?.agent) return null;
  const key = envAgentKey(es.agent, env.id);
  const limits = agentLimits[key] || DEFAULT_LIMITS;
  const budgets = budgetsWithin(limits.budget.dollars);
  const drafts = issueScenarioDrafts(issue, env, es);
  const built = issueFixRecord({
    issue, env, es, drafts, limits, budget: budgets[budgets.length - 1],
    regressionEnvIds: [env.id, ...(cfg.also || [])], seed: cfg.seed,
    /* Recorded the way the other seeded improvements are — kept exactly as seeded. */
    compatCheck: 0,
  });
  const at = hoursAgo(cfg.at);
  const record = {
    ...built,
    id: nextId(es.optimizations || []),
    name: `Epoch ${(es.optimizations || []).length + 1}`,
    status: built.status === OPT_STATUS.RUNNING ? OPT_STATUS.COMPLETED : built.status,
    createdAt: at,
    completedAt: new Date(Date.parse(at) + cfg.minutes * 60000).toISOString(),
    seededAs: cfg.as,
  };
  const ready = improvementOutcome(record).ending.key === "ready";
  if (!cfg.ship || !ready) return { record, draft: null, version: null };
  const draft = { where: cfg.ship.where, kind: cfg.ship.kind, at: record.completedAt };
  const winner = record.result?.winner;
  const version = draftVersionRecord(es, {
    optId: record.id, envId: env.id, source: "platform-draft", name: improvementRunName({ optimizations: [...(es.optimizations || []), record] }, record),
    issueId: cfg.issueId, where: `${cfg.ship.where} ${cfg.ship.kind}`, applied: winner?.proposals || [],
    fromRunId: record.fromRunId, basedOnVersion: record.baseAgentVersion, at: record.completedAt,
  });
  return { record, draft, version };
};

/* A Draft version belongs to the agent: every environment testing it learns
   it exists, and none of them switches to it. */
const withDraftVersion = (byEnv, key, version) => {
  Object.keys(byEnv).forEach((id) => {
    const es = byEnv[id];
    if (!es?.agent || envAgentKey(es.agent, id) !== key) return;
    const list = agentVersions(es);
    if (list.some((v) => v.label === version.label)) return;
    byEnv[id] = { ...es, agentVersions: [...list, version], activeAgentVersion: es.activeAgentVersion || currentAgentVersion(es)?.label };
  });
};

/**
 * The seeded fixes added to a state's environments. `envs` finds an
 * environment by id (templates or the state's own). Skips any fix whose
 * environment is gone, whose agent changed, or whose issue already has a
 * fix there.
 */
export const withErrorFeedFixes = (state, envs) => {
  if (!state?.byEnv) return state;
  const byEnv = { ...state.byEnv };
  const added = [];
  SEEDED_FIXES.forEach((cfg) => {
    const es = byEnv[cfg.envId];
    const env = getEnvironment(cfg.envId) || (envs || []).find((e) => e.id === cfg.envId);
    if (!es || !env) return;
    const records = es.optimizations || [];
    if (records.some((o) => o.seededAs === cfg.as || o.trigger?.issueId === cfg.issueId)) return;
    try {
      const seeded = seededFixFor(cfg, env, es, state.agentLimits);
      if (!seeded) return;
      byEnv[cfg.envId] = {
        ...es,
        optimizations: [...records, seeded.record],
        ...(seeded.draft && { handoffDrafts: { ...(es.handoffDrafts || {}), [seeded.record.id]: seeded.draft } }),
      };
      if (seeded.version) withDraftVersion(byEnv, envAgentKey(es.agent, cfg.envId), seeded.version);
      added.push(cfg);
    } catch (err) {
      // eslint-disable-next-line no-console
      console.warn("[store] seeded fix skipped", cfg.issueId, err);
    }
  });
  /* Released only after every fix is built, so each was searched against
     the production version it really started from. */
  added.filter((cfg) => cfg.releasedAfter != null).forEach((cfg) => withSeededRelease(byEnv, cfg));
  return { ...state, byEnv };
};

/*
  A person marked a seeded fix's draft released: it lands as its Draft
  version, the release is recorded (with the confirmation run of its
  targets, as Mark as released records it), and the version is released
  for the agent everywhere it is tested — production from then on.
*/
const withSeededRelease = (byEnv, cfg) => {
  const es = byEnv[cfg.envId];
  const record = (es?.optimizations || []).find((o) => o.seededAs === cfg.as);
  const version = record && draftVersionOf(es, record.id);
  if (!record || !version || es.handoffLandings?.[record.id]) return;
  const at = new Date(Date.parse(record.completedAt) + cfg.releasedAfter * 3600000).toISOString();
  const { patch } = releasePatch(es, { version: version.label, optId: record.id, at });
  byEnv[cfg.envId] = {
    ...es,
    ...patch,
    releases: [...patch.releases].sort((a, b) => (Date.parse(b.at) || 0) - (Date.parse(a.at) || 0)),
    handoffLandings: { ...(es.handoffLandings || {}), [record.id]: version.label },
  };
  const key = envAgentKey(es.agent, cfg.envId);
  Object.keys(byEnv).forEach((id) => {
    const other = byEnv[id];
    if (!other?.agent || envAgentKey(other.agent, id) !== key) return;
    const next = {
      ...other,
      agentVersions: agentVersions(other).map((v) => (v.label === version.label ? { ...v, status: "released", landedAt: at, releasedAt: at } : v)),
    };
    byEnv[id] = next.agentVersionPinned ? next : { ...next, ...agentPinPatch(next, null) };
  });
};

/*
  Seed 28: the Error Feed's watch shown end to end on Customer Support Line —
  Resolved, Watching and Reopened. A stored ISS-4402 fix nobody has touched
  (not landed, released or followed by another fix) goes, with its draft and
  Draft version, so it comes back after the two older releases and the
  versions stay in date order. Anything someone did themselves stays.
*/
export const withWatchedFixes = (state, envs) => {
  if (!state?.byEnv) return state;
  const byEnv = { ...state.byEnv };
  const es = byEnv["env-voice-support"];
  const records = es?.optimizations || [];
  const old = records.find((o) => o.seededAs === "ef-prompt");
  const untouched = old && !es.handoffLandings?.[old.id] && !es.handoffCanary?.[old.id]
    && !(es.releases || []).some((r) => r.optId === old.id)
    && !records.some((o) => o.id !== old.id && o.trigger?.issueId === old.trigger?.issueId)
    && !records.some((o) => o.seededAs === "ef-resolved" || o.seededAs === "ef-watching");
  if (untouched) {
    const { [old.id]: _drop, ...drafts } = es.handoffDrafts || {};
    byEnv["env-voice-support"] = { ...es, optimizations: records.filter((o) => o.id !== old.id), handoffDrafts: drafts };
    Object.keys(byEnv).forEach((id) => {
      const list = byEnv[id]?.agentVersions;
      if (!list?.some((v) => v.status === "draft" && v.draftOf?.optId === old.id && v.draftOf?.envId === "env-voice-support")) return;
      byEnv[id] = { ...byEnv[id], agentVersions: list.filter((v) => !(v.status === "draft" && v.draftOf?.optId === old.id && v.draftOf?.envId === "env-voice-support")) };
    });
  }
  return withErrorFeedFixes({ ...state, byEnv }, envs);
};
