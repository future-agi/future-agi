/*
  Fix on a run's issue (PRD v6, Story 20 AC-20.15, Story 11.5) — the same
  improvement flow the Error Feed's Fix starts, scoped to one issue on one
  run instead of one production cluster.

  It needs trace evidence. A hosted provider agent (Vapi, Retell…) reports
  every call, so its runs are traced as they happen. A code agent is traced
  only once traceAI is instrumented in it; without that the run can still be
  read, but there is nothing for the fix loop to stand on.

  Additive to the Error Feed: nothing here changes how its Fix behaves.
*/
import {
  agentSourceOf,
  envAgentKey,
  PROVIDER_LABELS,
} from "../_mock/agentRegistry";
import { observabilityOf } from "../_mock/observability";
import { buildImprovement, budgetsWithin } from "../_mock/improvementStart";
import { limitsOf } from "../agents/agentLimits";
import { issueRootFor } from "../_mock/runInsights";

export const TRACEAI_DOCS = "https://docs.futureagi.com/docs/observe";
export const NO_TRACE_REASON =
  "traceAI isn't instrumented in this agent. You can still run and read results, but the fix loop needs trace evidence.";

/* Providers that report every call on their own. */
const HOSTED = ["vapi", "retell", "bland", "elevenlabs", "livekit"];

/**
 * Whether this run's agent is traced, and if not, why.
 * { traced, mode: hosted|traceai|sdk|null, note?, reason?, docs? }
 */
export const runTracingOf = (agent, integrations, envId) => {
  if (!agent) {
    return {
      traced: false,
      mode: null,
      reason:
        "No agent is connected to this environment, so there is nothing to fix against.",
      docs: null,
    };
  }
  const keyed = agent.key
    ? agent
    : { ...agent, key: envAgentKey(agent, envId) };
  const provider = agent.values?.provider;
  const source = agentSourceOf(keyed);
  if (
    HOSTED.includes(provider) ||
    (source.kind === "platform" && provider && provider !== "other")
  ) {
    return {
      traced: true,
      mode: "hosted",
      note: `${PROVIDER_LABELS[provider] || source.label} reports every call, so this run is traced.`,
    };
  }
  const o = observabilityOf(keyed, integrations);
  if (o.on)
    return {
      traced: true,
      mode: o.mode,
      note: "traceAI is instrumented in this agent, so this run is traced.",
    };
  return {
    traced: false,
    mode: o.mode,
    reason: NO_TRACE_REASON,
    docs: TRACEAI_DOCS,
  };
};

/**
 * The improvement record a run issue's Fix starts — the run's own scenarios,
 * with the issue's tagged as what the fix must fix (the search's focus, the
 * same way an Error Feed issue's reproduced scenarios are), every other
 * scenario as what it must not break. Opens at the improvement view, and
 * hands off as a draft PR or a draft provider version like any other.
 */
export const runIssueImprovement = ({ env, es, state, run, tasks, issue }) => {
  const agentKey = envAgentKey(es?.agent, env.id);
  const limits = limitsOf(state, agentKey);
  const budgets = budgetsWithin(limits.budget.dollars);
  const focus = new Set(issue.memberIds);
  const scoped = (tasks || []).map((t) =>
    focus.has(t.id) ? { ...t, fromIssue: issue.id } : t,
  );
  return buildImprovement({
    env,
    es,
    tasks: scoped,
    seed: `run-issue-${issue.id}-${(es?.optimizations || []).length}`,
    fromRunId: run?.id || null,
    baseAgentVersion: run?.agentVersion || null,
    trigger: {
      kind: "run",
      goal: {
        id: `issue:${issue.id}`,
        label: `the pass rate on "${issue.title}"`,
      },
      issueId: issue.id,
      issueTitle: issue.title,
      severity: issue.severity,
      source: "run-insights",
      /* The issue's root and fix — the search writes its first candidates
         from these, so it searches on the issue, not the whole run. */
      issueRoot: issueRootFor(issue),
    },
    limits,
    budget: budgets[budgets.length - 1],
    regressionEnvIds: [env.id],
    /* Re-read the agent as it starts, so the baseline matches production. */
    state,
    extra: {
      diagnosis: [
        {
          id: issue.id,
          label: issue.title,
          headline: issue.headline.join(" · "),
          severity: issue.severity,
        },
      ],
      focus: {
        issueId: issue.id,
        scenarioIds: issue.memberIds,
        rootCause: issue.root.label,
        fix: issue.fix.text,
      },
    },
  });
};
