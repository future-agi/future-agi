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
 * It is not, however, the whole release decision, and saying it was would be the
 * same overstatement this file exists to avoid. The gate weighs mean return,
 * latency and cost alongside the pass rate; the search moves one of those. A
 * winner is a candidate for release, not a release, and the screen says so.
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
import { proposalsFor, projectedRate, optimisable, generalisesTo, passShareOf } from "./optimize";

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
    desc: "Genetic Pareto — keeps candidates that win on different scenarios",
    fit: "The only one that will not trade a release blocker for two routine passes.",
    config: { maxMetricCalls: 40 },
    trialsFor: (c) => Math.round(c.maxMetricCalls / 4),
  },
  {
    id: "bayesian",
    label: "Bayesian Search",
    desc: "Optuna over few-shot examples and phrasing",
    fit: "Needs more trials than the others to beat them.",
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

/* ── the split ───────────────────────────────────────────────────────────── */

/**
 * Training and held-out scenarios.
 *
 * Stratified on two things, because stratifying on one of them produced a
 * held-out set that could not do its job. Release blockers are dealt into both
 * halves — a split holding every blocker back optimizes against routine work
 * only, and one holding none back reports a number that says nothing about
 * whether the agent can ship.
 *
 * Failures are dealt into both halves too, and that was the omission. The
 * comment already claimed both halves "look like the suite" while the code
 * split on criticality alone, so a seed could hand every failing scenario to
 * training and leave a held-out set where everything already passed. Such a set
 * cannot show an improvement — the only thing it can report is a regression —
 * and the whole point of holding scenarios back is to find out whether a change
 * generalises.
 */
export const splitScenarios = (tasks = [], seed = "split") => {
  const r = rng(hashSeed(seed));
  const deal = (list) => {
    const shuffled = [...list].sort(() => r() - 0.5);
    const cut = Math.max(1, Math.round(shuffled.length * 0.65));
    return [shuffled.slice(0, cut), shuffled.slice(cut)];
  };

  const failing = (t) => t.status !== "unmeasured" && passShareOf(t) < 1;
  const bucket = (crit, fail) =>
    tasks.filter((t) => !!t.critical === crit && failing(t) === fail);

  const dealt = [
    deal(bucket(true, true)),
    deal(bucket(true, false)),
    deal(bucket(false, true)),
    deal(bucket(false, false)),
  ];

  return {
    train: dealt.flatMap(([a]) => a),
    held: dealt.flatMap(([, b]) => b),
  };
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
const scoreOf = (tasks, perScenario) => {
  if (!tasks.length) return 0;
  const after = (t) => {
    const state = perScenario[t.id];
    if (state === "fixed") return 1;
    if (state === "broke") return 0;
    return passShareOf(t);
  };
  return Math.round((tasks.reduce((a, t) => a + after(t), 0) / tasks.length) * 100);
};

export const runSearch = ({ env, tasks, optimizerId, config, seed = "opt" }) => {
  const optimizer = optimizerById(optimizerId);
  const cfg = { ...optimizer.config, ...config };
  const count = Math.max(2, Math.min(24, optimizer.trialsFor(cfg)));

  /*
    The split is a property of the suite, not of the run.

    Seeding it from the optimizer and the run index meant every optimization
    drew a different train/held-out deal — so the config screen previewed one
    split and the search used another, and two runs could never be compared
    because their held-out numbers came from different scenarios. It belongs to
    the environment and stays put.
  */
  const { train, held } = splitScenarios(tasks, `${env?.id || seed}-split`);
  const trainMeasured = train.filter((t) => t.status !== "unmeasured");
  const heldMeasured = held.filter((t) => t.status !== "unmeasured");
  const pool = proposalsFor(env, optimisable(train), trainMeasured);

  const r = rng(hashSeed(`${seed}-${optimizerId}`));
  const base = projectedRate(trainMeasured, []);

  const trials = [];
  let best = { score: base, picks: [] };

  for (let n = 1; n <= count; n += 1) {
    /*
      A search has to start somewhere near the starting prompt and work
      outwards, or the first trial lands on the answer and the other eleven are
      decoration. The first version of this dealt each candidate a random half
      of the pool, which on a four-scenario training split covered nearly
      everything on trial one: +52 points, then eleven trials of noise. That is
      a lottery drawn twelve times, not an optimizer.

      Directed algorithms grow the incumbent one change at a time and
      occasionally drop one — which is roughly what a textual gradient does, and
      what makes the growth curve worth looking at. The undirected ones sample
      subsets of increasing size, so random search still explores but does not
      get to start from the finish line.
    */
    const directed = ["protegi", "metaprompt", "promptwizard", "gepa"].includes(optimizer.id);
    let picks;

    if (directed) {
      /* `keep`, not `held` — the held-out split is already bound in this
         scope and shadowing it here was one careless edit away from scoring
         the search against the wrong set of scenarios. */
      const keep = r() < 0.2 && best.picks.length > 1
        ? best.picks.slice(0, -1)          // drop the least recent, try without it
        : best.picks;
      const rest = pool.filter((p) => !keep.includes(p.id));
      const next = rest.length ? rest[Math.floor(r() * rest.length)].id : null;
      picks = next ? [...keep, next] : keep;
    } else {
      const size = 1 + Math.floor(r() * Math.min(pool.length, 1 + Math.ceil((n / count) * pool.length)));
      picks = [...pool].sort(() => r() - 0.5).slice(0, size).map((p) => p.id);
    }

    const candidate = pool.filter((p) => picks.includes(p.id));

    /*
      What the candidate did to each scenario — and the score derived from it.

      These used to be computed independently: the score came from the union
      arithmetic plus a noise term, and the per-scenario outcomes were drawn
      separately. So the grid could show one scenario fixed while the headline
      claimed ninety percent, and both were displayed on the same screen as
      facts about the same trial. A trial has one outcome; the number is a
      summary of it, not a second opinion.

      Side effects are real — a line added for one scenario changes the agent's
      behaviour on all of them — so an unaddressed scenario can regress, which
      is where the noise now comes from rather than from an added term.
    */
    const perScenario = {};
    let brokeBlocker = false;
    trainMeasured.forEach((t) => {
      const addressed = candidate.some((p) => p.addresses.includes(t.id));
      const wasPassing = passShareOf(t) >= 1;
      if (addressed) {
        /* Addressed usually means fixed — but not always, which is why the
           trial is run rather than assumed. */
        perScenario[t.id] = r() < 0.82 ? "fixed" : "still-failing";
      } else if (wasPassing && r() < 0.12) {
        perScenario[t.id] = "broke";
        if (t.critical) brokeBlocker = true;
      } else {
        perScenario[t.id] = wasPassing ? "same" : "still-failing";
      }
    });

    const score = scoreOf(trainMeasured, perScenario);
    const improved = score > best.score;
    const added = candidate.filter((p) => !best.picks.includes(p.id))[0];

    trials.push({
      n,
      picks,
      score,
      delta: score - base,
      improved,
      perScenario,
      brokeBlocker,
      fixed: Object.values(perScenario).filter((v) => v === "fixed").length,
      broke: Object.values(perScenario).filter((v) => v === "broke").length,
      /* What this trial actually tried, rather than a line count. */
      tried: added
        ? `${improved ? "kept" : "tried"} ${added.title.toLowerCase()}`
        : picks.length < best.picks.length
          ? "dropped a change to see if it was carrying its weight"
          : "re-ran the incumbent",
      lines: candidate.flatMap((p) => p.diff.filter((d) => d.type === "add").map((d) => d.text)),
      addresses: [...new Set(candidate.flatMap((p) => p.addresses))],
      bestSoFar: improved ? score : best.score,
    });
    if (improved) best = { score, picks };
  }

  /*
    The winner is the best candidate that did not break a release blocker.

    Picking on score alone would hand back the candidate that traded the one
    scenario a release is decided on for two routine passes — with a higher
    number, so nobody would query it. Blocked candidates are kept and shown
    rather than deleted: "the highest score was rejected, here is why" is more
    useful than a leaderboard with a hole in it.
  */
  const eligible = trials.filter((t) => !t.brokeBlocker);
  const rejected = trials.filter((t) => t.brokeBlocker);
  const pickFrom = eligible.length ? eligible : trials;
  const winner = pickFrom.reduce((a, t) => (t.score > a.score ? t : a), pickFrom[0]);
  const topOverall = trials.reduce((a, t) => (t.score > a.score ? t : a), trials[0]);
  const winning = pool.filter((p) => winner.picks.includes(p.id));

  /*
    Reward hacking, checked on the winner.

    This is the part a prompt optimizer pointed at its own scoring function
    cannot be trusted without. Search finds whatever the metric rewards, and
    the cheapest way to pass a check that reads words is to write better words
    — so a scenario that failed because a tool was never called, and is now
    "fixed" by a change that only alters phrasing, has not been fixed. It has
    been dressed. The gaming analyzer exists precisely because this is what
    optimizers do, so it runs before a winner can be handed off.
  */
  const evidenceKinds = ["Tool description", "Architecture", "Memory"];
  const hollow = trainMeasured.filter((t) => {
    if (winner.perScenario?.[t.id] !== "fixed") return false;
    if (!(t.callLog?.missing || []).length) return false;
    const by = winning.filter((p) => p.addresses.includes(t.id));
    return by.length > 0 && !by.some((p) => evidenceKinds.includes(p.kind));
  });

  /*
    The held-out number, and it is deliberately not the training number. A gap
    is what tuning against a fixed set produces; reporting only the training
    score is how a 90% becomes a 79% in production and nobody knows why.
  */
  /*
    The held-out set, scored the same way — and it has to be able to move.

    This used to run the winner's proposals through the same union arithmetic as
    the training split. That could only ever return the baseline: a proposal's
    `addresses` list holds the training scenarios it was written from, none of
    which are in the held-out set, so nothing was ever counted as fixed and the
    only thing left was a random subtraction. Every run reported "no improvement
    on held-out" regardless of what the search found, and the one number the
    whole split exists to produce was a foregone conclusion.

    What generalises is the mechanism, so each held-out scenario is asked
    whether the winner's changes apply to it. They land less reliably than
    in-sample — which is where the overfit gap now comes from, rather than from
    a constant subtracted at the end.
  */
  const heldPerScenario = {};
  heldMeasured.forEach((t) => {
    const helped = winning.some((p) => generalisesTo(p, t));
    const wasPassing = passShareOf(t) >= 1;
    if (helped) {
      heldPerScenario[t.id] = r() < 0.62 ? "fixed" : "still-failing";
    } else if (wasPassing && r() < 0.1) {
      heldPerScenario[t.id] = "broke";
    } else {
      heldPerScenario[t.id] = wasPassing ? "same" : "still-failing";
    }
  });

  const heldBase = projectedRate(heldMeasured, []);
  const heldScore = scoreOf(heldMeasured, heldPerScenario);

  return {
    optimizer,
    config: cfg,
    trials,
    winner: { ...winner, proposals: winning },
    /* Every proposal the search drew from, kept on the result so trial ids
       can be resolved back into the change they represent — the "code per
       trial" view rebuilds each trial's file diffs by looking up its picks
       here. */
    pool,
    base,
    train,
    held,
    trainMeasured: trainMeasured.length,
    /* The scenarios themselves, for the per-scenario grid — a count cannot be
       drawn as columns. */
    trainMeasuredTasks: trainMeasured.map((t) => ({ id: t.id, title: t.title, critical: !!t.critical })),
    heldMeasured: heldMeasured.length,
    heldPerScenario,
    heldTasks: heldMeasured.map((t) => ({ id: t.id, title: t.title, critical: !!t.critical })),
    heldBase,
    heldScore,
    /* The one number worth reading, and the honest way to say it. */
    gap: winner.score - heldScore,
    rejected,
    /* Set when the highest-scoring candidate was passed over for a blocker. */
    overruled: topOverall.n !== winner.n && topOverall.score > winner.score ? topOverall : null,
    hollow: hollow.map((t) => ({ id: t.id, title: t.title })),
    /*
      Whether this search could have meant anything.

      A twelve-trial search over four training scenarios is theatre: one prompt
      change covers three of them, so the first candidate wins and the other
      eleven differ by noise. The temptation is to hide that behind a curve that
      slopes upward. It is worth more said out loud — a team whose suite is too
      small to optimize against needs to hear that before they spend a thousand
      episodes discovering it, and the smallest score step tells them exactly how
      coarse their measurement is.
    */
    underpowered: trainMeasured.length < 8,
    resolution: trainMeasured.length ? Math.round(100 / trainMeasured.length) : 0,
    /* Did the search actually search, or did trial one take it? */
    wonEarly: winner.n <= 2,
  };
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
export const searchTrace = (result) => {
  const { trials, winner } = result;

  return [
    {
      id: "split",
      label: "Splitting training and held-out scenarios",
      result: `${result.trainMeasured} train · ${result.heldMeasured} held out`,
      lines: [
        "release blockers dealt into both halves, so neither is only routine work",
        `starting prompt scores ${result.base}% on the training split`,
        result.underpowered
          ? `${result.resolution}-point resolution — one scenario is the smallest difference this split can see`
          : "resolution is fine enough for the search to discriminate",
      ],
    },
    {
      id: "candidates",
      label: "Reading the changes the diagnosis found",
      result: `${new Set(trials.flatMap((t) => t.picks)).size} in the pool`,
      lines: ["every candidate prompt is assembled from those, so each trial can be read line by line"],
    },
    {
      id: "trials",
      label: `Running ${trials.length} trials against the environment`,
      result: `best ${winner.score}% at trial ${winner.n}`,
      tone: "#16A34A",
      /* Every trial, because watching the incumbent hold is the point. */
      lines: trials.map(
        (t) => `trial ${t.n} — ${t.score}% ${t.improved ? "· new best" : "· no better"} · ${t.tried}`,
      ),
    },
    {
      id: "holdout",
      label: "Re-scoring the winner on scenarios it never saw",
      result: `${result.heldScore}% held out`,
      tone: result.gap > 12 ? "#DC2626" : result.gap > 6 ? "#CA8A04" : "#16A34A",
      lines: [
        `${result.heldMeasured} scenarios, from ${result.heldBase}% before the change`,
        result.gap > 6
          ? `${result.gap} points below the training score — the prompt fitted the split it was tuned on`
          : "in line with the training score",
      ],
    },
  ];
};

