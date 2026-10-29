/*
  Error Feed → Fix, without React or the store: which agent an issue came
  from, the scenarios its traces make, what reproducing finds, the improvement
  record a Fix writes and where that fix stands. `issueFix.js` re-exports all
  of it next to the hook; the seed reads it from here so it can build its
  Error Feed fixes the same way a press of Start fix does.
*/
import {
  PER_CONVERSATION,
  REPEATS,
  improvementOutcome,
  improvementPhaseOf,
} from "../_mock/optimizationRuns";
import {
  baseRunOf,
  buildImprovement,
  improvementEstimate,
  planSearch,
  COMPAT_CHECK,
} from "../_mock/improvementStart";
import { improvementRunName } from "../_mock/optimizer";
import { currentAgentVersion, productionAgentVersion } from "../_mock/versions";
import { measureTodayRun, measuredSummaryOf } from "../_mock/measureToday";
import { runPlan } from "../_mock/proofs";
import { needsRecheck } from "../improvements/behindProduction";
import { isComingSoon, needsLiveConsent } from "../_mock/rsiModel";
import { agentStatusFor, mainEnvFor } from "../_mock/improvementPolicy";
import {
  connectedAgentsFrom,
  envAgentKey,
  needsFoundCheck,
  needsToolSafety,
} from "../_mock/agentRegistry";
import { errorFeedScenarios } from "../_mock/scenarioSources";
import { runsSealed } from "../_mock/setupGaps";
import { projectLinkOf, projectsOfAgent } from "../_mock/projectLinks";
import { prOfImprovement } from "../_mock/improvementPr";
import { coderStatusOf } from "../_mock/improverAttempts";
import { mockCluster } from "src/api/errorFeed/mockAdapter";
import { paths } from "src/routes/paths";

/* The feed finds an issue's agent through its Observe project — the one
   link in _mock/projectLinks.js. */
/* Issues whose traces carry the agent's own id — closer evidence than the
   project they were filed under, so they go to that agent whatever the
   project is linked to. None today; each seeded project names its agent. */
const SEEDED_ISSUE_AGENTS = {};

/* The agent version an issue's traces were recorded on, where the feed has
   it — what "running in production" means when no release is recorded. */
const ISSUE_TRACE_VERSIONS = {
  "ISS-4536": "v2",
  "ISS-4529": "v1",
};

/* What each linked issue's traces look like as scenarios — the
   representative failure first, then the variations seen around it. */
const ISSUE_CASES = {
  "ISS-4501": [
    "Caller asks for the refund status straight after giving the first identity factor",
    "Caller reads the one-time code slowly; the agent starts confirming before it lands",
    "Caller interrupts the confirmation to repeat the code",
    "The identity check times out once and the caller tries again",
    "Caller asks to cancel the refund before verification completes",
  ],
  "ISS-4520": [
    "Caller asks for the refund total, then for the breakdown",
    "Partial refund with a restocking fee taken off",
    "Caller questions the amount and asks the agent to say it again",
    "Refund split across two payment methods",
  ],
  "ISS-4478": [
    "Order moved to another warehouse after it was placed",
    "Caller asks when a back-ordered item will ship",
    "Caller checks the same order twice in one call",
    "Item shows in stock, then sells out during the call",
  ],
  "ISS-4402": [
    "Caller cancels an order that falls under a policy exception",
    "Caller cancels a subscription inside the notice period",
    "Caller asks to cancel and keep a partial benefit",
    "Caller cancels after a price-match promise",
  ],
  "ISS-4366": [
    "Caller asks for a refund on an order outside the return window",
    "Caller asks for a refund on an item they say they wore once",
    "Caller asks for a refund on a discontinued product",
    "Caller asks for a refund and a return label in one go",
  ],
  "ISS-4431": [
    "Caller changes the address after the parcel left the warehouse",
    "Caller changes the address on the day of dispatch",
    "Caller asks to redirect a parcel already with the carrier",
    "Caller corrects one line of the address after dispatch",
  ],
  "ISS-4536": [
    "Passenger asks for a rebooking summary after one leg of the trip was cancelled",
    "Two passengers on one booking, one with no checked bags",
    "Passenger asks for the total owed after a fare difference on one segment only",
    "Summary requested again after a segment is removed mid-chat",
  ],
  "ISS-4529": [
    "Guest corrects the booking time after first giving the wrong one",
    "Guest changes the party size while the agent reads the booking back",
    "Guest spells their name a second time to fix it",
    "Guest moves an existing booking and corrects the new date",
  ],
  "ISS-4552": [
    "Guest asks for a table for twelve on a Saturday night",
    "Guest starts with a party of eight, then adds three more",
    "Guest asks to book nine and insists it's only a small group",
    "Guest books for ten under a company name",
  ],
  "ISS-4557": [
    "Caller gives only their name and asks to move an appointment",
    "Two patients share a surname; the caller gives the first name late",
    "Caller gives the wrong date of birth, then corrects it",
    "Caller cancels on behalf of a family member",
  ],
  "ISS-4563": [
    "Caller asks for the loyalty discount without saying their tier",
    "Caller insists they are gold tier when the account says silver",
    "Caller asks for a member code on someone else's order",
    "Caller asks for the discount twice in one call",
  ],
  "ISS-4566": [
    "Caller asks for a return label and label creation fails",
    "Caller says the last label never arrived and asks again",
    "Caller asks for a label for an order outside the return window",
    "Caller asks for the label by text instead of email",
  ],
  "ISS-4571": [
    "Guest asks for the deposit on a group of fourteen in spring",
    "Guest asks for a private-room deposit after prices changed",
    "Guest compares this year's deposit with last year's",
    "Guest changes the party size after hearing the deposit",
  ],
  "ISS-4574": [
    "Traveller asks to be rebooked onto a partner flight with an aisle seat",
    "Traveller keeps a window seat request after a cancellation",
    "Traveller asks for any flight tonight but needs an aisle",
    "Traveller with a paid seat is moved to a partner airline",
  ],
  "ISS-4578": [
    "Caller asks for a slot the calendar shows as taken",
    "Caller asks for the earliest slot when the first one is gone",
    "Caller books for a family member in a held slot",
    "Caller reschedules into a slot freed a minute ago",
  ],
  "ISS-4582": [
    "Caller asks for a refund on a broken item",
    "Caller refuses store credit and asks for the money back",
    "Caller asks for a refund on a final-sale item",
    "Caller asks which refund options they have",
  ],
  "ISS-4585": [
    "Customer asks why a late fee was charged",
    "Customer asks for the invoice with every charge included",
    "Customer asks for the total after being told about the fee",
    "Customer disputes the late fee and asks for the total without it",
  ],
  "ISS-4593": [
    "Traveller asks to move to a later, pricier flight",
    "Traveller asks for any flight before noon and accepts the first",
    "Traveller asks whether switching costs anything",
    "Traveller accepts a rebooking, then asks for the receipt",
  ],
  "ISS-4601": [
    "Guest books a table and hangs up straight after naming the time",
    "Guest gives the party size before the date",
    "Guest changes the day mid-booking",
    "Guest books for someone else and gives their name",
  ],
  "ISS-4602": [
    "Caller asks for a repeat prescription and gives only a name",
    "Caller books a pickup for a family member",
    "Caller can't remember their date of birth format",
    "Caller asks to collect today, before the check",
  ],
  "ISS-4603": [
    "Traveller rebooked onto a basic fare asks about bags",
    "Traveller asks if the old bag allowance carries over",
    "Traveller adds a bag after the rebooking",
    "Traveller on a partner flight asks about bags",
    "Traveller rebooked onto a higher fare asks about bags",
  ],
  "ISS-4604": [
    "Customer says the original card is cancelled and asks where the refund goes",
    "Customer asks for the refund on a different card",
    "Customer mentions the card was stolen and replaced",
    "Customer asks for store credit instead",
    "Customer's bank closed the account the purchase came from",
    "Customer asks to split the refund across two cards",
    "Customer says the card expired since the purchase",
    "Customer asks for a bank transfer instead of a card refund",
    "Customer gives the new card's last four digits",
    "Customer asks what happens if the refund bounces",
    "Customer paid with a gift card and a cancelled card",
    "Customer calls back after the refund went to the old card",
    "Customer asks for the refund in cash at the store",
    "Customer's partner paid, and that card is cancelled",
    "Customer asks to refund only the shipping to a new card",
    "Customer cancels the card during the call",
    "Customer asks for a refund on a subscription billed to an old card",
    "Customer is unsure whether the card is still active",
    "Customer asks the agent to check which card was used",
    "Customer asks to hold the refund until a new card arrives",
    "Customer says the card was frozen, not cancelled",
    "Customer asks for the refund to a digital wallet",
  ],
  "ISS-4605": [
    "Caller asks what closing an account involves",
    "Caller asks whether closing deletes their history",
    "Caller asks how long closing takes",
    "Caller asks to close, then changes their mind",
  ],
  "ISS-4606": [
    "Caller asks how long until a driver arrives",
    "Caller asks for a car at a busy time",
    "Caller asks for a pickup from a new address",
    "Caller asks to be called when the driver is near",
  ],
  "ISS-4589": [
    "Customer asks what cancelling would cost",
    "Customer asks about the cancellation fee on one order",
    "Customer asks whether a cancellation is refunded in full",
    "Customer asks to cancel, then changes their mind",
  ],
};

/* Which cases are variations of one another — a group always lands on one
   side of the held-back split, so a near-copy can't leak into training. */
const ISSUE_GROUPS = {
  "ISS-4501": [1, 2, 2, 3, 4],
  "ISS-4520": [1, 1, 2, 3],
  "ISS-4478": [1, 2, 1, 3],
  "ISS-4402": [1, 2, 2, 3],
  "ISS-4366": [1, 2, 2, 3],
  "ISS-4431": [1, 2, 1, 3],
  "ISS-4536": [1, 2, 3, 3],
  "ISS-4529": [1, 2, 1, 3],
  "ISS-4552": [1, 2, 1, 3],
  "ISS-4557": [1, 2, 3, 3],
  "ISS-4563": [1, 2, 1, 3],
  "ISS-4566": [1, 1, 2, 3],
  "ISS-4571": [1, 2, 1, 3],
  "ISS-4574": [1, 1, 1, 2],
  "ISS-4578": [1, 2, 3, 2],
  "ISS-4582": [1, 1, 2, 3],
  "ISS-4585": [1, 2, 2, 3],
  "ISS-4589": [1, 1, 2, 3],
  "ISS-4593": [1, 2, 1, 3],
  "ISS-4601": [1, 2, 1, 3],
  "ISS-4602": [1, 2, 1, 3],
  "ISS-4603": [1, 1, 2, 3, 3],
  "ISS-4604": [
    1, 2, 1, 3, 1, 4, 5, 4, 2, 6, 7, 8, 9, 10, 11, 8, 12, 5, 13, 6, 1, 14,
  ],
  "ISS-4605": [1, 2, 3, 4],
  "ISS-4606": [1, 2, 3, 4],
};

const hash = (s) => {
  let h = 2166136261;
  for (let i = 0; i < s.length; i += 1) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
};

/* The workspace's agents, as useConnectedAgents() reads them — kept for the
   last state seen, so a table of issues works them out once. */
let agentsMemo = { state: null, agents: [] };
const agentsOfState = (state) => {
  if (agentsMemo.state !== state)
    agentsMemo = { state, agents: connectedAgentsFrom(state) };
  return agentsMemo.agents;
};

/** The Observe project an issue's traces came from. */
export const issueProjectOf = (issue) => issue?.project_id || null;

/**
 * The agent an issue came from: the agent its traces name, else the agent
 * whose Observe project the issue's traces are in, or the one someone linked
 * that project to. A project link is per project, not per issue, so one
 * answer covers every issue from it.
 */
export const agentKeyForIssue = (issue, state, agents = []) => {
  const traced = SEEDED_ISSUE_AGENTS[issue?.cluster_id];
  if (traced) return traced;
  const link = projectLinkOf(issueProjectOf(issue), state, agents);
  return link.status === "linked" ? link.agentKey : null;
};

/** The issue's project and its agent link — see _mock/projectLinks.js. */
export const issueProjectLinkOf = (issue, state, agents = []) =>
  projectLinkOf(issueProjectOf(issue), state, agents);

/** The Error Feed projects an agent's issues come from — its own and any linked to it. */
/* Read against every agent, not just this one: a project another agent
   watches, or a demo agent reports into, stays that agent's. */
export const errorFeedProjectsOf = (agent, state) =>
  projectsOfAgent(agent, state, agentsOfState(state)).map((l) => l.project);

/** The environments that test an agent, main one first. */
/* A fix is in flight from the moment it starts until it ends unfixed or the
   issue reopens — while it is, the issue opens that fix and never a second. */
export const FIX_IN_FLIGHT = new Set([
  "fixing",
  "ready",
  "review",
  "watching",
  "resolved",
]);

export const envsForAgent = (agent, state) => {
  const list = (agent?.usedIn || [])
    .map((u) => state.myEnvironments.find((e) => e.id === u.envId))
    .filter(Boolean);
  const main = mainEnvFor(agent, state, list);
  return main ? [main, ...list.filter((e) => e.id !== main.id)] : list;
};

/* Whether an environment tests the channel the issue happened on — a voice
   issue needs callers that speak; a chat issue runs anywhere but voice-only. */
const VOICE_SURFACES = ["voice", "multi"];
export const envFitsIssue = (env, issue) => {
  if (!env || !issue?.modality) return true;
  return issue.modality === "voice"
    ? VOICE_SURFACES.includes(env.surface)
    : env.surface !== "voice";
};

/**
 * Where a fix runs unless the person picks another: the agent's main
 * environment when it tests the issue's channel, else the first one that
 * does, else the main one. `envs` is envsForAgent's list (main first).
 */
export const fixEnvFor = (envs = [], issue) =>
  (envFitsIssue(envs[0], issue) ? envs[0] : null) ||
  envs.find((e) => envFitsIssue(e, issue)) ||
  envs[0] ||
  null;

/**
 * Which version of the agent is in production — the one answer the Agents
 * page gives too: the version an admin made active, else the newest that
 * isn't an unreleased draft (synced, pushed or released). A fix improves
 * that version. When the issue's traces were recorded on another version,
 * the line says so.
 *   { label, source: "production" | "traces" | "current", line }
 */
export const productionVersionOf = (agent, state, issue, fallbackEs) => {
  const traced =
    issue?.agent_version || ISSUE_TRACE_VERSIONS[issue?.cluster_id] || null;
  const prod =
    (fallbackEs && productionAgentVersion(fallbackEs)?.label) ||
    agent?.production ||
    null;
  if (prod) {
    const also =
      traced && traced !== prod
        ? ` — this issue's traces were recorded on ${traced}`
        : "";
    return {
      label: prod,
      source: "production",
      line: `${prod} · running in production${also}`,
    };
  }
  if (traced)
    return {
      label: traced,
      source: "traces",
      line: `${traced} · the version this issue's traces were recorded on`,
    };
  const current = fallbackEs
    ? currentAgentVersion(fallbackEs)?.label
    : agent?.latest;
  return {
    label: current || null,
    source: "current",
    line: `${current || "Current version"} · current version`,
  };
};

/**
 * Why Fix can't start, said rather than hidden: the agent is disconnected or
 * archived, its environment is Coming soon (browser, computer use), or it runs
 * live and needs the user's consent first. Null when Fix can start.
 *   { key, title, body, action?: "connections" | "overview" }
 */
export const fixGateOf = (agent, env, es = null) => {
  if (!agent) return null;
  const status = agentStatusFor(agent);
  if (status.key === "archived") {
    return {
      key: "archived",
      title: `${agent.name} is archived`,
      body:
        status.reason ||
        "New runs and improvements are stopped. Restore the agent to fix this issue.",
    };
  }
  if (status.key === "disconnected") {
    return {
      key: "disconnected",
      title: `${agent.name} is disconnected`,
      body:
        status.reason ||
        "Reconnect it on the agent's Connections tab to fix this issue.",
      action: "connections",
    };
  }
  if (isComingSoon(agent) || (env && isComingSoon(env))) {
    return {
      key: "coming-soon",
      title: "Coming soon",
      body: "Browser and computer-use agents are coming soon. Improvements don't run on them yet.",
    };
  }
  /* A newly connected code agent is fixed, like it's run, once what we read
     from its code is confirmed (RSI PRD Flow 1) — the same rule runs follow. */
  if (needsToolSafety(agent) && es && !runsSealed(es)) {
    /* Live, like a live run: the review is where tools that must never run
       for real are marked, so it comes first. */
    return {
      key: "found-live",
      title: "Mark the tools that must never run for real first",
      body: `${agent.name} runs live, so its real tools would be called. Mark the ones that must never run for real, or switch it to sealed runs, then fix.`,
      action: "overview",
    };
  }
  if (needsFoundCheck(agent)) {
    return {
      key: "found",
      title: "Confirm what we found in its code first",
      body: `Optional — check what we read from ${agent.name}'s code on its Overview. Fix works now.`,
      action: "overview",
    };
  }
  if (needsLiveConsent(agent)) {
    return {
      key: "consent",
      title: "Needs your consent for live tests",
      body: `${agent.name} runs live: test copies or drafts are made in your account and its tools reach your real systems. Give consent on the agent's Connections tab first.`,
      action: "connections",
    };
  }
  return null;
};

/**
 * How sure the feed is about an issue — said, never a gate. Fix's first step
 * reproduces the issue, which is the real confirmation: a noisy issue stops
 * at "Could not reproduce" before any search is spent on it. (A deliberate
 * step away from the PRD's "Fix only on confirmed issues", agreed with the
 * user.) Null when the issue is confirmed.
 */
export const evidenceGateOf = (issue) => {
  const e = issue?.evidence_state;
  if (!e || e === "confirmed") return null;
  const why =
    e === "emerging"
      ? `The pattern is still emerging (${issue.trace_count || "a few"} traces so far).`
      : e === "dismissed"
        ? "This issue was dismissed once."
        : "The feed couldn't confirm it from its traces yet.";
  return {
    key: e,
    title: "Not confirmed yet",
    body: `${why} Fix reproduces it first — if it can't, it stops at Could not reproduce before any search is spent.`,
  };
};

/**
 * Scenario drafts built from an issue's traces, for one environment. Each
 * keeps the issue and the traces it came from, so its badge links back. The
 * failing step is a critical sub-goal, and the caller's persona is inferred
 * from how the traces went — the same reading the Error Feed import does.
 */
export const issueScenarioDrafts = (issue, env, _envState) => {
  const id = issue.cluster_id;
  const cases = ISSUE_CASES[id] || [
    `As seen in production: ${issue.error?.name}`,
    "The same request from a caller in a hurry",
    "The caller corrects a detail halfway through",
    "The caller asks the agent to repeat itself",
  ];
  const traces = Math.max(cases.length, issue.trace_count || cases.length);
  /* How many of the issue's traces each situation stands for — the first
     cases are the most common, so they carry more. */
  const weights = cases.map((_, i) => cases.length - i);
  const wsum = weights.reduce((a, b) => a + b, 0);
  const share = weights.map((w) =>
    Math.max(1, Math.round((traces * w) / wsum)),
  );
  const raw = cases.map((title, i) => ({
    id: `from-feed::${id}::${i + 1}`,
    groupId: `from-feed::${id}::g${(ISSUE_GROUPS[id] || [])[i] || i + 1}`,
    useCase: issue.error?.name,
    name: `${id.toLowerCase()}-${i + 1}`,
    title,
    summary: issue.error?.name,
    task: `Reproduces Error Feed issue ${id}: ${issue.error?.name}.`,
    situation: `${title}. Built from ${share[i]} of the issue's ${issue.trace_count || traces} traces.`,
    traceCount: share[i],
    expected: `The agent handles this without repeating the issue: ${issue.error?.name.charAt(0).toLowerCase()}${issue.error?.name.slice(1)} does not happen.`,
    turns: 8,
    critical: issue.severity === "critical",
    errorFeedIssue: {
      id,
      title: issue.error?.name,
      traceIds: [0, 1, 2].map(
        (k) => `tr_${hash(`${id}:${i}:${k}`).toString(16).slice(0, 7)}`,
      ),
    },
    env: env?.id,
  }));
  /* Persona and sub-goals as the Error Feed import reads them — the import
     runs the same reading again, which lands on the same values. */
  const read = errorFeedScenarios(raw, issue);
  return raw.map((d, i) => ({
    ...d,
    persona: read[i]?.persona,
    subGoals: read[i]?.subGoals,
  }));
};

/**
 * Run scenarios only: a run of just the issue's scenarios — no fix. Those
 * not in the environment ride on the run, as a fix's do, so the environment
 * is untouched until someone adds them from the run.
 */
export const issueRunFor = (env, es, issue, drafts, { id, cap } = {}) => {
  const base = fixRunFor(env, es, issue, drafts);
  if (!base) return null;
  return {
    ...base,
    id,
    seed: id,
    scenarioIds: drafts.map((d) => d.id),
    partial: true,
    measuredFor: { kind: "issue", issueId: issue.cluster_id },
    startedBy: `Run scenarios · ${issue.cluster_id}`,
    label: `${issue.cluster_id} · its scenarios`,
    ...(cap && { budget: { cap } }),
  };
};

/* A fix runs at most this many of an issue's scenarios — the most common
   situations first. Every scenario runs on every repeat, candidate and
   finalist, so more costs a lot and makes the search noisier, not better. */
export const FIX_SCENARIO_CAP = 12;

/** The scenarios a fix uses by default, and the rest. Drafts come most common first. */
export const fixScenariosOf = (drafts = []) => {
  const all = [...drafts].sort(
    (a, b) => (b.traceCount || 0) - (a.traceCount || 0),
  );
  return {
    all,
    used: all.slice(0, FIX_SCENARIO_CAP),
    capped: all.length > FIX_SCENARIO_CAP,
  };
};

/** Scenarios grouped by the situation they share: { id, label, scenarios, traces }. */
export const scenarioGroupsOf = (drafts = []) => {
  const byId = new Map();
  drafts.forEach((d) => {
    const g = byId.get(d.groupId) || {
      id: d.groupId,
      label: d.title,
      scenarios: 0,
      traces: 0,
    };
    g.scenarios += 1;
    g.traces += d.traceCount || 0;
    byId.set(d.groupId, g);
  });
  return [...byId.values()].sort((a, b) => b.traces - a.traces);
};

/**
 * Does the failure actually happen on today's agent? Each scenario runs
 * three times. It counts as reproduced when it fails at least twice; once in
 * three is inconclusive — never a pass, never a reproduction, never a
 * target; never is a pass. Any reproduction is enough to search on: the
 * reproduced scenarios become what the fix must fix, the rest join the
 * scenarios it must not break. With none reproduced the run stops and says so.
 *
 * Confirmed issues reproduce on most of their scenarios, emerging ones on
 * fewer, and one the feed couldn't confirm doesn't reproduce reliably at all.
 */
export const reproduceOf = (issue, drafts = [], repeats = REPEATS) => {
  const evidence = issue.evidence_state || "confirmed";
  const total = drafts.length;
  const perScenario = {};
  drafts.forEach((d, i) => {
    const h = hash(`${issue.cluster_id}:${i}`) % 100;
    let fails;
    if (evidence === "unresolved") fails = i === 1 ? 1 : 0;
    else if (evidence === "emerging")
      fails =
        i < Math.max(1, total - 2) ? (h < 50 ? 3 : 2) : i === total - 2 ? 1 : 0;
    else fails = i === total - 1 && h < 50 ? 1 : h < 60 ? 3 : 2;
    /* Over the run's own repeats (two for voice): never more fails than tries. */
    fails = Math.min(fails, repeats);
    perScenario[d.id] = {
      fails,
      of: repeats,
      state:
        fails >= 2 ? "reproduced" : fails === 1 ? "inconclusive" : "passed",
    };
  });
  const count = (st) =>
    Object.values(perScenario).filter((x) => x.state === st).length;
  return {
    reproduced: count("reproduced"),
    inconclusive: count("inconclusive"),
    passed: count("passed"),
    total,
    perScenario,
  };
};

/* What went wrong in each issue's traces, as the call log shows it — which
   decides what kind of change can honestly fix it. */
const ISSUE_MECHANISM = {
  "ISS-4501": { missing: "verify_identity", match: /verif|identity|auth/i },
  "ISS-4520": { unsupportedClaim: true },
  "ISS-4478": {
    missing: "check_inventory",
    match: /inventory|stock|availability/i,
  },
  "ISS-4402": {
    missing: "cancel_order",
    match: /cancel/i,
    unsupportedClaim: true,
  },
  "ISS-4366": { missing: "check_return_eligibility", match: /eligib/i },
  "ISS-4431": {
    missing: "shipment_status",
    match: /shipment/i,
    unsupportedClaim: true,
  },
  "ISS-4536": {
    missing: "lookup_booking",
    match: /lookup_booking/i,
    unsupportedClaim: true,
  },
  "ISS-4529": { missing: "modify_booking", match: /modify/i },
  "ISS-4552": { unsupportedClaim: true },
  "ISS-4557": { unsupportedClaim: true },
  "ISS-4563": { missing: "check_loyalty_status", match: /loyalty|tier/i },
  "ISS-4566": { unsupportedClaim: true },
  "ISS-4571": { unsupportedClaim: true },
  "ISS-4574": { unsupportedClaim: true },
  "ISS-4578": { unsupportedClaim: true },
  "ISS-4582": { unsupportedClaim: true },
  "ISS-4585": { unsupportedClaim: true },
  "ISS-4589": { unsupportedClaim: true },
  "ISS-4593": { unsupportedClaim: true },
  "ISS-4601": { unsupportedClaim: true },
  "ISS-4602": { missing: "verify_identity", match: /verif|identity|auth|dob/i },
  "ISS-4603": { unsupportedClaim: true },
  "ISS-4604": { unsupportedClaim: true },
  "ISS-4605": { unsupportedClaim: true },
  "ISS-4606": { unsupportedClaim: true },
};

/** How the issue's traces fail — a skipped tool, a claim no tool backs, or both. */
export const issueMechanismOf = (id) =>
  ISSUE_MECHANISM[id] || { unsupportedClaim: true };

/**
 * The issue's scenarios as the search sees them, with what reproducing
 * found: reproduced ones fail and are targets; inconclusive ones (failed
 * once in three) are flaky and never targets; the rest pass today and must
 * keep passing.
 */
export const issueTasks = (issue, drafts, repro, env) => {
  const m = ISSUE_MECHANISM[issue.cluster_id] || { unsupportedClaim: true };
  const tool = m.missing
    ? (env?.tools || []).find((t) => m.match?.test(t.name))?.name || m.missing
    : null;
  return drafts.map((d) => {
    const r = repro?.perScenario?.[d.id] || {
      state: "reproduced",
      fails: REPEATS,
    };
    const failing = r.state !== "passed";
    return {
      id: d.id,
      groupId: d.groupId || d.id,
      title: d.title,
      task: d.task,
      critical: !!d.critical,
      fromIssue: issue.cluster_id,
      status:
        r.state === "reproduced"
          ? "failed"
          : r.state === "inconclusive"
            ? "flaky"
            : "passed",
      /* Only a reproduction is something the fix must fix. */
      target: r.state === "reproduced",
      ...(r.state === "inconclusive" && { inconclusive: true }),
      passShare: (REPEATS - r.fails) / REPEATS,
      callLog: failing
        ? {
            missing: tool ? [tool] : [],
            unsupportedClaim: !!m.unsupportedClaim,
          }
        : { missing: [] },
      evalResults: [{ name: "Task success", passed: !failing }],
      steps: [],
    };
  });
};

export const fixSeed = (es, issueId) =>
  `fix-${issueId}-${(es?.optimizations || []).length}`;

/* Measure today, for a fix: the environment's scenarios on the version in
   production, recorded as a run named for the issue (measureToday.js). */
export const todayRunFor = (env, es, issueId, now = new Date()) =>
  issueId
    ? measureTodayRun(env, es, {
        key: fixSeed(es, issueId),
        measuredFor: { kind: "fix", issueId },
        startedBy: `Fix · ${issueId}`,
        now,
      })
    : null;
const summaryOf = measuredSummaryOf;

/**
 * The issue's scenarios that can run on this environment, by the same rule
 * the run applies (a scenario that can't stage there is skipped) — so the
 * fix counts, reproduces and targets only what its first run actually
 * plays. { runnable, cant: [{ id, title, why }] }
 */
export const runnableDraftsOf = (env, es, drafts = []) => {
  if (!env || !es || !drafts.length) return { runnable: drafts, cant: [] };
  const have = new Set((es.scenarios || []).map((x) => x.id));
  const riding = drafts.filter((d) => !have.has(d.id));
  if (!riding.length) return { runnable: drafts, cant: [] };
  let skipped = [];
  try {
    skipped = runPlan(env, {
      ...es,
      scenarios: [...(es.scenarios || []), ...riding],
    }).skipped;
  } catch (e) {
    return { runnable: drafts, cant: [] };
  }
  const out = new Map(
    skipped
      .filter((x) => riding.some((d) => d.id === x.id))
      .map((x) => [x.id, x.skipReason]),
  );
  return {
    runnable: drafts.filter((d) => !out.has(d.id)),
    cant: drafts
      .filter((d) => out.has(d.id))
      .map((d) => ({ id: d.id, title: d.title, why: out.get(d.id) })),
  };
};

/**
 * The fix's first run, played live where Start fix lands: today's agent on
 * the environment's scenarios (the baseline), then the issue's own — which
 * fail as often as Reproduce found, so the run and the fix never disagree.
 * The issue's scenarios ride on the run (`fixScenarios`); they stay out of
 * the environment until someone adds them.
 */
export const fixRunFor = (env, es, issue, drafts, now = new Date()) => {
  const today = todayRunFor(env, es, issue?.cluster_id, now);
  if (!today) return null;
  const reps = today.repeats || REPEATS;
  const repro = reproduceOf(issue, drafts, reps);
  /* One already in the environment (added earlier) runs once, as its own. */
  const inEnv = new Set((es?.scenarios || []).map((x) => x.id));
  const riding = drafts.filter((d) => !inEnv.has(d.id));
  const fixScenarios = riding.map((d) => {
    const fails = Math.min(repro.perScenario?.[d.id]?.fails || 0, reps);
    return {
      ...d,
      fromIssue: issue.cluster_id,
      fixScenario: true,
      forcedSamples: Array.from({ length: reps }, (_, i) =>
        i < fails ? "failed" : "passed",
      ),
    };
  });
  const {
    finishedAt: _f,
    total: _t,
    passed: _p,
    failed: _x,
    flaky: _fl,
    unmeasured: _u,
    ...open
  } = today;
  return {
    ...open,
    scenarioIds: [...(today.scenarioIds || []), ...riding.map((d) => d.id)],
    fixScenarios,
    /* Played when its page opens, like a run-group member. */
    status: "running",
    pending: true,
    label: `Fix · ${issue.cluster_id}`,
  };
};

/**
 * The improvement record a Fix writes, for Start fix and for the seed alike:
 * the environment's newest run plus the issue's scenarios, reproduced first
 * (the budget pays for that, and what's left caps the search), the issue's
 * scenarios kept with the improvement until someone adds them, and a
 * snapshot of the issue as it was when Fix was pressed.
 */
export const issueFixRecord = ({
  issue,
  env,
  es,
  drafts,
  limits,
  budget,
  regressionEnvIds,
  seed,
  state,
  reread,
  textSearch,
  targetMember,
  compatCheck = COMPAT_CHECK,
  rootCause = null,
  diagnosis = null,
  lineage,
  today = null,
}) => {
  const issueId = issue.cluster_id;
  /* A production issue is fixed on the production version: the search
     starts from today's measurement of it (`today`, made as the fix starts)
     — else, for fixes written before that, the newest run on it. */
  const prodLabel = productionAgentVersion(es)?.label || null;
  const measured = today
    ? summaryOf(env, es, today)
    : baseRunOf(env, es, prodLabel) || baseRunOf(env, es);
  /* The baseline is the environment's scenarios; the issue's — on the first
     run too, when it carries them — are added once, below. */
  const draftIds = new Set(drafts.map((d) => d.id));
  const base = measured
    ? { ...measured, tasks: measured.tasks.filter((t) => !draftIds.has(t.id)) }
    : null;
  const envScenarioCount = (today?.scenarioIds || []).filter(
    (id) => !draftIds.has(id),
  ).length;
  const repro = reproduceOf(issue, drafts, today?.repeats || REPEATS);
  return buildImprovement({
    env,
    es,
    tasks: base
      ? [...base.tasks, ...issueTasks(issue, drafts, repro, env)]
      : null,
    seed: seed || fixSeed(es, issueId),
    fromRunId: base?.id || null,
    baseAgentVersion:
      prodLabel || base?.agentVersion || currentAgentVersion(es)?.label || null,
    trigger: {
      kind: "error-feed",
      issueId,
      issueTitle: issue.error?.name,
      severity: issue.severity,
    },
    limits,
    budget,
    regressionEnvIds,
    spentFirst:
      (drafts.length + envScenarioCount) *
      (today?.repeats || REPEATS) *
      PER_CONVERSATION[env.surface === "voice" ? "voice" : "chat"],
    hold: !repro.reproduced,
    state,
    reread,
    textSearch,
    /* Earlier versions' changes offered to the search again (RSI PRD:
       "Search across a lineage of versions"). */
    ...(lineage?.length && { lineage }),
    targetMember,
    compatCheck,
    /* Improvement-only until added from its page or the Scenarios tab. */
    issueScenarios: drafts.map((d) => ({
      ...d,
      source: "error-feed",
      ...(repro.perScenario?.[d.id]?.state === "reproduced" && {
        target: true,
      }),
    })),
    extra: {
      reproduce: repro,
      scenarioIds: drafts.map((d) => d.id),
      /* The issue as it was when Fix was pressed — it doesn't change
         after this, whatever the feed learns later. */
      snapshot: {
        issueId,
        title: issue.error?.name,
        severity: issue.severity,
        traceIds: [
          ...new Set(drafts.flatMap((d) => d.errorFeedIssue?.traceIds || [])),
        ].slice(0, 5),
        /* The root cause the issue page showed when Fix was pressed — the Root
           cause tab's finding when there is one, else the feed's own reading.
           Diagnose starts from it (RSI PRD). */
        rootCause: rootCause?.headline || issue.why_shown || null,
        suggestedFix: rootCause?.fix || null,
        rootCauseFrom: rootCause?.source || (issue.why_shown ? "feed" : null),
        /* Where in the agent it goes wrong, as the Fix page left it — what
           the search starts from, not the feed's symptom. */
        ...(diagnosis && { diagnosis }),
        fixLayer: issue.fix_layer || null,
        /* Reproduced ones only — an inconclusive one is checked, never a target. */
        mustStartPassing: drafts
          .filter((d) => repro.perScenario?.[d.id]?.state === "reproduced")
          .map((d) => d.id),
        inconclusive: drafts
          .filter((d) => repro.perScenario?.[d.id]?.state === "inconclusive")
          .map((d) => d.id),
        mustKeepPassing: (base?.tasks || []).filter(
          (t) => t.status === "passed",
        ).length,
      },
    },
  });
};

/**
 * The search a fix would run, worked out up front so its cost is a range, not
 * a guess — on the environment's scenarios plus the issue's, as if every
 * issue scenario reproduces (the dearest case).
 */
export const fixPreview = (
  env,
  es,
  issue,
  drafts,
  limits,
  { textSearch, state = null, regressionEnvIds = [] } = {},
) => {
  /* What measuring today will find — the same run Start records. */
  const today = env && issue ? todayRunFor(env, es, issue.cluster_id) : null;
  const base = summaryOf(env, es, today);
  if (!base || !issue) return { base, today, result: null };
  const tasks = [...base.tasks, ...issueTasks(issue, drafts, null, env)];
  return {
    base,
    today,
    result: planSearch({
      env,
      es,
      agentVersion: base.agentVersion || currentAgentVersion(es)?.label || null,
      tasks,
      limits,
      seed: fixSeed(es, issue.cluster_id),
      textSearch,
      state,
      regressionEnvIds,
    }),
  };
};

/**
 * What a fix will spend: reproducing the issue's scenarios first, every trial
 * of the search, the held-back check, and the other environments' regression
 * scenarios for the finalists — each repeated so a lucky run can't win.
 */
export const fixEstimate = ({
  preview,
  scenarioCount,
  regressionCount,
  surface,
  inEnvCount = 0,
}) =>
  improvementEstimate({
    /* Measuring today covers the issue's scenarios and the environment's. */
    preview: preview?.result,
    surface,
    extraScenarios: regressionCount,
    firstConversations:
      (scenarioCount -
        inEnvCount +
        (preview?.today?.scenarioIds?.length || 0)) *
      (preview?.today?.repeats || REPEATS),
    firstDetail:
      inEnvCount >= scenarioCount
        ? `today's agent on the environment's ${preview?.today?.scenarioIds?.length || 0} scenarios — the issue's ${scenarioCount} are among them`
        : `today's agent on the environment's ${preview?.today?.scenarioIds?.length || 0} scenarios and the issue's ${scenarioCount - inEnvCount}${inEnvCount ? ` (${inEnvCount} more already in it)` : ""}`,
  });

/* How long a released fix is watched before the issue counts as resolved —
   the same 30 days fix survival is measured over. */
export const WATCH_DAYS = 30;
const DAY = 24 * 60 * 60 * 1000;

/* "Fix in review · Vapi copy" — the draft's own name on its platform. */
/* Vapi has no drafts — a change ships as a copy of the assistant (RSI PRD). */
const draftLabel = (draft) =>
  `${draft.where} ${draft.kind || (/vapi/i.test(draft.where || "") ? "copy" : "draft")}`;

const HOUR = 60 * 60 * 1000;

/* The releases of this issue's fixes in one environment, oldest first. A
   release names its issue; one recorded before releases did is matched
   through the improvement it released. */
const fixReleasesOf = (es, issueId) =>
  (es?.releases || [])
    .filter(
      (r) =>
        (r.issueId ||
          (es?.optimizations || []).find((o) => o.id === r.optId)?.trigger
            ?.issueId) === issueId,
    )
    .sort((a, b) => (Date.parse(a.at) || 0) - (Date.parse(b.at) || 0));

/**
 * Did the issue come back after this release? Read per release, not per
 * issue: the feed records, for each fix released for an issue (first,
 * second, …), whether and when it was seen again (`recurs_after` on the
 * issue). So a first fix can reopen while the next one holds. Null when it
 * hasn't come back (yet).
 *   { at, version }
 */
export const recurrenceOf = (record, es, now = Date.now()) => {
  const issueId = record?.trigger?.issueId;
  const landed = es?.handoffLandings?.[record?.id];
  const release = landed
    ? (es?.releases || []).find((r) => r.version === landed)
    : null;
  if (!issueId || !release) return null;
  const seen = mockCluster(issueId)?.recurs_after || [];
  if (!seen.length) return null;
  const nth =
    fixReleasesOf(es, issueId).findIndex((r) => r.version === landed) + 1;
  const hit = seen.find((x) => x.release === nth);
  if (!hit) return null;
  const at = (Date.parse(release.at) || 0) + (hit.after_hours || 0) * HOUR;
  return at <= now ? { at: new Date(at).toISOString(), version: landed } : null;
};

/*
  A shipped draft reads "Fix in review · PR #123" on an issue's fix (owner's
  wording for the PRD's Draft shipped, from Flow 3's chip), "In review · …"
  on any other improvement.
*/
const inReview = (record, via) =>
  `${record?.trigger?.issueId ? "Fix in review" : "In review"} · ${via}`;

/**
 * Where a shipped proposal is (RSI PRD, Part 3 "Improvement states": Draft
 * shipped — said "Fix in review" — → Released → Watching → Resolved or
 * Reopened). Null until it ships.
 * The Error Feed's chip, the issue page, the Improvements list and the
 * improvement page all read this, and it works out recurrence itself — so
 * no caller can pass something different and disagree. `recurs: true`
 * still forces Reopened, for older callers.
 *   { key: "review" | "watching" | "resolved" | "reopened", label, detail?, change? }
 */
export const shippedStateOf = (
  record,
  es,
  prs = [],
  { recurs, now = Date.now(), envId = null } = {},
) => {
  if (!record) return null;
  const landed = es?.handoffLandings?.[record.id];
  if (landed) {
    const release = (es?.releases || []).find((r) => r.version === landed);
    /* Saved but not released yet: still a shipped draft (RSI PRD states). */
    if (!release)
      return {
        key: "review",
        label: inReview(record, `draft version ${landed}`),
        detail: landed,
        via: landed,
        awaitingRelease: true,
      };
    /* Seen again in production reopens it on its own; a person escalating a
       watched or resolved issue reopens it too (triageFixEffect). */
    const byPerson = record.reopenedBy
      ? { at: record.reopenedBy.at, version: landed, by: record.reopenedBy.by }
      : null;
    const back =
      recurrenceOf(record, es, now) ||
      byPerson ||
      (recurs === true ? { at: null, version: landed } : null);
    if (back) {
      /* Reopened links to the change that was meant to fix it. */
      const pr = prOfImprovement(prs, record.id, envId);
      const draft = es?.handoffDrafts?.[record.id];
      return {
        key: "reopened",
        label: "Reopened",
        detail: back.by ? `by ${back.by}` : `seen again on ${landed}`,
        ...(back.by && { byPerson: true }),
        change: {
          version: landed,
          releasedAt: release.at,
          seenAt: back.at,
          ...(back.by && { reopenedBy: back.by }),
          name: improvementRunName(es, record),
          via: pr ? `PR #${pr.number}` : draft ? draftLabel(draft) : null,
        },
      };
    }
    const days = Math.floor((now - Date.parse(release.at)) / DAY);
    return days >= WATCH_DAYS
      ? {
          key: "resolved",
          label: "Resolved",
          detail: `${landed} · ${WATCH_DAYS} days clean`,
          version: landed,
        }
      : {
          key: "watching",
          label: "Watching",
          detail: `${WATCH_DAYS - days} days left`,
          version: landed,
        };
  }
  const pr = prOfImprovement(prs, record.id, envId, { open: true });
  /* One set of words for a fix's state on every surface: Ready for review →
     Fix in review → Watching → Resolved | Reopened (RSI PRD). */
  if (pr)
    return {
      key: "review",
      label: inReview(record, `PR #${pr.number}`),
      via: `PR #${pr.number}`,
    };
  const draft = es?.handoffDrafts?.[record.id];
  if (draft)
    return {
      key: "review",
      label: inReview(record, draftLabel(draft)),
      via: draftLabel(draft),
    };
  return null;
};

/**
 * Where the issue's fix stands, for the agent the issue belongs to now. Only
 * environments that test that agent are read: after its project moves to
 * another agent, a fix the previous one started is its history, not the new
 * agent's fix in flight. With no agent linked there is no fix to show.
 */
export const fixStatusOf = (
  issue,
  state,
  prs = [],
  now = Date.now(),
  agents = agentsOfState(state),
) => {
  const issueId = issue?.cluster_id;
  const agentKey = agentKeyForIssue(issue, state, agents);
  if (!issueId || !agentKey) return null;
  let found = null;
  state.myEnvironments.forEach((env) => {
    const es = state.byEnv[env.id];
    if (!es?.agent || envAgentKey(es.agent, env.id) !== agentKey) return;
    (es?.optimizations || []).forEach((o) => {
      if (!issueId || o.trigger?.issueId !== issueId) return;
      if (
        !found ||
        Date.parse(o.createdAt) > Date.parse(found.record.createdAt)
      )
        found = { record: o, env, es };
    });
  });
  if (!found) return null;
  const { record, env, es } = found;
  const at = { envId: env.id, optId: record.id, runId: record.fromRunId };
  /* Handed to the customer's coding agent: its PR, once opened, leads;
     until then the agent's session says where it stands. */
  if (record.improver) {
    const shippedNow = shippedStateOf(record, es, prs, { now, envId: env.id });
    if (shippedNow) return { ...at, ...shippedNow };
    const coder = coderStatusOf(record, env.id, { now, surface: env.surface });
    return {
      ...at,
      key: coder.key === "running" ? "fixing" : coder.key,
      label: coder.label,
      improver: coder.improver,
    };
  }
  const { ending, beforeSearch } = improvementOutcome(record);
  /* While it runs, the PRD's own phases: Reproducing → Searching → Verifying. */
  if (ending.key === "running")
    return {
      ...at,
      key: "fixing",
      label: improvementPhaseOf(record, now)?.label || "Searching",
    };
  if (ending.key === "not-reproduced") {
    const inconclusive = record.reproduce?.inconclusive || 0;
    return {
      ...at,
      key: "not-reproduced",
      label: "Could not reproduce",
      ...(inconclusive && { detail: `${inconclusive} inconclusive` }),
    };
  }
  if (ending.key === "failed")
    return { ...at, key: "no-fix", label: "Failed for infrastructure reasons" };
  if (ending.key === "no-fix")
    return { ...at, key: "no-fix", label: "No safe improvement" };
  /* Stopped by a person: nothing was checked, so nothing to review. */
  if (ending.key === "stopped")
    return { ...at, key: "stopped", label: "Stopped" };
  /* Stopped at budget before searching: no change was tried, so there's
     nothing to review — Fix again, with a bigger budget, is the way on. A
     stop mid-search keeps its best so far and reads Ready below. */
  if (ending.key === "budget" && beforeSearch)
    return {
      ...at,
      key: "over-budget",
      label: "Stopped at budget",
      detail: "before searching",
    };
  /* Recurrence is the record's own (shippedStateOf reads it); a caller's
     explicit flag still forces Reopened. */
  const shipped = shippedStateOf(record, es, prs, {
    now,
    envId: env.id,
    ...(issue?.recurs_after_fix === true && { recurs: true }),
  });
  if (shipped) return { ...at, ...shipped };
  /* Another change went live after it measured the agent: its proof is on
     the old version, so it's re-checked before it can ship. */
  const behind =
    ["ready", "stopped-early", "budget"].includes(ending.key) &&
    needsRecheck({ record, env, envState: es, agents });
  if (behind)
    return {
      ...at,
      key: "ready",
      recheck: true,
      label: `Needs re-check on ${behind.prod}`,
    };
  if (ending.key === "budget")
    return { ...at, key: "ready", label: "Stopped at budget" };
  /* Stopped once two runs in a row didn't beat the best; the best was verified. */
  if (ending.key === "stopped-early")
    return { ...at, key: "ready", label: "Stopped early" };
  if (ending.key === "suggestion")
    return { ...at, key: "ready", label: "Suggestion only" };
  /* The PRD's name. It sits in its own Fix column on the Error Feed, apart
     from the issue's own triage status ("For review"). */
  return { ...at, key: "ready", label: "Ready for review" };
};

/**
 * An issue's fix, on the improvement's own page (RSI PRD, Flow 3: "Start.
 * The improvement page opens") — not a drawer over some environment run.
 * It carries the issue along, so the page's back goes to the issue.
 * Returns [to, options] for navigate().
 */
export const issueFixNav = (at, issueId) => [
  `${paths.dashboard.simulate.improvements}/${at.envId}/${at.optId}`,
  issueId ? { state: { fromIssue: issueId } } : undefined,
];

/**
 * What changing an issue's triage status does to its fix. Escalating an
 * issue whose fix is Watching or Resolved says it's back: the fix reopens
 * and Fix again is offered. Resolving an issue a person reopened (before a
 * new fix starts) takes the reopen back. Everything else leaves the fix alone.
 *   → { note, reopen: true | false } or null
 */
export const triageFixEffect = (fix, status) => {
  if (!fix) return null;
  if (
    status === "escalating" &&
    (fix.key === "watching" || fix.key === "resolved")
  ) {
    return {
      reopen: true,
      note: `Reopens the fix on ${fix.version}. Fix again starts a new one; this fix keeps its page.`,
    };
  }
  if (status === "resolved" && fix.key === "reopened" && fix.byPerson) {
    return { reopen: false, note: "Takes back the reopen." };
  }
  return null;
};
