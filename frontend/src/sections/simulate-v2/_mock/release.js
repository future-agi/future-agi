/*
  Environment releases (clarified plan, catalogue.md and rl.md).

  Release checks gate Ready: it starts, it resets, the reference solution
  passes, a wrong answer fails and hidden checks don't leak. They're recorded
  against the environment version they ran on; a later version is Checks
  pending until they run again. A ready release can be locked: tasks,
  train/test splits and the scorer freeze. Only a locked release has RL
  access — a split, an API key and sessions metered in credits.
*/
import { currentEnvVersion } from "./versions";

export const RELEASE_CHECKS = [
  { id: "starts", label: "Starts", proves: "A fresh world starts with its seed data and tools." },
  { id: "resets", label: "Resets", proves: "Two sessions in a row start from the same state." },
  { id: "reference", label: "Reference passes", proves: "The reference solution passes every scenario." },
  { id: "wrong", label: "Wrong answer fails", proves: "A deliberately wrong answer fails the checks." },
  { id: "leaks", label: "No leaks", proves: "Hidden checks and reference answers never reach the agent." },
];

const versionOf = (env, envState) => currentEnvVersion(env, envState)?.label || "v1";

/** Where the checks stand on this version: { version, checkedVersion, stale, ready, results }. */
export const checksStateOf = (env, envState) => {
  const version = versionOf(env, envState);
  const rec = envState?.releaseChecks || null;
  const stale = !!rec && rec.version !== version;
  const results = Object.fromEntries(
    RELEASE_CHECKS.map((c) => [c.id, rec && !stale ? rec.results?.[c.id] || "pending" : "pending"]),
  );
  return {
    version,
    checkedVersion: rec?.version || null,
    stale,
    ready: RELEASE_CHECKS.every((c) => results[c.id] === "passed"),
    results,
  };
};

/** The checks run on this version. A world with no scenarios can't prove its reference. */
export const runChecksPatch = (env, envState) => {
  const hasScenarios = (envState?.scenarios || []).some((s) => !s.archived);
  return {
    releaseChecks: {
      version: versionOf(env, envState),
      at: new Date().toISOString(),
      results: Object.fromEntries(
        RELEASE_CHECKS.map((c) => [c.id, c.id === "reference" && !hasScenarios ? "failed" : "passed"]),
      ),
    },
  };
};

/* A small seeded hash, so the same scenarios always land in the same split. */
const hash = (s) => {
  let h = 0;
  for (let i = 0; i < s.length; i += 1) h = ((h << 5) - h + s.charCodeAt(i)) | 0;
  return Math.abs(h);
};

/** Lock: freeze this version, split its scenarios about 80/20 into train and test. */
export const lockPatch = (env, envState) => {
  const ids = (envState?.scenarios || []).filter((s) => !s.archived).map((s) => s.id);
  const test = ids.filter((id) => hash(id) % 5 === 0);
  const train = ids.filter((id) => !test.includes(id));
  if (!test.length && train.length > 1) test.push(train.pop());
  const titleOf = (id) => (envState.scenarios || []).find((s) => s.id === id)?.title || id;
  const now = Date.now();
  /* A trainer already tried it: what the sessions table looks like in use. */
  const rlSessions = [
    ...train.slice(0, 3).map((id, i) => ({
      id: `rl-${id}`,
      task: titleOf(id),
      split: "train",
      status: "Finished",
      reward: [1, 0, 0.5][i],
      credits: 12 + i * 3,
    })),
    ...test.slice(0, 1).map((id) => ({
      id: `rl-${id}`,
      task: titleOf(id),
      split: "test",
      status: "Grading error",
      reward: null,
      rewardNote: "Not scored",
      credits: 9,
    })),
  ];
  return {
    lockedRelease: {
      version: versionOf(env, envState),
      at: new Date(now).toISOString(),
      splits: { train, test },
      rewardVersion: "reward r1",
    },
    rlSessions,
  };
};

/** A new API key for RL access — only its masked form is ever kept. */
export const rlKeyPatch = (envState, split) => {
  const tail = Math.random().toString(36).slice(2, 6);
  return {
    rlKeys: [
      { id: `rlk-${Date.now().toString(36)}`, masked: `fi_rl_••••••••${tail}`, split, createdAt: new Date().toISOString() },
      ...(envState?.rlKeys || []),
    ],
  };
};

/** The trainer's calls, on the futureagi SDK: tasks, a session, a tool call with its reward. */
export const rlCodeOf = (env, envState, split) => {
  const r = envState?.lockedRelease;
  const tool = (env?.tools || [])[0]?.name || "lookup_order";
  return `from futureagi import Client

client = Client()  # FI_API_KEY: your RL access key
envr = client.rl.environment("${env?.id || "<environment-id>"}", release="${r?.version || "v1"}", split="${split}")

for task in envr.tasks():
    session = envr.open_session(task.id)      # a fresh, isolated world
    prompt = session.prompt()                 # instructions and tools
    result = session.call_tool("${tool}", {...})
    print(result.output, result.reward, result.finished)
    session.close()                           # final reward, reward ${r?.rewardVersion?.replace("reward ", "") || "r1"}`;
};
