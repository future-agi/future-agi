/*
  What the issue page's header says about the fix action (an epoch scoped
  to this issue), worked out once for the header's primary button and the
  one strip under it:

    prereq   — what's missing before an epoch can run on this issue:
               "no-agent"  no agent is connected at all → Connect agent
               "unlinked"  agents exist, none linked to the issue's project → Link agent
    version  — the traces came from another version than the agent runs now:
               a quiet note, the action still works
    ended    — the issue's last epoch ended without a fix and nothing is in
               flight: stopped (couldn't run — Retry it), stopped by a person,
               or ended without a safe change

  Demo knobs (query string on the issue page, view only — nothing is saved):
    ?prereq=none      as if no agent were connected
    ?prereq=unlinked  as if no agent were linked to this project
    ?traces=v3        as if the traces were recorded on v3
    ?epoch=stopped    as if the issue's last epoch had stopped (couldn't run)
*/
import { useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import {
  OPT_STATUS,
  improvementTotalSpend,
} from "../_mock/optimizationRuns";
import { creditsLabel } from "../_mock/metering";
import { improvementRunName } from "../_mock/optimizer";
import {
  envsForAgent,
  fixEnvFor,
  issueProjectLinkOf,
  issueTraceVersionOf,
  productionVersionOf,
  useIssueFix,
} from "./issueFix";

/* Ended without a fix: the header offers Fix again (or Retry). */
const ENDED = new Set([
  "no-fix",
  "not-reproduced",
  "over-budget",
  "stopped",
]);

/** The page's demo knobs — see the note at the top. */
export const useIssueKnobs = () => {
  const [params] = useSearchParams();
  return {
    prereq: params.get("prereq"),
    traces: params.get("traces"),
    epoch: params.get("epoch"),
  };
};

const epochNumberOf = (name) => {
  const m = /(\d+)\s*$/.exec(String(name || ""));
  return m ? m[1] : null;
};

export function useIssueFixHeader(issue) {
  const fix = useIssueFix(issue);
  const { state, agent, agents, status, gate } = fix;
  const knobs = useIssueKnobs();

  return useMemo(() => {
    const link = issueProjectLinkOf(issue, state, agents);
    const anyAgent =
      knobs.prereq !== "none" && agents.some((a) => !a?.deleted?.at);
    const prereq =
      knobs.prereq === "none" || (!agent && !anyAgent)
        ? "no-agent"
        : knobs.prereq === "unlinked" || !agent
          ? "unlinked"
          : null;

    const envs = agent && !prereq ? envsForAgent(agent, state) : [];
    const main = fixEnvFor(envs, issue);
    const mainState = main ? state.byEnv[main.id] : null;

    /* The version the epoch starts from: production, else the current one.
       (No issue passed, so the traces' own version isn't used as a stand-in.) */
    const now =
      agent && !prereq
        ? productionVersionOf(agent, state, null, mainState).label
        : null;
    const traced = knobs.traces || issueTraceVersionOf(issue);
    const version =
      now && traced && traced !== now ? { traced, now } : null;

    /* The last epoch, when it ended without a fix. */
    let ended = null;
    const demoStopped = knobs.epoch === "stopped" && !prereq;
    if (!prereq && (demoStopped || (status && ENDED.has(status.key)))) {
      const envId = status?.envId || main?.id || null;
      const env = state.myEnvironments.find((e) => e.id === envId) || null;
      const es = envId ? state.byEnv[envId] : null;
      const record = status?.optId
        ? (es?.optimizations || []).find((o) => o.id === status.optId) || null
        : null;
      /* The epoch's name as its own page says it (older records were
         saved as "Improvement N"). */
      const name = record
        ? improvementRunName(es, record)
        : `Epoch ${(es?.optimizations || []).length || 1}`;
      const usd = record ? improvementTotalSpend(record, env?.surface) : 0;
      const failed = demoStopped
        ? { cause: "ours", canRetry: true }
        : status.failed || null;
      const kind = failed
        ? failed.cause === "yours"
          ? "blocked"
          : "stopped"
        : status.key === "stopped" ||
            record?.status === OPT_STATUS.STOPPED
          ? "stopped-by"
          : "ended";
      ended = {
        kind,
        demo: demoStopped,
        name,
        number: epochNumberOf(name),
        canRetry: !!failed?.canRetry && kind === "stopped",
        label: status?.label || "Stopped",
        detail: status?.detail || null,
        env,
        envId,
        optId: record?.id || null,
        spent: usd ? creditsLabel(usd) : null,
        stoppedBy: record?.stoppedBy?.by || null,
        reason: record?.infra?.reason || null,
      };
    }

    /* A disconnected or archived agent holds the action: said under the
       header with the way out, not just on hover. Consent and Check what
       we found aren't holds — the action asks for them itself. */
    const hold =
      !prereq && gate && !["consent", "found"].includes(gate.key)
        ? gate
        : null;

    return { link, prereq, version, ended, hold, main, agent, status };
  }, [issue, state, agents, agent, status, gate, knobs.prereq, knobs.traces, knobs.epoch]);
}

/** The issue's epoch as its own page names it ("Epoch 6"), or null. */
export const epochNameOf = (state, status) => {
  if (!status?.envId || !status?.optId) return null;
  const es = state?.byEnv?.[status.envId];
  const record = (es?.optimizations || []).find((o) => o.id === status.optId);
  return record ? improvementRunName(es, record) : null;
};
