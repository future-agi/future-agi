/**
 * Versions, and what pairs with what.
 *
 * The product goals ask for two things that pull in the same direction: an
 * environment must serve more than one agent version (G1), and environment
 * creation must not be welded to scenario creation (G7). Both fall out of one
 * rule — scenarios belong to the environment, and a run is a pairing:
 *
 *     run = environment version × agent version
 *
 * So the same proved scenarios can be pointed at v3 of an agent and then at
 * v2, and the difference is attributable to the agent rather than to a
 * scenario someone rewrote in between.
 */

const iso = (daysAgo) => new Date(Date.now() - daysAgo * 86400000).toISOString();

/* ── environment versions ────────────────────────────────────────────────── */

/**
 * Why an environment gets a new version: the world changed. Seed data, tool
 * handlers, rules. Scenarios carry the version they were proved against, so a
 * world change that invalidates a proof is visible rather than silent.
 */
export const environmentVersions = (env, envState) => {
  /* Minted versions win; the seeded three are what an environment starts life
     with. Both shapes are identical, so nothing downstream can tell which is
     which — and nothing downstream should care. */
  const stored = envState?.envVersions;
  const list = stored?.length ? [...stored].reverse() : (() => {
    /*
      The seeded history counts against the scenarios this environment
      actually has. It used to carry 32 / 28 / 21 as literals, which put
      "32 scenarios" in the version bar above a runs table that never
      showed more than nine.
    */
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

  /*
    Stamp `.current` based on the active version pointer, so the version
    picker and the Settings list agree on which one is live. Without a
    pin, the newest is current — same behaviour as before switching
    existed.
  */
  const active = envState?.activeEnvVersion || list[0]?.label;
  return list.map((v) => ({ ...v, current: v.label === active }));
};

/**
 * What changing the world can consist of.
 *
 * Named rather than free-text because the *kind* of change decides which
 * proofs survive it: reseeding can strip the state a scenario presumes, and
 * rewriting checks can turn a check that used to fail on an empty run into one
 * that passes. A rules change does neither — it changes how a run is judged,
 * not whether it can be staged.
 */
export const ENV_CHANGES = [
  { id: "seed", label: "Reseeded the world", invalidates: true, blurb: "New or regenerated data — the state a scenario presumes may be gone." },
  { id: "checks", label: "Rewrote the checks", invalidates: true, blurb: "A check proved to fail on an empty run may now pass." },
  { id: "contract", label: "Tools changed", invalidates: true, blurb: "The reference solution may no longer run." },
  { id: "rules", label: "Rules changed", invalidates: false, blurb: "Graded differently; every scenario can still be staged." },
];

/** The next environment version, given what already exists. */
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
    /* Minted after the scenarios existed. Marked, because a proof cannot be
       retroactively made against a version that did not exist when it was
       proved — see provedAgainst in proofs.js. */
    minted: true,
  };
};

/* ── agent versions ──────────────────────────────────────────────────────── */

/**
 * Versions of the thing under test. These are the customer's, not ours — we
 * drive whichever one they point us at and never host it (an explicit
 * non-goal), so a version here is a label plus how to reach it.
 *
 * A version is minted when the agent changes, and a run pins whichever one was
 * current when it started. It is emphatically *not* derived from the run count:
 * that made a second run of an unchanged agent claim to be a new version, and
 * made re-running the same version to check a flaky scenario impossible to
 * express. Change, then run — those are two events, and only the first one
 * makes a version.
 */
const firstAgentVersion = () => ({
  id: "agent-v1",
  label: "v1",
  note: "First version connected to this environment.",
  reach: "endpoint",
  createdAt: new Date().toISOString(),
});

/** The number in a version label — what phrasing and pinning key off. */
export const versionNumber = (label) => parseInt(String(label || "").replace(/\D/g, ""), 10) || 1;

/** Every agent version this environment knows about, oldest first. */
export const agentVersions = (envState) => {
  const stored = envState?.agentVersions;
  return stored?.length ? stored : [firstAgentVersion()];
};

/** The one the next run will use. */
export const currentAgentVersion = (envState) => {
  const list = agentVersions(envState);
  return list[list.length - 1];
};

/**
 * The next version.
 *
 * Product concept shift: a version isn't just a label attached to whatever
 * happens to be at the endpoint any more. When we mint one from an optimize
 * flow we know *which* changes went into it, and we know which version they
 * were applied on top of. Both travel on the version so a reader six weeks
 * from now can answer "what was actually different about v3" without digging
 * through the run that produced it.
 *
 *   applied         — the code changes bundled into this version, each with
 *                     its filepath and diff. Empty for versions minted by
 *                     "connect to a different endpoint".
 *   basedOnVersion  — the label of the version these changes were applied on
 *                     top of. Usually the previous one, but pinning it makes
 *                     branching possible if a user rolls back to v2 and edits
 *                     from there.
 *   fromRunId       — the run whose diagnosis produced these changes, so the
 *                     evidence lives beside the outcome.
 */
export const nextAgentVersion = (envState, { note, reach = "endpoint", now, applied = [], fromRunId = null, basedOnVersion = null } = {}) => {
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
 * Version switching is now a first-class action: `envState.activeEnvVersion`
 * pins whichever version the user chose to work from. If nothing is pinned
 * the newest is treated as active — same behaviour as before switching
 * existed, so environments that never invoked the picker keep reading the
 * way they used to.
 *
 * Runs stamp whichever version was active when they started; the version
 * list marks the active one with `.current` so the header pin and the
 * Settings list read the same story.
 */
export const currentEnvVersion = (env, envState) => {
  const list = environmentVersions(env, envState);
  const pinned = envState?.activeEnvVersion;
  const explicit = pinned ? list.find((v) => v.label === pinned) : null;
  return explicit || list.find((v) => v.current) || list[0];
};

