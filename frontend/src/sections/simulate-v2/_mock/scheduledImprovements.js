/*
  Scheduled improvements, with autonomy levels the customer caps.

  An agent whose Constraints tab sets a weekly schedule gets an improvement
  started at that time in its main environment — the same improvement "Run
  now" starts, from the production version's newest run, held to the
  agent's limits. When one the schedule started ends Ready for review and
  the agent's autonomy is "Open drafts", its change goes out as a draft — a
  draft PR, a platform draft or a draft version — exactly what handing it
  over by hand records. Never a release: a person always approves that.

  The hook runs inside the store (store.jsx mounts it once): on load, and
  every minute after. It reads the store, never the screen, so it works
  whichever page is open.
*/
import { useEffect, useRef } from "react";
import { agentSourceOf, connectedAgentsFrom, definitionOf, envAgentKey } from "./agentRegistry";
import { mainEnvOf } from "./agentLifecycle";
import { productionAgentVersion } from "./versions";
import { baseRunOf, buildImprovement } from "./improvementStart";
import { measureTodayRun, measuredSummaryOf } from "./measureToday";
import { improvementOutcome, OPT_STATUS, settleImprovements } from "./optimizationRuns";
import { improvementRunName } from "./optimizer";
import { changeFileFor } from "./optimize";
import { PLATFORM_TESTING } from "./rsiModel";
import { elevenlabsVersioningOn, retellPinnedOf } from "./improvementPolicy";
import { canWriteTo, getIntegrations, openPr, prFilesOf, repoNameOf } from "./integrations";
import { currentUser } from "./scenarioProvenance";
import { limitsOf, nextScheduledAt } from "../agents/limitsModel";
import { draftVersionRecord } from "../agents/versionRelease";

const MINUTE = 60 * 1000;

/**
 * The improvement a schedule (or Run now) starts for an agent: in its main
 * environment, held to its limits, and measured today first — the
 * environment on the production version, as a run (`today`, measureToday.js)
 * the caller records. Null when the agent has no environment or nothing to run.
 *   trigger — { kind: "schedule", cadence, autonomy } or { kind: "manual", by }
 */
export const agentImprovementFor = (agent, state, trigger, { now = Date.now() } = {}) => {
  const main = mainEnvOf(agent, state);
  const env = main?.env;
  const es = env ? state?.byEnv?.[env.id] : null;
  if (!env || !es) return null;
  const prod = productionAgentVersion(es)?.label || null;
  const seed = `${trigger.kind}-${env.id}-${now.toString(36)}`;
  const today = measureTodayRun(env, es, {
    key: seed, now: new Date(now),
    measuredFor: { kind: trigger.kind === "schedule" ? "schedule" : "goal" },
    startedBy: trigger.kind === "schedule" ? "Scheduled improvement" : trigger.by || "Improvement",
  });
  const base = measuredSummaryOf(env, es, today) || baseRunOf(env, es, prod) || baseRunOf(env, es);
  if (!base?.tasks) return null;
  const limits = limitsOf(state, agent.key);
  const record = buildImprovement({
    env, es, tasks: base.tasks, seed,
    fromRunId: base.id, baseAgentVersion: prod || base.agentVersion || null,
    trigger: { ...trigger, goal: { id: "pass-rate", label: "Pass rate" } },
    limits, budget: limits.budget.dollars, regressionEnvIds: [env.id], state,
  });
  return { env, es, record, today };
};

/** Run now: the same improvement, started by the person who pressed it. */
export const manualImprovementFor = (agent, state) => agentImprovementFor(agent, state, { kind: "manual", by: currentUser().name });

/* What a platform calls a draft: Vapi a copy, ElevenLabs and Bland a branch. */
const DRAFT_KIND = { vapi: "copy", elevenlabs: "branch", bland: "branch" };

/*
  Where an automatic run's change goes at "Open drafts" — the same routes
  handing it over by hand takes. A route that needs a person first (write
  access to the repository, Retell numbers pinned, ElevenLabs versioning)
  or that has no draft at all (an agent we only call, an upload) leaves the
  change waiting on the improvement and says why.
*/
const draftRouteOf = (agent) => {
  const def = definitionOf(agent);
  const source = agentSourceOf(agent);
  const provider = agent?.values?.provider;
  if (def.kind === "repo" || (def.kind === "framework" && def.repoUrl)) {
    const repo = repoNameOf(def.repoUrl || "");
    return repo && canWriteTo(repo) ? { kind: "pr", repo } : { kind: "wait", why: `no write access to ${repo || "its repository"}` };
  }
  if (source.kind === "builtin") return { kind: "version" };
  if (source.kind === "platform" && source.syncable && PLATFORM_TESTING[provider]) {
    if (provider === "retell" && !retellPinnedOf(agent)?.pinned) return { kind: "wait", why: "Retell's live numbers aren't pinned to a released version" };
    if (provider === "elevenlabs" && !elevenlabsVersioningOn(agent)) return { kind: "wait", why: "versioning isn't switched on in ElevenLabs" };
    return { kind: "platform", where: source.label, draftKind: DRAFT_KIND[provider] || "draft" };
  }
  return { kind: "wait", why: source.kind === "upload" ? "the change is a patch for your upload" : "the change is a patch — we can only call this agent" };
};

/*
  Opens the draft for one finished scheduled run. Returns the env-state
  patch and any agent version to record; never releases anything.
*/
const draftFor = (env, es, record, at) => {
  const agent = es?.agent;
  const proposals = record.result?.winner?.proposals || [];
  const route = draftRouteOf(agent);
  const name = improvementRunName(es, record);
  const mark = (autoDraft) => ({ optimizations: (es.optimizations || []).map((o) => (o.id === record.id ? { ...o, autoDraft } : o)) });
  const version = (source, where, extra = {}) => draftVersionRecord(es, {
    optId: record.id, envId: env.id, source, name, where, applied: proposals,
    fromRunId: record.fromRunId || null, basedOnVersion: record.baseAgentVersion || null, at, ...extra,
  });
  if (route.kind === "pr") {
    const opened = openPr({
      repo: route.repo,
      title: `Fix: ${proposals[0]?.title || name}`,
      body: [`## ${name}`, "", improvementOutcome(record).headline, "", "_Opened by Future AGI as a draft from a scheduled improvement. Nothing changes in your agent until you merge._"].join("\n"),
      files: prFilesOf(env, proposals, changeFileFor),
      handoffKey: record.id,
      envId: env.id, envName: env.name, fromRunId: record.fromRunId || null,
      agentKey: envAgentKey(agent, env.id),
    });
    return { patch: mark({ at, where: `PR #${opened.number}` }), version: version("pr", `PR #${opened.number}`, { prNumber: opened.number }) };
  }
  if (route.kind === "platform") {
    const where = `${route.where} ${route.draftKind}`;
    return {
      patch: {
        ...mark({ at, where }),
        handoffDrafts: { ...(es.handoffDrafts || {}), [record.id]: { where: route.where, kind: route.draftKind, at, auto: true } },
      },
      version: version("platform-draft", where),
    };
  }
  if (route.kind === "version") {
    const v = version("builtin", "Future AGI");
    return { patch: { ...mark({ at, where: v.label }), handoffLandings: { ...(es.handoffLandings || {}), [record.id]: v.label } }, version: v };
  }
  return { patch: mark({ at, skipped: route.why }), version: null };
};

/* One pass: start what's due, settle what's finished, open the drafts. */
export const runScheduler = (state, commit, now = Date.now()) => {
  if (!state?.hydrated) return;
  const agents = connectedAgentsFrom(state);
  agents.forEach((agent) => {
    const limits = limitsOf(state, agent.key);
    const sched = limits.schedule;
    if (sched?.mode !== "weekly" || !mainEnvOf(agent, state)?.env) return;
    const next = nextScheduledAt(sched, new Date(now)).toISOString();
    const due = Date.parse(sched.nextAt || "");
    /* First seen (or set before the scheduler kept a time): just book the next slot. */
    if (!Number.isFinite(due)) {
      commit({ type: "setAgentLimits", agentKey: agent.key, limits: { ...limits, schedule: { ...sched, nextAt: next } } });
      return;
    }
    if (now < due) return;
    /* Due — one run, however many slots passed while nobody had it open. */
    const started = agentImprovementFor(agent, state, {
      kind: "schedule", cadence: "weekly", autonomy: sched.autonomy === "canary" ? "drafts" : sched.autonomy, scheduledFor: new Date(due).toISOString(),
    }, { now });
    commit({ type: "setAgentLimits", agentKey: agent.key, limits: { ...limits, schedule: { ...sched, nextAt: next, lastStartedAt: new Date(now).toISOString() } } });
    if (started) {
      if (started.today) commit({ type: "recordRun", envId: started.env.id, run: started.today });
      commit({ type: "patchEnvState", envId: started.env.id, patch: { optimizations: [...(started.es.optimizations || []), { ...started.record, startedAt: new Date(now).toISOString() }] } });
    }
  });

  const prs = getIntegrations().prs || [];
  (state.myEnvironments || []).forEach((env) => {
    const es0 = state.byEnv?.[env.id];
    if (!(es0?.optimizations || []).some((o) => o.trigger?.kind === "schedule")) return;
    /* A scheduled run whose search has played out is finished, page open or not. */
    const es = settleImprovements(es0, now);
    let patch = es !== es0 ? { optimizations: es.optimizations } : null;
    let cur = es;
    (es.optimizations || []).forEach((record) => {
      /* Only runs the scheduler itself started (`scheduledFor`) draft on their own. */
      if (record.trigger?.kind !== "schedule" || !record.trigger.scheduledFor || record.trigger.autonomy !== "drafts" || record.autoDraft) return;
      /* A verified winner drafts whether the search used every run or stopped early. */
      if (record.status !== OPT_STATUS.COMPLETED || !["ready", "stopped-early"].includes(improvementOutcome(record).ending.key)) return;
      const shipped = cur.handoffDrafts?.[record.id] || cur.handoffLandings?.[record.id]
        || prs.some((p) => (p.handoffKey || p.meta?.handoffKey) === record.id && p.status !== "closed");
      if (shipped) return;
      const at = new Date(now).toISOString();
      const out = draftFor(env, cur, record, at);
      cur = { ...cur, ...out.patch };
      patch = { ...(patch || {}), ...out.patch };
      if (out.version && cur.agent) {
        commit({ type: "recordAgentVersion", key: envAgentKey(cur.agent, env.id), version: out.version });
      }
    });
    if (patch) commit({ type: "patchEnvState", envId: env.id, patch });
  });
};

/** Mounted once, inside the store: runs the scheduler on load and every minute. */
export function useScheduledImprovements(state, commit) {
  const ref = useRef(state);
  ref.current = state;
  const hydrated = !!state?.hydrated;
  useEffect(() => {
    if (!hydrated) return undefined;
    const tick = () => {
      try {
        runScheduler(ref.current, commit, Date.now());
      } catch (e) {
        /* A scheduler slip must never take the screen down. */
        // eslint-disable-next-line no-console
        console.warn("[scheduler] skipped a pass", e);
      }
    };
    tick();
    const t = setInterval(tick, MINUTE);
    return () => clearInterval(t);
  }, [hydrated, commit]);
}
