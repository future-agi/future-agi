/**
 * Prompt search, scored by the environment.
 *
 * The platform already has a prompt optimizer — six algorithms, trials, prompt
 * diffs, a growth curve. What it has never had is a good objective. Optimizing
 * against a dataset means maximising a proxy: the candidate that wins is the one
 * that words things best for a grader reading text, which is precisely the
 * failure the gaming analyzer exists to catch.
 *
 * An RL environment is a better objective because it is the same world. A trial
 * runs the scenario set at the same seed, through the same tools, against the
 * same evals — so the rate it reports is the rate the run reports, with no
 * translation in between.
 *
 * It is not, however, the whole decision, and saying it was would be the same
 * overstatement this file exists to avoid. Verification weighs mean return,
 * latency and cost alongside the pass rate; the search moves one of those. A
 * winner is a candidate for review, not a shipped change, and the screen says so.
 *
 * Two things keep that from turning into a lie:
 *
 *   The candidates are grounded. A trial's prompt is assembled from the changes
 *   Omega derived from this run's own evidence, combined in different subsets —
 *   not free-form text invented by a model that never saw the call logs.
 *
 *   The headline number is held out. A prompt tuned against nine scenarios and
 *   then scored on those same nine is not a prediction, it is a memory. The
 *   optimizer sees the training split only, and the number reported at the end
 *   comes from scenarios it never saw.
 */

import { rng, hashSeed } from "./runStream";
import { proposalsFor, skillProposalsFor, issueProposals, projectedRate, optimisable, generalisesTo, passShareOf, changeFileFor, SECTION_FOR } from "./optimize";
import { staleForAgentOf } from "./decoupled";
import { contractCandidateOf } from "./improvementPolicy";
import { withScenarioSetSeed } from "./scenarioSetSeed";
import { scenarioTitle } from "./scenarios";
import { mustPassOf, passesEveryRepeat, flakyToday as flakyTodayOf } from "./mustPass";

/* ── the algorithms ──────────────────────────────────────────────────────── */

/**
 * The same six the platform's optimizer offers, with what each one costs here.
 *
 * `trialsFor` matters more in this context than it does against a dataset: a
 * trial is a full run of the training split, so an algorithm that wants forty
 * metric calls is asking for forty scenario sweeps. Stated up front rather than
 * discovered when the bill arrives.
 */
export const OPTIMIZERS = [
  {
    id: "protegi",
    label: "ProTeGi",
    desc: "Textual gradients — reads what failed and edits toward it",
    fit: "Strongest when failures cluster into a few causes, which is what the diagnosis above found.",
    config: { numGradients: 4, errorsPerGradient: 4, beamSize: 4, numRounds: 3 },
    trialsFor: (c) => c.numRounds * c.beamSize,
  },
  {
    id: "metaprompt",
    label: "Meta-Prompt",
    desc: "A stronger model rewrites the prompt each round",
    fit: "Good first pass. Cheapest of the directed searches.",
    config: { numRounds: 4 },
    trialsFor: (c) => c.numRounds,
  },
  {
    id: "promptwizard",
    label: "PromptWizard",
    desc: "Mutate, critique, refine",
    fit: "Explores further from the starting prompt than the gradient methods.",
    config: { mutateRounds: 3, refineIterations: 2, beamSize: 2 },
    trialsFor: (c) => c.mutateRounds * c.beamSize + c.refineIterations,
  },
  {
    id: "gepa",
    label: "GEPA",
    desc: "Genetic Pareto — keeps runs that win on different scenarios",
    /* Every algorithm's winner skips candidates that broke a critical
       scenario or cheated — that filter is the search's, not GEPA's. */
    fit: "Useful when fixes pull in different directions — keeps each run that wins somewhere.",
    config: { maxMetricCalls: 40 },
    trialsFor: (c) => Math.round(c.maxMetricCalls / 4),
  },
  {
    id: "bayesian",
    label: "Bayesian Search",
    desc: "Optuna over few-shot examples and phrasing",
    fit: "Needs more runs than the others to beat them.",
    config: { minExamples: 2, maxExamples: 4, nTrials: 12 },
    trialsFor: (c) => c.nTrials,
  },
  {
    id: "random_search",
    label: "Random Search",
    desc: "Simple random variations",
    fit: "The baseline the others have to beat. Worth running once to know they did.",
    config: { numVariations: 8 },
    trialsFor: (c) => c.numVariations,
  },
];

export const optimizerById = (id) => OPTIMIZERS.find((o) => o.id === id) || OPTIMIZERS[0];

/* ── the scope ───────────────────────────────────────────────────────────── */

const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/**
 * Which of a run's scenarios a search may use, and how — PRD v7 §4.10.9.
 *
 *   archived    doesn't run at all.
 *   stale       Outdated, Re-check, or stale for the agent version being
 *               improved (a tool it needs was removed) — counts toward no
 *               decision, so left out.
 *   proposed    a candidate that hasn't joined the set — left out. The one
 *               exception is a fix's own scenarios (`fromIssue`): production-
 *               mined ones can still be proposed, and they are what the fix
 *               is for, so they stay in as its target.
 *
 * `holdable` says which of the kept scenarios may be held back.
 */
export const searchScope = ({ env, envState, tasks = [], agentVersion = null }) => {
  /* Read through the same seed migration every screen reads. */
  const es = withScenarioSetSeed(env?.id, envState) || envState;
  const rows = new Map((es?.scenarios || []).map((r) => [r.id, r]));
  const rowOf = (t) => rows.get(t.id) || rows.get(t.scenarioId) || t;
  const staleFor = (row) => !!agentVersion && staleForAgentOf(row, agentVersion, env, es).stale;

  const left = { proposed: 0, archived: 0, stale: 0 };
  const kept = [];
  let proposedTargets = 0;
  tasks.forEach((t) => {
    const row = rowOf(t);
    /* Rows from the removed cheat checks never reach a search. */
    if (row.canary || t.canary) return;
    if (row.archived) left.archived += 1;
    /* Outdated, Re-check or Stale: it counts toward no decision. */
    else if (isStale(row) || staleFor(row)) left.stale += 1;
    else if (row.proposed && !t.fromIssue) left.proposed += 1;
    else {
      kept.push(t);
      if (row.proposed) proposedTargets += 1;
    }
  });
  return {
    tasks: kept,
    holdable: (t) => !rowOf(t).proposed,
    anchor: () => false,
    /* A scenario and its variations — one group always lands on one side. */
    groupOf: (t) => rowOf(t).groupId || t.groupId || t.id,
    /* Kept as must-pass by an earlier fix's release. */
    remembered: (t) => !!rowOf(t).regressionMemory,
    left,
    proposedTargets,
  };
};

/* Outdated or Re-check — the scenario's grading no longer matches the set. */
const isStale = (row) => !!(row?.outdatedBy || row?.subGoalsUnproved || ["outdated", "recheck"].includes(row?.state));

/* ── the split ───────────────────────────────────────────────────────────── */

/**
 * Training and held-out scenarios.
 *
 * Holdouts come from the scenarios in the set (`holdable`); a fix's
 * still-proposed scenarios go to training. What's held back is
 * never shown to a candidate.
 *
 * Stratified on two things, because stratifying on one of them produced a
 * held-out set that could not do its job. Critical scenarios are dealt into
 * both halves — a split holding every one back optimizes against routine work
 * only, and one holding none back reports a number that says nothing about
 * the scenarios that matter most.
 *
 * Failures are dealt into both halves too, and that was the omission. The
 * comment already claimed both halves "look like the suite" while the code
 * split on criticality alone, so a seed could hand every failing scenario to
 * training and leave a held-out set where everything already passed. Such a set
 * cannot show an improvement — the only thing it can report is a regression —
 * and the whole point of holding scenarios back is to find out whether a change
 * generalises.
 */
/*
  A seeded Fisher–Yates shuffle. `sort(() => r() - 0.5)` looked the same but
  its result depends on the engine's sort, so one seed dealt a different split
  in Chrome, Safari and Node — the same improvement read differently by
  browser. This one draws exactly one number per position, everywhere.
*/
const shuffle = (list, r) => {
  const out = [...list];
  for (let i = out.length - 1; i > 0; i -= 1) {
    const j = Math.floor(r() * (i + 1));
    [out[i], out[j]] = [out[j], out[i]];
  }
  return out;
};

export const splitScenarios = (all = [], seed = "split", holdable = () => true) => {
  const tasks = all.filter(holdable);
  const trainOnly = all.filter((t) => !holdable(t));
  const r = rng(hashSeed(seed));
  const deal = (list) => {
    const shuffled = shuffle(list, r);
    const cut = Math.max(1, Math.round(shuffled.length * 0.65));
    return [shuffled.slice(0, cut), shuffled.slice(cut)];
  };

  const failing = (t) => t.status !== "unmeasured" && passShareOf(t) < 1;
  const bucket = (crit, fail) =>
    tasks.filter((t) => !t.fromIssue && !!t.critical === crit && failing(t) === fail);
  /*
    A fix's own scenarios — built from an Error Feed issue's traces — are
    dealt last and into both halves, so the held-back set always has some of
    the issue in it. Without that, "fixed on the held-back set" would say
    nothing about the issue the fix was for. Dealing them last leaves the
    environment's own split exactly where it was.
  */
  const issue = (fail) => tasks.filter((t) => t.fromIssue && failing(t) === fail);

  const dealt = [
    deal(bucket(true, true)),
    deal(bucket(true, false)),
    deal(bucket(false, true)),
    deal(bucket(false, false)),
    deal(issue(true)),
    deal(issue(false)),
  ];

  return {
    /* What may not be held back trains — after the deal, so a suite that is
       all core splits exactly as it always did. */
    train: [...dealt.flatMap(([a]) => a), ...trainOnly],
    held: dealt.flatMap(([, b]) => b),
  };
};

/**
 * The split for an issue fix — the updated PRD's three scenario sets.
 *
 *   held back   35% of the issue's reproducing scenario groups, plus one fresh
 *               variation of each held-back group, written after the search
 *               and kept only because it reproduces on today's agent.
 *   target      the other 65% of reproducing groups — all the improver sees
 *               in full (`target`).
 *   must-pass   the rest of the suite, read in the search: marked must-pass,
 *               regression memory, and everything that passes every repeat
 *               today — pass or fail only, run for finalists.
 *
 * `train` (targets plus everything else) is what improvements recorded
 * before the three sets were kept apart still search on.
 *
 * A group is a scenario and its variations (`groupOf`), and it always lands
 * on one side, so a near-copy of a held-back scenario can't leak into
 * training. A fix's own scenarios split this way even while proposed — the
 * last round made them always train, which left "fixed on the held-back set"
 * resting on scenarios the search had already seen.
 *
 * When too few groups reproduce to hold any back (one), nothing is held back
 * and the result says the held-back check is thin.
 */
export const HELD_SHARE = 0.35;
/*
  An Error Feed scenario that failed once in three tries is inconclusive, not
  a reproduction — it's no target, so it never makes a group reproduce (new
  improvements only: `inconclusiveOut`).
*/
export const isInconclusive = (t) => !!t && (t.reproduce === "inconclusive" || t.reproduce === "not-reproduced" || (!!t.fromIssue && t.status === "flaky"));
/*
  Environment scenarios held back with a fix (new improvements): the rest of
  the suite's scenarios that fail today the way the issue does — a tool the
  issue's scenarios skip, or a claim with no tool behind it. A handful of the
  issue's own scenarios can't prove a fix; these widen the set it's proven on.
*/
export const ENV_HELD_MAX = 8;
export const splitIssueScenarios = (all = [], seed = "split", { groupOf = (t) => t.id, inconclusiveOut = false, sameFailure = false } = {}) => {
  const issue = all.filter((t) => t.fromIssue);
  const others = all.filter((t) => !t.fromIssue);
  const failing = (t) => t.status !== "unmeasured" && passShareOf(t) < 1 && !(inconclusiveOut && isInconclusive(t));
  const groups = new Map();
  issue.forEach((t) => {
    const g = groupOf(t);
    if (!groups.has(g)) groups.set(g, []);
    groups.get(g).push(t);
  });
  const reproducing = [...groups.entries()].filter(([, list]) => list.some(failing));
  const quiet = [...groups.entries()].filter(([, list]) => !list.some(failing));
  const r = rng(hashSeed(seed));
  const dealt = shuffle(reproducing, r);
  const heldN = Math.min(Math.round(dealt.length * HELD_SHARE), Math.max(0, dealt.length - 1));
  const heldGroups = dealt.slice(0, heldN);
  const trainGroups = dealt.slice(heldN);
  const issueFailing = issue.filter(failing);
  const skipped = new Set(issueFailing.flatMap((t) => t.callLog?.missing || []));
  const claims = issueFailing.some((t) => t.callLog?.unsupportedClaim);
  const failsSameWay = (t) => (t.callLog?.missing || []).some((m) => skipped.has(m)) || (claims && !!t.callLog?.unsupportedClaim);
  const envHeld = sameFailure && heldN
    ? shuffle(others.filter((t) => !t.proposed && failing(t) && !isInconclusive(t) && failsSameWay(t)), r)
      .slice(0, ENV_HELD_MAX)
    : [];
  const envHeldIds = new Set(envHeld.map((t) => t.id));
  const rest = others.filter((t) => !envHeldIds.has(t.id));
  /* One fresh variation per held-back group: the same failure, worded anew. */
  const fresh = heldGroups.map(([g, list]) => {
    const rep = list.find(failing) || list[0];
    return {
      ...rep,
      id: `${g}::fresh`,
      title: `Fresh variation · ${scenarioTitle(rep.title)}`,
      groupId: g,
      fresh: true,
      proposed: true,
      status: "failed",
      passShare: 0,
    };
  });
  return {
    train: [...trainGroups.flatMap(([, list]) => list), ...quiet.flatMap(([, list]) => list), ...rest],
    /* The target set alone — the reproducing groups not held back. New
       improvements keep it apart from the rest of the suite. */
    target: trainGroups.flatMap(([, list]) => list),
    held: [...heldGroups.flatMap(([, list]) => list), ...fresh, ...envHeld.map((t) => ({ ...t, envHeld: true }))],
    groups: { reproducing: dealt.length, held: heldN, train: trainGroups.length, fresh: fresh.length, ...(sameFailure && { envHeld: envHeld.length }) },
    thin: heldN === 0,
  };
};

/**
 * The same split for a goal or a run's failures (new improvements only):
 * 35% of the groups that fail the goal are held back, plus one fresh
 * variation of each, and a group always lands on one side. Only a group
 * whose every scenario may be held back (not proposed) can be;
 * the rest train. Too few failing groups to hold any back reads as thin.
 */
export const splitGoalScenarios = (all = [], seed = "split", { groupOf = (t) => t.id, isTarget = () => false, holdable = () => true } = {}) => {
  const groups = new Map();
  all.forEach((t) => {
    const g = groupOf(t);
    if (!groups.has(g)) groups.set(g, []);
    groups.get(g).push(t);
  });
  const failingGroups = [...groups.entries()].filter(([, list]) => list.some(isTarget) && list.every(holdable));
  const failingIds = new Set(failingGroups.map(([g]) => g));
  const quiet = [...groups.entries()].filter(([g]) => !failingIds.has(g));
  const r = rng(hashSeed(seed));
  const dealt = shuffle(failingGroups, r);
  const heldN = Math.min(Math.round(dealt.length * HELD_SHARE), Math.max(0, dealt.length - 1));
  const heldGroups = dealt.slice(0, heldN);
  const trainGroups = dealt.slice(heldN);
  const fresh = heldGroups.map(([g, list]) => {
    const rep = list.find(isTarget) || list[0];
    return {
      ...rep,
      id: `${g}::fresh`,
      title: `Fresh variation · ${scenarioTitle(rep.title)}`,
      groupId: g,
      fresh: true,
      proposed: true,
      status: "failed",
      passShare: 0,
    };
  });
  return {
    train: [...trainGroups.flatMap(([, list]) => list), ...quiet.flatMap(([, list]) => list)],
    target: trainGroups.flatMap(([, list]) => list),
    held: [...heldGroups.flatMap(([, list]) => list), ...fresh],
    groups: { reproducing: dealt.length, held: heldN, train: trainGroups.length, fresh: fresh.length },
    thin: heldN === 0,
  };
};

/* ── what a goal asks for ────────────────────────────────────────────────── */

/**
 * Whether a scenario falls short of the improvement's goal — the scenarios a
 * goal improvement targets. The pass rate: any scenario not passing every
 * repeat. An evaluation: a scenario that eval failed on (any failure when the
 * eval wasn't recorded). An outcome read from tool calls: a scenario that
 * needed the tool and never called it.
 */
export const failsGoal = (t, goal) => {
  if (!t || t.status === "unmeasured") return false;
  const failing = passShareOf(t) < 1;
  if (!goal || goal.id === "pass-rate" || goal.group === "rate") return failing;
  if (goal.group === "outcome" || String(goal.id).startsWith("outcome:")) {
    const tool = String(goal.id).replace(/^outcome:/, "");
    return (t.callLog?.missing || []).includes(tool);
  }
  const ev = (t.evalResults || []).find((e) => e.id === goal.id || e.name === goal.label || e.name === goal.id);
  return ev ? ev.passed === false : failing;
};

/** "the pass rate", "Booking success rate", "Policy adherence" — for a sentence. */
export const goalWords = (goal) => (!goal || goal.id === "pass-rate" ? "the pass rate" : goal.label || goal.id);

/* ── settings, typed ─────────────────────────────────────────────────────── */

/*
  The platform schema for the settings a search may edit: a settings change
  is a typed edit within these values, and a value outside them is rejected
  at Static checks.
*/
export const SETTING_SCHEMA = {
  temperature: { label: "Temperature", min: 0, max: 1, step: 0.1, area: "model" },
  interruptionSensitivity: { label: "Interruption sensitivity", min: 0.1, max: 0.9, step: 0.05, area: "turnTaking" },
  silenceTimeoutMs: { label: "Silence before answering", min: 200, max: 1500, step: 50, unit: "ms", area: "turnTaking" },
  topK: { label: "Retrieved passages", min: 1, max: 10, step: 1, area: "knowledge" },
};
export const settingRange = (key) => {
  const s = SETTING_SCHEMA[key];
  return s ? `${s.min}–${s.max}${s.unit ? ` ${s.unit}` : ""}` : null;
};
/* The typed setting a proposal edits, read off its diff. */
const settingOf = (p) => {
  if (p.kind !== "Agent setting") return null;
  const line = (p.diff || []).find((d) => d.type === "add")?.text || "";
  const temp = /temperature:\s*([\d.]+)/i.exec(line);
  if (temp) return { key: "temperature", value: Number(temp[1]) };
  const ms = /after\s*(\d+)\s*ms/i.exec(line);
  if (ms) return { key: "silenceTimeoutMs", value: Number(ms[1]) };
  const sens = /sensitivity:\s*([\d.]+)/i.exec(line);
  if (sens) return { key: "interruptionSensitivity", value: Number(sens[1]) };
  return null;
};
/* Now and then the proposer writes a value the platform doesn't accept — by
   hash on the run and the change, so a new search sometimes shows one. */
const OUT_OF_RANGE = { temperature: 1.4, silenceTimeoutMs: 2400, interruptionSensitivity: 0.95, topK: 14 };
const typedSetting = (seed, p) => {
  const s = settingOf(p);
  if (!s) return null;
  const schema = SETTING_SCHEMA[s.key];
  const value = hashSeed(`${seed}:${p.id}:range`) % 9 === 0 ? OUT_OF_RANGE[s.key] : s.value;
  return { ...s, value, label: schema.label, range: settingRange(s.key), inRange: value >= schema.min && value <= schema.max };
};

/* ── configuration areas ─────────────────────────────────────────────────── */

/* Which of the twelve configuration areas a change edits (null: code). */
const SETTING_AREA = { turn_taking: "turnTaking", model_settings: "model", retrieval: "knowledge", voice: "speechOut", transcriber: "speechIn", variables: "variables" };
export const areaOfProposal = (p) => {
  if (!p) return null;
  if (p.area) return p.area;
  if (p.kind === "System prompt") return ["Opening", "Greeting"].includes(SECTION_FOR(p)) ? "opening" : "instructions";
  if (p.kind === "Tool description") return "tools";
  if (p.kind === "Agent setting") return SETTING_AREA[p.step] || (settingOf(p) && SETTING_SCHEMA[settingOf(p).key]?.area) || "model";
  if (p.kind === "Model") return "model";
  if (p.kind === "Architecture") return "structure";
  return null;
};

/* ── Static checks ───────────────────────────────────────────────────────── */

/*
  Static checks, before anything runs (new improvements only): the model
  lock, the protected parts, the switched-off configuration areas and their
  sub-rules, settings within their allowed values, approved speech
  providers, nothing touching the judges, and a code change only with the
  repository's tests. The real rules are checked as they are; now and then a
  candidate trips one by hash, the way a real proposer sometimes does.
*/
const SPEECH_PROVIDERS = ["Deepgram", "AssemblyAI", "Whisper", "ElevenLabs", "Cartesia", "Azure Speech"];
const staticOf = (key, candidate, cfg) => {
  const fail = (kind, reason) => ({ pass: false, kind, reason });
  const rsi = cfg.rsi || {};
  const off = new Set(rsi.areasOff || []);
  for (const p of candidate) {
    if (p.setting && !p.setting.inRange) return fail("range", `Sets ${p.setting.label.toLowerCase()} to ${p.setting.value} — allowed ${p.setting.range}.`);
    if (p.area && off.has(p.area)) return fail("area", `Changes ${rsi.areaLabels?.[p.area] || p.area}, which this agent doesn't allow.`);
    if (p.kind === "Model" && !(cfg.models || []).includes(p.model)) return fail("model", `Switches the model to ${p.model} while the model is locked.`);
    if ((p.addsTool || p.removesTool) && !(rsi.codeAgent && rsi.areaRules?.toolsAddRemove)) {
      return fail("tools", rsi.codeAgent
        ? `${p.addsTool ? "Adds" : "Removes"} a tool, which this agent's Tools area doesn't allow.`
        : `${p.addsTool ? "Adds" : "Removes"} a tool — only code agents can add or remove tools.`);
    }
    if (p.whoSpeaksFirst && !rsi.areaRules?.whoSpeaksFirst) return fail("opening", "Changes who speaks first, which this agent's Opening and closing area doesn't allow.");
    if (p.area === "variables" && !p.defaultOnly) return fail("variables", "Changes a variable's value for every conversation — only its default may change.");
  }
  const h = hashSeed(`${key}:static`);
  const prot = (cfg.protected || []);
  const promptChange = candidate.find((p) => p.kind === "System prompt");
  if (prot.length && promptChange && h % 29 === 0) {
    const x = prot[h % prot.length];
    const what = x.startsWith("section:") ? `prompt section '${x.split("#")[1]}'` : x.startsWith("tool:") ? `tool ${x.slice(5)}` : x.replace(/^.*\//, "");
    return fail("protected", `Edits ${what}, which is protected.`);
  }
  if (h % 47 === 7) {
    const evals = rsi.evalNames?.length ? rsi.evalNames : ["Task success"];
    const name = evals[h % evals.length];
    return fail("judges", h % 2 ? `Edits the pass threshold of the ${name} evaluation — the improver may not change its judges.` : `Edits scenarios/ — a scenario rewritten to pass. Scenarios and evaluations are frozen.`);
  }
  const speech = candidate.find((p) => ["speechIn", "speechOut", "turnTaking"].includes(p.area));
  if (speech && (rsi.approvedSpeech || []).length && h % 7 === 3) {
    const approved = rsi.approvedSpeech;
    const other = SPEECH_PROVIDERS.find((x) => !approved.includes(x)) || "Whisper";
    return fail("speech", `Switches the ${speech.area === "speechOut" ? "voice" : "transcriber"} to ${other}, which isn't an approved speech provider (${approved.join(", ")}).`);
  }
  const code = candidate.some((p) => ["code", "structure"].includes(KIND_SURFACE[p.kind] || "code"));
  if (code && !cfg.repoTests) return fail("repoTests", "A code change needs the repository's own tests, and none are configured.");
  return { pass: true };
};
/*
  What a run tried, in plain words. A repeat names the run whose change it
  repeats; records written before this stored the engine's own words.
*/
export const triedLabelOf = (t, trials = []) => {
  const raw = t?.tried;
  if (!raw) return null;
  if (/^re-ran the incumbent|^ran the best change again/.test(raw)) {
    const prev = trials.filter((x) => x.n < t.n && x.improved).pop();
    return prev ? `Trial ${prev.n}'s change again, to check it holds` : "The best change again, to check it holds";
  }
  if (/^dropped a change|^removed one change/.test(raw)) return "One change removed, to check it's needed";
  if (t.feedback) return `Revised: ${t.feedback.why}`;
  const m = /^(kept|tried) (.+)$/.exec(raw);
  const what = m ? m[2] : raw;
  return m ? `Tried: ${what}` : `${what.charAt(0).toUpperCase()}${what.slice(1)}`;
};

/** A candidate stopped at Static checks. */
export const staticFailed = (t) => !!t?.static && t.static.pass === false;

/* ── baseline twice ──────────────────────────────────────────────────────── */

/*
  Calibration (new improvements only): the current version run against
  itself, twice, before any candidate. A scenario whose two runs disagree is
  noisy — a flip there counts only when it repeats. Which ones disagree is
  read off the scenario and the run, never the search's stream; a scenario
  that's flaky today disagrees far more often than one that isn't.
*/
export const calibrate = (seed, measured = [], version, todayRuns = []) => {
  const noisy = measured.filter((t) => {
    const share = passShareOf(t);
    const odds = share > 0 && share < 1 ? 55 : 4;
    return hashSeed(`${seed}:calibrate:${t.id}`) % 100 < odds;
  });
  const n = measured.length;
  /* Runs of this version that already played some of these scenarios once
     each — held-back scenarios are fresh variants, so a run covers only the
     rest. */
  const reused = todayRuns
    .map(({ id, label, scenarioIds }) => ({
      id,
      label,
      covered: measured.filter((t) => scenarioIds.includes(t.id)).length,
    }))
    .filter((r) => r.covered)
    .slice(0, 2);
  return {
    runs: 2,
    ...(reused.length && { reused }),
    version: version || null,
    scenarios: n,
    disagreed: noisy.length,
    noisyIds: noisy.map((t) => t.id),
    /* Two runs of the same agent differ by about half the scenarios that
       disagree, as points of rate. */
    noise: n ? Math.round((100 * noisy.length) / (2 * n)) : 0,
  };
};

/* ── finalists in the other environments ─────────────────────────────────── */

/* A finalist runs once in each other environment picked; each must-pass set
   there has to hold. Drawn by hash on the finalist and the environment. */
const otherEnvRun = (key, e) => {
  const h = hashSeed(`${key}:env:${e.id}`);
  const list = e.mustPass || [];
  const broke = list.length && h % 9 === 0 ? list[h % list.length] : null;
  const repeated = broke ? hashSeed(`${key}:env:${e.id}:rerun`) % 100 < 50 : false;
  return { envId: e.id, name: e.name, mustPass: list.length, scenarios: e.scenarios || list.length, broke: broke ? { id: broke.id, title: broke.title, repeated } : null, held: !repeated };
};

/* ── GEPA's pool ─────────────────────────────────────────────────────────── */

/**
 * GEPA keeps the best candidate for each scenario rather than one overall
 * best — the pool is every eligible trial that is the best on at least one
 * training scenario it fixed.
 */
export const paretoPool = (result) => {
  const trials = (result?.trials || []).filter((t) => !t.brokeBlocker && !t.cheats && !compatFailed(t) && !staticFailed(t) && !overLimitFailed(t) && !(t.repoTests && !t.repoTests.pass));
  const tasks = result?.trainMeasuredTasks || [];
  const byTrial = new Map();
  tasks.forEach((s) => {
    const fixers = trials.filter((t) => t.perScenario?.[s.id] === "fixed");
    if (!fixers.length) return;
    const best = fixers.reduce((a, t) => (t.score > a.score || (t.score === a.score && t.n < a.n) ? t : a), fixers[0]);
    if (!byTrial.has(best.n)) byTrial.set(best.n, { n: best.n, score: best.score, scenarios: [] });
    byTrial.get(best.n).scenarios.push(s.title);
  });
  return [...byTrial.values()].sort((a, b) => b.scenarios.length - a.scenarios.length || a.n - b.n);
};

/* ── the search ──────────────────────────────────────────────────────────── */

/**
 * One optimizer run.
 *
 * Every candidate is a subset of the changes Omega derived, so a trial's prompt
 * can be read line by line and argued with. The score is what that subset would
 * do to the training split — the same union arithmetic the projection uses —
 * carrying trial noise, because a real run of a real agent does not return the
 * same number twice.
 */
/**
 * A run's rate, read off the per-scenario outcomes.
 *
 * The same measure the rest of the product uses — the mean of per-scenario pass
 * proportions — so a trial's score, the starting rate and the projection are
 * all the same kind of number and can be put beside each other.
 */
/*
  The simulated caller went off script (new improvements): the conversation
  stopped testing the scenario, so it says nothing about the change — shown,
  never counted, like the run screens' own off-script calls. Drawn by hash,
  so the search's own streams stay where they were.
*/
const OFF_SCRIPT_PCT = 3;
const offScript = (key) => hashSeed(`${key}:script`) % 100 < OFF_SCRIPT_PCT;

const scoreOf = (all, perScenario, cheated = new Set()) => {
  const tasks = all.filter((t) => perScenario[t.id] !== "off-script");
  if (!tasks.length) return 0;
  const after = (t) => {
    /* fail · cheat scores 0, whatever the scenario passed before. */
    if (cheated.has(t.id)) return 0;
    const state = perScenario[t.id];
    if (state === "fixed") return 1;
    if (state === "broke") return 0;
    /* Passed two repeats of three; an unscored one keeps what it had. */
    if (state === "flaky") return Math.max(passShareOf(t), 2 / 3);
    return passShareOf(t);
  };
  return Math.round((tasks.reduce((a, t) => a + after(t), 0) / tasks.length) * 100);
};

/* What part of the agent a change touches — what an agent's limits allow or not. */
export const KIND_SURFACE = {
  "System prompt": "prompt",
  "Tool description": "tools",
  Memory: "code",
  Architecture: "structure",
  "Agent setting": "settings",
  Skill: "skills",
  /* Not one of the kinds an agent allows — a model switch is tried only when
     its constraints name an approved list, and only to models on it. */
  Model: "model",
};

/*
  Switching to an approved model, as a candidate change. Which failing
  scenarios a stronger or different model gets right is a property of the
  model and the scenario, not of the search — so it's decided by both ids,
  the same way on every run.
*/
const modelProposals = (models, failing) => models.map((m) => ({
  id: `model-${m}`,
  kind: "Model",
  title: `Switch the model to ${m}`,
  why: `${m} is on this agent's approved list. Tried like any other change: it has to fix more than it breaks, within the cost and latency limits.`,
  diff: [{ type: "add", text: `model: ${m}` }],
  addresses: failing.filter((t) => modelHelps(m, t)).map((t) => t.id),
  model: m,
}));
const modelHelps = (m, t) => hashSeed(`${m}:${t.id}`) % 3 === 0;

/*
  Search across the lineage: changes earlier versions made, offered again as
  candidates — a rule a later edit dropped, or wording that worked before.
  Each is a prompt change, so it's only tried when prompts may change.
*/
const lineageProposals = (lineage, failing) => lineage.flatMap((v) => (v.changes || []).map((c, i) => {
  const id = `lineage-${v.label}-${i}`;
  return {
    id,
    kind: "System prompt",
    title: `Bring back ${v.label}'s change: ${String(c.title || "instructions").replace(/\.$/, "").toLowerCase()}`,
    why: `${v.label} ${c.detail ? `${c.detail.charAt(0).toLowerCase()}${c.detail.slice(1)}` : "made this change"}. Tried again from the agent's history rather than written fresh.`,
    diff: [{ type: "add", text: (c.addendum || c.detail || c.title || "").replace(/^- /, "") }],
    addresses: failing.filter((t) => hashSeed(`${id}:${t.id}`) % 3 === 0).map((t) => t.id),
    fromVersion: v.label,
  };
}));

/* A protected entry names the agent's file ("prompts/system.md"); a change
   names it under the environment ("<envId>/prompts/system.md"). Either matches. */
export const isProtected = (path, protectedPaths = []) => !!path
  && protectedPaths.some((x) => path === x || path.endsWith(`/${x}`));

/*
  Protected parts are files, tools ("tool:<name>") or prompt sections
  ("section:<file>#<heading>"). A change is off-limits when it edits any of
  them: its file, the tool it describes, or the section of the prompt it
  rewrites.
*/
const fileName = (x) => x.replace(/^.*\//, "");
export const protectedHit = (proposal, list = []) => {
  if (!list.length || !proposal) return null;
  if (isProtected(proposal.path, list.filter((x) => !/^(tool|section):/.test(x)))) return proposal.path;
  if (proposal.kind === "Tool description" && proposal.step && list.includes(`tool:${proposal.step}`)) return `tool ${proposal.step}`;
  /* A system-prompt change edits the agent's main prompt, whatever its file
     is called; headings written as "Rules", "Tools" or "Style" are the same
     sections as Policies, Tool use and Closing. */
  const section = SECTION_FOR(proposal);
  const ALIAS = { Rules: "Policies", Tools: "Tool use", Style: "Closing" };
  const sec = section && list.find((x) => x.startsWith("section:") && (ALIAS[x.split("#")[1]] || x.split("#")[1]) === section);
  return sec ? `${fileName(sec.slice(8).split("#")[0])} › ${sec.split("#")[1]}` : null;
};

/* How deep a change goes — a search prefers the shallowest that verifies. */
const DEPTH = { prompt: 0, tools: 1, skills: 1, settings: 2, model: 2, code: 3, structure: 4 };
export const depthOf = (proposals) => Math.max(0, ...proposals.map((p) => DEPTH[KIND_SURFACE[p.kind] || "code"] ?? 3));

/*
  Outcomes a repeat can't settle. A scenario that passed some repeats and
  failed others is flaky; one the judge couldn't score is inconclusive.
  Neither counts as fixed — inconclusive is never a pass.
*/
const unsettled = (key) => {
  const h = hashSeed(key) % 100;
  return h < 4 ? "flaky" : h < 6 ? "inconclusive" : null;
};

/* How many finalists are scored on the held-back set, once, at the end. */
export const FINALISTS = 3;

/*
  Stopping early (new improvements): once this many runs in a row don't beat
  the best so far on the target scenarios by more than run-to-run noise (the
  baseline-twice calibration), the search stops and the best goes straight
  to verifying — never before MIN_RUNS_BEFORE_STOP, so a flaky first pair
  can't end it. The planned run count is a maximum. Older records keep the
  rule they were searched with — four in a row, after six.
*/
export const NO_GAIN_RUNS = 2;
export const MIN_RUNS_BEFORE_STOP = 4;
/*
  Most searches keep finding gains — a directed search builds on the best
  so far, so a run that follows one with no gain usually lands. About one in
  five plateaus instead (fixed per improvement, by its seed), and the stop
  rule fires there. The Clinic line's agent is already near its ceiling: its
  searches always plateau.
*/
const PLATEAU_SHARE = 5;
const PLATEAU_ENVS = new Set(["env-clinic-line"]);
export const plateausFor = (seed, envId) => PLATEAU_ENVS.has(envId) || hashSeed(`${seed}:plateau`) % PLATEAU_SHARE === 0;
const LEGACY_NO_GAIN = { runs: 4, after: 6 };
/* How many runs a search could have used — the planned count, before any early stop. */
export const maxRunsOf = (result) => result?.stoppedEarly?.of || (result?.trials || []).length;
/* A stop on runs that stopped gaining while there was still something to
   gain — the "Stopped early" ending. Null for older records and for a
   search that stopped because every failing target was already fixed. */
export const plateauStopOf = (result) => (result?.stoppedEarly?.window && !result.stoppedEarly.complete ? result.stoppedEarly : null);
/* The rule a stop was made under — its own window, or the older four. */
export const noGainRunsOf = (stoppedEarly) => stoppedEarly?.window || LEGACY_NO_GAIN.runs;

/*
  A must-pass failure counts only when it repeats on a re-run — one bad run
  isn't a regression. Decided by hash on the trial and the scenario, never
  from the search's stream.
*/
const repeatsOnRerun = (key) => hashSeed(`${key}:rerun`) % 100 < 20;

/*
  The repository's own tests, run on every code candidate as a static check.
  Most pass; a change that breaks them stops there.
*/
const repoTestsPass = (key) => hashSeed(`${key}:repo-tests`) % 9 !== 0;

/*
  Fits the environment — the sealed environment was built from one commit:
  its image, its install steps, its entry point and its start command. A
  candidate that moves the entry point, changes the build (Dockerfile,
  lockfiles, a dependency that needs the image rebuilt) or changes how the
  agent is started can't be tested in it, so it stops there, before any
  scenario runs. Only the agent's own code is checked — prompt, tool
  descriptions and settings never touch the build.

  The prototype has no real diff, so which candidates do this is drawn by
  hash on the run and the change, never from the search's stream: now and
  then a structural change is written as a new router module that becomes
  the entry point, or with a new start command, and now and then a code
  change pulls in a new dependency. A change whose own files include a
  build file always fails.
*/
export const BUILD_FILES = /(^|\/)(Dockerfile|docker-compose\.ya?ml|requirements[^/]*\.txt|package(-lock)?\.json|yarn\.lock|pnpm-lock\.yaml|pyproject\.toml|poetry\.lock|uv\.lock|setup\.py)$/i;
const COMPAT_SURFACES = ["code", "structure"];
const dirOf = (path) => (String(path).includes("/") ? String(path).replace(/\/[^/]*$/, "") : "agent");
const DEPENDENCY_FOR = {
  Memory: { python: ["redis>=5.0", "requirements.txt"], node: ["ioredis@5", "package.json"] },
  Architecture: { python: ["langgraph-checkpoint-postgres", "requirements.txt"], node: ["@langchain/langgraph-checkpoint-postgres", "package.json"] },
};
const compatOf = (key, candidate, { entry, envVersion, runtime } = {}) => {
  const own = candidate.filter((p) => COMPAT_SURFACES.includes(KIND_SURFACE[p.kind] || "code"));
  if (!own.length) return null;
  const from = String(entry || "agent/main.py").replace(/:.*$/, "");
  const node = runtime === "node" || /\.(t|j)s$/.test(from);
  const sealed = `the sealed environment ${envVersion || "version"}`;
  const tail = `it no longer fits ${sealed}. Rebuild the environment from this commit to test it.`;
  /* A change that edits a build file itself. */
  const buildFile = own.flatMap((p) => [p.path, ...(p.files || []).map((f) => f.path || f)]).find((f) => f && BUILD_FILES.test(f));
  if (buildFile) {
    const name = buildFile.replace(/^.*\//, "");
    return { pass: false, kind: "build", file: name, reason: `Edits ${name}, which changes the build — ${tail}` };
  }
  for (const p of own) {
    const h = hashSeed(`${key}:${p.id}:fits`);
    if (p.kind === "Architecture" && h % 16 === 0) {
      const to = `${dirOf(from)}/router${node ? ".ts" : ".py"}`;
      return { pass: false, kind: "entry", from, to, reason: `Moves the entry point from ${from} to ${to} — ${tail}` };
    }
    if (p.kind === "Architecture" && h % 16 === 8) {
      const cmd = node ? `node ${dirOf(from)}/router.js` : `uvicorn ${dirOf(from).replace(/\//g, ".")}.router:app`;
      return { pass: false, kind: "run", to: cmd, reason: `Changes the start command to ${cmd} — ${tail}` };
    }
    const dep = DEPENDENCY_FOR[p.kind]?.[node ? "node" : "python"];
    if (dep && h % 20 === 0) {
      return { pass: false, kind: "build", file: dep[1], dep: dep[0], reason: `Adds ${dep[0]} to ${dep[1]}, which needs a rebuild of the sealed image — ${tail}` };
    }
  }
  return { pass: true, note: `entry point ${from}, build and start command unchanged` };
};
/** A candidate that doesn't fit the sealed environment. */
export const compatFailed = (t) => !!t?.compat && t.compat.pass === false;

/** A candidate over the agent's cost or latency ceiling (new improvements). */
export const overLimitFailed = (t) => !!t?.overLimit;
/* Cost per conversation a candidate saves when it fixes every failing
   scenario: failing conversations run long (retries, loops back). */
const COST_SAVED_IF_ALL_FIXED = 6;

/*
  A retry learns from the last try (new improvements): with nothing new to
  add, the search doesn't run the incumbent's change again blind — it hands
  the proposer the last run's per-scenario results and its failing
  conversations, and the change comes back revised for what still failed.
  The revision names what it learned from and what it adds.
*/
const lowerFirst = (x) => `${x.charAt(0).toLowerCase()}${x.slice(1)}`;
const revisionOf = (baseP, still, last, repeats, n) => {
  const first = still[0];
  const tool = first.callLog?.missing?.[0] || baseP.step || null;
  const claim = !!first.callLog?.unsupportedClaim;
  const when = lowerFirst(scenarioTitle(first.title)).replace(/\.$/, "").replace(/^(caller|guest|traveller|customer|user|patient)\b/, "the $1");
  const line = claim
    ? `Including when ${when}: say only what the tool returned. If it failed, say so and offer another way.`
    : tool
      ? `Including when ${when}: call ${tool} before you answer, and wait for it to return.`
      : `Including when ${when}: follow the same rule.`;
  const reason = claim ? "still told the caller it was done" : tool ? `still answered without ${tool}` : "still failed";
  return {
    ...baseP,
    id: `${baseP.revisionOf || baseP.id}~r${n}`,
    revisionOf: baseP.revisionOf || baseP.id,
    title: `${(baseP.title || "").replace(/ — revised after (Run|Trial) \d+$/, "")} — revised after Trial ${last.n}`,
    addresses: [...new Set([...(baseP.addresses || []), ...still.map((t) => t.id)])],
    diff: [...(baseP.diff || []), { type: "add", text: line }],
    feedback: {
      from: last.n,
      fixed: Object.values(last.perScenario || {}).filter((v) => v === "fixed").length,
      stillFailing: still.map((t) => ({ id: t.id, title: t.title })),
      conversations: still.length * repeats,
      why: `Trial ${last.n} ${reason} on “${scenarioTitle(first.title).replace(/\.$/, "")}”${still.length > 1 ? ` and ${still.length - 1} more` : ""}`,
      adds: line,
    },
  };
};

/* A candidate carries one new version of a skill at most: a later version of
   the same skill replaces the earlier one rather than stacking on it. */
const oneVersionPerSkill = (pool, ids) => {
  const skillOf = (id) => pool.find((p) => p.id === id)?.skillId || null;
  return ids.filter((id, i) => !skillOf(id) || !ids.slice(i + 1).some((x) => skillOf(x) === skillOf(id)));
};

export const runSearch = ({ env, tasks, optimizerId, config, seed = "opt", scope: given, limitGate = null }) => {
  const scope = given || searchScope({ env, tasks });
  const optimizer = optimizerById(optimizerId);
  const cfg = { ...optimizer.config, ...config };
  /* A budget caps the trials — the search stops with what it found so far. */
  const planned = Math.max(2, Math.min(24, optimizer.trialsFor(cfg)));
  const count = cfg.maxTrials ? Math.max(1, Math.min(planned, cfg.maxTrials)) : planned;

  /*
    The split is a property of the suite, not of the run.

    Seeding it from the optimizer and the run index meant every optimization
    drew a different train/held-out deal — so the config screen previewed one
    split and the search used another, and two runs could never be compared
    because their held-out numbers came from different scenarios. It belongs to
    the environment and stays put.

    An issue fix — any search with a fix's own scenarios in it — holds back
    35% of the issue's reproducing groups plus fresh variations; a goal keeps
    holdouts from the scenarios in the set.
  */
  const issueFix = scope.tasks.some((t) => t.fromIssue);
  const groupOf = scope.groupOf || ((t) => t.groupId || t.id);
  /*
    New improvements (`cfg.rsi`): an inconclusive issue scenario is no
    target, and a goal or a run's failures hold back 35% of the groups that
    fail the goal — the same grouped split a fix uses — instead of the core
    split. The goal's own targets steer the search.
  */
  const rsi = cfg.rsi || null;
  const goalMode = !!rsi && !issueFix;
  const goalTarget = (t) => failsGoal(t, cfg.goal);
  const issueSplit = issueFix
    ? splitIssueScenarios(scope.tasks, `${env?.id || seed}-split-issue`, { groupOf, inconclusiveOut: !!rsi, sameFailure: !!rsi })
    : goalMode
      ? splitGoalScenarios(scope.tasks, `${env?.id || seed}-split-goal`, { groupOf, isTarget: goalTarget, holdable: scope.holdable })
      : null;
  /*
    Must-pass: what the user marked must-pass, regression memory, and every
    other scenario that passes all its repeats on the current version.
    Flaky ones (pass only some repeats today) are shown, never disqualify.

    New improvements gate only what passed every baseline repeat — marked
    or not. A marked scenario that's flaky today would reject real fixes on
    its own noise, so it's named as marked but not counted until it passes
    every repeat again.
  */
  /* One rule with the setup and the other environments (mustPass.js). */
  const passesAll = passesEveryRepeat;
  const marked = (t) => !!t.critical || !!scope.remembered?.(t);
  const mustPass = (t) => (rsi ? passesAll(t) : marked(t) || passesAll(t));
  const flakyToday = flakyTodayOf;
  /*
    Three sets that never overlap (new improvements): the targets the
    improver tunes on and sees in full, the held-back set it never sees, and
    the must-pass set — the rest of the suite that has to keep passing, read
    pass or fail only and run for finalists only. Scenarios in none of them
    (failing for some other reason, flaky today) aren't part of this
    improvement. Older records search on target plus the rest, as before.
  */
  const split = issueSplit || splitScenarios(scope.tasks, `${env?.id || seed}-split`, scope.holdable);
  /* An issue that reproduced nowhere (Continue anyway) still aims at its own scenarios. */
  const train0 = !rsi ? split.train
    : split.target.length || !issueFix ? split.target
      : scope.tasks.filter((t) => t.fromIssue && !split.held.includes(t));
  /*
    The person's own pick (Edit plan → Cases to fix / Cases to protect):
    the targets and held-back cases narrow to the ones chosen to fix, and
    the protected set is exactly the ones chosen to protect. Without a pick,
    the suggestion stands.
  */
  const casePick = cfg.pick || null;
  const fixing = casePick?.fix ? new Set(casePick.fix) : null;
  const train = fixing ? train0.filter((t) => fixing.has(t.id)) : train0;
  const held = fixing ? split.held.filter((t) => fixing.has(t.id)) : split.held;
  const fixPool = new Set([...train0, ...split.held].map((t) => t.id));
  const inSets = new Set([...train, ...held].map((t) => t.id));
  /* From the whole suite when the search kept a slice of it (voice). */
  const protectPool = rsi ? (cfg.mustPassPool || scope.tasks).filter((t) => !fixPool.has(t.id)) : [];
  const protecting = casePick?.protect ? new Set(casePick.protect) : null;
  const mustPassSet = rsi
    ? protectPool.filter((t) => !inSets.has(t.id) && (protecting ? protecting.has(t.id) : mustPass(t)))
    : [];
  const trainMeasured = train.filter((t) => t.status !== "unmeasured");
  const heldMeasured = held.filter((t) => t.status !== "unmeasured");
  const mustPassMeasured = mustPassSet.filter((t) => t.status !== "unmeasured");
  /* Only the kinds of change the agent's constraints allow are ever tried,
     and nothing that edits a part it protects. */
  /* New improvements tag each change with its configuration area and, for a
     setting, the typed value and the range it must sit in. Knowledge content
     is only ever a suggestion, never a candidate. */
  const tag = (p) => {
    if (!rsi) return p;
    const area = areaOfProposal(p);
    const setting = typedSetting(seed, p);
    return { ...p, ...(area && { area }), ...(setting && { setting }) };
  };
  const suggestions = [];
  const permitted = (list) => list
    .map((p) => tag({ ...p, path: changeFileFor(env, p)?.path || null }))
    .filter((p) => !(rsi && p.knowledgeContent && suggestions.push(p)))
    .filter((p) => !cfg.allowed || cfg.allowed.includes(KIND_SURFACE[p.kind] || "code"))
    .filter((p) => !protectedHit(p, cfg.protected || []));
  /*
    A Fix on a run's issue writes its first candidates from the issue's root
    cause and suggested fix, aimed at the issue's scenarios the search can see
    that still fail. Only a run-insights Fix passes `cfg.issue`; without it the
    pool is exactly what it always was.
  */
  /* The issue's scenarios the search can see that still fail — the target
     strata a run issue's Fix is judged on first. */
  const isTargetIssue = (t) => t.fromIssue && passShareOf(t) < 1 && !(rsi && isInconclusive(t));
  const issueTargets = cfg.issue ? trainMeasured.filter(isTargetIssue) : [];
  const feedTargets = issueFix && !cfg.issue ? trainMeasured.filter(isTargetIssue) : [];
  /* A goal's targets: the scenarios the search can see that fall short of it. */
  const goalTargets = goalMode ? trainMeasured.filter(goalTarget) : [];
  const allGoalTargets = goalMode ? [...trainMeasured, ...heldMeasured].filter(goalTarget).length : null;
  const issuePool = cfg.issue ? permitted(issueProposals(env, cfg.issue, issueTargets, trainMeasured)) : [];
  /* What the changes are written for — a goal's targets when it has them. */
  const failingTrain = goalMode && goalTargets.length ? goalTargets : optimisable(train);
  const pool = [
    ...issuePool,
    ...permitted(proposalsFor(env, failingTrain, trainMeasured)),
    /* New versions of the skills it may change — description, or description and steps. */
    ...(cfg.skills?.length
      ? permitted(skillProposalsFor(cfg.skills, failingTrain, { depth: cfg.skillDepth, observed: !!cfg.skillObserved }))
      : []),
    ...modelProposals(cfg.models || [], failingTrain).map(tag).filter((p) => p.addresses.length),
    ...(!cfg.allowed || cfg.allowed.includes("prompt")
      ? lineageProposals(cfg.lineage || [], failingTrain).map(tag).filter((p) => p.addresses.length)
      : []),
  ];
  /* Reviewed before running: only the changes kept, as edited. */
  if (cfg.candidates?.items?.length) pool.splice(0, pool.length, ...cfg.candidates.items);
  /* A fix searches from the issue outwards: changes that address its
     scenarios are tried first, then whatever else helps. A goal does the
     same from its targets. */
  const focus = new Set(goalMode ? goalTargets.map((t) => t.id) : train.filter((t) => t.fromIssue).map((t) => t.id));
  const onFocus = (p) => p.addresses.some((id) => focus.has(id));
  /* The issue's own proposals, tried before anything else, in the order they
     were written (the suggested fix first). */
  const issueIds = issuePool.map((p) => p.id);
  const triedIssue = new Set();
  /* A fix's diagnosis says what kind of change fits — a skipped tool or a
     claim no tool backs. Changes of that kind are tried first. */
  const framed = (p) => (cfg.frame === "claim" ? p.id === "unsupported-claims" || p.mechanism === "claim"
    : cfg.frame === "tool" ? p.id.startsWith("missing-") || p.mechanism === "tool" : false);

  const r = rng(hashSeed(`${seed}-${optimizerId}`));
  const base = projectedRate(trainMeasured, []);
  /* Baseline twice, before any candidate (new improvements only). */
  const calibration = rsi?.calibration ? calibrate(seed, [...trainMeasured, ...heldMeasured], rsi.version, rsi.todayRuns || []) : null;
  const noisyIds = new Set(calibration?.noisyIds || []);

  const trials = [];
  let best = { score: base, picks: [], issueFixed: 0, per: null };
  let stoppedEarly = null;
  /* New improvements only: does this search plateau (see plateausFor)? */
  const plateau = rsi ? plateausFor(seed, env?.id) : true;
  /* A gain inside run-to-run noise isn't a gain for the stop rule. */
  const gainMargin = rsi ? Math.max(1, calibration?.noise || 0) : 0;

  for (let n = 1; n <= count; n += 1) {
    /*
      A search has to start somewhere near the starting prompt and work
      outwards, or the first trial lands on the answer and the other eleven are
      decoration. Directed algorithms grow the incumbent one change at a time
      and occasionally drop one — which is roughly what a textual gradient
      does. The undirected ones sample subsets of increasing size, so random
      search still explores but does not get to start from the finish line.
    */
    const directed = ["protegi", "metaprompt", "promptwizard", "gepa"].includes(optimizer.id);
    let picks;

    if (directed) {
      /* `keep`, not `held` — the held-out split is already bound in this scope. */
      const keep = r() < 0.2 && best.picks.length > 1
        ? best.picks.slice(0, -1)          // drop the least recent, try without it
        : best.picks;
      /* A change the incumbent already carries a revision of isn't offered
         again beside it, and a revision is only ever made, never drawn. */
      const revised = new Set(keep.map((id) => pool.find((p) => p.id === id)?.revisionOf).filter(Boolean));
      const rest = pool.filter((p) => !keep.includes(p.id) && !revised.has(p.id) && !p.revisionOf);
      /* A run issue's Fix tries each of the issue's own proposals first; once
         each has had a turn, an issue proposal that didn't stick is tried
         again half the time while none is in the incumbent. */
      const carried = (id) => keep.includes(id) || revised.has(id);
      const untriedIssue = issueIds.filter((id) => !carried(id) && !triedIssue.has(id));
      const issueLeft = issueIds.filter((id) => !carried(id));
      let next;
      if (untriedIssue.length) {
        [next] = untriedIssue;
      } else if (issueLeft.length && !issueIds.some(carried) && r() < 0.5) {
        next = issueLeft[Math.floor(r() * issueLeft.length)];
      } else {
        const toward = rest.filter(onFocus);
        const framedFirst = cfg.frame && !keep.some((id) => framed(pool.find((p) => p.id === id) || { id: "" })) ? toward.filter(framed) : [];
        const towardIssue = framedFirst.length ? framedFirst : toward;
        const from = towardIssue.length && (!keep.some((id) => onFocus(pool.find((p) => p.id === id) || { addresses: [] })) || r() < 0.5)
          ? towardIssue
          : rest;
        next = from.length ? from[Math.floor(r() * from.length)].id : null;
      }
      if (next && issueIds.includes(next)) triedIssue.add(next);
      picks = next ? oneVersionPerSkill(pool, [...keep, next]) : keep;
      /* Nothing new to add: revise the incumbent's newest change from what
         the last run still failed, rather than run it again unchanged. */
      const last = trials[trials.length - 1];
      const still = !next && rsi && last && keep.length
        ? trainMeasured.filter((t) => focus.has(t.id) && ["still-failing", "flaky"].includes(last.perScenario?.[t.id]))
        : [];
      if (still.length) {
        const baseP = pool.find((p) => p.id === keep[keep.length - 1]);
        if (baseP) {
          const rev = revisionOf(baseP, still, last, cfg.repeats || 3, n);
          pool.push(rev);
          picks = [...keep.slice(0, -1), rev.id];
        }
      }
    } else {
      const size = 1 + Math.floor(r() * Math.min(pool.length, 1 + Math.ceil((n / count) * pool.length)));
      picks = oneVersionPerSkill(pool, shuffle(pool, r).slice(0, size).map((p) => p.id));
    }

    const candidate = pool.filter((p) => picks.includes(p.id));

    /*
      What the candidate did to each scenario — and the score derived from it.
      A trial has one outcome; the number is a summary of it. Side effects
      are real — a line added for one scenario changes the agent's behaviour
      on all of them — so an unaddressed scenario can regress.
    */
    const perScenario = {};
    /* A must-pass scenario that broke — and whether it broke again on a re-run. */
    const mustPassFails = [];
    trainMeasured.forEach((t) => {
      const addressed = candidate.some((p) => p.addresses.includes(t.id));
      const wasPassing = passShareOf(t) >= 1;
      if (addressed) {
        perScenario[t.id] = r() < 0.82 ? "fixed" : "still-failing";
      } else if (wasPassing && r() < 0.12) {
        perScenario[t.id] = "broke";
        /* Newer records check must-pass on its own set, for finalists only. */
        if (!rsi && mustPass(t)) mustPassFails.push({ id: t.id, title: t.title, repeated: repeatsOnRerun(`${seed}:${n}:${t.id}`) });
      } else {
        perScenario[t.id] = wasPassing ? "same" : "still-failing";
      }
      /* Some outcomes don't settle across repeats — read off the ids, so
         the search's own draws stay where they were. */
      if (perScenario[t.id] === "fixed" || perScenario[t.id] === "still-failing") {
        perScenario[t.id] = unsettled(`${seed}:${n}:${t.id}`) || perScenario[t.id];
      }
      if (rsi && offScript(`${seed}:${n}:${t.id}`)) perScenario[t.id] = "off-script";
    });
    /*
      Fits the environment — new improvements only (`cfg.compat`); drawn by
      hash, so the search's own stream is untouched. A candidate that doesn't
      fit stops before any scenario runs: nothing it did is counted, and its
      score is the starting point.
    */
    /* Static checks first, new improvements only — a candidate that fails
       one stops before anything runs, like one that doesn't fit. */
    const stat = rsi ? (candidate.length ? staticOf(`${seed}:${n}`, candidate, cfg) : { pass: true }) : null;
    const statOk = !stat || stat.pass;
    const compat = cfg.compat && statOk ? compatOf(seed, candidate, cfg.compat) : null;
    const fits = statOk && (!compat || compat.pass);
    if (!fits) {
      trainMeasured.forEach((t) => { perScenario[t.id] = passShareOf(t) >= 1 ? "same" : "still-failing"; });
      mustPassFails.length = 0;
    }
    /* Disqualified only when a must-pass failure repeats on a re-run. */
    let brokeBlocker = mustPassFails.some((f) => f.repeated);
    /* Held to the agent's cost and latency ceilings as it runs (new
       improvements): a candidate over one is dropped, not crowned. */
    /* A fix that turns failing conversations into passing ones also makes
       them shorter — no retries, no loops back — which pays for some of the
       tokens it adds (new improvements only). */
    const failingBefore = rsi ? trainMeasured.filter((t) => passShareOf(t) < 1).length : 0;
    const fixedNow = rsi && fits ? trainMeasured.filter((t) => perScenario[t.id] === "fixed").length : 0;
    const costSavings = failingBefore ? Math.round((COST_SAVED_IF_ALL_FIXED * fixedNow) / failingBefore * 10) / 10 : 0;
    const overLimit = limitGate && fits && candidate.length ? limitGate(candidate, costSavings) : null;

    /* A search that plateaus never fixes every failing target — something
       is always left that no run can reach. */
    if (rsi && plateau && fits) {
      const failing = trainMeasured.filter((t) => passShareOf(t) < 1 && perScenario[t.id] !== "off-script");
      if (failing.length && failing.every((t) => perScenario[t.id] === "fixed")) perScenario[failing[failing.length - 1].id] = "still-failing";
    }
    let score = fits ? scoreOf(trainMeasured, perScenario) : base;
    let issueFixed = issueTargets.filter((t) => perScenario[t.id] === "fixed").length;
    const beatsBest = () => fits && !overLimit && (cfg.issue
      ? !mustPassFails.some((f) => f.repeated) && (issueFixed > best.issueFixed || (issueFixed === best.issueFixed && score > best.score))
      : score > best.score);
    let improved = beatsBest();
    /*
      A search that doesn't plateau builds on the best so far after a run
      with no gain: what the best fixed stays fixed, nothing it adds breaks,
      and one more failing scenario — the focus first — is fixed. Applied
      only where the run could count (it fits and is within the limits).
    */
    const prev = trials[trials.length - 1];
    if (!plateau && !improved && prev && !prev.improved && fits && !overLimit) {
      const was = best.per || {};
      trainMeasured.forEach((t) => {
        if (was[t.id] === "fixed") perScenario[t.id] = "fixed";
        else if (perScenario[t.id] === "broke") perScenario[t.id] = "same";
      });
      const open = (t) => passShareOf(t) < 1 && perScenario[t.id] !== "fixed" && perScenario[t.id] !== "off-script";
      const next = trainMeasured.find((t) => open(t) && (cfg.issue ? issueTargets.includes(t) : focus.has(t.id))) || trainMeasured.find(open);
      if (next) perScenario[next.id] = "fixed";
      mustPassFails.length = 0;
      brokeBlocker = false;
      score = scoreOf(trainMeasured, perScenario);
      issueFixed = issueTargets.filter((t) => perScenario[t.id] === "fixed").length;
      improved = beatsBest();
    }
    /* A search that plateaus keeps its first gain and finds nothing past
       it: what a later run fixes beyond the best doesn't hold. */
    if (rsi && plateau && improved && best.per) {
      trainMeasured.forEach((t) => {
        if (perScenario[t.id] === "fixed" && best.per[t.id] !== "fixed") perScenario[t.id] = "still-failing";
      });
      score = scoreOf(trainMeasured, perScenario);
      issueFixed = issueTargets.filter((t) => perScenario[t.id] === "fixed").length;
      improved = beatsBest();
    }
    const added = candidate.filter((p) => !best.picks.includes(p.id))[0];
    const revised = added?.feedback ? added : null;
    /* The repository's tests, on any candidate that changes code. */
    const touchesCode = cfg.repoTests && candidate.some((p) => ["code", "structure"].includes(KIND_SURFACE[p.kind] || "code"));
    const repoTests = touchesCode && fits ? { command: cfg.repoTests, pass: repoTestsPass(`${seed}:${n}`) } : null;

    /* For the stop rule: a gain that clears run-to-run noise (an issue fix is a count, so any new one counts). */
    const clearGain = improved && (cfg.issue && issueFixed > best.issueFixed || score > best.score + gainMargin);
    trials.push({
      n,
      picks,
      score,
      delta: score - base,
      improved,
      clearGain,
      perScenario,
      brokeBlocker,
      ...(mustPassFails.length && { mustPassFails }),
      ...(stat && { static: stat }),
      ...(overLimit && { overLimit }),
      ...(costSavings > 0 && { costSavings }),
      ...(compat && { compat }),
      ...(repoTests && { repoTests }),
      ...(goalMode && { goalFixed: goalTargets.filter((t) => perScenario[t.id] === "fixed").length, goalTargets: goalTargets.length }),
      ...(rsi && { depth: depthOf(candidate) }),
      fixed: Object.values(perScenario).filter((v) => v === "fixed").length,
      broke: Object.values(perScenario).filter((v) => v === "broke").length,
      ...(revised && { feedback: revised.feedback }),
      tried: revised
        ? `revised Run ${revised.feedback.from}'s change`
        : added
        ? `${improved ? "kept" : "tried"} ${added.trialLabel || added.title.toLowerCase()}`
        : picks.length < best.picks.length
          ? "removed one change, to check it's needed"
          : "ran the best change again, to check it holds",
      lines: candidate.flatMap((p) => p.diff.filter((d) => d.type === "add").map((d) => d.text)),
      addresses: [...new Set(candidate.flatMap((p) => p.addresses))],
      bestSoFar: improved ? score : best.score,
      ...(cfg.issue ? { issueFixed, issueTargets: issueTargets.length } : {}),
      /* An Error Feed fix is judged on its issue too — shown, not searched on. */
      ...(!cfg.issue && issueFix && feedTargets.length ? { issueFixed: feedTargets.filter((t) => perScenario[t.id] === "fixed").length, issueTargets: feedTargets.length } : {}),
    });
    if (improved) best = { score, picks, issueFixed, per: { ...perScenario } };
    /* Stop after runs with no gain: two in a row that don't beat the best so
       far on the target scenarios (new improvements); older records, four in
       a row once the search has had six to find its footing. */
    const sinceGain = trials.length - 1 - trials.map((t) => (rsi ? t.clearGain : t.improved)).lastIndexOf(true);
    const noGain = rsi ? NO_GAIN_RUNS : LEGACY_NO_GAIN.runs;
    if (n >= (rsi ? MIN_RUNS_BEFORE_STOP : LEGACY_NO_GAIN.after) && n < count && sinceGain >= noGain) {
      /* With every failing target already fixed there was nothing left to
         gain: the search is done, not stalled (`complete`). */
      const headroom = trainMeasured.some((t) => passShareOf(t) < 1 && best.per?.[t.id] !== "fixed" && best.per?.[t.id] !== "off-script");
      stoppedEarly = { after: n, of: count, ...(rsi && { window: noGain }), ...(rsi && !headroom && { complete: true }) };
      break;
    }
  }

  /*
    Eligible: no must-pass failure that repeated, no cheat, and — for a code
    change — the repository's own tests still pass. Rejected candidates are
    kept and shown rather than deleted.
  */
  const repoFailed = (t) => !!t.repoTests && !t.repoTests.pass;
  const eligible = trials.filter((t) => !t.brokeBlocker && !t.cheats && !repoFailed(t) && !compatFailed(t) && !staticFailed(t) && !overLimitFailed(t));
  const rejected = trials.filter((t) => t.brokeBlocker || t.cheats || repoFailed(t) || compatFailed(t) || staticFailed(t) || overLimitFailed(t));
  const evidenceKinds = ["Tool description", "Architecture", "Memory"];
  const hollowOf = (t) => {
    const picked = pool.filter((p) => t.picks.includes(p.id));
    return trainMeasured.filter((x) => {
      if (t.perScenario?.[x.id] !== "fixed") return false;
      if (!(x.callLog?.missing || []).length) return false;
      const by = picked.filter((p) => p.addresses.includes(x.id));
      return by.length > 0 && !by.some((p) => evidenceKinds.includes(p.kind) || p.evidence || (p.issueId && x.fromIssue === p.issueId));
    });
  };
  const mostFixed = cfg.issue && eligible.length ? Math.max(...eligible.map((t) => t.issueFixed)) : null;
  const ranked = mostFixed == null ? eligible : eligible.filter((t) => t.issueFixed === mostFixed);
  const clean = ranked.filter((t) => !hollowOf(t).length);
  const beats = (t, a) => (cfg.issue && t.issueFixed !== a.issueFixed ? t.issueFixed > a.issueFixed : t.score > a.score);
  const topOf = (list) => list.reduce((a, t) => (beats(t, a) ? t : a), list[0]);
  const top = clean.length ? topOf(clean) : ranked.length ? topOf(ranked) : null;
  /* The smallest surface that fixes it: within two points of the best, the shallowest change. */
  const depthTrial = (t) => depthOf(pool.filter((p) => t.picks.includes(p.id)));
  const near = top ? (clean.length ? clean : ranked).filter((t) => top.score - t.score <= 2 && (!cfg.issue || t.issueFixed === top.issueFixed)) : [];
  const shallowest = near.length
    ? near.reduce((a, t) => (depthTrial(t) < depthTrial(a) || (depthTrial(t) === depthTrial(a) && t.score > a.score) ? t : a), near[0])
    : null;
  /* The search's own pick — the first finalist. */
  const pick = shallowest && depthTrial(shallowest) < depthTrial(top) ? shallowest : top;

  /*
    Finalists — three by default. Only they are scored on the held-back set,
    once, at the end: the search's pick, then the next best distinct
    candidates (clean before hollow, issue-first for a run issue's Fix, then
    score). A candidate never sees the held-back set before this.
  */
  const sig = (t) => [...t.picks].sort().join("|");
  const order = [...eligible].sort((a, b) => (hollowOf(a).length ? 1 : 0) - (hollowOf(b).length ? 1 : 0)
    || (cfg.issue ? b.issueFixed - a.issueFixed : 0)
    || b.score - a.score
    || a.n - b.n);
  const finalistTrials = [];
  if (pick) {
    const seen = new Set([sig(pick)]);
    finalistTrials.push(pick);
    order.forEach((t) => {
      if (finalistTrials.length >= (cfg.finalists || FINALISTS) || seen.has(sig(t))) return;
      seen.add(sig(t));
      finalistTrials.push(t);
    });
  }

  /*
    The noise floor, from baseline-versus-baseline calibration: two runs of
    the same agent differ by about this much. A gain inside it isn't a gain.
  */
  const binomialNoise = Math.max(1, Math.round(100 * Math.sqrt((0.05 * 0.95) / Math.max(1, heldMeasured.length))));
  /* A new improvement measures it: the larger of the formula and what its
     own baseline-twice run found. */
  const noise = calibration ? Math.max(binomialNoise, calibration.noise) : binomialNoise;
  const heldBase = projectedRate(heldMeasured, []);
  /* The issue's held-back scenarios that failed today — what a fix must move there. */
  const heldIssueTargets = heldMeasured.filter((t) => t.fromIssue && passShareOf(t) < 1 && !(rsi && isInconclusive(t)));
  /* Deeper changes need stronger proof: a code or structural finalist gets
     two more repeats and the full regression set (new improvements only). */
  const baseRepeats = cfg.repeats || 3;
  const deep = (f) => depthOf(pool.filter((p) => f.picks.includes(p.id))) >= 3;

  /*
    One finalist on the held-back set. What generalises is the mechanism, so
    each held-back scenario is asked whether the finalist's changes apply to
    it; they land less reliably than in-sample. The search's pick draws from
    the same streams the single re-score always used, so its numbers read
    exactly as before.
  */
  const scoreHeld = (f, i, stream = null) => {
    const winning = pool.filter((p) => f.picks.includes(p.id));
    const key = stream ? `${seed}:held:${stream}` : i === 0 ? `${seed}:held` : `${seed}:held:f${f.n}`;
    const rh = rng(hashSeed(stream ? `${seed}-held-${stream}` : i === 0 ? `${seed}-held` : `${seed}-held-f${f.n}`));
    const per = {};
    heldMeasured.forEach((t) => {
      const helped = winning.some((p) => generalisesTo(p, t));
      const wasPassing = passShareOf(t) >= 1;
      if (helped) {
        per[t.id] = rh() < (t.fromIssue ? 0.85 : 0.62) ? "fixed" : "still-failing";
      } else if (wasPassing && rh() < 0.1) {
        per[t.id] = "broke";
      } else {
        per[t.id] = wasPassing ? "same" : "still-failing";
      }
      if (per[t.id] === "fixed" || per[t.id] === "still-failing") {
        per[t.id] = unsettled(`${key}:${t.id}`) || per[t.id];
      }
      /* A flip on a scenario that disagreed with itself at calibration
         counts only when it repeats. */
      if (per[t.id] === "fixed" && noisyIds.has(t.id) && hashSeed(`${key}:${t.id}:repeat`) % 2 === 1) per[t.id] = "flaky";
      if (rsi && offScript(`${key}:${t.id}`)) per[t.id] = "off-script";
    });
    /* No held-back set (a thin one) means no held-back run to check. */
    const score = scoreOf(heldMeasured, per);
    /*
      Must-pass, finalists only (new improvements): each scenario on its own
      set, pass or fail; a failure counts only when it repeats on a re-run.
      Drawn by hash, so the search's own streams are untouched. Older
      records read must-pass scenarios off the held-back set.
    */
    /* A confirmation re-runs the held-back set only — must-pass already ran. */
    const drawnFails = rsi && !stream
      ? mustPassMeasured
        .filter((t) => passShareOf(t) >= 1 && hashSeed(`${key}:mp:${t.id}`) % 100 < 3)
        .map((t) => ({ id: t.id, title: t.title, critical: !!t.critical, repeated: repeatsOnRerun(`${key}:mp:${t.id}`) }))
      : [];
    /* A skill broadened past what it was written for loads on a protected
       case it shouldn't, and breaks it — on the re-run too. */
    const overReach = winning.find((p) => p.overReach);
    const reached = overReach && rsi && !stream
      ? mustPassMeasured.find((t) => passShareOf(t) >= 1 && !overReach.addresses.includes(t.id) && !drawnFails.some((x) => x.id === t.id))
      : null;
    const mustPassFails = reached
      ? [...drawnFails, { id: reached.id, title: reached.title, critical: !!reached.critical, repeated: true, skill: overReach.skillId }]
      : drawnFails;
    const blockers = rsi
      ? mustPassFails.filter((x) => x.repeated).map(({ id, title, critical, skill }) => ({ id, title, critical, ...(skill && { skill }) }))
      : heldMeasured
        .filter((t) => mustPass(t) && per[t.id] === "broke" && repeatsOnRerun(`${key}:${t.id}`))
        .map((t) => ({ id: t.id, title: t.title, critical: !!t.critical }));
    const lift = score - heldBase;
    const hollow = hollowOf(f).length > 0;
    const issueHeldFixed = heldIssueTargets.filter((t) => per[t.id] === "fixed").length;
    /* Once in each other environment picked; each must-pass set there holds. */
    const envs = rsi && !stream && (cfg.otherEnvs || []).length ? cfg.otherEnvs.map((e) => otherEnvRun(`${key}:f${f.n}`, e)) : null;
    const envBroke = (envs || []).find((e) => !e.held);
    /* The acceptance rule, as the verdict reads it. */
    const skillBlock = blockers.find((b) => b.skill);
    const why = skillBlock ? `${skillBlock.skill} loaded on “${skillBlock.title}”, a protected case it wasn't written for, and broke it again on a re-run`
      : blockers.length ? "a must-pass scenario failed again on a re-run"
        : envBroke ? `a must-pass scenario failed in ${envBroke.name}, and again on a re-run`
        : issueFix
          ? (heldIssueTargets.length && !issueHeldFixed ? "didn't fix the issue on the held-back set"
            : score < heldBase ? "held-back rate fell" : null)
          : hollow ? "fixes without tool evidence"
            : heldBase >= 100 ? (score < heldBase ? "broke a held-back scenario" : null)
              : score < heldBase ? "held-back rate fell"
                : lift <= noise ? "gain within run-to-run noise" : null;
    return {
      per, score, blockers, lift, accepted: !why, why, issueHeldFixed,
      ...(rsi && { mustPassFails }),
      ...(envs && { envs }),
      ...(rsi && { repeats: deep(f) ? baseRepeats + 2 : baseRepeats, ...(deep(f) && { fullRegression: true }) }),
    };
  };
  const scored = finalistTrials.map((f, i) => ({ trial: f, ...scoreHeld(f, i) }));
  /* The winner is the best finalist that passes; none passing, the search's
     pick stands so the verdict can say why. */
  const chosen = scored.find((x) => x.accepted) || scored[0] || null;
  /*
    The held-back run that picked the winner can't also prove it — the best
    of three on one run flatters whichever got lucky. The winner runs on the
    held-back set once more, on fresh draws, and the verdict reads that run
    (new improvements).
  */
  const confirm = rsi && chosen?.accepted && heldMeasured.length && !issueSplit?.thin ? scoreHeld(chosen.trial, 0, "confirm") : null;
  const proof = confirm || chosen;
  const winner = chosen?.trial || null;
  const winning = winner ? pool.filter((p) => winner.picks.includes(p.id)) : [];
  const shallower = winner && winner === pick && top && pick.n !== top.n ? { over: top.n, gap: top.score - pick.score } : null;
  const topOverall = trials.reduce((a, t) => (t.score > a.score ? t : a), trials[0]);
  const hollow = winner ? hollowOf(winner) : [];
  const heldPerScenario = proof ? proof.per : Object.fromEntries(heldMeasured.map((t) => [t.id, passShareOf(t) >= 1 ? "same" : "still-failing"]));
  const heldScore = proof ? proof.score : scoreOf(heldMeasured, heldPerScenario);
  const heldBlockers = chosen ? chosen.blockers : [];

  /* A candidate that adds a tool parameter can't be proven on this
     environment version — set aside, said, never trialled. */
  const contract = contractCandidateOf({ env, allowed: cfg.allowed || [], failing: optimisable(train) });

  /* The suite must-pass was drawn from is read, never stored. */
  const { mustPassPool: _pool, ...kept } = cfg;
  return {
    optimizer,
    config: kept,
    planned,
    trials,
    winner: winner ? { ...winner, proposals: winning } : null,
    /* Every proposal the search drew from, so trial ids resolve back into changes. */
    pool,
    base,
    train,
    held,
    /* What Edit plan can pick from, with today's suggestion as the default. */
    pickable: rsi
      ? {
          fix: [...train0, ...split.held].map((t) => ({
            id: t.id, title: t.title, status: t.status,
            held: split.held.includes(t), suggested: true,
          })),
          protect: protectPool.map((t) => ({
            id: t.id, title: t.title, status: t.status,
            suggested: mustPass(t), flakyToday: !!flakyToday(t),
          })),
        }
      : null,
    trainMeasured: trainMeasured.length,
    trainMeasuredTasks: trainMeasured.map((t) => ({
      id: t.id, title: t.title, critical: !!t.critical,
      ...(scope.anchor(t) && { anchor: true }),
      ...(scope.remembered?.(t) && { remembered: true }),
      ...(flakyToday(t) && { flakyToday: true }),
      ...(t.fromIssue && { fromIssue: t.fromIssue, before: t.status, groupId: groupOf(t) }),
    })),
    heldMeasured: heldMeasured.length,
    heldPerScenario,
    heldTasks: heldMeasured.map((t) => ({
      id: t.id, title: t.title, critical: !!t.critical,
      ...(t.fromIssue && { fromIssue: t.fromIssue, before: t.status, groupId: t.groupId || groupOf(t) }),
      ...(t.fresh && { fresh: true }),
      ...(t.envHeld && { envHeld: true, before: t.status }),
    })),
    heldBase,
    heldScore,
    gap: winner ? winner.score - heldScore : 0,
    rejected,
    heldBlockers,
    /* The finalists and their held-back scores — the winner is the best that passes. */
    finalists: scored.map((x, i) => ({
      n: x.trial.n,
      score: x.trial.score,
      delta: x.trial.delta,
      heldScore: x.score,
      lift: x.lift,
      accepted: x.accepted,
      why: x.why,
      pick: i === 0,
      winner: x === chosen,
      ...(issueFix && { issueHeldFixed: x.issueHeldFixed }),
      ...(x.mustPassFails && { mustPassFails: x.mustPassFails }),
      ...(x.envs && { envs: x.envs }),
      ...(x.repeats && { repeats: x.repeats }),
      ...(x.fullRegression && { fullRegression: true }),
      ...(x === chosen && confirm && { confirmScore: confirm.score }),
    })),
    /* The winner's second held-back run, after the pick — what proves it. */
    ...(confirm && {
      confirm: {
        selectionScore: chosen.score,
        score: confirm.score,
        lift: confirm.lift,
        held: confirm.accepted,
        why: confirm.why,
        ...(issueFix && { selectionIssueFixed: chosen.issueHeldFixed, issueFixed: confirm.issueHeldFixed }),
      },
    }),
    /* The three sets, for the proof. */
    split: issueSplit
      ? { kind: issueFix ? "issue" : "goal", ...issueSplit.groups, thin: issueSplit.thin }
      : { kind: "core" },
    /* The must-pass set: its own scenarios (new improvements), else read off the two halves. */
    mustPass: (() => {
      const list = rsi ? mustPassMeasured : [...trainMeasured, ...heldMeasured].filter(mustPass);
      return {
        total: list.length,
        marked: list.filter((t) => t.critical).length,
        remembered: list.filter((t) => scope.remembered?.(t)).length,
        passing: list.filter((t) => !t.critical && !scope.remembered?.(t) && passesAll(t)).length,
      };
    })(),
    ...(rsi && {
      /* Marked must-pass (or regression memory) but not passing every repeat today. */
      markedNotCounted: mustPassOf((cfg.mustPassPool || scope.tasks).filter((t) => !inSets.has(t.id)), { marked }).markedNotCounted,
      mustPassMeasured: mustPassMeasured.length,
      mustPassTasks: mustPassMeasured.map((t) => ({ id: t.id, title: t.title, critical: !!t.critical, ...(scope.remembered?.(t) && { remembered: true }) })),
    }),
    flakyToday: (rsi ? scope.tasks.filter((t) => !inSets.has(t.id)) : [...trainMeasured, ...heldMeasured]).filter(flakyToday).map((t) => ({ id: t.id, title: t.title })),
    /*
      An issue's fix: what else failed on today's agent — not the issue's, so
      never a target and never protected. The winner is re-run on them once,
      so the review can say what it also fixed along the way, and what's
      still failing to fix separately.
    */
    ...(rsi && issueFix && {
      /* From the whole suite when the search kept a slice of it (voice) —
         the same pool must-pass reads, so it matches the run's own count. */
      otherFailing: (cfg.mustPassPool || scope.tasks)
        .filter((t) => !inSets.has(t.id) && !t.fromIssue && !t.fixScenario && t.status !== "unmeasured" && passShareOf(t) < 1)
        .map((t) => ({ id: t.id, title: t.title, fixed: hashSeed(`${seed}:${t.id}:along`) % 10 < 3 })),
    }),
    failingToday: [...trainMeasured, ...heldMeasured].filter((t) => passShareOf(t) < 1).length,
    ...(contract && { contract }),
    ...(cfg.textSearch && { textSearch: true }),
    ...(cfg.repoTests && { repoTests: cfg.repoTests }),
    ...(cfg.compat && { compat: cfg.compat }),
    /* New improvements only — what the newer engine measured and checked. */
    ...(rsi && {
      rsi: 1,
      repeats: baseRepeats,
      staticChecked: true,
      ...(calibration && { calibration }),
      binomialNoise,
      ...(goalMode && { goal: cfg.goal || { id: "pass-rate", label: "Pass rate" }, goalTargets: allGoalTargets, goalTrainTargets: goalTargets.length }),
      ...(suggestions.length && { suggestions }),
      ...((cfg.otherEnvs || []).length && { otherEnvs: cfg.otherEnvs.map((e) => ({ id: e.id, name: e.name, mustPass: (e.mustPass || []).length, scenarios: e.scenarios || 0 })) }),
      ...(cfg.voiceCap && { voiceCap: cfg.voiceCap }),
      mustPassIds: mustPassMeasured.map((t) => t.id),
      inconclusive: [...trainMeasured, ...heldMeasured].filter((t) => isInconclusive(t)).map((t) => t.id),
    }),
    scope: {
      left: scope.left,
      proposedTargets: scope.proposedTargets,
    },
    overruled: winner && topOverall.n !== winner.n && topOverall.score > winner.score ? topOverall : null,
    hollow: hollow.map((t) => ({ id: t.id, title: t.title })),
    noise,
    stoppedEarly,
    shallower,
    underpowered: trainMeasured.length < 8,
    resolution: trainMeasured.length ? Math.round(100 / trainMeasured.length) : 0,
    wonEarly: !!winner && winner.n <= 2,
  };
};

/**
 * The two verdicts that stop a winner outright, checked before the rest.
 *
 * Kept here rather than in `optimizationVerdict` so every surface that reads
 * a verdict asks this first — a red here means nothing gets run or handed over.
 */
export const blockingVerdict = (result) => {
  if (!result) return null;
  if (!result.winner) {
    const broke = (result.trials || []).filter((t) => t.brokeBlocker).length;
    const tests = (result.trials || []).filter((t) => t.repoTests && !t.repoTests.pass).length;
    const unfit = (result.trials || []).filter(compatFailed).length;
    const stat = (result.trials || []).filter(staticFailed).length;
    const over = (result.trials || []).filter(overLimitFailed).length;
    const reasons = [broke ? "failed a must-pass scenario twice" : null, tests ? "broke the repository's tests" : null, unfit ? "didn't fit the sealed environment" : null, stat ? "failed a static check" : null, over ? "went over the cost or latency limit" : null].filter(Boolean);
    const why = reasons.length > 1 ? `${reasons.slice(0, -1).join(", ")} or ${reasons[reasons.length - 1]}` : reasons[0] || "failed a check";
    return {
      tone: "#DC2626",
      title: `No eligible winner — every run ${why}`,
      body: `Each run fixed something and was rejected — a must-pass failure that repeated on a re-run, ${unfit ? "the repository's own tests, or a change the sealed environment can't run (rebuild the environment from that commit to test it)" : "or the repository's own tests"}. Read what each run was rejected for before trying again.`,
    };
  }
  if (result.heldBlockers?.length) {
    const [first] = result.heldBlockers;
    return {
      tone: "#DC2626",
      title: result.heldBlockers.length === 1
        ? `The winner failed the must-pass scenario “${first.title}”, and again on a re-run`
        : `The winner failed ${result.heldBlockers.length} must-pass scenarios, each again on a re-run`,
      body: "A must-pass failure that repeats stops the winner whatever the pass rate says. Must-pass scenarios are run only for finalists, so this shows up at the end.",
    };
  }
  return null;
};

/** What a person calls an improvement — its place in the environment's list. */
export const improvementRunName = (envState, record) => {
  /* A name given when it started wins; the default numbers itself by position. */
  if (record?.name && !/^(self[- ]?improvement|improvement|optimization)( run)? \d+$/i.test(record.name)) return record.name;
  const i = (envState?.optimizations || []).findIndex((o) => o.id === record?.id);
  return `Improvement ${i >= 0 ? i + 1 : (envState?.optimizations || []).length + 1}`;
};

/**
 * The search as a trace.
 *
 * The trials are the interesting part and they were hidden behind "Running 12
 * trials against the environment" for four seconds. Here they arrive one at a
 * time with their scores, which is both the honest picture of a search — most
 * candidates do not beat the incumbent — and the only way to notice that trial
 * one already won, which on a suite this size it usually has.
 */
/* A held-back set dealt by group — a fix's (reproducing groups) or, for a
   new goal improvement, the groups failing its goal. */
export const groupedSplit = (result) => (["issue", "goal"].includes(result?.split?.kind) ? result.split : null);

/**
 * The split in one line — "12 target · 6 held back · 30 must-pass · 3 proposed left out".
 */
export const splitLine = (result) => {
  if (!result) return "";
  const { left = {}, proposedTargets = 0 } = result.scope || {};
  const out = [
    `${left.proposed || 0} proposed`,
  ].filter((x) => !/^0 /.test(x));
  const off = [
    left.archived ? `${left.archived} archived` : null,
    (left.stale || 0) + (left.na || 0) ? `${(left.stale || 0) + (left.na || 0)} stale` : null,
  ].filter(Boolean);
  const issue = groupedSplit(result);
  const groupWord = result.split?.kind === "goal" ? "failing" : "reproducing";
  return [
    `${result.trainMeasured} target`,
    issue
      ? issue.thin
        ? "none held back — held-back check is thin"
        : `${result.heldMeasured} held back · ${issue.held} of ${issue.reproducing} ${groupWord} groups + ${plural(issue.fresh, "fresh variation")}`
      : `${result.heldMeasured} held back`,
    result.mustPassMeasured != null ? `${result.mustPassMeasured} must-pass` : null,
    out.length ? `${out.join(" and ")} left out` : null,
    off.length ? `${off.join(" and ")} not run` : null,
    proposedTargets && !issue
      ? `${plural(proposedTargets, "proposed issue scenario")} used as the target — approve ${proposedTargets === 1 ? "it" : "them"} into the set from the Scenarios tab`
      : null,
  ].filter(Boolean).join(" · ");
};

/* Why a trial was rejected, in a phrase. */
export const rejectedFor = (t) => {
  if (staticFailed(t)) return `failed static checks — ${t.static.reason.replace(/\.$/, "").replace(/^./, (c) => c.toLowerCase())}`;
  if (compatFailed(t)) return `doesn't fit the sealed environment — ${t.compat.kind === "entry" ? `moves the entry point to ${t.compat.to}` : t.compat.kind === "run" ? "changes the start command" : `changes the build (${t.compat.dep ? `adds ${t.compat.dep} to ` : "edits "}${t.compat.file})`}`;
  if (t?.brokeBlocker) return "failed a must-pass scenario, and again on a re-run";
  if (t?.repoTests && !t.repoTests.pass) return `broke the repository's tests (${t.repoTests.command})`;
  if (overLimitFailed(t)) return `over the ${t.overLimit.id === "cost" ? "cost" : "latency"} limit — ${t.overLimit.actual}`;
  return null;
};

/* The smallest kind of change that fits the cause — the winner's, else the
   shallowest the diagnosis wrote. */
const SURFACE_WORDS = { prompt: "prompt", tools: "tool descriptions", skills: "skills", settings: "settings", model: "model", code: "code", structure: "structure" };
export const smallestSurfaceOf = (result) => {
  const list = result?.winner?.proposals?.length ? result.winner.proposals : result?.pool || [];
  if (!list.length) return null;
  const surfaces = list.map((p) => KIND_SURFACE[p.kind] || "code");
  const pickOne = result?.winner?.proposals?.length
    ? surfaces.reduce((a, x) => ((DEPTH[x] ?? 3) > (DEPTH[a] ?? 3) ? x : a), surfaces[0])
    : surfaces.reduce((a, x) => ((DEPTH[x] ?? 3) < (DEPTH[a] ?? 3) ? x : a), surfaces[0]);
  return { id: pickOne, label: SURFACE_WORDS[pickOne] || pickOne };
};

/**
 * What the Diagnose step found: the root cause (from the issue snapshot, the
 * diagnosis the run was started against, or the search's own pool) and the
 * smallest surface that fits it. Null for records stamped before the step.
 */
export const diagnosisSummaryOf = (record, result = record?.result) => {
  if (!record?.frozen?.diagnoseStep) return null;
  const d = (record.diagnosis || [])[0] || null;
  const fromPool = (result?.pool || []).slice().sort((a, b) => (b.addresses?.length || 0) - (a.addresses?.length || 0))[0];
  const code = record.snapshot?.diagnosis || null;
  const cause = code?.cause || record.snapshot?.rootCause || d?.headline || d?.label || fromPool?.why || null;
  const surface = smallestSurfaceOf(result);
  return {
    cause,
    layer: record.snapshot?.fixLayer || d?.layer || null,
    source: code ? (code.edited ? "the diagnosis, as you edited it" : "the code-level diagnosis") : record.snapshot ? "Error Feed" : d ? "Debug failures" : "the search's diagnosis",
    surface,
    ...(code && { code }),
  };
};

export const searchTrace = (result, record = null) => {
  const { trials, winner } = result;
  const issue = groupedSplit(result);
  const goalSplit = result.split?.kind === "goal";
  const finalists = result.finalists || [];
  const reread = record?.reread || null;
  const suggestion = record?.fixDepth === "suggestion";
  const reps = result.repeats || 3;
  const repWord = reps === 3 ? "three times" : `${reps} times`;
  /* The newer engine's steps — baseline twice and diagnose — for records
     stamped with them (and their results). */
  const cal = result.calibration || null;
  const diag = record ? diagnosisSummaryOf(record, result) : null;
  const repro = record?.reproduce && result.rsi ? [{
    id: "reproduce",
    label: `Reproduced on ${record.reproduce.reproduced || 0} of ${plural(record.reproduce.total || 0, "scenario")}`,
    result: record.reproduce.inconclusive ? `${record.reproduce.inconclusive} inconclusive — not targets` : "every reproducing scenario is a target",
    lines: [`each issue scenario run ${repWord} on the agent as it runs today`, ...(record.reproduce.inconclusive ? ["a scenario that failed once is inconclusive, not a reproduction — kept as a scenario, never a target"] : [])],
  }] : [];
  const calibrateStep = cal ? [{
    id: "calibrate",
    label: `Baseline twice · ${cal.version || "current version"} against itself`,
    result: `${cal.disagreed} of ${plural(cal.scenarios, "scenario")} disagreed`,
    lines: [
      cal.reused?.length
        ? `ran ${cal.version || "the current version"} twice on every scenario before any run — ${cal.reused.map((r) => `${r.label} already covered ${r.covered} of ${cal.scenarios}`).join(", ")}, so those weren't run again`
        : `ran ${cal.version || "the current version"} twice on every scenario before any run`,
      cal.disagreed ? "a flip on one of those must show up on more than one repeat to count" : "no scenario disagreed with itself — every flip counts",
      `run-to-run noise ±${result.noise} pts`,
    ],
  }] : [];
  const diagnoseStep = diag ? [{
    id: "diagnose",
    label: "Diagnosing the cause",
    result: diag.surface ? `smallest surface that fits the cause: ${diag.surface.label}` : "no cause found",
    lines: [
      ...(diag.cause ? [`root cause (${diag.source}): ${String(diag.cause).replace(/\.$/, "")}`] : []),
      ...(diag.code?.where?.length ? [`where: ${diag.code.where.map((w) => `${w.kind} · ${w.ref}`).join(", ")}`] : []),
      ...(diag.code?.change ? [`change to make: ${String(diag.code.change).replace(/\.$/, "")}`] : []),
      ...(diag.layer ? [`fix layer: ${diag.layer}`] : []),
      "the search starts from the shallowest change that fits — prompt before settings before code",
    ],
  }] : [];

  const first = reread
    ? [{
      id: "reread",
      label: reread.changed ? `Changed outside Future AGI → baseline on ${reread.version || "the new version"}` : "Re-read the agent — no outside change",
      result: reread.changed ? "the baseline matches production" : "baseline is what runs in production",
      lines: [reread.changed
        ? `the agent was edited outside Future AGI since the last run, so the baseline runs on ${reread.version || "the new version"}`
        : "the agent's definition matches the version the environment last ran"],
    }]
    : [];

  /* An agent we can only call: reproduce, diagnose, suggest — nothing is tried. */
  if (suggestion) {
    return [
      ...first,
      {
        id: "reproduce",
        label: "Reproducing the failure",
        result: `${result.trainMeasured + result.heldMeasured} scenarios`,
        lines: [`each scenario called ${repWord} on the agent as it runs today`],
      },
      {
        id: "diagnose",
        label: "Reading what went wrong",
        result: `${result.pool.length} ${result.pool.length === 1 ? "cause" : "causes"} found`,
        lines: ["from the calls themselves — tool calls, what the agent said, where it stopped"],
      },
      {
        id: "suggest",
        label: "Writing a suggested fix",
        result: "no change can be tried on an agent we can only call",
        lines: ["apply it, mark the new version released, then re-run on the same environment version to prove it"],
      },
    ];
  }

  return [
    ...first,
    ...repro,
    ...calibrateStep,
    ...diagnoseStep,
    {
      id: "split",
      label: result.mustPassMeasured != null ? "Splitting target, held-back and must-pass scenarios" : "Splitting target and held-back scenarios",
      result: splitLine(result),
      lines: [
        ...(issue
          ? [
            "held back by group: a scenario and its variations always land on one side, so near-copies can't leak",
            issue.thin
              ? `too few groups ${goalSplit ? "fail the goal" : "reproduced"} to hold any back — the held-back check is thin`
              : `${issue.held} of ${issue.reproducing} ${goalSplit ? "failing" : "reproducing"} groups held back, plus one fresh variation of each, kept because it ${goalSplit ? "fails" : "reproduces"} today`,
            ...(goalSplit && result.goal ? [`targets: ${plural(result.goalTargets || 0, "scenario")} falling short of ${goalWords(result.goal)} — ${result.goalTrainTargets || 0} visible to the search`] : []),
            ...((result.inconclusive || []).length ? [`${plural(result.inconclusive.length, "inconclusive scenario")} kept, never a target`] : []),
          ]
          : [
            "held back from the set, never shown while searching",
            "critical scenarios dealt into both halves, so neither is only routine work",
          ]),
        ...(result.mustPassMeasured != null
          ? ["the search sees target scenarios in full and must-pass scenarios as pass or fail only — must-pass runs for finalists"]
          : []),
        ...(result.mustPass
          ? [`must-pass: ${result.mustPass.total} — ${[result.mustPass.marked ? `${result.mustPass.marked} marked` : null, result.mustPass.remembered ? `${result.mustPass.remembered} regression memory` : null, `${result.mustPass.passing} passing every repeat today`].filter(Boolean).join(", ")}${result.flakyToday?.length ? ` · ${plural(result.flakyToday.length, "flaky scenario")} shown, never disqualifying` : ""}`]
          : []),
        `starting prompt scores ${result.base}% on the target scenarios`,
        result.underpowered
          ? `${result.resolution}-point resolution — one scenario is the smallest difference this split can see`
          : "resolution is fine enough for the search to discriminate",
      ],
    },
    {
      id: "candidates",
      label: "Reading the changes the diagnosis found",
      result: (() => { const n = new Set(trials.flatMap((t) => t.picks)).size; return `${n} ${n === 1 ? "change" : "changes"} to try`; })(),
      lines: [
        "every run's prompt is assembled from those, so each run can be read line by line",
        ...(result.contract ? [`${result.contract.title.toLowerCase()} — set aside: it changes the environment's contract, so it can't be proven on the frozen version`] : []),
      ],
    },
    {
      id: "trials",
      label: `Running ${trials.length} trials against the environment${result.textSearch ? " · in text" : ""}`,
      result: trials.some((t) => !t.brokeBlocker && !t.cheats) ? `best ${Math.max(...trials.filter((t) => !t.brokeBlocker && !t.cheats).map((t) => t.score))}% on the target scenarios` : "every run was rejected",
      tone: "#16A34A",
      /* Every trial, because watching the incumbent hold is the point. */
      lines: [
        ...(result.textSearch ? ["voice agent searched in text — faster and cheaper; only finalists run as real calls"] : []),
        ...(result.staticChecked ? ["static checks on every run before it starts — areas, protected parts, settings in range, judges untouched"] : []),
        ...trials.map(
          (t) => `run ${t.n} — ${staticFailed(t) ? "not run" : `${t.score}%`} ${t.improved ? "· new best" : "· no better"} · ${triedLabelOf(t, trials)}${rejectedFor(t) ? ` · rejected, ${rejectedFor(t)}` : ""}${t.repoTests?.pass ? ` · ${t.repoTests.command} passed` : ""}`,
        ),
        ...(result.stoppedEarly?.window ? [result.stoppedEarly.complete
          ? `stopped after ${result.stoppedEarly.after} of up to ${result.stoppedEarly.of} trials — the best already fixes every failing target, nothing left to gain`
          : `stopped early after ${result.stoppedEarly.after} of up to ${result.stoppedEarly.of} runs — the last ${result.stoppedEarly.window} didn't beat the best by more than run-to-run noise, so the best goes straight to verifying`] : []),
      ],
    },
    {
      id: "holdout",
      label: `Scoring ${plural(finalists.length, "finalist")} once on held-back${result.mustPassMeasured != null ? " and must-pass" : ""} scenarios${result.textSearch ? " · as real calls" : ""}`,
      result: winner ? `run ${winner.n} wins · ${result.heldScore}% held back` : "no finalist to score",
      tone: result.gap > 12 ? "#DC2626" : result.gap > 6 ? "#CA8A04" : "#16A34A",
      lines: [
        `${result.heldMeasured} held-back scenarios, from ${result.heldBase}% before the change`,
        ...(result.mustPassMeasured != null ? [`${result.mustPassMeasured} must-pass scenarios for each finalist — pass or fail, a failure counts only when it repeats on a re-run`] : []),
        ...finalists.map((f) => `run ${f.n} — ${f.heldScore}% held back${f.accepted ? " · passes" : ` · ${f.why}`}${f.winner ? " · winner" : ""}${f.fullRegression ? ` · code change → ${f.repeats} repeats and the full regression set` : ""}`),
        ...((result.otherEnvs || []).length ? [`each finalist also ran once in ${result.otherEnvs.map((e) => e.name).join(", ")} — its must-pass set there has to hold`] : []),
        result.gap > 6
          ? `${result.gap} points below the target score — the change fitted the scenarios it was tuned on`
          : "in line with the target score",
      ],
    },
  ];
};
