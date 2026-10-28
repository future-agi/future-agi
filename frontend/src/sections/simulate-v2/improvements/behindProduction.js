/*
  An improvement that fell behind production while it waited: another
  change (another fix, an edit outside Future AGI) became the agent's
  production version after this one measured its baseline. Its proof is on
  the old version, so it's re-checked on the new one before it can ship —
  said the same on its page, the Improvements list and the Error Feed.
*/
import { envAgentKey } from "../_mock/agentRegistry";
import { versionNumber } from "../_mock/versions";
import { isGitAgent, versionNameOf } from "../_mock/codeRefs";

/**
 * { since, base, prod, prodLabel, git, recheck } or null.
 *   recheck — { on, runId, at } once it was re-checked on today's version.
 */
export const behindProductionOf = ({ record, env, envState, agents = [] }) => {
  if (!record || !envState?.agent) return null;
  const regAgent =
    agents.find((a) => a.key === envAgentKey(envState.agent, env?.id)) || null;
  const startRun = (envState.runs || []).find((r) => r.id === record.fromRunId);
  const base = record.baseAgentVersion || startRun?.agentVersion || null;
  const prod = regAgent?.production || null;
  if (
    !regAgent ||
    !base ||
    !prod ||
    base === prod ||
    versionNumber(prod) <= versionNumber(base)
  )
    return null;
  const since = (regAgent.versions || []).filter(
    (v) =>
      versionNumber(v.label) > versionNumber(base) &&
      versionNumber(v.label) <= versionNumber(prod) &&
      !v.pendingDraft &&
      v.kind !== "credentials",
  ).length;
  if (!since) return null;
  return {
    since,
    base: versionNameOf(regAgent, base).main,
    prod: versionNameOf(regAgent, prod).main,
    prodLabel: prod,
    git: isGitAgent(regAgent),
    recheck: record.recheck?.on === prod ? record.recheck : null,
  };
};

/** Behind, and not re-checked on today's version yet — Ship waits. */
export const needsRecheck = (args) => {
  const b = behindProductionOf(args);
  return b && !b.recheck ? b : null;
};
