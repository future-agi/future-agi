import PropTypes from "prop-types";
import { useErrorFeedOverview } from "src/api/errorFeed/mockAdapter";
import CopyBriefButton from "src/sections/simulate-v2/components/CopyBriefButton";
import { briefBreakdownOf, codingAgentBrief } from "src/sections/simulate-v2/_mock/codingAgentBrief";
import { codeDiagnosisOf } from "src/sections/simulate-v2/errorFeed/codeDiagnosis";
import { useSimStore } from "src/sections/simulate-v2/store";
import { limitsOf } from "src/sections/simulate-v2/agents/agentLimits";
import { agentDefinitionOf, envAgentKey } from "src/sections/simulate-v2/_mock/agentRegistry";
import { FIX_IN_FLIGHT, envsForAgent, issueFixNav, issueScenarioDrafts, useIssueFix } from "src/sections/simulate-v2/errorFeed/issueFix";
import { budgetsWithin } from "src/sections/simulate-v2/_mock/improvementStart";
import { startImproverSession } from "src/sections/simulate-v2/_mock/integrations";
import useIssueRootCause from "./useIssueRootCause";

/**
 * "Copy for coding agent" on an Error Feed issue — the issue, where in the
 * agent it goes wrong (the fix's diagnosis, else one drafted the same way),
 * the Root cause tab's finding as the symptom, a per-scenario breakdown, a
 * handful of failing traces, and the agent's constraints.
 *
 * Wraps the shared button so the issue page only places it.
 */
export default function IssueBriefButton({ issue, agent, sx }) {
  const { state } = useSimStore();
  const { data: overview } = useErrorFeedOverview(issue?.cluster_id);
  /* The same root cause Fix carries — see useIssueRootCause. */
  const root = useIssueRootCause(issue);
  const { status, gate, start } = useIssueFix(issue);
  /* The fix this agent has on the issue — one written in an environment
     testing another agent (before the project moved) isn't its fix. */
  const ownEs = status?.envId ? state.byEnv[status.envId] : null;
  const own = agent && ownEs?.agent && envAgentKey(ownEs.agent, status.envId) === agent.key ? status : null;

  /*
    Handed to a coding agent (Open in Claude Code, Dispatch to …): its
    attempts need an improvement to land on. A fix already in flight takes
    them; otherwise one starts for the agent — today measured on the main
    environment with the issue's scenarios, no search of ours. Without an
    environment there's nothing to judge on yet, so the brief alone goes.
    Fix's own gate holds it too — an archived, disconnected or Coming soon
    agent, unconfirmed code, or live tests without consent: the brief goes,
    nothing starts.
  */
  const handOff = (coder) => {
    if (!agent || !issue?.cluster_id || gate) return null;
    if (own?.envId && own?.optId && FIX_IN_FLIGHT.has(own.key)) {
      startImproverSession({ agentId: coder.id, envId: own.envId, optId: own.optId, where: "future-agi" });
      const [href, opts] = issueFixNav(own, issue.cluster_id);
      return { href, state: opts?.state, label: "See its attempts on the fix" };
    }
    const env = envsForAgent(agent, state)[0] || null;
    if (!env) return null;
    const es = state.byEnv[env.id];
    const limits = limitsOf(state, agent.key);
    const at = start({
      env, drafts: issueScenarioDrafts(issue, env, es), regressionEnvIds: [env.id],
      budget: budgetsWithin(limits.budget.dollars).at(-1), rootCause: root || null,
      improver: { id: coder.id, label: coder.label },
    });
    const [href, opts] = issueFixNav(at, issue.cluster_id);
    return { href, state: opts?.state, label: "See its attempts" };
  };

  const brief = () => {
    const env = envsForAgent(agent, state)[0] || null;
    const es = env ? state.byEnv[env.id] : null;
    const def = agent && env ? agentDefinitionOf({ agent, env, envState: es }) : null;
    const failed = (overview?.representative_traces || []).filter((t) => t.status === "fail");
    const seen = new Set();
    const evidence = failed
      .filter((t) => !seen.has(t.evidence?.input) && seen.add(t.evidence?.input))
      .map((t) => ({ source: t.trace_id, text: `Caller: "${t.evidence.input}" — Agent: "${t.evidence.output}"` }));
    const fix = root?.fix;
    /* A fix already started carries its own diagnosis and run; else both are drafted. */
    const record = own?.optId
      ? (ownEs?.optimizations || []).find((o) => o.id === own.optId) || null
      : null;
    const diagnosis = record?.snapshot?.diagnosis
      || codeDiagnosisOf({ issue, env, promptText: def?.instructions?.visible ? def.instructions.text : null, rootCause: root });
    const drafts = env ? issueScenarioDrafts(issue, env, es) : [];
    const breakdown = record?.result || record?.reproduce
      ? briefBreakdownOf(record)
      : drafts.map((d) => ({ title: d.title, today: `fails in ${(d.errorFeedIssue?.traceIds || []).length || "its"} production traces` }));

    return codingAgentBrief({
      /* The whole record — the submit step depends on where the agent lives. */
      agent: agent || null,
      env,
      issue: { title: issue?.error?.name, severity: issue?.severity, id: issue?.cluster_id },
      rootCause: root?.headline,
      diagnosis,
      breakdown,
      /* The diagnosis's What to change stands in for a written fix. */
      suggestedFix: fix || (diagnosis ? null
        : issue?.fix_layer ? `No written fix yet — the feed places it in the ${issue.fix_layer} layer.` : null),
      evidence,
      failingScenarios: drafts.map((d) => d.title),
      limits: limitsOf(state, agent?.key),
      model: def?.model?.visible ? def.model.name : null,
      /* Without an environment there is nothing to run a branch against yet —
         the issue's own traces become the scenarios. */
      submit: env ? undefined : `Push a branch, then call the \`submit_candidate\` tool (Future AGI MCP server or Claude Code plugin) with the commit and issue ${issue?.cluster_id}. Future AGI builds scenarios from this issue's traces, runs your branch against the held-back set and computes the verdict — read it with \`get_verdict\`.`,
    });
  };

  /* Every kind of agent works: the brief's submit step says what to send —
     a commit, a settings patch for a hosted agent, a draft version for one
     built here (candidateRoute). */
  return (
    <CopyBriefButton
      getBrief={brief}
      target={issue?.cluster_id ? { kind: "issue", issueId: issue.cluster_id } : undefined}
      onOpened={handOff}
      sx={sx}
    />
  );
}

IssueBriefButton.propTypes = {
  issue: PropTypes.object,
  agent: PropTypes.object,
  sx: PropTypes.object,
};
