/*
  The fix-or-policy question, read from the nudges (PRD v7 §4.10.6).

  A new agent version that changes a rule a sub-goal tests raises a rule
  nudge in each environment that uses the agent (nudgeEffects.raiseNudgesIn).
  The question is answered on that nudge card — Policy change / Not intended —
  never by minting an environment version: an agent change never
  moves E. A stricter rule asks nothing (it's labelled a likely fix).

  This module keeps the readers other screens use (Compare asks which agent
  versions still have an open question) and the world-touch reading of a
  repository commit, which now proposes a behaviour nudge instead of saving
  an environment version.
*/
import { displayEnvLabel } from "./envModel";
import { agentVersions, environmentVersions } from "./versions";
import { openNudgesOf } from "./decoupled";

export { touchingChangeOf, proposedWordingOf } from "./nudgeEffects";

/* ── which questions are still open ──────────────────────────────────────── */

/* A new rule nothing tests holds no sub-goal back, so Compare has nothing to wait on for it. */
const openRuleNudges = (envState, agentVersion) => openNudgesOf(envState)
  .filter((n) => n.kind === "rule" && !n.newRule && (!agentVersion || n.agentVersion === agentVersion));

/** Whether the rule question for this version has been answered (or dismissed). */
export const isAnswered = (envState, ruleId, agentVersion) => !openRuleNudges(envState, agentVersion).some((n) => n.ruleId === ruleId);

/* A rule nudge read as the impact shape Compare already renders. */
const asImpact = (n) => ({
  rule: { id: n.ruleId, text: n.rule || n.ruleId },
  subGoals: (n.subGoals || []).map((g) => ({ ...g })),
  scenarios: n.scenarioIds || [],
  nudge: n,
});

/** The open rule questions this version raised in one environment. */
export const openRuleQuestions = (version, env, envState) => {
  if (!version || !env) return [];
  return openRuleNudges(envState, version.label).map(asImpact);
};

/** Open questions across several environments, one per rule per environment. */
export const openQuestionsIn = (state, envs, version) => (envs || []).flatMap((env) => {
  const envState = state?.byEnv?.[env?.id];
  if (!envState) return [];
  return openRuleQuestions(version, env, envState).map((impact) => ({ env, envState, impact }));
});

/**
 * The agent versions in a comparison that still have an open rule question —
 * its sub-goal can't be read until someone answers it on the nudge card.
 */
export const unansweredForRuns = (runs, env, envState) => {
  const labels = [...new Set((runs || []).map((r) => r.agentVersion).filter(Boolean))];
  const known = agentVersions(envState);
  return labels.flatMap((label) => {
    const version = known.find((v) => v.label === label) || { label };
    return openRuleQuestions(version, env, envState).map((impact) => ({ version, impact }));
  });
};

/* ── where a version came from ───────────────────────────────────────────── */

/**
 * Where the version came from, said the way a person would. A provider-side
 * edit is the one nobody in the team may have seen happen.
 */
export const versionOriginOf = (version, source) => {
  if (version?.commit) return `Commit ${version.commit.sha} by ${version.commit.author} — ${version.commit.message}`;
  if (version?.origin === "sync" && source?.kind === "platform") return `Someone edited the assistant on ${source.label}`;
  if (version?.origin === "sync" && (source?.kind === "repo" || source?.kind === "framework")) return `A new commit on ${source.label}`;
  if (version?.origin === "sync") return `Re-read from ${source?.label || "the source"}`;
  if (version?.origin === "upload") return `A new upload${version.bundle ? ` — ${version.bundle}` : ""}`;
  if (version?.origin === "switch") return "The environment was pointed at another agent";
  if (version?.fromRunId || version?.applied?.length) return "Applied from an improvement";
  if (version?.origin === "manual") return "Recorded by hand";
  return "A new agent version";
};

/** An env label as it reads now — plain vN. */
export const envLabelReader = (env, envState) => {
  const versions = environmentVersions(env, envState);
  return (label) => displayEnvLabel(label, versions);
};

/* ── commits that change the world ───────────────────────────────────────── */

/* Paths in a repository that are the world, not the agent (V7). */
const WORLD_PATH = /^(tools|tools-api|db)\/|schema|seed/i;

/**
 * The paths a synced commit touched. Sync reports changes, not files, so a
 * repository agent's are read off the change: its prompt, its config, and
 * for a tool both the agent's wrapper and the handler the world serves.
 */
export const commitPathsOf = (result) => (result?.changes || []).flatMap((c) => {
  if (c.paths) return c.paths;
  if (c.kind === "instructions") return ["agent/prompts/system.md"];
  if (c.kind === "model") return ["agent/config.yaml"];
  if (c.kind === "tool" && c.tool?.name) {
    return [`agent/tools/${c.tool.name}.py`, `tools-api/${c.tool.name}.py`, ...(c.tool.args?.length ? ["db/schema.sql"] : [])];
  }
  return [];
});

/**
 * Whether a synced commit also changed the world — only a repository holds
 * both. Null when it didn't; otherwise the world paths, the env change kinds
 * they amount to, and the tools whose handlers moved. It proposes a nudge;
 * it never saves an environment version on its own.
 */
export const worldTouchOf = (result, source) => {
  if (source?.kind !== "repo" && source?.kind !== "framework") return null;
  const paths = commitPathsOf(result).filter((p) => WORLD_PATH.test(p));
  if (!paths.length) return null;
  const changed = [
    ...(paths.some((p) => /^tools(-api)?\//.test(p)) ? ["contract"] : []),
    ...(paths.some((p) => /^db\/|schema|seed/i.test(p)) ? ["seed"] : []),
  ];
  const tools = (result?.changes || []).filter((c) => c.kind === "tool" && c.tool?.name).map((c) => c.tool.name);
  return { paths, changed, tools };
};
