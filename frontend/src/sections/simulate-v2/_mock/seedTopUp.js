/**
 * Topping up a stored copy of the prototype's state.
 *
 * Lifted out of store.jsx so the store file exports components and hooks
 * only, and so the seed's migrations can be checked outside the app.
 */
import { seededState, AIRLINE_AGENT_KEY } from "./seedState";
import { getEnvironment } from "./environments";
import { envAgentKey, repairAgentRecord } from "./agentRegistry";
import { defaultRefFor, migrateEnvVars, scrubAgentSecrets } from "./secretRefs";
import { withDemoFaultRun, replaceLegacyDemoRun, withPassingRun, DEMO_RUN_ENV, PASS_RUN_ENV } from "./demoRun";
import { withScenarioSetSeed, withArchivedMigration, withImprovementScenarioSeed, isRetiredCheckRow } from "./scenarioSetSeed";
import { withErrorFeedFixes, withAirlineTracing, withWatchedFixes, SEEDED_RETELL_VALUES } from "./seedErrorFeedFixes";
import { withVoiceRunHistory, HISTORY_ENV } from "./voiceRunHistory";
import { withFiveAGoal, TOPUP_ENV } from "./voiceScenarioTopUp";
import { PRE_VERSIONING_AGENT_NOTE, PRE_VERSIONING_ENV_NOTE, recordedBeforeVersioning } from "./versions";
import { withCommitChanges, withCommitHistory, withCommitTies, withFixPr } from "./commitHistorySeed";
import { withProjectLinkRepair } from "./projectLinkRepair";

/*
  Bumped whenever the seed gains an environment.

  Bumping the storage key instead would work, and would also throw away every
  run the person looking at this has made since — which is the state that makes
  the prototype worth showing. So a stored state is topped up rather than
  replaced: environments it has never seen are added, environments it already
  has are left exactly as they are, and an environment someone deliberately
  removed stays removed because the marker moves on regardless.
*/
export const SEED_VERSION = 35;
/* Environments a seed version added, so a later top-up adds only those —
   never one someone removed since the last. */
const ADDED_AT = { 21: ["env-clinic-line"] };

/*
  Nothing stored may hold a raw secret. An agent connected with a pasted key
  keeps a reference to where it now lives instead; a secret env var keeps its
  reference. Applied to every stored copy on load and to every write that can
  carry an agent, so a flow that still sends a key can't land one in storage.
*/
export const scrubAgentList = (list) => (Array.isArray(list) ? list.map(scrubAgentSecrets) : list);
export const scrubEnvState = (es) => {
  if (!es) return es;
  const next = { ...es };
  if (es.agent) next.agent = scrubAgentSecrets(es.agent);
  if (es.additionalAgents) next.additionalAgents = scrubAgentList(es.additionalAgents);
  if (es.envVars) next.envVars = migrateEnvVars(es.envVars);
  return next;
};
export const scrubPatch = (patch) => {
  if (!patch) return patch;
  const next = { ...patch };
  if (patch.agent) next.agent = scrubAgentSecrets(patch.agent);
  if (patch.additionalAgents) next.additionalAgents = scrubAgentList(patch.additionalAgents);
  if (patch.envVars) next.envVars = migrateEnvVars(patch.envVars);
  return next;
};

/*
  Seed 13: credentials became references, and the support line gained its
  finished fault-injection run. A stored copy keeps every reference it has;
  a seeded environment with none takes the seed's. The demo run is added
  once — deleting it afterwards keeps it deleted, since the marker moves on.
*/
const fillCredentialRefs = (es, seedEs) => {
  if (!es || es.credentialRefs || !seedEs?.credentialRefs) return es;
  return { ...es, credentialRefs: seedEs.credentialRefs };
};
const fillDemoRun = (byEnv, myEnvironments) => {
  const es = byEnv[DEMO_RUN_ENV];
  const env = getEnvironment(DEMO_RUN_ENV) || (myEnvironments || []).find((e) => e.id === DEMO_RUN_ENV);
  if (!es || !env) return;
  try {
    byEnv[DEMO_RUN_ENV] = withDemoFaultRun(env, es);
  } catch (err) {
    /* A stored copy the demo can't be rebuilt on — leave it as it is. */
    // eslint-disable-next-line no-console
    console.warn("[store] demo run skipped", err);
  }
};

const upgradeSeededRun = (byEnv, myEnvironments, envId, apply) => {
  const es = byEnv[envId];
  const env = getEnvironment(envId) || (myEnvironments || []).find((e) => e.id === envId);
  if (!es || !env) return;
  try {
    byEnv[envId] = apply(env, es);
  } catch (err) {
    // eslint-disable-next-line no-console
    console.warn("[store] seeded run skipped", envId, err);
  }
};

/*
  What the seeded agents gained after a stored copy was made — where they're
  reached (an MCP server), what they're read from (a repository), and what
  each version changed. Filled in only where the stored copy has nothing,
  never over something someone edited.
*/
const SEED_AGENT_VALUES = ["endpoint", "hasApiKey", "observability", "endpointConsent"];
const fillSeededAgent = (es, seedEs) => {
  if (!es?.agent || !seedEs?.agent) return es;
  const a = es.agent;
  const s = seedEs.agent;
  const values = { ...(a.values || {}) };
  SEED_AGENT_VALUES.forEach((k) => { if (values[k] === undefined && s.values?.[k] !== undefined) values[k] = s.values[k]; });
  const seedVersions = new Map((seedEs.agentVersions || []).map((v) => [v.id, v]));
  const agentVersions = (es.agentVersions || []).map((v) => {
    const sv = seedVersions.get(v.id);
    if (!sv) return v;
    return {
      ...v,
      ...(!v.changes && sv.changes && { changes: sv.changes }),
      ...(v.note === "First version connected to this environment." && { note: sv.note }),
    };
  });
  /* "MCP manifest" was never a way to connect an agent — an MCP server is an
     environment's tools. A seeded agent saved that way is reached at its
     endpoint, as the seed now says. */
  const legacyMcp = a.sourceKind === "mcp" && s.sourceKind !== "mcp" ? { sourceKind: s.sourceKind, via: s.via } : {};
  return { ...es, agent: { ...a, ...legacyMcp, values, ...(!a.definition && s.definition && { definition: s.definition }) }, agentVersions };
};

/*
  The seeded scheduled improvement, for a stored environment that has none —
  appended under the next free id so it can't collide with a run someone
  started, and only while the run it was searched against is still there.
*/
/* The seeded code fix on the Airline agent — added once, next to anything
   already there, when the run it started from still exists. */
const fillCodeFixRun = (es, seedEs) => {
  const seeded = (seedEs?.optimizations || []).find((o) => o.seededAs === "code-fix");
  const records = es?.optimizations || [];
  if (!seeded || records.some((o) => o.seededAs === "code-fix")) return es;
  if (!(es.runs || []).some((r) => r.id === seeded.fromRunId)) return es;
  const n = records.reduce((m, o) => Math.max(m, Number(String(o.id).replace(/\D/g, "")) || 0), 0) + 1;
  return { ...es, optimizations: [...records, { ...seeded, id: `OPT-${String(n).padStart(5, "0")}` }] };
};

const fillScheduledRun = (es, seedEs) => {
  const seeded = (seedEs?.optimizations || []).find((o) => o.trigger?.kind === "schedule");
  const records = es?.optimizations || [];
  if (!seeded || records.some((o) => o.trigger?.kind === "schedule")) return es;
  if (!(es.runs || []).some((r) => r.id === seeded.fromRunId)) return es;
  const n = records.reduce((m, o) => Math.max(m, Number(String(o.id).replace(/\D/g, "")) || 0), 0) + 1;
  const id = `OPT-${String(n).padStart(5, "0")}`;
  const draft = seedEs.handoffDrafts?.[seeded.id];
  return {
    ...es,
    optimizations: [...records, { ...seeded, id }],
    ...(draft && { handoffDrafts: { ...(es.handoffDrafts || {}), [id]: draft } }),
  };
};

/*
  The After hours environment is seeded to test the same agent as Customer
  Support Line. A saved copy whose Customer Support Line agent has its own
  identity (connected or re-keyed since) got a second, different agent there.
  Point After hours at the Customer Support Line agent — its record and its
  version history — but only while After hours is still exactly as seeded,
  and drop constraints left on the seeded agent nothing tests any more.
*/
const SEEDED_AFTER_HOURS_AGENT = "asst_v1c2f3";
const shareSupportAgent = (byEnv, agentLimits) => {
  const main = byEnv["env-voice-support"];
  const ah = byEnv["env-voice-after-hours"];
  if (!main?.agent || !ah?.agent) return;
  const mainKey = envAgentKey(main.agent, "env-voice-support");
  const ahKey = envAgentKey(ah.agent, "env-voice-after-hours");
  if (mainKey === ahKey) return;
  const untouched = ah.agent.values?.agentId === SEEDED_AFTER_HOURS_AGENT
    && (ah.agentVersions || []).length <= 1 && (ah.runs || []).length <= 1 && !(ah.optimizations || []).length;
  if (!untouched) return;
  byEnv["env-voice-after-hours"] = {
    ...ah,
    agent: { ...main.agent },
    agentVersions: main.agentVersions || ah.agentVersions,
    ...(main.activeAgentVersion && { activeAgentVersion: main.activeAgentVersion }),
  };
  const stillUsed = Object.entries(byEnv).some(([id, es]) => es?.agent && envAgentKey(es.agent, id) === ahKey);
  if (!stillUsed) delete agentLimits[ahKey];
};

/*
  Seed 17 (RSI PRD): what the seed gains on top of seedState — the scenarios
  the support line's scheduled improvement wrote and nobody has added yet.
  Applied to a first-run state and, once, to a stored copy.
*/
export const withRsiSeed = (state) => {
  if (!state?.byEnv) return state;
  const byEnv = { ...state.byEnv };
  Object.keys(byEnv).forEach((id) => {
    const env = getEnvironment(id) || (state.myEnvironments || []).find((e) => e.id === id);
    try {
      byEnv[id] = withImprovementScenarioSeed(id, byEnv[id], env);
    } catch (err) {
      // eslint-disable-next-line no-console
      console.warn("[store] improvement scenarios skipped", id, err);
    }
  });
  return { ...state, byEnv };
};

const MOVED_DEMO_FIXES = { "ef-code": "ISS-4453", "ef-retell": "ISS-4471" };
const withMovedDemoFixes = (state, envs) => {
  const byEnv = { ...state.byEnv };
  Object.keys(byEnv).forEach((id) => {
    const es = byEnv[id];
    const gone = (es?.optimizations || []).filter((o) => MOVED_DEMO_FIXES[o.seededAs] && o.trigger?.issueId === MOVED_DEMO_FIXES[o.seededAs]);
    if (!gone.length) return;
    const ids = new Set(gone.map((o) => o.id));
    const drafts = { ...(es.handoffDrafts || {}) };
    ids.forEach((x) => { delete drafts[x]; });
    byEnv[id] = { ...es, optimizations: es.optimizations.filter((o) => !ids.has(o.id)), handoffDrafts: drafts };
    /* The Draft agent version each one recorded, wherever that agent is tested. */
    Object.keys(byEnv).forEach((other) => {
      const list = byEnv[other]?.agentVersions;
      if (!list?.some((v) => v.status === "draft" && v.draftOf?.envId === id && ids.has(v.draftOf?.optId))) return;
      byEnv[other] = { ...byEnv[other], agentVersions: list.filter((v) => !(v.status === "draft" && v.draftOf?.envId === id && ids.has(v.draftOf?.optId))) };
    });
  });
  /* The Airline agent reported into pickup-dispatch; it has its own project now. */
  const air = byEnv["env-chat-travel"]?.agent;
  if (air?.values?.observability?.project === "pickup-dispatch") {
    byEnv["env-chat-travel"] = { ...byEnv["env-chat-travel"], agent: { ...air, values: { ...air.values, observability: { ...air.values.observability, project: "airline-rebooking" } } } };
  }
  return withErrorFeedFixes({ ...state, byEnv }, envs).byEnv;
};

/*
  Concepts the prototype no longer has, cleared from any stored copy on every
  load — idempotent, so it needs no seed version:
    · the impossible-by-design "canary" rows of the removed cheat checks
    · a rule question answered "Unsure", which reads as unanswered again
    · the template's stand-in agent ("Template baseline"): every environment
      now belongs to an agent from Agents, so an old copy of the stand-in is
      marked as the placeholder it was and kept off the lists and pickers
*/
const TEMPLATE_STAND_IN = "Template baseline";
const withoutRetiredConcepts = (state) => {
  if (!state?.byEnv) return state;
  let changed = false;
  const byEnv = {};
  const standIns = (state.connectedAgents || []).filter((a) => a.origin === "environment" && a.name === TEMPLATE_STAND_IN);
  Object.entries(state.byEnv).forEach(([id, es]) => {
    let next = es;
    if (es?.agent?.name === TEMPLATE_STAND_IN && !es.agent.seeded) next = { ...next, agent: { ...es.agent, seeded: true } };
    if (es?.scenarios?.some(isRetiredCheckRow)) next = { ...next, scenarios: es.scenarios.filter((r) => !isRetiredCheckRow(r)) };
    if (es?.nudges?.some((n) => n.answer === "unsure")) {
      const reset = new Set(es.nudges.filter((n) => n.answer === "unsure").map((n) => n.id));
      next = {
        ...next,
        subGoalShadows: (es.subGoalShadows || []).filter((sh) => !reset.has(sh.nudgeId)),
        nudges: es.nudges.map((n) => {
          if (n.answer !== "unsure") return n;
          const { answer: _a, resolvedAt: _r, ...rest } = n;
          return { ...rest, status: "open" };
        }),
      };
    }
    if (next !== es) changed = true;
    byEnv[id] = next;
  });
  if (standIns.length) {
    changed = true;
    return { ...state, byEnv, connectedAgents: state.connectedAgents.filter((a) => !standIns.includes(a)) };
  }
  return changed ? { ...state, byEnv } : state;
};

export const topUpSeed = (stored) => withoutRetiredConcepts(topUpSeedVersions(stored));

/* How a seeded environment's first version was worded, before and after the seed history. */
const SEEDED_FIRST_BUILD_NOTES = ["First build, read from the agent source.", "First build from the agent."];

const topUpSeedVersions = (stored) => {
  if ((stored.seedVersion || 0) >= SEED_VERSION) return { ...stored, seedVersion: SEED_VERSION };
  const seed = seededState();
  const have = new Set((stored.myEnvironments || []).map((e) => e.id));
  const from = stored.seedVersion || 0;
  /* An older copy gains every seeded environment it lacks; a current one
     only those added since — Seed 21: the Bland clinic line. */
  const added = new Set(Object.entries(ADDED_AT).filter(([v]) => Number(v) > from).flatMap(([, ids]) => ids));
  const missing = seed.myEnvironments.filter((e) => !have.has(e.id) && (from < 20 || added.has(e.id)));

  const byEnv = { ...stored.byEnv };
  Object.keys(seed.byEnv).forEach((id) => { if (byEnv[id]) byEnv[id] = fillCredentialRefs(fillCodeFixRun(fillScheduledRun(fillSeededAgent(byEnv[id], seed.byEnv[id]), seed.byEnv[id]), seed.byEnv[id]), seed.byEnv[id]); });
  /* Raw secrets in any stored environment, seeded or not, become references. */
  Object.keys(byEnv).forEach((id) => { byEnv[id] = scrubEnvState(byEnv[id]); });
  if ((stored.seedVersion || 0) < 13 && have.has(DEMO_RUN_ENV)) fillDemoRun(byEnv, stored.myEnvironments);
  /* Seed 14: the demo run moved to one that leaves fewer calls unmeasured,
     and the returns line gained a run that passes the release gate. */
  if ((stored.seedVersion || 0) < 14 && have.has(DEMO_RUN_ENV)) upgradeSeededRun(byEnv, stored.myEnvironments, DEMO_RUN_ENV, replaceLegacyDemoRun);
  /* Seed 15: the passing run moved to one that also holds against run 2. */
  if ((stored.seedVersion || 0) < 15 && have.has(PASS_RUN_ENV)) upgradeSeededRun(byEnv, stored.myEnvironments, PASS_RUN_ENV, withPassingRun);
  /* Seed 16 (PRD v7): nothing is retired any more — a retired scenario reads
     as archived, everywhere. */
  if ((stored.seedVersion || 0) < 16) {
    Object.keys(byEnv).forEach((id) => {
      byEnv[id] = seed.byEnv[id] ? withScenarioSetSeed(id, byEnv[id]) : withArchivedMigration(byEnv[id]);
    });
  }
  /* Seed 17 (RSI PRD): scenarios an improvement wrote, waiting to be added. */
  if ((stored.seedVersion || 0) < 17) {
    Object.keys(byEnv).forEach((id) => {
      const env = getEnvironment(id) || (stored.myEnvironments || []).find((e) => e.id === id);
      try { byEnv[id] = withImprovementScenarioSeed(id, byEnv[id], env); } catch (err) {
        // eslint-disable-next-line no-console
        console.warn("[store] improvement scenarios skipped", id, err);
      }
    });
  }
  missing.forEach((e) => { byEnv[e.id] = withImprovementScenarioSeed(e.id, seed.byEnv[e.id], e); });
  /* Seed 18 (RSI PRD, Agents defined): the support line is a squad serving
     voice and chat, and the seeded live agents carry their consent. Filled in
     only where the stored agent is the seeded one and has none of it. */
  if ((stored.seedVersion || 0) < 18) {
    ["env-voice-support", "env-voice-after-hours", "env-returns-line"].forEach((id) => {
      const a = byEnv[id]?.agent;
      const s = seed.byEnv[id]?.agent;
      if (!a || !s || a.values?.agentId !== s.values?.agentId) return;
      const values = { ...a.values };
      ["channels", "liveTestConsent"].forEach((k) => { if (values[k] === undefined && s.values?.[k] !== undefined) values[k] = s.values[k]; });
      const definition = !a.definition?.members && s.definition?.members ? { ...(a.definition || {}), ...s.definition } : a.definition;
      byEnv[id] = { ...byEnv[id], agent: { ...a, values, ...(definition && { definition }) } };
    });
  }
  /* Seeded constraints go to the agent the scheduled run's environment tests
     now — which may not be the one it was seeded with. They fill an agent
     that has none; one that has its own only gains the schedule. */
  const agentLimits = { ...(stored.agentLimits || {}) };
  Object.keys(seed.byEnv).forEach((id) => {
    if (!(seed.byEnv[id].optimizations || []).some((o) => o.trigger?.kind === "schedule")) return;
    const l = seed.agentLimits?.[envAgentKey(seed.byEnv[id].agent, id)];
    const key = byEnv[id]?.agent ? envAgentKey(repairAgentRecord(byEnv[id].agent, `env:${id}`), id) : null;
    if (!l || !key) return;
    if (!agentLimits[key]) agentLimits[key] = l;
    else if (!agentLimits[key].schedule) agentLimits[key] = { ...agentLimits[key], schedule: l.schedule };
  });
  shareSupportAgent(byEnv, agentLimits);
  /* The Airline agent may take code changes — only if it has no constraints of its own. */
  const airline = byEnv["env-chat-travel"]?.agent ? envAgentKey(byEnv["env-chat-travel"].agent, "env-chat-travel") : null;
  if (airline && !agentLimits[airline] && seed.agentLimits?.[AIRLINE_AGENT_KEY]) agentLimits[airline] = seed.agentLimits[AIRLINE_AGENT_KEY];
  const myEnvironments = [...(stored.myEnvironments || []), ...missing];
  /* Seed 19 (RSI PRD exit criteria): the Retell agent's numbers pinned and
     its calls watched (filled only where the stored agent is the seeded one
     and has none of it), agent improvement on as a design partner preview
     unless someone switched it, and the Error Feed fixes — each added once,
     never over an issue someone already ran a fix for. */
  let flags = stored.flags;
  let upgraded = byEnv;
  if ((stored.seedVersion || 0) < 19) {
    const gb = byEnv["env-guest-booking"]?.agent;
    const sgb = seed.byEnv["env-guest-booking"]?.agent;
    if (gb && sgb && gb.values?.agentId === sgb.values?.agentId) {
      const values = { ...gb.values };
      Object.entries(SEEDED_RETELL_VALUES(gb.connectedAt || new Date().toISOString())).forEach(([k, v]) => { if (values[k] === undefined) values[k] = v; });
      byEnv["env-guest-booking"] = { ...byEnv["env-guest-booking"], agent: { ...gb, values } };
    }
    if (flags?.agentImprovement === undefined) flags = { ...(flags || {}), agentImprovement: true };
    upgraded = withErrorFeedFixes({ ...stored, myEnvironments, byEnv, agentLimits }, myEnvironments).byEnv;
    upgraded = withAirlineTracing(upgraded, upgraded["env-chat-travel"]?.agent?.connectedAt || new Date().toISOString());
  }
  /* Seed 20: the Airline and Retell demo fixes moved to issues from their
     own Observe projects (ISS-4536, ISS-4529) — the pickup-dispatch issues
     they used read like a different agent. The old seeded records, their
     drafts and Draft versions go; the new ones are added once. Anything
     someone ran themselves stays. */
  if ((stored.seedVersion || 0) < 20 && (stored.seedVersion || 0) >= 19) {
    upgraded = withMovedDemoFixes({ ...stored, myEnvironments, byEnv: upgraded, agentLimits }, myEnvironments);
  }
  /* Seed 22–23: Customer Support Line's run history (v2, v3, v10), so its
     Runs tab has a trend to read. Added once (23 swaps 22's for one whose
     draws climb); runs renumber by when they happened. */
  if ((stored.seedVersion || 0) < 23 && upgraded[HISTORY_ENV]) {
    upgraded = { ...upgraded };
    upgradeSeededRun(upgraded, myEnvironments, HISTORY_ENV, withVoiceRunHistory);
  }
  /* Seed 24: Customer Support Line's goals topped up to five scenarios each,
     appended to its runs so they have a history. Added once. */
  if ((stored.seedVersion || 0) < 24 && upgraded[TOPUP_ENV]) {
    upgraded = { ...upgraded };
    upgradeSeededRun(upgraded, myEnvironments, TOPUP_ENV, withFiveAGoal);
  }
  /* Seed 26: the same consent and key fix on each environment's copy of its
     agent — the copy Agents reads an environment-tested agent from. */
  if ((stored.seedVersion || 0) < 26) {
    upgraded = Object.fromEntries(Object.entries(upgraded).map(([id, es]) => [id, withEnvAgentConsentAndKey(es)]));
  }
  /* Seed 27: the seeded "Could not reproduce" fix goes — it ran on an
     unconfirmed issue, which Fix no longer allows. Only the seeded record;
     one someone started themselves stays. */
  if ((stored.seedVersion || 0) < 27) {
    upgraded = Object.fromEntries(Object.entries(upgraded).map(([id, es]) => [id, (es?.optimizations || []).some((o) => o.seededAs === "ef-not-reproduced")
      ? { ...es, optimizations: es.optimizations.filter((o) => o.seededAs !== "ef-not-reproduced") }
      : es]));
  }
  /* Seed 28: the fix lifecycle after release on Customer Support Line —
     one Resolved, one Watching, and ISS-4402 released and Reopened. */
  if ((stored.seedVersion || 0) < 28) {
    upgraded = withWatchedFixes({ ...stored, myEnvironments, byEnv: upgraded, agentLimits }, myEnvironments).byEnv;
  }
  /* Seeds 29–30: the returns line was running before versioning started —
     its first agent and environment versions read as recorded, not
     connected or built. Only the seeded records, untouched since. */
  if ((stored.seedVersion || 0) < 30 && upgraded["env-returns-line"]) {
    const es = upgraded["env-returns-line"];
    const [a1, ...aRest] = es.agentVersions || [];
    const [e1, ...eRest] = es.envVersions || [];
    upgraded = {
      ...upgraded,
      "env-returns-line": {
        ...es,
        ...(a1?.label === "v1" && !a1.origin && a1.note === "First version connected." && {
          agentVersions: [recordedBeforeVersioning(a1, PRE_VERSIONING_AGENT_NOTE), ...aRest],
        }),
        ...(e1?.label === "v1" && !e1.origin && SEEDED_FIRST_BUILD_NOTES.includes(e1.note) && {
          envVersions: [recordedBeforeVersioning(e1, PRE_VERSIONING_ENV_NOTE), ...eRest],
        }),
      },
    };
  }
  const topped = {
    ...stored,
    seedVersion: SEED_VERSION,
    myEnvironments,
    byEnv: upgraded,
    agentLimits,
    ...(flags && { flags }),
    connectedAgents: (stored.seedVersion || 0) < 25
      ? withRecordedConsentAndKeys(scrubAgentList(stored.connectedAgents), upgraded)
      : scrubAgentList(stored.connectedAgents),
  };
  /* Seed 31: a commit history for two code agents (commitHistorySeed);
     Seed 32: each of those commits carries the change it made. */
  const withHistory = (stored.seedVersion || 0) < 31 ? withCommitHistory(topped) : topped;
  const withChanges = (stored.seedVersion || 0) < 32 ? withCommitChanges(withHistory) : withHistory;
  /* Seed 33: each agent back on the one Observe project its traces land in. */
  const withLinks = (stored.seedVersion || 0) < 33 ? withProjectLinkRepair(withChanges) : withChanges;
  /* Seed 34: the seeded fix branches tied to the pull requests they are. */
  const withTies = (stored.seedVersion || 0) < 34 ? withCommitTies(withLinks) : withLinks;
  /* Seed 35: Pickup dispatch's fix branch opened as its own draft PR. */
  return (stored.seedVersion || 0) < 35 ? withFixPr(withTies) : withTies;
};

/*
  Seed 25: a seeded agent that has already run live had its consent and its
  key recorded on the environment's copy, not on the agent in Agents — so its
  Connections tab asked for consent it had given and showed a key with no
  reference. The agent now carries both: consent as of when it connected,
  and the key as the workspace vault slot its environment reads.
*/
const withEnvAgentConsentAndKey = (es) => {
  const v = es?.agent?.values;
  if (!v) return es;
  const key = v.hasApiKey && !v.apiKeyRef && v.provider
    ? es.credentialRefs?.[`${v.provider}_api_key`] || defaultRefFor(`${v.provider}_api_key`)
    : null;
  const consent = !v.liveTestConsent && (es.runs || []).some((r) => r.finishedAt)
    ? { at: es.agent.connectedAt || new Date().toISOString(), replayFlag: false }
    : null;
  if (!key && !consent) return es;
  return { ...es, agent: { ...es.agent, values: { ...v, ...(key && { apiKeyRef: key }), ...(consent && { liveTestConsent: consent }) } } };
};
const withRecordedConsentAndKeys = (agents, byEnv) => {
  if (!Array.isArray(agents)) return agents;
  const ran = new Set();
  Object.entries(byEnv || {}).forEach(([envId, es]) => {
    if (!es?.agent || !(es.runs || []).some((r) => r.finishedAt)) return;
    try { ran.add(envAgentKey(es.agent, envId)); } catch { /* an agent the registry can't key */ }
  });
  return agents.map((a) => {
    const v = a?.values || {};
    const key = v.hasApiKey && !v.apiKeyRef && v.provider ? defaultRefFor(`${v.provider}_api_key`) : null;
    const consent = !v.liveTestConsent && ran.has(a.key) ? { at: a.connectedAt || new Date().toISOString(), replayFlag: false } : null;
    if (!key && !consent) return a;
    return { ...a, values: { ...v, ...(key && { apiKeyRef: key }), ...(consent && { liveTestConsent: consent }) } };
  });
};
