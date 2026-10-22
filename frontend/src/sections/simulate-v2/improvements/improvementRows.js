/**
 * Improvements as rows — one per improvement run, not per environment.
 *
 * Records live on the environment that scored them (`byEnv[envId].optimizations`);
 * this flattens them into what the Improvements list and an agent's
 * Improvements tab both read, so the two can't drift.
 */
import { envAgentKey, repairAgentRecord } from "../_mock/agentRegistry";
import { improvementOutcome, improvementTotalSpend, searchMinutes, searchSpend, REPEATS, improvementPhaseOf } from "../_mock/optimizationRuns";
import { attemptsOf, coderStatusOf } from "../_mock/improverAttempts";
import { improverSessionFor } from "../_mock/integrations";
import { prOfImprovement } from "../_mock/improvementPr";
import { improvementRunName, depthOf } from "../_mock/optimizer";
import { creditsForUsd } from "../_mock/metering";
import { runIdentityOf } from "../_mock/runIdentity";
import { snapshotOfImprovement } from "../_mock/envSnapshot";
import { shippedStateOf } from "../errorFeed/issueFix";
import { improvementPath } from "./improvementNav";

/**
 * "agent v3 × env v7" for an improvement — the agent version it improved and
 * the environment version it was frozen on, named the way every run is.
 */
export const improvementIdentityOf = (record, env, es) => {
  if (!record) return null;
  const fromRun = (es?.runs || []).find((r) => r.id === record.fromRunId) || null;
  const agentVersion = record.baseAgentVersion || fromRun?.agentVersion || null;
  const envVersion = fromRun?.envVersion || record.frozen?.compat?.envVersion || null;
  if (!agentVersion && !envVersion && !env) return null;
  /* The environment version it was frozen on — stored on new records, read
     from the pins it froze on older ones. */
  const envSnapshot = env ? snapshotOfImprovement(record, env, es)?.label || null : null;
  return runIdentityOf({ agentVersion, envVersion, envSnapshot, envId: env?.id }, es, env).label;
};

/*
  What a shipped improvement became, in the Error Feed's own words
  (`shippedStateOf`, the same reading the issue's fix chip uses): In review
  (PR #n, a Vapi copy, a Retell draft, a draft version) → Watching
  (n days left) → Resolved, or Reopened. A goal improvement is watched the
  same way; only an issue fix can come back as its issue.
*/
/* The run's status once its change has left Future AGI. */
const SHIPPED_STATUS = {
  draft: { key: "shipped", label: "In review" },
  pr: { key: "shipped", label: "In review" },
  version: { key: "shipped", label: "In review" },
  watching: { key: "released", label: "Watching" },
  resolved: { key: "released", label: "Resolved" },
  reopened: { key: "failed", label: "Reopened" },
};

const lifecycleOf = (record, env, es, prs, now) => {
  /* shippedStateOf reads the issue's recurrence itself — the Error Feed and
     this list always agree. */
  let st = null;
  try { st = shippedStateOf(record, es, prs, { now, envId: env?.id }); } catch (e) { st = null; }
  if (!st) return null;
  const landed = es?.handoffLandings?.[record.id] || null;
  const pr = prOfImprovement(prs, record.id, env?.id, { open: true });
  const kind = st.key === "review" ? (landed ? "version" : pr ? "pr" : "draft") : st.key;
  return {
    kind,
    /* Verbatim: "Fix in review · PR #12", "Watching · 23 days left". */
    label: st.key === "review" ? st.label : `${st.label}${st.detail ? ` · ${st.detail}` : ""}`,
    version: st.version || landed,
    ...(pr && { merged: pr.status === "merged" }),
  };
};

/*
  Where an improvement came from — the product area that started it:
  Observe (an Error Feed issue from production traces), Simulation (a run,
  a goal, a schedule or by hand) or Dataset (a dataset optimization).
*/
const SOURCE_OF = { "error-feed": "Observe", dataset: "Dataset" };

/** What started the run, in a few words, and the area it came from. */
export const triggerOf = (trigger) => ({ ...triggerOnly(trigger), source: SOURCE_OF[trigger?.kind] || "Simulation" });

const triggerOnly = (trigger) => {
  if (trigger?.kind === "error-feed") {
    return { kind: "error-feed", group: "Error Feed", label: `Error Feed · ${trigger.issueId || "issue"}` };
  }
  if (trigger?.kind === "goal") {
    return { kind: "goal", group: "Goal", label: `Goal · ${trigger.goal?.label || "Pass rate"}` };
  }
  /* A Fix on one issue in a run's Insights tab. */
  if (trigger?.kind === "run" && trigger.source === "run-insights") {
    return { kind: "run", group: "Run insights", label: `Insights · ${trigger.issueTitle || trigger.issueId || "issue"}` };
  }
  if (trigger?.kind === "run") return { kind: "run", group: "Debug failures", label: "Debug failures" };
  /* Started on its own, by the agent's automatic-improvement schedule. */
  if (trigger?.kind === "schedule") {
    return { kind: "schedule", group: "Scheduled", label: "Scheduled · weekly" };
  }
  /* Run now on the agent's Constraints tab: a person started it. */
  return { kind: "manual", group: "Manual", label: trigger?.by ? `Started by ${trigger.by}` : "Manual" };
};

/*
  The held-back set in short: "Fixed 3 of 4 held back · broke 0". A run
  that kept no change says so — never the rejected run's gains — and a run
  chosen over the search's pick shows its projected figure until verified.
*/
export const heldShortOf = (outcome) => {
  const h = outcome?.held;
  if (!h) return null;
  if (outcome.ending?.key === "no-fix") return { text: "No change kept", broke: null };
  if (outcome.chosen) {
    return { text: `${outcome.chosen.projected ? "≈" : ""}${outcome.chosen.held}% held back`, broke: null, note: outcome.chosen.verified ? `Run ${outcome.chosen.n}, your pick` : `Run ${outcome.chosen.n} · not verified` };
  }
  if (outcome.thin) return { text: "Too few to hold back", broke: null };
  return {
    text: h.failingBefore ? `Fixed ${h.fixed} of ${h.failingBefore} held back` : "Held back already passing",
    broke: h.broke,
  };
};

/*
  Which agent a record improved. An environment that changed agents keeps its
  older improvement runs, and those belong to the agent it tested then — the
  first one replaced after the run was created.
*/
const agentKeyAt = (es, envId, createdAt, agents) => {
  const at = Date.parse(createdAt || 0) || 0;
  const earlier = [...(es?.previousAgents || [])]
    .filter((p) => p.key && (Date.parse(p.replacedAt || 0) || 0) > at)
    .sort((a, b) => Date.parse(a.replacedAt) - Date.parse(b.replacedAt))[0];
  if (earlier) return earlier.key;
  /* The connected agent testing it now — keyed the way the Agents page keys it. */
  const now = agents.find((a) => (a.usedIn || []).some((u) => u.envId === envId));
  return now?.key || (es?.agent ? envAgentKey(repairAgentRecord(es.agent, `env:${envId}`), envId) : null);
};

/** Every improvement run in the workspace, newest first. */
export const improvementRowsOf = (args) => improvementRowsRaw(args);

const improvementRowsRaw = ({ state, agents = [], prs = [], now = Date.now() }) => {
  const byKey = new Map(agents.map((a) => [a.key, a]));
  const envName = (id) => (state?.myEnvironments || []).find((e) => e.id === id)?.name;
  return (state?.myEnvironments || []).flatMap((env) => {
    const es = state?.byEnv?.[env.id];
    return (es?.optimizations || []).map((record) => {
      const outcome = improvementOutcome(record);
      const agentKey = agentKeyAt(es, env.id, record.createdAt, agents);
      const agent = byKey.get(agentKey);
      const trigger = triggerOf(record.trigger);
      /* The other environments it was proven in, besides the one it ran in. */
      const others = (record.regressionEnvIds || []).filter((id) => id !== env.id);
      const landed = es?.handoffLandings?.[record.id];
      const running = outcome.ending.key === "running";
      /*
        Everything it spent: reproducing, the search, and any coding
        agent's attempts — the figure the improvement page shows too. One
        that stopped before searching still spent what measuring it cost.
      */
      const session = improverSessionFor(env.id, record.id);
      const attempts = session && record.result ? attemptsOf(record, session, { now, surface: env.surface, budgetLeft: (record.budget || Infinity) - searchSpend(record.result, env.surface) }) : [];
      const spend = running ? null : improvementTotalSpend(record, env.surface, { attempts });
      /* Fix to proposal, from what the run had to simulate — reproducing,
         every trial, the held-back check. */
      const r = record.result;
      const first = (record.scenarioIds || []).length * REPEATS;
      const minutes = r && !running
        ? record.fixDepth === "suggestion"
          ? Math.round(8 + ((r.trainMeasured + r.heldMeasured) * REPEATS + first) / (env.surface === "voice" ? 40 : 120))
          : searchMinutes(r, env.surface, { firstConversations: first })
        : null;
      const release = landed ? (es?.releases || []).find((x) => x.version === landed) : null;
      /* Running reads as where it is: Reproducing, Searching, Verifying. */
      const phase = running ? improvementPhaseOf(record, now) : null;
      const lifecycle = lifecycleOf(record, env, es, prs, now);
      /* A coding agent's improvement reads its session: fixing, accepted, or out of attempts. */
      const coder = record.improver ? coderStatusOf(record, env.id, { now, surface: env.surface }) : null;
      /* Prompt or code — what time to proposal is measured against. */
      const changeKind = r?.winner ? (depthOf(r.winner.proposals || []) >= 3 ? "code" : "prompt") : null;
      return {
        id: `${env.id}::${record.id}`,
        envId: env.id,
        recordId: record.id,
        fromRunId: record.fromRunId || null,
        name: improvementRunName(es, record),
        envName: env.name,
        otherEnvNames: others.map(envName).filter(Boolean),
        agentKey,
        agentName: agent?.name || es?.agent?.name || `${env.name} agent`,
        trigger,
        /* Once its change ships, the run's status follows it — never "Ready
           for review" beside a released change. */
        status: phase ? { key: "running", label: phase.label, phase: phase.key } : (lifecycle && SHIPPED_STATUS[lifecycle.kind]) || (coder && { key: coder.key, label: coder.label }) || outcome.ending,
        headline: coder?.headline || outcome.headline || "",
        /* A coding agent's improvement: what its attempts came to, in the Result column. */
        held: coder
          ? {
            text: coder.attempt ? `Accepted · attempt ${coder.attempt.n}` : coder.key === "no-fix" ? "No attempt accepted" : `${coder.attempts.length} ${coder.attempts.length === 1 ? "attempt" : "attempts"} so far`,
            broke: null, note: `by ${coder.improver}`,
          }
          : running ? null : heldShortOf(outcome),
        /* Who wrote the change — a coding agent's improvement says so. */
        improver: coder?.improver || null,
        output: lifecycle,
        identity: improvementIdentityOf(record, env, es),
        href: improvementPath(env.id, record.id),
        changeKind,
        scenarios: r ? r.trainMeasured + r.heldMeasured + (r.mustPassMeasured || 0) : null,
        /* What the run used, in the workspace's unit — AI credits. */
        credits: spend != null ? creditsForUsd(spend) : null,
        minutes,
        issueId: record.trigger?.issueId || null,
        releasedAt: release?.at || null,
        createdAt: record.createdAt || null,
      };
    });
  }).sort((a, b) => (Date.parse(b.createdAt || 0) || 0) - (Date.parse(a.createdAt || 0) || 0));
};
