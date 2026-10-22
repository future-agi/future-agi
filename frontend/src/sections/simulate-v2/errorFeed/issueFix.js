/*
  Error Feed → Fix. Everything the Fix button needs, mocked.

  A Fix starts an improvement run scoped to one issue: scenarios are built
  from the issue's traces and added to the agent's environment, the failure
  is reproduced first, then the usual search runs. The issue's chip on the
  Error Feed reads its state back from that improvement run, its hand-off
  and the environment's releases — there is no separate "fix" record to
  drift out of step.

  The pure parts live in issueFixModel.js (the seed builds its fixes from
  them); this file adds the hook and re-exports the rest.
*/
import { useCallback, useMemo } from "react";
import { useSimStore, useConnectedAgents } from "../store";
import { limitsOf } from "../agents/agentLimits";
import { startImproverSession, useIntegrations } from "../_mock/integrations";
import { asCoderImprovement } from "../_mock/improverAttempts";
import { rereadFor } from "../_mock/improvementPolicy";
import { currentUser } from "../_mock/scenarioProvenance";
import {
  agentKeyForIssue, envsForAgent, evidenceGateOf, fixGateOf, fixStatusOf, issueFixRecord, issueProjectOf, todayRunFor,
} from "./issueFixModel";

export * from "./issueFixModel";

/** The issue's fix state plus the actions the Error Feed needs. */
export function useIssueFix(issue) {
  const { state, commit } = useSimStore();
  const { agents } = useConnectedAgents();
  const { prs } = useIntegrations();
  const issueId = issue?.cluster_id;
  const agentKey = agentKeyForIssue(issue, state, agents);
  const agent = useMemo(() => agents.find((a) => a.key === agentKey) || null, [agents, agentKey]);
  const status = useMemo(() => (issueId ? fixStatusOf(issue, state, prs || []) : null), [issue, issueId, state, prs]);

  /* The issue's Observe project — its agent was set when the project was. */
  const project = issueProjectOf(issue);

  /*
    Start: today is measured first — the environment's scenarios on the
    production version, recorded as a run — and the improvement is written
    against that run, on its scenarios plus the issue's. The issue's scenarios — the ones
    picked on the Fix sheet — are added to the agent's main environment as
    it starts (RSI PRD: the fix runs in the main environment; new scenarios
    make a new version), so the failure is tested where its must-pass set is
    the regression check, and stays covered after the fix ships. It
    re-reads the agent first — a change made outside Future AGI is saved as a
    version and becomes the baseline — then reproduces; when the failure
    doesn't happen, the run stops there and keeps the search it would have
    run for "Continue anyway".
  */
  /* Fix runs Future AGI's engine. Handing the issue to the customer's own
     coding agent (Open in Claude Code, Dispatch to …) starts the same
     improvement with `improver` set: today is measured and the scenarios
     frozen, but the agent writes the candidates and we only judge them
     (RSI PRD, Bring your own improver). */
  const start = useCallback(({ env, drafts, regressionEnvIds, budget, minutes, textSearch, targetMember, rootCause, lineage, improver = null }) => {
    const es = state.byEnv[env.id];
    const reread = rereadFor(agent, state);
    if (reread?.changed && reread.versionRecord && agentKey) {
      commit({ type: "recordAgentVersion", key: agentKey, version: reread.versionRecord, activateEnvId: env.id });
    }
    /* The time budget the sheet set caps the search the same way dollars do. */
    const limits = limitsOf(state, agentKey);
    /* Measure today: the environment's scenarios on the production version,
       recorded as a run of it — the baseline and must-pass set come from it. */
    const today = todayRunFor(env, es, issue.cluster_id);
    if (today) commit({ type: "recordRun", envId: env.id, run: today });
    const esNow = today ? { ...es, runs: [today, ...(es?.runs || [])] } : es;
    const built = issueFixRecord({
      issue, env, es: esNow, drafts, budget, regressionEnvIds, state, reread, textSearch, targetMember, rootCause, lineage, today,
      limits: minutes ? { ...limits, budget: { ...limits.budget, minutes } } : limits,
    });
    const record = improver ? asCoderImprovement(built, improver) : built;
    /* The agent's submissions land on this improvement as its attempts. */
    if (improver) startImproverSession({ agentId: improver.id, envId: env.id, optId: record.id, where: "future-agi" });
    /* Saved now — Start fix leaves the Error Feed straight after. The
       issue's scenarios belong to this improvement only (RSI PRD: "They join
       the environment only if the user adds them, or through regression
       memory") — the improvement page and Scenarios tab offer to add them. */
    const optimizations = [...(es?.optimizations || []), record];
    commit({ type: "patchEnvState", envId: env.id, patch: { optimizations } });
    return { envId: env.id, runId: record.fromRunId, optId: record.id };
  }, [state, commit, issue, agentKey, agent]);

  /* Why Fix can't start, if it can't — said on the button and the sheet. */
  const gate = useMemo(() => (agent ? fixGateOf(agent, envsForAgent(agent, state)[0]) : null), [agent, state]);
  /* How sure the feed is — a note on the sheet, never a gate. */
  const evidence = useMemo(() => evidenceGateOf(issue), [issue]);

  /* A person reopening (or taking back a reopen of) the shipped fix, from the
     issue's triage status. Saved with commit: the Error Feed and the
     improvement page are different stores. */
  const setReopened = useCallback((on) => {
    if (!status?.envId || !status?.optId) return;
    const es = state.byEnv[status.envId];
    const optimizations = (es?.optimizations || []).map((o) => {
      if (o.id !== status.optId) return o;
      if (on) return { ...o, reopenedBy: { by: currentUser().name, at: new Date().toISOString() } };
      const { reopenedBy: _drop, ...rest } = o;
      return rest;
    });
    commit({ type: "patchEnvState", envId: status.envId, patch: { optimizations } });
  }, [status, state, commit]);

  return { state, agent, agentKey, agents, project, status, gate, evidence, start, setReopened };
}
