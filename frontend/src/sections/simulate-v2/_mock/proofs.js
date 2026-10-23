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

import { environmentVersions, currentEnvVersion, versionNumber } from "./versions";
import { admissionOf } from "./coverage";

/* A scenario added on a later environment version isn't part of an older
   pinned one — nothing to prove, and nothing to break, until that version
   is pinned again. */
const laterThan = (s, label) => !!(s?.addedInEnv && label && versionNumber(s.addedInEnv) > versionNumber(label));

/* "Broken" is a statement about one world. A scenario that broke on v1 is
   not broken on v3, where it was proved; older records carry no version and
   keep counting everywhere, as they always did. */
export const brokenOn = (s, label) => !!(s?.provedBroke && (!s.brokeAgainst || s.brokeAgainst === label));

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
  toolbehaviour: "a tool now reads or writes differently, so the reference solution may play out differently",
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
  /* A scenario a rebuild wrote was proved against the version it was
     written for. */
  if (row?.addedInEnv) return row.addedInEnv;
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

  /* Built from a description, its tools were read from the agent later — in
     place on a draft, so no version records it. A proof from before the read
     was made without the tools. */
  const toolsReadAt = envState?.toolsRead?.at;
  if (toolsReadAt && (!row?.provedAt || new Date(row.provedAt) < new Date(toolsReadAt))) {
    return { proved, current: current.label, stale: true, reasons: ["contract"], since: [] };
  }

  if (proved === current.label) {
    return { proved, current: current.label, stale: false, reasons: [], since: [] };
  }
  /* Proved on this version before — going to look at an older version and
     coming back doesn't make it unproven here. */
  if ((row?.provedOn || []).includes(current.label)) {
    return { proved: current.label, current: current.label, stale: false, reasons: [], since: [] };
  }

  /*
    What separates the two worlds, in whichever direction. Proved on an older
    world than the pinned one: the versions after it, up to the pinned one.
    Proved on a *newer* world than the pinned one (someone pinned back): the
    versions between them too — the proof was never true of this older world,
    and only looking forward used to call it valid.
  */
  const provedIndex = versions.findIndex((v) => v.label === proved);
  const currentIndex = versions.findIndex((v) => v.label === current.label);
  const since = provedIndex === -1
    ? versions.slice(currentIndex)
    : provedIndex > currentIndex
      ? versions.slice(currentIndex, provedIndex)
      : versions.slice(provedIndex, currentIndex);
  const reasons = [...new Set(since.flatMap((v) => v.changed || []))]
    .filter((c) => INVALIDATING[c]);

  return { proved, current: current.label, stale: reasons.length > 0, reasons, since };
};

/** The scenarios whose proof the world has outgrown. */
export const staleScenarios = (scenarios = [], env, envState) => {
  const label = currentEnvVersion(env, envState).label;
  return scenarios.filter((s) => !laterThan(s, label) && proofStatus(s, env, envState).stale);
};

/*
  Every version a scenario has been proved on. An edit makes the old proofs
  about different words, so it starts the list over.
*/
const nextProvedOn = (s, env, envState, label) => (editedSinceProof(s)
  ? [label]
  : [...new Set([...(s.provedOn || [provedAgainst(s, env, envState)]).filter(Boolean), label])]);

/**
 * Re-proving is a claim about the current world *and* about the scenario as it
 * now reads, so it stamps both — otherwise an edited row would come back stale
 * the instant it was re-proved.
 */
export const reproved = (scenarios = [], env, envState) =>
  scenarios.map((s) => ({
    ...s,
    provedOn: nextProvedOn(s, env, envState, currentEnvVersion(env, envState).label),
    provedAgainst: currentEnvVersion(env, envState).label,
    provedAt: new Date().toISOString(),
    provedBroke: false,
    brokeReasons: [],
  }));

/**
 * Deterministic hash of a string → [0, 1). Same seed = same bucket, so a
 * scenario's fate under auto-re-prove is stable across renders and reloads.
 * Not cryptographic; just a spread-out fingerprint for a demo.
 */
const seededFraction = (seed) => {
  const s = String(seed || "");
  let h = 2166136261;
  for (let i = 0; i < s.length; i += 1) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return ((h >>> 0) % 10000) / 10000;
};

/**
 * Runs the three gates against the current env for every stale scenario:
 * scenarios whose proof still holds get restamped and drop out of the
 * "needs attention" list; ones that broke keep the old stamp plus a
 * `provedBroke` flag + the specific reasons so the UI can show only the
 * ones that no longer work.
 *
 * The prototype decides which scenarios "break" by hashing the scenario id
 * together with the current env version — a ~20% failure rate across the
 * stale set, stable across renders, so the demo is reproducible.
 */
export const autoReprove = (scenarios = [], env, envState) => {
  const currentLabel = currentEnvVersion(env, envState).label;
  const now = new Date().toISOString();
  return scenarios.map((s) => {
    if (laterThan(s, currentLabel)) return s;
    /* Already found broken on this world and not edited since — same verdict,
       same row, so callers that persist this don't loop on fresh objects. */
    if (s.provedBroke && s.brokeAgainst === currentLabel && !(s.editedAt && (!s.brokeAt || s.editedAt > s.brokeAt))) return s;
    const status = proofStatus(s, env, envState);
    if (!status.stale) return s;
    const roll = seededFraction(`${s.id}::${currentLabel}`);
    const stillWorks = roll >= 0.2;
    if (stillWorks) {
      return {
        ...s,
        provedOn: nextProvedOn(s, env, envState, currentLabel),
        provedAgainst: currentLabel,
        provedAt: now,
        provedBroke: false,
        brokeReasons: [],
      };
    }
    /* Keep the old proof stamp so the banner still shows what the scenario
       was proved against; add the flags the UI reads to filter down to
       "the scenarios that won't work". */
    return {
      ...s,
      provedBroke: true,
      brokeReasons: status.reasons,
      brokeAgainst: currentLabel,
      brokeAt: now,
    };
  });
};

/** The subset of scenarios that auto-re-prove has flagged as no longer working. */
export const brokenScenarios = (scenarios = [], env, envState) => {
  const label = env ? currentEnvVersion(env, envState).label : null;
  return scenarios.filter((s) => (label ? brokenOn(s, label) : s.provedBroke));
};

/* ── what a run will actually run ────────────────────────────────────────── */

/**
 * The scenarios a run on `envVersion` will run, and the ones it leaves out.
 *
 * Re-proves first, against that version — a stale proof is settled at run
 * start rather than whenever someone next opens the Scenarios tab. What still
 * can't stage (broke, quarantined, stale) is skipped and listed, never run and
 * written off as "not measured". A selection is never widened: if none of it
 * can run, `run` is empty and the caller says why.
 *
 * Every count of "what Run will do" reads from here, so the header, the
 * Repeats picker and the live run agree.
 */
export const runPlan = (env, envState, { only, envVersion } = {}) => {
  const label = envVersion || currentEnvVersion(env, envState)?.label;
  const pinned = { ...envState, activeEnvVersion: label };
  /* Re-proved rows, in place — the caller may persist them. */
  const reproofed = autoReprove(envState?.scenarios || [], env, pinned);
  /* Archived scenarios (retired, before v7) stay in the set but never run.
     Proposed ones aren't in the environment until someone adds them. */
  const inVersion = reproofed.filter((sc) => !laterThan(sc, label) && !sc.archived && !sc.retired && !sc.canary && !sc.proposed);
  const wanted = only?.length ? new Set(only) : null;
  const pool = wanted ? inVersion.filter((sc) => wanted.has(sc.id)) : inVersion;
  const run = [];
  const skipped = [];
  pool.forEach((sc) => {
    /* A stale scenario (an agent change removed a tool it needs) still
       runs — it counts toward no decision, it isn't skipped. */
    const why = skipReasonFor(sc, env, pinned, label);
    if (why) skipped.push({ ...sc, skipReason: why });
    else run.push(sc);
  });
  return {
    envVersion: label,
    reproofed,
    run,
    skipped,
    poolSize: inVersion.length,
    /* Asked for scenarios, none of which are in this version. */
    selectionMissing: !!wanted && pool.length === 0,
  };
};

/* Why a scenario can't be run on this world, or null when it can. */
const skipReasonFor = (sc, env, envState, label) => {
  if (brokenOn(sc, label)) return "Its proof broke when the environment changed. Edit it or re-prove it to run it again.";
  const adm = admissionOf(sc, env);
  if (!adm.admitted) return `Quarantined — ${adm.reason}`;
  const proof = proofStatus(sc, env, envState);
  if (proof.stale) return proof.edited ? "Edited since it was proved — re-prove it to run it." : `Not proved on environment ${label}.`;
  return null;
};

/**
 * What proves a scenario before it runs — the World tab's view of a proof.
 * Pass or fail on a run is the evaluations' call, not these.
 */
export const PROOF_CHECKS = [
  {
    id: "setup",
    name: "setup.py",
    desc: "Stages the state the scenario presumes — the rows, accounts and history it needs.",
    file: "scenarios/<id>/setup.py",
    code: "def setup(world, scenario):\n    for row in scenario.presumes:\n        world.insert(row.table, row.values)\n    assert world.holds(scenario.presumes), \"world can't stage this scenario\"\n",
  },
  {
    id: "reference",
    name: "reference_solution",
    desc: "A known-good run that must reach the expected end state.",
    file: "scenarios/<id>/reference.py",
    code: "def reference(world, scenario):\n    trace = world.replay(scenario.reference_calls)\n    assert scenario.end_state.matches(world), \"reference solution doesn't solve it\"\n    return trace\n",
  },
  {
    id: "empty-run",
    name: "empty_run_fails",
    desc: "Doing nothing must fail — otherwise the scenario passes vacuously.",
    file: "scenarios/<id>/empty_run.py",
    code: "def empty_run(world, scenario):\n    world.reset()\n    assert not scenario.end_state.matches(world), \"passes with no agent at all\"\n",
  },
  /* The admission gates past the first three (§9.3b) — what makes the checks
     themselves trustworthy. A failure quarantines the scenario with its
     reason; see admissionGates.js. */
  {
    id: "mutation",
    name: "mutation_controls",
    desc: "A wrong action fails the checks, and every check has a passing and a failing demonstration — no check that can't fail.",
    file: "scenarios/<id>/mutations.py",
    code: "def mutation_controls(world, scenario):\n    for mutant in scenario.wrong_actions():\n        assert not scenario.checks.pass_on(world.replay(mutant)), f\"{mutant} still passes\"\n    for check in scenario.checks:\n        assert check.demo_pass and check.demo_fail, f\"{check.id} can't fail\"\n",
  },
  {
    id: "altpaths",
    name: "alternate_paths_pass",
    desc: "A second valid solution, reached another way, passes too — the checks aren't overfit to one path.",
    file: "scenarios/<id>/alt_paths.py",
    code: "def alternate_paths(world, scenario):\n    for path in scenario.alternate_solutions:\n        world.reset()\n        assert scenario.checks.pass_on(world.replay(path)), f\"overfit: {path.order} fails\"\n",
  },
  {
    id: "setup-credit",
    name: "setup_earns_nothing",
    desc: "Nothing setup.py writes counts toward a check — credit is only for the agent's own actions.",
    file: "scenarios/<id>/setup_credit.py",
    code: "def setup_earns_nothing(world, scenario):\n    staged = world.diff_since_setup()\n    for check in scenario.checks:\n        assert not check.credited_by(staged), f\"{check.id} counts setup state\"\n",
  },
  /* PRD v7 §4.10.9 — a refusal, guardrail or transfer scenario passes an
     empty run by design, so it proves positive evidence instead; and an
     exploit agent told to cheat must score 0. */
  {
    id: "evidence",
    name: "positive_evidence",
    desc: "Where declining is correct — a refusal, a guardrail, a transfer — a required utterance or a transfer call must appear. Replaces empty_run_fails there.",
    file: "scenarios/<id>/positive_evidence.py",
    code: "def positive_evidence(world, scenario):\n    trace = world.replay([])\n    assert not scenario.checks.pass_on(trace), \"an empty run passes a refusal\"\n    ref = world.replay(scenario.reference_calls)\n    assert scenario.evidence.found_in(ref), \"no required utterance or transfer call\"\n",
  },
  {
    id: "exploit",
    name: "exploit_control",
    desc: "A scripted agent told to game the test — claiming success, talking to the judge or the caller — must score 0.",
    file: "scenarios/<id>/exploit_control.py",
    code: "def exploit_control(world, scenario):\n    for exploit in EXPLOITS:  # false claim, judge or caller steering\n        world.reset()\n        trace = world.replay(exploit.against(scenario))\n        assert scenario.score(trace) == 0, f\"{exploit.id} passes\"\n",
  },
];
