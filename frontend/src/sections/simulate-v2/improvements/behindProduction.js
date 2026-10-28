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
import { hashSeed } from "../_mock/runStream";

/* The files the kept change edits — what a rebase onto newer commits can
   conflict in. */
const changedPathsOf = (record) => {
  const r = record?.result;
  const n = record?.preferredTrial ?? r?.winner?.n;
  const t = (r?.trials || []).find((x) => x.n === n);
  return [
    ...new Set(
      (r?.pool || [])
        .filter((p) => t?.picks?.includes(p.id))
        .map((p) => p.path)
        .filter(Boolean)
        /* Repo-relative, as people read it — not the environment's prefix. */
        .map((path) => path.replace(/^env-[^/]+\//, "")),
    ),
  ];
};

/**
 * { since, base, prod, prodLabel, git, recheck, conflict } or null.
 *   recheck — { on, runId, at } once it was re-checked on today's version.
 *   conflict — repo agents: the commits since touched a file the patch
 *   edits, so it's rebased by a person before the re-check:
 *   { path, resolved } (resolved once the rebase onto this commit is accepted).
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
  const git = isGitAgent(regAgent);
  /* Whether newer commits touched the same lines — decided per record and
     commit, so the demo tells the same story every time. */
  const paths = git ? changedPathsOf(record) : [];
  const conflict =
    paths.length && hashSeed(`${record.id}:${prod}:rebase`) % 2 === 0
      ? { path: paths[0], resolved: record.rebase?.onto === prod }
      : null;
  return {
    since,
    base: versionNameOf(regAgent, base).main,
    prod: versionNameOf(regAgent, prod).main,
    prodLabel: prod,
    git,
    recheck: record.recheck?.on === prod ? record.recheck : null,
    conflict,
  };
};

/** Behind, and not re-checked on today's version yet — Ship waits. */
export const needsRecheck = (args) => {
  const b = behindProductionOf(args);
  return b && !b.recheck ? b : null;
};
