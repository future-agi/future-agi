/**
 * Versions, and what pairs with what.
 *
 * An environment must serve more than one agent version, and environment
 * creation must not be welded to scenario creation. Both fall out of one rule —
 * scenarios belong to the environment, and a run is a pairing:
 *
 *     run = environment version × agent version
 *
 * So the same proved scenarios can be pointed at v3 of an agent and then at v2,
 * and the difference is attributable to the agent rather than to a scenario
 * someone rewrote in between.
 *
 * Ported from the prototype's versions mock; pure data + helpers.
 */

const iso = (daysAgo) => new Date(Date.now() - daysAgo * 86400000).toISOString();

/**
 * Why an environment gets a new version: the world changed. Seed data, tool
 * handlers, rules. Scenarios carry the version they were proved against, so a
 * world change that invalidates a proof is visible rather than silent.
 */
export const environmentVersions = (env, envState) => {
  // Minted versions win; the seeded three are what an environment starts life
  // with. Both shapes are identical, so nothing downstream can tell which is
  // which — and nothing downstream should care.
  const stored = envState?.envVersions;
  const list = stored?.length
    ? [...stored].reverse()
    : (() => {
        // The seeded history counts against the scenarios this environment
        // actually has, rather than carrying literal counts that disagree with
        // the runs table.
        const now = envState?.scenarios?.length || 0;
        const back = (n) => Math.max(1, Math.round(now * n));
        return [
          {
            id: `${env?.id}-v3`,
            label: "v3",
            createdAt: iso(2),
            note: "Seeded the refusal paths so the scenarios that should be declined have something to be declined against.",
            scenarios: now,
            changed: ["seed", "checks"],
          },
          {
            id: `${env?.id}-v2`,
            label: "v2",
            createdAt: iso(11),
            note: "Moved a rule out of the prompt and into the world, so breaking it now fails rather than reads badly.",
            scenarios: back(0.78),
            changed: ["rules"],
          },
          {
            id: `${env?.id}-v1`,
            label: "v1",
            createdAt: iso(24),
            note: "First build, read from the agent source.",
            scenarios: back(0.55),
            changed: ["contract", "seed"],
          },
        ];
      })();

  // Stamp `.current` based on the active version pointer, so the version picker
  // and the Settings list agree on which one is live. Without a pin, the newest
  // is current.
  const active = envState?.activeEnvVersion || list[0]?.label;
  return list.map((v) => ({ ...v, current: v.label === active }));
};

/** The number in a version label — what phrasing and pinning key off. */
export const versionNumber = (label) =>
  parseInt(String(label || "").replace(/\D/g, ""), 10) || 1;

/**
 * The next environment version, given what already exists. The count is read
 * off the current history (seeded three, or whatever has been minted) so a
 * fresh env re-derives to v4 rather than colliding on v1.
 */
export const nextEnvVersion = (env, envState, { changed = [], note, now } = {}) => {
  const list = environmentVersions(env, envState);
  const n = list.length + 1;
  return {
    id: `${env?.id}-v${n}`,
    label: `v${n}`,
    createdAt: now || new Date().toISOString(),
    note: note || "World changed.",
    scenarios: envState?.scenarios?.length || 0,
    changed,
    minted: true,
  };
};

/**
 * Versions of the thing under test. These are the customer's, not ours — we
 * drive whichever one they point us at and never host it, so a version here is
 * a label plus how to reach it. A version is minted when the agent changes, and
 * a run pins whichever one was current when it started.
 */
const firstAgentVersion = () => ({
  id: "agent-v1",
  label: "v1",
  note: "First version connected to this environment.",
  reach: "endpoint",
  createdAt: new Date().toISOString(),
});

/** Every agent version this environment knows about, oldest first. */
export const agentVersions = (envState) => {
  const stored = envState?.agentVersions;
  return stored?.length ? stored : [firstAgentVersion()];
};

/** The one the next run will use — the active pin if set, else the newest. */
export const currentAgentVersion = (envState) => {
  const list = agentVersions(envState);
  const activeLabel = envState?.activeAgentVersion;
  if (activeLabel) return list.find((v) => v.label === activeLabel) || list[list.length - 1];
  return list[list.length - 1];
};

/**
 * The next agent version.
 *
 *   applied         — the code changes bundled into this version, each with its
 *                     filepath and diff. Empty for versions minted by
 *                     "connect to a different endpoint".
 *   basedOnVersion  — the label of the version these changes were applied on top
 *                     of. Usually the previous one, but pinning it makes
 *                     branching possible.
 *   fromRunId       — the run whose diagnosis produced these changes, so the
 *                     evidence lives beside the outcome.
 */
export const nextAgentVersion = (
  envState,
  { note, reach = "endpoint", now, applied = [], fromRunId = null, basedOnVersion = null } = {},
) => {
  const list = agentVersions(envState);
  const n = list.length + 1;
  return {
    id: `agent-v${n}`,
    label: `v${n}`,
    note: note || "Modified between runs.",
    reach,
    createdAt: now || new Date().toISOString(),
    applied,
    basedOnVersion: basedOnVersion || list[list.length - 1]?.label || "v1",
    fromRunId,
  };
};

/** Newest first, with each version's run history attached — for list UIs. */
export const agentVersionsWithRuns = (envState) => {
  const runs = envState?.runs || [];
  return [...agentVersions(envState)].reverse().map((v, i) => ({
    ...v,
    current: i === 0,
    runs: runs.filter((r) => r.agentVersion === v.label).length,
  }));
};

/**
 * The env version the workspace is currently working off.
 *
 * `envState.activeEnvVersion` pins whichever version the user chose to work
 * from. If nothing is pinned the newest is treated as active. Runs stamp
 * whichever version was active when they started.
 */
export const currentEnvVersion = (env, envState) => {
  const list = environmentVersions(env, envState);
  const pinned = envState?.activeEnvVersion;
  const explicit = pinned ? list.find((v) => v.label === pinned) : null;
  return explicit || list.find((v) => v.current) || list[0];
};
