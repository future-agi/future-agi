/**
 * Proofs, and when the world outgrows them.
 *
 * A scenario is not "written", it is **proved**: setup.py builds the state it
 * presumes, a reference solution satisfies its checks, and every check is made
 * to fail on a deliberately wrong run. That proof is only true of the world it
 * was run against — so it is true of an environment *version*, not of the
 * environment.
 *
 * Which is the whole reason the environment is versioned. Reseed the data and
 * a scenario that presumed an order outside the return window may presume a
 * row that no longer exists; rewrite the checks and a check that used to fail
 * on an empty run may now pass. Neither breaks loudly. Both quietly turn a
 * proved scenario into an unproved one that still reports a result, which is
 * the failure this file exists to make visible.
 */

import { environmentVersions, currentEnvVersion } from "./versions";

/**
 * What kind of change can invalidate a proof.
 *
 * A rules change does not: rules are graded, and grading is a judgement about
 * a run rather than a claim about whether the scenario can be staged at all.
 */
export const INVALIDATING = {
  seed: "the seeded world changed, so the state a scenario presumes may be gone",
  checks: "the checks changed, so a check proved to fail on an empty run may now pass",
  contract: "the tools changed, so the reference solution may no longer run",
  edited: "the scenario was edited after it was proved, so the proof is of a different scenario",
};

/**
 * The other way a proof dies: the scenario itself was edited.
 *
 * A proof is a claim about one scenario against one world. Change the world and
 * it lapses — that is what the versions above are for. Change the *scenario* —
 * its task, its expected outcome, its checks — and it lapses just as
 * completely, and this one is easier to miss because nothing about the
 * environment moved. Editing therefore stamps the row, and the stamp outranks
 * everything else here.
 */
export const editedSinceProof = (row) =>
  !!row?.editedAt && (!row?.provedAt || new Date(row.editedAt) > new Date(row.provedAt));

/** Mark a row as edited, which is what makes it need re-proving. */
export const markEdited = (row, now) => ({ ...row, editedAt: now || new Date().toISOString() });

const hash = (s = "") => {
  let h = 2166136261;
  for (let i = 0; i < s.length; i += 1) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
  return h >>> 0;
};

/**
 * The version a scenario was proved against.
 *
 * Stored on the row once it has been re-proved. Before that it is derived, so
 * an environment that has never been touched still shows the honest thing:
 * scenarios carried over from older worlds, not a uniform green tick.
 */
export const provedAgainst = (row, env, envState) => {
  if (row?.provedAgainst) return row.provedAgainst;
  const versions = environmentVersions(env, envState);
  const oldest = versions[versions.length - 1];
  /* Two in five were carried over from the previous world. Deterministic, so
     the same scenario is always the same story. */
  const h = hash(row?.id || "");
  /*
    Never the version that was just minted.

    A scenario whose proof is derived rather than stamped was proved against the
    world as it stood when it was generated — so the newest version that already
    existed, never one created afterwards. Defaulting to "current" meant minting
    a version silently re-proved every scenario against a world none of them had
    ever been run in, which is the exact failure this whole mechanism exists to
    make visible.
  */
  const preexisting = versions.filter((v) => !v.minted);
  const atGeneration = preexisting[0] || versions[versions.length - 1];
  if (h % 5 < 2) return preexisting[Math.min(1, preexisting.length - 1)]?.label || oldest.label;
  return atGeneration.label;
};

/**
 * Everything that changed between the version a scenario was proved against
 * and the version the environment is on now — and whether any of it matters.
 */
export const proofStatus = (row, env, envState) => {
  const versions = environmentVersions(env, envState); // newest first
  const current = currentEnvVersion(env, envState);
  const proved = provedAgainst(row, env, envState);

  /* An edit invalidates regardless of what the environment has done since. */
  if (editedSinceProof(row)) {
    return {
      proved,
      current: current.label,
      stale: true,
      edited: true,
      reasons: ["edited"],
      since: [],
    };
  }

  if (proved === current.label) {
    return { proved, current: current.label, stale: false, reasons: [], since: [] };
  }

  const provedIndex = versions.findIndex((v) => v.label === proved);
  const since = versions.slice(0, provedIndex === -1 ? versions.length : provedIndex);
  const reasons = [...new Set(since.flatMap((v) => v.changed || []))]
    .filter((c) => INVALIDATING[c]);

  return { proved, current: current.label, stale: reasons.length > 0, reasons, since };
};

/** The scenarios whose proof the world has outgrown. */
export const staleScenarios = (scenarios = [], env, envState) =>
  scenarios.filter((s) => proofStatus(s, env, envState).stale);

/**
 * Re-proving is a claim about the current world *and* about the scenario as it
 * now reads, so it stamps both — otherwise an edited row would come back stale
 * the instant it was re-proved.
 */
export const reproved = (scenarios = [], env, envState) =>
  scenarios.map((s) => ({
    ...s,
    provedAgainst: currentEnvVersion(env, envState).label,
    provedAt: new Date().toISOString(),
  }));
