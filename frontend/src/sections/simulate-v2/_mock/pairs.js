/*
  Comparing runs — PRD v7 §4.10.7 / §4.10.8, AC-14.5 – 14.14, AC-16.11.

  Comparability is decided per PAIR: one scenario, by stable id, in two runs.
  A pair compares cleanly only when everything except the agent matches for
  that scenario. Nothing here ever refuses: a mismatch changes the class of
  the pairs it touches, the label carries the uncertainty, and every way to
  firm it up (re-grade, bridge, backfill, raise k) is offered with a price
  and starts only on a click.

    exact          same scenario version (patch diffs allowed), unchanged
                   world elements it touches, same sub-goal versions, eval,
                   simulated-user model, surface and fidelity     → in the delta
    regraded       only a grader fix (minor bump, sub-goal fix) or the eval
                   version differs; both re-graded to one version → in, badged
    rechecked      a world element it touches changed (a tool's dial or
                   handler, seed data), its own definition didn't → in, badged,
                   one click excludes them
    changed        major bump, or a different simulated-user model → out
    new            in the newer run only                          → out, own band
    not-applicable stale for one of the agent versions (a tool it
                   needs was removed) — shown as "Stale"           → out
    archived       archived by hand, in the older run only        → out
    removed        in the older run only, for no recorded reason  → out

  Runs from before versions existed carry fewer pins: a missing pin reads as
  the current value, so they compare as exact unless something recorded
  says otherwise.
*/
import { bumpBetween, playedVersionOf, staleForAgentOf, requiredCapsOf, scenarioVersionsPin } from "./decoupled";
import {
  evalVersionOf, catalogVersionOf, simulatorOf, simulatorForModel, pinsForNewRun, subGoalsOfScenario, isConnectOnly,
} from "./envModel";
import { snapshotOfRun, snapshotLabelOf } from "./envSnapshot";
import { toolFaultsOf } from "./toolEnv";
import { environmentVersions, currentEnvVersion, agentVersions, versionNumber } from "./versions";
import { runEnvVersion, knownToolsFor } from "./toolFit";
import { replicationOf } from "./replication";
import { sourceKindOf } from "./agentRegistry";
import { estimateRun, evalCreditsPerRun, creditsLabel } from "./metering";
import { resolveEval } from "./evals";
import { isMeasured } from "./failures";
import { runSummary } from "./comparison";
import { scenarioTitle } from "./scenarios";

/* ── vocabulary ──────────────────────────────────────────────────────────── */

export const PAIR_CLASSES = {
  exact: { label: "Exact", short: "Exact", inDelta: true, tip: "Same scenario version, world, sub-goals, eval and simulated caller — only the agent differs." },
  regraded: { label: "Re-graded", short: "Re-graded", inDelta: true, tip: "Only a grader fix or the eval version differs; both runs are read under one version." },
  rechecked: { label: "Rechecked", short: "Rechecked", inDelta: true, tip: "A world element it touches changed, its own definition didn't. In the delta, but the delta may mix agent and world effects." },
  changed: { label: "Changed", short: "Changed", inDelta: false, tip: "The scenario was edited in a way that changes what the caller does, or the simulated caller changed. Side by side, outside the delta, until a bridge run makes it exact." },
  new: { label: "New", short: "New", inDelta: false, tip: "Only in the newer run. Read on its own, outside the delta, until backfilled." },
  "not-applicable": { label: "Stale", short: "Stale", inDelta: false, tip: "Stale for one of the agent versions — an agent change removed a tool it needs. It ran, but counts toward no decision, so it's outside the delta." },
  archived: { label: "Archived", short: "Archived", inDelta: false, tip: "Archived by hand after the older run — outside the delta." },
  removed: { label: "Removed", short: "Removed", inDelta: false, tip: "Only in the older run — the newer one didn't include it." },
};

/* Header order: the breakdown reads like the PRD's verdict line. */
export const PAIR_ORDER = ["exact", "regraded", "rechecked", "changed", "new", "not-applicable", "archived", "removed"];
const COUNT_WORD = {
  exact: "exact", regraded: "re-graded", rechecked: "rechecked", changed: "changed",
  new: "new", "not-applicable": "stale", archived: "archived", removed: "removed",
};

/** Points either side of zero inside which a change isn't meaningful — only
    until a comparison has a measured noise band (noiseBandOf). */
export const NO_CHANGE_POINTS = 10;
/** Below this many comparable pairs there is no aggregate — rows only. A display rule, not a gate. */
export const PAIR_FLOOR = 10;
/* Default spreads until the workspace has data (calibrated on the PRD's
   worked examples: 32 pairs at k=3 → ±13.4; 50 → ±10.7). */
const DEFAULT_BETWEEN = 0.035;
const DEFAULT_TRIAL = 0.17;
const Z95 = 1.96;

/* ── what a run held, per pair ───────────────────────────────────────────── */

/**
 * The comparability key a run recorded — everything but the scenarios.
 * A missing pin reads as what's current now.
 */
/* The caller a run played against: its own pin, else the one its environment
   version held when it ran — never today's, which may have moved since. */
const simulatorOfRun = (run, env, envState) => {
  if (run?.simulator) return run.simulator;
  const now = simulatorOf(env, envState);
  const then = run ? snapshotOfRun(run, env, envState)?.parts?.caller : null;
  return then && then !== now.model ? simulatorForModel(then, env?.surface === "voice") : now;
};

export const pairKeyOf = (run, env, envState) => {
  const sim = simulatorOfRun(run, env, envState);
  return {
    envId: env?.id || null,
    agent: run?.agentVersion || null,
    env: runEnvVersion(env, envState, run),
    /* The environment version people see ("v7") — every part the run held
       fixed, one number. Whether two runs can be compared is decided on this,
       never on the world label underneath. */
    snapshot: run ? snapshotLabelOf(run, env, envState) : null,
    eval: run?.evalVersion || evalVersionOf(envState),
    catalog: run?.catalogVersion || catalogVersionOf(envState),
    simulator: sim?.model || sim?.label || null,
    simulatorLabel: sim?.label || sim?.model || null,
    /* The tool dial, per tool: a run that recorded its dial reads that,
       one that didn't reads the dial as it stands. */
    faults: run?.toolEnv ? (run.toolFaults || {}) : (toolFaultsOf(env, envState) || {}),
    surface: run?.surface || env?.surface || "chat",
    fidelity: run?.fidelity || replicationOf(envState?.agent, env).fidelity,
    repeats: run?.repeats || 1,
    finishedAt: run?.finishedAt || null,
  };
};

const sameJson = (a, b) => JSON.stringify(a || null) === JSON.stringify(b || null);
const faultDiffTools = (fa = {}, fb = {}) => [...new Set([...Object.keys(fa), ...Object.keys(fb)])]
  .filter((t) => !sameJson(fa[t], fb[t]));

/** Environment versions after the lower label up to the higher one — what changed in the world between two runs. */
const worldChangesBetween = (env, envState, la, lb) => {
  if (!la || !lb || la === lb) return null;
  const lo = Math.min(versionNumber(la), versionNumber(lb));
  const hi = Math.max(versionNumber(la), versionNumber(lb));
  const between = environmentVersions(env, envState).filter((v) => {
    const n = versionNumber(v.label);
    return n > lo && n <= hi;
  });
  if (!between.length) return null;
  const known = knownToolsFor(env, envState).map((t) => (typeof t === "string" ? t : t.name)).filter(Boolean);
  const text = between.map((v) => v.note || "").join(" ");
  return {
    labels: between.map((v) => v.label),
    kinds: new Set(between.flatMap((v) => v.changed || [])),
    /* Tools a version's change summary names — a seed or handler change
       touches the scenarios that call them. None named: every scenario that
       calls a tool. */
    tools: known.filter((t) => new RegExp(`\\b${t}\\b`).test(text)),
  };
};

/* Sub-goal edits between two catalogue pins. Each edit mints the next c-number. */
const subGoalEditsBetween = (envState, ca, cb) => {
  const a = versionNumber(ca);
  const b = versionNumber(cb);
  if (a === b) return [];
  const lo = Math.min(a, b);
  const hi = Math.max(a, b);
  return (envState?.subGoalEdits || [])
    .map((e, i) => ({ ...e, n: versionNumber(e.version || `c${i + 2}`) }))
    .filter((e) => e.n > lo && e.n <= hi);
};

const scenarioOf = (envState, id) => (envState?.scenarios || []).find((sc) => sc.id === id) || null;

/* Did this run play the scenario? Tasks when we have them, else its ids. */
const ranIn = (run, id) => {
  if (Array.isArray(run?.tasks) && run.tasks.length) return run.tasks.some((t) => t.id === id);
  if (Array.isArray(run?.scenarioIds) && run.scenarioIds.length) return run.scenarioIds.includes(id);
  return true;
};

const envOfRun = (run, env) => run?._env || env;
const stateOfRun = (run, envState) => run?._envState || envState;

/* "Environment v5 → v6" — the versions people see, never the world label. */
const versionSpan = (ka, kb) => (ka.snapshot && kb.snapshot && ka.snapshot !== kb.snapshot
  ? `Environment ${ka.snapshot} → ${kb.snapshot}`
  : "The environment");

/**
 * One scenario in two runs → its pair class, and why.
 * `row` is the scenario (or a comparison row carrying its id).
 * { cls, reasons: [string], criteria: [{ id, name }], bump, tools: [string] }
 */
export const pairClassOf = (row, runA, runB, env, envState) => {
  const id = row?.id;
  const sc = scenarioOf(envState, id) || scenarioOf(stateOfRun(runB, envState), id) || row;
  const inA = ranIn(runA, id);
  const inB = ranIn(runB, id);
  const out = { cls: "exact", reasons: [], criteria: [], bump: null, tools: [] };
  if (!inA && !inB) return null;

  /* Stale for either agent version (a tool it needs was removed): it ran,
     but counts toward no decision. Internally still the "not-applicable" class. */
  const napA = staleForAgentOf(sc, runA?.agentVersion, env, envState);
  const napB = staleForAgentOf(sc, runB?.agentVersion, env, envState);
  const nap = napA.stale ? { ...napA, agent: runA?.agentVersion } : napB.stale ? { ...napB, agent: runB?.agentVersion } : null;

  if (inA && !inB) {
    if (sc?.archived || sc?.retired) return { ...out, cls: "archived", reasons: ["Archived by hand after the older run."] };
    if (nap) return { ...out, cls: "not-applicable", reasons: [`Stale for ${nap.agent} — ${nap.reason || "a tool it needs was removed"}`] };
    return { ...out, cls: "removed", reasons: ["Only in the older run."] };
  }
  if (!inA && inB) {
    if (nap) return { ...out, cls: "not-applicable", reasons: [`Stale for ${nap.agent} — ${nap.reason || "a tool it needs was removed"}`] };
    return { ...out, cls: "new", reasons: ["Only in the newer run."] };
  }
  if (nap) return { ...out, cls: "not-applicable", reasons: [`Stale for ${nap.agent} — ${nap.reason || "a tool it needs was removed"}`] };

  const ka = runA?._key || pairKeyOf(runA, envOfRun(runA, env), stateOfRun(runA, envState));
  const kb = runB?._key || pairKeyOf(runB, envOfRun(runB, env), stateOfRun(runB, envState));
  const changed = [];
  const rechecked = [];
  const regraded = [];

  /* The scenario's own version. */
  const va = playedVersionOf(runA, sc);
  const vb = playedVersionOf(runB, sc);
  const bump = bumpBetween(va, vb);
  out.bump = bump;
  if (bump === "major") changed.push("Scenario edited: what the caller does changed");
  if (bump === "minor") regraded.push("Scenario edited: a grader fix");

  /* Run-level: the simulated caller, surface, fidelity. */
  if (ka.simulator !== kb.simulator) changed.push(`Simulated caller ${ka.simulatorLabel} → ${kb.simulatorLabel}`);
  if (ka.surface !== kb.surface) changed.push(`Surface ${ka.surface} → ${kb.surface}`);
  if (ka.fidelity !== kb.fidelity) changed.push(`Run fidelity ${ka.fidelity} → ${kb.fidelity} — never mixed`);

  /* World elements it touches: the tools it needs. */
  const caps = requiredCapsOf(sc, env, envState);
  out.tools = caps;
  const dialMoved = faultDiffTools(ka.faults, kb.faults).filter((t) => caps.includes(t));
  if (dialMoved.length) rechecked.push(`Tool dial changed on ${dialMoved.join(", ")}`);
  if (ka.envId === kb.envId) {
    const world = worldChangesBetween(env, envState, ka.env, kb.env);
    if (world) {
      const worldKinds = ["seed", "contract", "toolbehaviour"].filter((k) => world.kinds.has(k));
      const touches = world.tools.length ? caps.some((t) => world.tools.includes(t)) : caps.length > 0;
      if (worldKinds.length && touches && !isConnectOnly(env)) {
        rechecked.push(`${versionSpan(ka, kb)} changed ${worldKinds.map((k) => (k === "contract" ? "tools" : k === "toolbehaviour" ? "tool behaviour" : "seed data")).join(" and ")} it uses`);
      }
      if (world.kinds.has("checks") || world.kinds.has("rules")) regraded.push(`${versionSpan(ka, kb)} rewrote checks`);
    }
  }

  /* Sub-goals: a fix re-grades; a policy change is "criteria changed" for
     that sub-goal only — the scenario still pairs on the others. */
  const edits = subGoalEditsBetween(envState, ka.catalog, kb.catalog);
  if (edits.length) {
    const mine = new Set(subGoalsOfScenario(sc, env, envState).map((g) => g.id));
    edits.filter((e) => mine.has(e.id)).forEach((e) => {
      if (e.policy) {
        if (!out.criteria.some((c) => c.id === e.id)) out.criteria.push({ id: e.id, name: e.name || e.id, version: e.version });
      } else regraded.push(`Sub-goal ${e.name || e.id} fixed`);
    });
  }

  /* The eval version. */
  if (ka.eval !== kb.eval) regraded.push("Evaluations changed between the runs");

  /* Worst wins. */
  if (changed.length) return { ...out, cls: "changed", reasons: changed };
  if (rechecked.length) return { ...out, cls: "rechecked", reasons: [...rechecked, ...regraded] };
  if (regraded.length) return { ...out, cls: "regraded", reasons: regraded };
  return out;
};

/* ── pass shares and flips ───────────────────────────────────────────────── */

const shareOf = (task) => (task && task.status !== "missing" && isMeasured(task)
  ? (task.passShare ?? (task.status === "passed" ? 1 : 0))
  : null);

const verdictWord = (task) => {
  if (!task) return "not run";
  if (!isMeasured(task)) return "not measured";
  if (task.status === "passed") return "passed";
  if (task.status === "flaky") return "flaky";
  return "failed";
};

/** passed → failed reads as a regression; failed → passed as an improvement. */
export const flipOf = (taskA, taskB) => {
  const a = verdictWord(taskA);
  const b = verdictWord(taskB);
  const kind = a === b ? "same"
    : a === "passed" && (b === "failed" || b === "flaky") ? "regressed"
      : (a === "failed" || a === "flaky") && b === "passed" ? "improved"
        : "moved";
  return { a, b, kind, text: a === b ? a : `${a} → ${b}` };
};

/* ── interval maths ──────────────────────────────────────────────────────── */

const smooth = (p, k) => (p * k + 0.5) / (k + 1);
const mean = (xs) => (xs.length ? xs.reduce((a, x) => a + x, 0) / xs.length : 0);

/**
 * Pass-rate delta over paired scenarios with a 95% interval. Trials are
 * nested in scenarios: the paired differences carry the between-scenario
 * spread, and each scenario's binomial trial noise floors it (normal
 * approximation, deterministic).
 * { n, delta, lo, hi, half } in points.
 */
export const pairedInterval = (diffs) => {
  const n = diffs.length;
  if (!n) return { n: 0, delta: null, lo: null, hi: null, half: null };
  const d = diffs.map((x) => x.b - x.a);
  const m = mean(d);
  const s2 = n > 1 ? d.reduce((a, x) => a + (x - m) ** 2, 0) / (n - 1) : DEFAULT_BETWEEN;
  const trial = mean(diffs.map((x) => {
    const pa = smooth(x.a, x.ka);
    const pb = smooth(x.b, x.kb);
    return (pa * (1 - pa)) / x.ka + (pb * (1 - pb)) / x.kb;
  }));
  const half = Z95 * Math.sqrt(Math.max(s2, trial) / n);
  const r = (v) => Math.round(v * 1000) / 10;
  return { n, delta: r(m), lo: r(m - half), hi: r(m + half), half: r(half), s2, trial };
};

/** Improved / Regressed / No meaningful change / Inconclusive — or none below the floor. */
export const verdictLabelOf = (iv, band = NO_CHANGE_POINTS) => {
  if (!iv || iv.n < PAIR_FLOOR) return { id: "rows", label: "Rows only", note: `Fewer than ${PAIR_FLOOR} comparable pairs — no aggregate; read the rows.` };
  if (iv.lo > 0) return { id: "improved", label: "Improved", note: "The whole interval is above zero." };
  if (iv.hi < 0) return { id: "regressed", label: "Regressed", note: "The whole interval is below zero." };
  if (iv.lo >= -band && iv.hi <= band) return { id: "same", label: "No meaningful change", note: `The whole interval sits inside the ±${band}-point noise.` };
  return { id: "inconclusive", label: "Inconclusive", note: `The interval crosses zero and reaches past the ±${band}-point noise.` };
};

/**
 * The band inside which a change isn't meaningful — measured, not assumed:
 * the run-to-run noise Reproduce found by running this agent version twice
 * for an improvement, else two finished runs of it against each other, else
 * an estimate from the pairs and repeats (two runs of 32 scenarios at three
 * repeats differ by about ±14 on chance alone).
 */
export const noiseBandOf = (env, envState, key, { n = 0, ka = 1, kb = 1, skipId = null } = {}) => {
  const rec = (envState?.optimizations || [])
    .filter((o) => o.result?.calibration && key?.agent && o.baseAgentVersion === key.agent)
    .sort((x, y) => Date.parse(y.createdAt || 0) - Date.parse(x.createdAt || 0))[0];
  if (rec) {
    const c = rec.result.calibration;
    return { points: Math.max(1, Math.round(rec.result.noise)), source: "reproduce", agent: key.agent, improvement: rec.name || rec.id, disagreed: c.disagreed, n: c.scenarios };
  }
  const self = selfNoiseOf(env, envState, key, skipId);
  if (self) return { points: Math.max(1, Math.round(self.noise)), source: "self", agent: self.agent, disagreed: self.disagreed, n: self.n };
  if (!n) return { points: NO_CHANGE_POINTS, source: "default" };
  return { points: Math.max(1, Math.round(100 * Z95 * Math.sqrt(0.25 * (1 / (n * ka) + 1 / (n * kb))))), source: "estimate", n, ka, kb };
};

/** Where a noise band came from, in a sentence. */
export const noiseBandText = (band) => {
  if (!band) return null;
  if (band.source === "reproduce") return `Noise ±${band.points} pts, measured when “${band.improvement}” reproduced its failures: ${band.agent} ran twice and ${band.disagreed} of ${band.n} scenarios disagreed.`;
  if (band.source === "self") return `Noise ±${band.points} pts, measured by running ${band.agent} against itself: ${band.disagreed} of ${band.n} scenarios disagreed.`;
  if (band.source === "estimate") return `Noise ±${band.points} pts, estimated from ${band.n} pairs at ${band.ka === band.kb ? `${band.ka}` : `${band.ka} and ${band.kb}`} ${band.ka === 1 && band.kb === 1 ? "repeat" : "repeats"} — run this version twice to measure it.`;
  return `Noise ±${band.points} pts by default.`;
};

/* Predicted half-width for n pairs at kA/kB trials, from a paired variance. */
const predictHalf = (n, ka, kb, between = DEFAULT_BETWEEN, trial = DEFAULT_TRIAL) => (n > 0
  ? Math.round(Z95 * Math.sqrt((between + trial / ka + trial / kb) / n) * 1000) / 10
  : null);

/* ── bridges: can the older agent version still answer? (§4.10.8) ────────── */

/**
 * Whether an older agent version can run again here.
 * { can, how, why }
 */
export const bridgeOf = (env, envState, agentLabel) => {
  const agent = envState?.agent;
  if (!agent) return { can: false, why: "No agent is connected." };
  const kind = sourceKindOf(agent);
  const mode = replicationOf(agent, env).mode;
  const v = agent.values || {};
  if (["repo", "framework", "upload"].includes(kind) || mode === "sandbox_build") {
    return { can: true, how: `re-runs ${agentLabel} at its pinned commit` };
  }
  /* Hosted agents answer as an older version through what we pinned for it:
     Vapi's test copy, Retell's pinned draft, a branch — unless it was deleted
     or its credentials expired (then the user re-pins). */
  const PINNED = { provider_clone: "test copy", exported_mocked: "settings overridden for one call", platform_draft: "pinned draft version", platform_branch: "branch" };
  if (PINNED[mode]) return { can: true, how: `runs ${agentLabel} through its ${PINNED[mode]}` };
  if (mode === "run_here") return { can: true, how: `runs ${agentLabel}'s pinned definition here` };
  /* Phone numbers and endpoints answer as the newest version. */
  const where = v.registeredVersions?.[agentLabel];
  if (where) return { can: true, how: `calls ${agentLabel} where it's registered (${where})` };
  const phone = v.contact?.mode === "phone" || /phone/i.test(agent.typeId || "");
  return {
    can: false,
    why: `${agentLabel} isn't reachable any more; register ${phone ? "a number" : "an endpoint"} where it still answers.`,
  };
};

/* ── pricing ─────────────────────────────────────────────────────────────── */

const evalsOf = (envState) => (envState?.evals || []).map(resolveEval).filter(Boolean);

/** Calls and their price (list-price dollars, said as credits) for running `scenarios` × `repeats`. */
export const priceOf = (env, envState, scenarios, repeats) => {
  const est = estimateRun({ env, scenarios, repeats, evals: evalsOf(envState), runLocation: envState?.agent?.values?.runLocation || null });
  return { calls: scenarios * repeats, usd: est.usd };
};

export const priceLine = (p) => `${p.calls} call${p.calls === 1 ? "" : "s"} ≈ ${creditsLabel(p.usd || 0)}`;

/* ── the comparison ──────────────────────────────────────────────────────── */

const relationOf = (envA, envB) => {
  if (!envA || !envB || envA.id === envB.id) return "same";
  const parent = (e) => (typeof e?.forkedFrom === "string" ? e.forkedFrom : e?.forkedFrom?.envId) || null;
  if (parent(envA) === envB.id || parent(envB) === envA.id || (parent(envA) && parent(envA) === parent(envB))) return "fork";
  return "unrelated";
};

const classCounts = (pairs) => {
  const counts = Object.fromEntries(PAIR_ORDER.map((k) => [k, 0]));
  pairs.forEach((p) => { counts[p.cls] += 1; });
  return counts;
};

/** "Compared on 46 of 50 · 42 exact · 2 re-graded · …" */
export const countsSentence = (counts, total) => {
  const inDelta = counts.exact + counts.regraded + counts.rechecked;
  const parts = PAIR_ORDER.filter((k) => counts[k]).map((k) => `${counts[k]} ${COUNT_WORD[k]}`);
  return [`Compared on ${inDelta} of ${total}`, ...parts].join(" · ");
};

/**
 * Whether two runs sit on different environment versions — the version people
 * see (the snapshot), else the world label for keys that carry no snapshot.
 * Different environments are always different versions.
 */
export const differentVersions = (ka, kb) => {
  if (!ka || !kb) return false;
  if (ka.envId && kb.envId && ka.envId !== kb.envId) return true;
  if (ka.snapshot && kb.snapshot) return ka.snapshot !== kb.snapshot;
  return !!ka.env && !!kb.env && ka.env !== kb.env;
};

/**
 * Every scenario in the baseline and the newest run, classed and read.
 * `runs` are run summaries (tasks included), baseline first; a run from
 * another environment carries `_env` / `_envState`.
 * opts: { excludeRechecked, regrades: [{ a, b, to }] }
 */
export const comparisonOf = (runs, env, envState, opts = {}) => {
  if (!runs || runs.length < 2) return null;
  const keyed = runs.map((r) => ({ ...r, _key: pairKeyOf(r, envOfRun(r, env), stateOfRun(r, envState)) }));
  const a = keyed[0];
  const b = keyed[keyed.length - 1];
  const relation = relationOf(envOfRun(a, env), envOfRun(b, env));
  const taskOf = (run, id) => (run.tasks || []).find((t) => t.id === id) || null;
  const ids = [...new Set([...(a.tasks || []), ...(b.tasks || [])].map((t) => t.id))];

  const pairs = ids.map((id) => {
    const row = scenarioOf(envState, id) || taskOf(b, id) || taskOf(a, id) || { id };
    const pc = pairClassOf(row, a, b, env, envState);
    if (!pc) return null;
    const ta = taskOf(a, id);
    const tb = taskOf(b, id);
    return { id, title: scenarioTitle(row.title), ...pc, flip: flipOf(ta, tb), shareA: shareOf(ta), shareB: shareOf(tb) };
  }).filter(Boolean);

  const counts = classCounts(pairs);
  const total = pairs.length;
  const inClasses = opts.excludeRechecked ? ["exact", "regraded"] : ["exact", "regraded", "rechecked"];
  const usable = pairs.filter((p) => inClasses.includes(p.cls) && p.shareA != null && p.shareB != null);
  const iv = pairedInterval(usable.map((p) => ({ a: p.shareA, b: p.shareB, ka: a._key.repeats, kb: b._key.repeats })));
  /* RSI PRD: "two runs are comparable only when the environment version
     matches." Across versions — or environments — the runs sit side by side
     with no verdict; re-running one agent on the other's version makes them
     comparable. */
  const otherVersion = relation !== "unrelated" && differentVersions(a._key, b._key);
  const sideBySide = relation === "unrelated" || otherVersion;
  const selfNoise = sideBySide || a._env || a._key.agent === b._key.agent ? null : selfNoiseOf(env, envState, a._key, b.id);
  const band = sideBySide ? null : noiseBandOf(env, envState, a._key, { n: iv.n, ka: a._key.repeats || 1, kb: b._key.repeats || 1, skipId: b.id });
  const read = relation === "unrelated"
    ? { id: "side", label: "Side by side", note: "Unrelated environments share no scenarios — no verdict." }
    : otherVersion
      ? {
        id: "side",
        label: "Side by side",
        note: a._key.envId !== b._key.envId
          ? "Different environments — runs are compared only on the same environment version, so these sit side by side with no verdict."
          : `Environment ${a._key.snapshot || "—"} and ${b._key.snapshot || "—"} — runs are comparable only on the same environment version. Re-run one agent on the other's version to compare them.`,
      }
      : verdictLabelOf(iv, band?.points);
  /* A change counts only when it beats the noise measured for the
     baseline's version. Inside it, the read is Inconclusive. */
  const measuredBand = band && (band.source === "reproduce" || band.source === "self");
  const label = measuredBand && (read.id === "improved" || read.id === "regressed") && Math.abs(iv.delta) <= band.points
    ? { id: "inconclusive", label: "Inconclusive", note: `Within the run-to-run noise measured for ${band.agent} (±${band.points} pts).` }
    : read;

  /* Run-level mismatches, each with its offer (AC-14.12). */
  const regraded = (opts.regrades || []).some((g) => g.a === a.id && g.b === b.id);
  const warnings = [];
  /* Side by side there is no verdict to firm up — no re-grade or bridge offers. */
  if (!sideBySide && a._key.eval !== b._key.eval) {
    const to = versionNumber(a._key.eval) > versionNumber(b._key.eval) ? a._key.eval : b._key.eval;
    const n = counts.exact + counts.regraded + counts.rechecked;
    const credits = evalCreditsPerRun({ evals: evalsOf(envState), scenarios: n, repeats: a._key.repeats })
      + evalCreditsPerRun({ evals: evalsOf(envState), scenarios: n, repeats: b._key.repeats });
    warnings.push({
      id: "eval", kind: "regrade", done: regraded, to,
      title: regraded ? "Re-graded both to the newer evaluations" : "Graded under different evaluations",
      body: regraded
        ? "Both runs read from their stored traces under one set of evaluations."
        : "Re-grade both runs to the newer evaluations from their stored traces — no calls are made.",
      action: regraded || !n ? null : `Re-grade both · ≈ ${credits || n * 2} credits`,
    });
  }
  const bridge = bridgeOf(env, envState, a._key.agent);
  if (!sideBySide && a._key.simulator !== b._key.simulator) {
    const ids2 = pairs.filter((p) => p.cls === "changed").map((p) => p.id);
    warnings.push({
      id: "simulator", kind: "bridge",
      title: `Different simulated callers (${a._key.simulatorLabel} and ${b._key.simulatorLabel})`,
      body: bridge.can
        ? `Pairs show as changed until re-run. A bridge ${bridge.how} with ${b._key.simulatorLabel} on the ${ids2.length}.`
        : `Pairs show as changed until re-run. ${bridge.why}`,
      action: bridge.can && ids2.length ? `Bridge ${ids2.length} · ${priceLine(priceOf(env, envState, ids2.length, b._key.repeats))}` : null,
      scenarioIds: ids2, agent: a._key.agent, repeats: b._key.repeats,
    });
  }

  /* The cheapest way to firm up an inconclusive read, priced (§4.10.8). */
  const fixes = [];
  if (!sideBySide) {
    const k = b._key.repeats;
    const half = iv.half ?? predictHalf(Math.max(1, iv.n), a._key.repeats, k);
    const changedIds = pairs.filter((p) => p.cls === "changed").map((p) => p.id);
    const newIds = pairs.filter((p) => p.cls === "new").map((p) => p.id);
    const bridgeIds = [...changedIds, ...newIds];
    if (bridgeIds.length) {
      const p = priceOf(env, envState, bridgeIds.length, k);
      const after = iv.n >= PAIR_FLOOR
        ? Math.round(half * Math.sqrt(iv.n / (iv.n + bridgeIds.length)) * 10) / 10
        : predictHalf(iv.n + bridgeIds.length, k, k);
      const what = [changedIds.length && `${changedIds.length} changed`, newIds.length && `${newIds.length} new`].filter(Boolean).join(" and ");
      fixes.push({
        id: "bridge", kind: "bridge", can: bridge.can, why: bridge.why, how: bridge.how,
        label: changedIds.length ? `Bridge ${what}` : `Backfill ${what}`,
        line: !bridge.can ? bridge.why
          : iv.n >= PAIR_FLOOR
            ? `${changedIds.length ? "Bridging" : "Backfilling"} the ${what} costs ${p.calls} calls at k=${k} and narrows ±${half} to ±${after}`
            : `${changedIds.length ? "Bridging" : "Backfilling"} the ${what} costs ${p.calls} calls at k=${k} and gives a verdict to about ±${after}`,
        calls: p.calls, usd: p.usd, half: after,
        scenarioIds: bridgeIds, agent: a._key.agent, repeats: k,
      });
    }
    const kNext = Math.max(a._key.repeats, k) < 5 ? 5 : 10;
    if (iv.n >= PAIR_FLOOR && Math.max(a._key.repeats, k) < 10) {
      const extra = iv.n * Math.max(0, kNext - a._key.repeats) + iv.n * Math.max(0, kNext - k);
      const ratio = Math.sqrt((DEFAULT_BETWEEN + 2 * DEFAULT_TRIAL / kNext) / (DEFAULT_BETWEEN + DEFAULT_TRIAL / a._key.repeats + DEFAULT_TRIAL / k));
      const after = Math.round(half * ratio * 10) / 10;
      const p = priceOf(env, envState, 1, extra);
      fixes.push({
        id: "raise-k", kind: "raise-k", can: true,
        label: `Raise k to ${kNext}`,
        line: `Topping both runs up to ${kNext} repeats on the ${iv.n} comparable costs ${p.calls} calls and narrows ±${half} to ±${after}`,
        calls: p.calls, usd: p.usd, half: after, repeats: kNext,
        scenarioIds: usable.map((x) => x.id), agent: b._key.agent,
      });
    }
    fixes.sort((x, y) => (x.can === y.can ? x.calls - y.calls : x.can ? -1 : 1));
  }

  /* Trend across several agent versions (AC-14.13): the scenarios
     comparable between the baseline and every other run. */
  let trend = null;
  if (keyed.length >= 3) {
    const allIds = ids.filter((id) => keyed.every((r) => shareOf(taskOf(r, id)) != null)
      && keyed.slice(1).every((r) => {
        const pc = pairClassOf(scenarioOf(envState, id) || { id }, a, r, env, envState);
        return pc && PAIR_CLASSES[pc.cls].inDelta && !(opts.excludeRechecked && pc.cls === "rechecked");
      }));
    trend = {
      n: allIds.length,
      points: keyed.map((r) => ({
        runId: r.id, letter: r.letter, color: r.color, agent: r._key.agent,
        rate: allIds.length ? Math.round(mean(allIds.map((id) => shareOf(taskOf(r, id)))) * 100) : null,
      })),
    };
  }

  return {
    baselineId: a.id, targetId: b.id, relation,
    keys: { a: a._key, b: b._key },
    pairs, byId: new Map(pairs.map((p) => [p.id, p])),
    counts, total,
    sentence: countsSentence(counts, total),
    interval: iv, label, warnings, fixes, trend, selfNoise, band,
    excludeRechecked: !!opts.excludeRechecked,
  };
};

/**
 * Baseline-versus-baseline calibration: the two latest finished runs of the
 * same agent version on the same environment version, paired. Their spread
 * is the simulator's own noise — a flip smaller than it doesn't count.
 * { agent, runIds, n, disagreed, noise } — noise is the pass-rate gap between
 * the two runs, in points — or null with fewer than
 * two such runs or too few comparable pairs.
 */
export const selfNoiseOf = (env, envState, key, skipId = null) => {
  if (!key?.agent) return null;
  const same = (envState?.runs || []).filter(finished).filter((r) => r.id !== skipId)
    .filter((r) => {
      /* A true self-run: everything but the clock matches — same agent,
         environment version, tool dial, simulated caller and eval. */
      const k = pairKeyOf(r, env, envState);
      return k.agent === key.agent && !differentVersions(k, key) && k.eval === key.eval
        && k.simulator === key.simulator && sameJson(k.faults, key.faults);
    })
    .sort((x, y) => Date.parse(y.finishedAt) - Date.parse(x.finishedAt))
    .slice(0, 2);
  if (same.length < 2) return null;
  const [sa, sb] = same.map((r, i) => runSummary(env, envState, r, i));
  const pairs = (sb.tasks || []).map((t) => {
    const a = shareOf(t);
    const b = shareOf((sa.tasks || []).find((x) => x.id === t.id));
    return a == null || b == null ? null : { a, b, ka: sb.repeats || 1, kb: sa.repeats || 1 };
  }).filter(Boolean);
  if (pairs.length < PAIR_FLOOR) return null;
  const iv = pairedInterval(pairs);
  return {
    agent: key.agent,
    runIds: same.map((r) => r.id),
    n: pairs.length,
    disagreed: pairs.filter((x) => x.a !== x.b).length,
    noise: Math.abs(iv.delta),
  };
};

/** Pair info per comparison row, baseline against the newest run. */
export const withPairs = (comparison, env, envState, opts) => {
  const read = comparisonOf(comparison?.runs, env, envState, opts);
  if (!read) return { comparison, read: null };
  const rows = comparison.rows.map((row) => ({ ...row, pair: read.byId.get(row.id) || null }));
  return { comparison: { ...comparison, rows }, read };
};

/** Pair-class counts for one run against the baseline — the Summary view. */
export const pairCountsVsBaseline = (comparison, index, env, envState) => {
  const runs = comparison?.runs || [];
  if (!index || !runs[index]) return null;
  const read = comparisonOf([runs[0], runs[index]], env, envState);
  if (!read) return null;
  const regressed = read.pairs.filter((p) => p.cls === "exact" && p.flip.kind === "regressed").length;
  const improved = read.pairs.filter((p) => PAIR_CLASSES[p.cls].inDelta && p.flip.kind === "improved").length;
  return { ...read, regressed, improved };
};

/* ── before a run: the comparability preview ─────────────────────────────── */

const finished = (r) => r && !r.pending && r.status !== "running" && r.finishedAt && r.kind !== "trial";

/**
 * The default baseline for a new run of `agentVersion`: the latest finished
 * run of the previous agent version on this environment; failing that, the
 * latest finished run of any version.
 */
export const baselineRunFor = (envState, agentVersion) => {
  const runs = (envState?.runs || []).filter(finished)
    .sort((x, y) => Date.parse(y.finishedAt) - Date.parse(x.finishedAt));
  const list = agentVersions(envState);
  const at = list.findIndex((v) => v.label === agentVersion);
  const prev = at > 0 ? list[at - 1].label : null;
  const ofPrev = prev ? runs.find((r) => r.agentVersion === prev) : null;
  if (ofPrev) return { run: ofPrev, why: `the latest run of ${prev}, the previous version` };
  const any = runs[0];
  return any ? { run: any, why: any.agentVersion === agentVersion ? `the latest run, also on ${agentVersion}` : `the latest run (${any.agentVersion})` } : null;
};

/* What the workspace's own data says about spread, once it has some: the
   observed paired variance between the baseline and the run before it. */
const observedSpread = (env, envState, base) => {
  const runs = (envState?.runs || []).filter(finished).filter((r) => r.id !== base.run.id)
    .sort((x, y) => Date.parse(y.finishedAt) - Date.parse(x.finishedAt));
  const other = runs[0];
  if (!other) return null;
  const sa = runSummary(env, envState, other, 0);
  const pairs = base.summary.tasks.map((t) => {
    const o = sa.tasks.find((x) => x.id === t.id);
    const a = shareOf(o);
    const b = shareOf(t);
    return a == null || b == null ? null : { a, b, ka: sa.repeats, kb: base.summary.repeats };
  }).filter(Boolean);
  if (pairs.length < PAIR_FLOOR) return null;
  const iv = pairedInterval(pairs);
  return { s2: Math.max(iv.s2, iv.trial), k: (sa.repeats + base.summary.repeats) / 2 };
};

/**
 * What a run about to start will be able to say against its baseline.
 * { baseline, why, counts, total, comparable, half, tone, headline, options }
 *   tone: "green" | "amber" | "grey"
 */
export const previewOf = (env, envState, { agentVersion, scenarioIds, repeats = 3 } = {}) => {
  const agent = agentVersion || agentVersions(envState).slice(-1)[0]?.label || "v1";
  const live = (envState?.scenarios || []).filter((sc) => !sc.archived && !sc.retired);
  const ids = scenarioIds?.length ? scenarioIds : live.map((sc) => sc.id);
  const k = Math.max(1, Number(repeats) || 3);
  const found = baselineRunFor(envState, agent);
  if (!found) {
    return {
      baseline: null, tone: "grey", counts: classCounts([]), total: ids.length, comparable: 0, half: null,
      headline: "No comparable scenarios; this run starts a new baseline",
      detail: "Nothing has finished in this environment yet.",
      options: [{ id: "new-baseline", label: "Run as the first baseline", line: `${priceLine(priceOf(env, envState, ids.length, k))}`, primary: true }],
    };
  }
  const order = (envState.runs || []).filter(finished).sort((x, y) => Date.parse(x.finishedAt) - Date.parse(y.finishedAt));
  const summary = runSummary(env, envState, found.run, Math.max(0, order.findIndex((r) => r.id === found.run.id)));
  const next = {
    id: "preview", agentVersion: agent,
    envVersion: currentEnvVersion(env, envState)?.label,
    ...pinsForNewRun(env, envState),
    scenarioIds: ids, repeats: k,
    scenarioVersions: scenarioVersionsPin(envState),
    finishedAt: new Date().toISOString(),
  };
  const union = [...new Set([...(summary.tasks || []).map((t) => t.id), ...ids])];
  const pairs = union.map((id) => {
    const pc = pairClassOf(scenarioOf(envState, id) || { id }, summary, next, env, envState);
    return pc ? { ...pc, id } : null;
  }).filter(Boolean);
  const counts = classCounts(pairs);
  const comparable = counts.exact + counts.regraded + counts.rechecked;
  const spread = observedSpread(env, envState, { run: found.run, summary });
  const trial = (() => {
    const shares = summary.tasks.map(shareOf).filter((v) => v != null);
    if (summary.repeats < 2 || shares.length < PAIR_FLOOR) return DEFAULT_TRIAL;
    return Math.max(0.05, (mean(shares.map((p) => p * (1 - p))) * summary.repeats) / (summary.repeats - 1));
  })();
  const halfAt = (n, ka, kb) => {
    if (!n) return null;
    if (spread) {
      const v = Math.max(0.02, spread.s2 - (2 * trial) / spread.k + trial / ka + trial / kb);
      return Math.round(Z95 * Math.sqrt(v / n) * 1000) / 10;
    }
    return predictHalf(n, ka, kb, DEFAULT_BETWEEN, trial);
  };
  const half = halfAt(comparable, summary.repeats, k);
  const tone = !comparable ? "grey" : comparable >= PAIR_FLOOR && half <= 10 ? "green" : "amber";
  const baseLabel = `Run ${found.run.ordinal || ""} (${found.run.agentVersion})`.replace("Run  ", "Run ");

  const options = [];
  const evalsDiffer = pairKeyOf(summary, env, envState).eval !== pairKeyOf(next, env, envState).eval;
  if (evalsDiffer && comparable) {
    const credits = evalCreditsPerRun({ evals: evalsOf(envState), scenarios: comparable, repeats: summary.repeats });
    options.push({ id: "regrade", label: `Re-grade the baseline to ${evalVersionOf(envState)}`, line: `From stored traces, no calls · ≈ ${credits || comparable * 2} credits` });
  }
  const changedIds = pairs.filter((p) => p.cls === "changed").map((p) => p.id);
  const newIds = pairs.filter((p) => p.cls === "new").map((p) => p.id);
  const bridge = bridgeOf(env, envState, found.run.agentVersion);
  if ((changedIds.length || newIds.length) && found.run.agentVersion !== agent) {
    const m = changedIds.length + newIds.length;
    const p = priceOf(env, envState, m, k);
    const what = [changedIds.length && `${changedIds.length} changed`, newIds.length && `${newIds.length} new`].filter(Boolean).join(" and ");
    options.push({
      id: "bridge", label: `${changedIds.length ? "Bridge" : "Backfill"} ${what} on ${found.run.agentVersion}`,
      line: bridge.can ? `${priceLine(p)} · verdict to ±${halfAt(comparable + m, k, k)} points` : bridge.why,
      disabled: !bridge.can, scenarioIds: [...changedIds, ...newIds], agent: found.run.agentVersion, repeats: k,
    });
  }
  if (comparable >= PAIR_FLOOR && k < 10) {
    const kNext = k < 5 ? 5 : 10;
    options.push({
      id: "raise-k", label: `Raise k to ${kNext}`,
      line: `${priceLine(priceOf(env, envState, ids.length, kNext))} · verdict to ±${halfAt(comparable, summary.repeats, kNext)} points`,
      repeats: kNext,
    });
  }
  const comparableIds = pairs.filter((p) => PAIR_CLASSES[p.cls].inDelta && ids.includes(p.id)).map((p) => p.id);
  if (comparable && comparableIds.length < ids.length) {
    options.push({
      id: "only-comparable", label: `Run only the ${comparableIds.length} comparable`,
      line: `${priceLine(priceOf(env, envState, comparableIds.length, k))} · saves ${(ids.length - comparableIds.length) * k} calls`,
      scenarioIds: comparableIds,
    });
  }
  options.push({ id: "new-baseline", label: comparable ? "Run as planned" : "Proceed as a new baseline", line: priceLine(priceOf(env, envState, ids.length, k)), primary: true });

  return {
    baseline: { id: found.run.id, label: baseLabel, agentVersion: found.run.agentVersion, why: found.why, repeats: summary.repeats },
    agentVersion: agent,
    counts, total: pairs.length, comparable, half, tone,
    sentence: countsSentence(counts, pairs.length),
    headline: tone === "grey"
      ? "No comparable scenarios; this run starts a new baseline"
      : comparable < PAIR_FLOOR
        ? `Likely inconclusive: only ${comparable} comparable — row diffs, no aggregate`
        : tone === "green"
          ? `Verdict to ±${half} points`
          : `Likely inconclusive: ±${half} points`,
    options,
  };
};
