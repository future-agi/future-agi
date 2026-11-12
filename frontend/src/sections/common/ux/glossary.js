import { GLOSSARY as PREVIEW_GLOSSARY } from "src/test-fixtures/build-data/glossary";

const PROTOTYPE_BUILD =
  import.meta.env.VITE_ENVIRONMENT === "local" &&
  import.meta.env.VITE_PROTOTYPE_MODE === "true";
const INCLUDE_PREVIEW = PROTOTYPE_BUILD || import.meta.env.MODE === "test";

// The glossary (R3 G0 §B3, SVP-P0-3). `<Term k="passAt1">pass@1</Term>`
// shows `short` (+ `long`, + `Learn more ↗` when `docs`). The `?` sheet's
// Glossary tab lists every entry. Keys and `short` copy are frozen: lanes
// depend on them. Pure data.

const EXPERIMENTS_DOCS =
  "https://docs.futureagi.com/docs/dataset/features/experiments";
const EVAL_DOCS = "https://docs.futureagi.com/docs/evaluation";
const DATASET_DOCS = "https://docs.futureagi.com/docs/dataset";

/**
 * @typedef {object} GlossaryEntry
 * @property {string} label   the plain name (`Success rate`)
 * @property {string} short   one or two sentences, shown in the popover
 * @property {string} [long]  an optional second paragraph
 * @property {string} [docs]  an existing docs URL
 * @property {string[]} [aliases]  words the palette / sheet search also match
 * @property {boolean} [absent]  names a path that is absent from the product
 *   (Q12): still resolvable by key for the hover cards on seeded runs that
 *   used it, but never listed by searchGlossary (Cmd+K, the ? sheet).
 */

/** @type {Readonly<Record<string, GlossaryEntry>>} */
export const GLOSSARY = Object.freeze({
  successRate: {
    label: "Success rate",
    short:
      "Share of tries where the task succeeded. Errored tries count as fails; tries that didn't run are not counted. 65.6% = 59 of 90 tries.",
    long: "Also called pass@1. It is the headline number on every screen.",
    aliases: ["pass rate", "pass@1", "task success"],
    docs: EXPERIMENTS_DOCS,
  },
  passAt1: {
    label: "pass@1",
    short: "Share of attempts that succeeded. 65.6% = 59 of 90 attempts.",
    aliases: ["pass at 1", "success rate"],
    docs: EXPERIMENTS_DOCS,
  },
  passHatK: {
    label: "pass^3",
    short:
      "Tasks solved on all 3 tries. Stricter than pass@1; shows how reliable the agent is.",
    aliases: ["pass^k", "pass hat k", "pass^3", "reliable"],
    docs: EXPERIMENTS_DOCS,
  },
  reliable: {
    label: "Reliable on all 3 tries",
    short:
      "Tasks solved on all 3 tries. Stricter than pass@1; shows how reliable the agent is.",
    aliases: ["pass^3", "pass^k", "reliability"],
    docs: EXPERIMENTS_DOCS,
  },
  flaky: {
    label: "Flaky",
    short:
      "Passes some tries and fails others on the same task. Needs more trials or a fix.",
    long: "Example: 2 of 3 tries passed. A flaky task can flip a verdict, so it is counted apart.",
    aliases: ["unstable", "inconsistent"],
  },
  trial: {
    label: "Trial",
    short:
      "One try of one task. A run tries each task as many times as its Tries setting.",
    long: "One attempt at a task; 3 trials = 3 tries of the same task.",
    aliases: ["try", "attempt", "repeat"],
  },
  run: {
    label: "Run",
    short:
      "One configured way to run the rows or tasks (a prompt, an agent graph or an HTTP endpoint), like #2B.",
    aliases: ["variant", "candidate"],
    docs: EXPERIMENTS_DOCS,
  },
  experiment: {
    label: "Experiment",
    short: "A group of runs on the same dataset version.",
    docs: EXPERIMENTS_DOCS,
  },
  baseline: {
    label: "Baseline",
    short: "The run everything else is compared to. Change with B.",
    aliases: ["reference", "control"],
  },
  delta: {
    label: "Delta (pts)",
    short:
      "Percentage points vs the baseline run. +20 pts means 20 more tasks out of 100.",
    aliases: ["pts", "points", "percentage points", "difference"],
  },
  ci95: {
    label: "95% CI",
    short:
      "The range the true score probably lies in. With 30 tasks, differences under about 15 pts may be noise.",
    aliases: ["confidence interval", "ci", "uncertainty", "error bars"],
  },
  withinNoise: {
    label: "Within noise",
    short:
      "The difference is smaller than the uncertainty, so it may be chance.",
    long: "The two 95% ranges overlap. Run more tasks or more tries to tell them apart.",
    aliases: ["not significant", "noise", "significance"],
  },
  regressed: {
    label: "Regressed",
    short: "Worse than the baseline on this task.",
    aliases: ["got worse", "regression", "worse"],
  },
  improved: {
    label: "Improved",
    short: "Better than the baseline on this task.",
    aliases: ["got better", "better"],
  },
  judge: {
    label: "Judge",
    short:
      "An LLM that scores outputs against a rubric. Check its agreement with reviewers.",
    aliases: ["llm judge", "llm-as-a-judge", "rubric"],
    docs: EVAL_DOCS,
  },
  check: {
    label: "Check",
    short: "A deterministic test on the end state or output (no LLM).",
    aliases: ["assertion", "deterministic eval", "test"],
    docs: EVAL_DOCS,
  },
  ...(INCLUDE_PREVIEW ? PREVIEW_GLOSSARY : {}),
  costPerTask: {
    label: "Cost per task",
    short:
      "Model cost of one task, averaged over its tries. Sandbox compute not included.",
    aliases: ["cost", "price", "$ per task"],
  },
  totalCost: {
    label: "Total cost",
    short: "Cost of the whole run: tasks × tries.",
    aliases: ["run cost", "spend"],
  },
  timePerTask: {
    label: "Time per task",
    short: "Median wall time of one try (p50).",
    aliases: ["latency", "speed", "p50", "duration"],
  },
  firstWrongStep: {
    label: "First wrong step",
    short: "The earliest step whose result caused a failed check.",
    aliases: ["root cause", "where it failed"],
  },
  firstDifference: {
    label: "First difference",
    short:
      "The first step where this run did something different from the baseline.",
    aliases: ["divergence", "diverged", "where it changed"],
  },
  milestone: {
    label: "Milestone",
    short:
      "A step that matters for the outcome: a tool call, a reply, a file edit, a command or the end state.",
    aliases: ["step", "alignment"],
  },
  differ: {
    label: "Differ",
    short: "Runs disagree on this task: some pass, some fail.",
    aliases: ["disagree", "disagreement"],
  },
  carried: {
    label: "Carried",
    short: "Result reused from an earlier experiment; not rerun.",
    aliases: ["reused", "carried over"],
  },
  confidence: {
    label: "Confidence",
    short:
      "How sure we are that the difference is real. High: significant with 50 or more tasks. Medium: significant with fewer tasks. Low: it may be chance.",
    aliases: ["significance", "how sure", "certainty"],
  },
  // options-truth L1 C11/S2: the key stays (screens point at it) but it
  // names the one stand-in tag, `Simulated`, not a status.
  preview: {
    label: "Simulated",
    short:
      "Simulated: the tag next to a stand-in button. The button stands in for an outside machine sending results; the results it sends are scored like any run's.",
    aliases: ["simulated", "stand-in"],
  },
  // ---------------------------------------------------------- R4
  mcp: {
    label: "MCP server",
    short:
      "A Model Context Protocol server: a bundle of tools an agent can call (acme-support has 12). The run records which servers it had.",
    aliases: ["model context protocol", "mcp"],
  },
  executionMode: {
    label: "Where it ran",
    short:
      "Where a run ran: in Future AGI, on your endpoint, or, for runs recorded outside Future AGI, the machine, CI job or sandbox that ran it.",
    aliases: ["execution mode"],
    absent: true,
  },
  traceIds: {
    label: "Run, row and try ids",
    short:
      "Three ids (fi.run_id, fi.row_id, fi.trial) on every span of a run, linking a trace to its row and try.",
    aliases: ["trace linking"],
  },
  captureMode: {
    label: "Capture",
    short:
      "How much of a seeded harness run was recorded: Full steps (from the harness transcript) or Final result only (no transcript).",
    aliases: ["capture", "full steps"],
    absent: true,
  },
  graphQuality: {
    label: "Graph quality",
    short:
      "Full graph: the spans name their graph nodes. Basic graph: we only know which span called which (parent → child). We say which one you are looking at.",
    aliases: ["full graph", "basic graph", "graph"],
  },
  declaredParam: {
    label: "Declared parameter",
    short: "A setting the run reported, e.g. model or temperature.",
    aliases: ["param", "parameter", "params_schema"],
    absent: true,
  },
  sideEffects: {
    label: "Side effects",
    short:
      "Tools that change something real (issue_refund). With several tries per task they may run several times; use tool stubs or a dry run to avoid that.",
    aliases: ["side effect", "dry run", "tool stubs"],
  },
  pathShare: {
    label: "Share of tries",
    short:
      "How many tries took this path, out of all tries of the run. 20% = 18 of 90 tries.",
    aliases: ["path share", "share"],
  },
  graphDiff: {
    label: "Graph diff",
    short:
      "Two runs on one graph: steps only one run took, steps both took, and how often each was visited. Needs a full graph on both runs.",
    aliases: ["graph diff", "overlay"],
  },
  latencyP90: {
    label: "Latency p90",
    short:
      "9 of 10 agent replies started within this time after the caller stopped speaking. p50 is the typical reply; p90 shows the slow ones.",
    aliases: ["p90", "p50", "p99", "latency"],
  },
  ttfw: {
    label: "Time to first word",
    short:
      "How long the caller waits from picking up to the agent's first word.",
    aliases: ["ttfw", "time to first byte", "ttfb"],
  },
  bargeIn: {
    label: "Barge-in",
    short:
      "The caller talks while the agent is speaking. A good agent stops quickly; Kept talking over the caller counts the times it did not.",
    aliases: ["barge in", "interruption", "kept talking"],
  },
  wer: {
    label: "WER",
    short:
      "Word error rate: how many words the agent's speech recognition got wrong, against what the simulated caller said. Lower is better.",
    aliases: ["word error rate", "asr"],
  },
  talkRatio: {
    label: "Talk ratio",
    short: "Share of the call the agent spent talking vs the caller.",
    aliases: ["talk time"],
  },
  deadAir: {
    label: "Dead air",
    short: "The longest silence in the call when nobody spoke.",
    aliases: ["silence"],
  },
  textOnlyDepth: {
    label: "Text only",
    short:
      "The agent's logic tested with text instead of speech: fast and cheap, but it says nothing about hearing or speaking. Never merged with full-voice results.",
    aliases: ["text only", "depth"],
  },
  fullVoiceDepth: {
    label: "Full voice",
    short:
      "A simulated caller talks to your agent over audio, so speech recognition, turn-taking and latency are tested too.",
    aliases: ["full voice", "audio"],
  },
  // options-truth L1 C11: no status terms (Preview, Works today, Coming
  // soon); OTel keeps its technical fact in neutral words.
  otelPassthrough: {
    label: "OTel",
    short:
      "Traced through OpenTelemetry (no traceAI package): the framework's own OpenTelemetry spans become the trace.",
    aliases: ["otel", "opentelemetry"],
  },
  // IT-R1 G2 P1/P11: two named counts, and the words testers asked about.
  worse: {
    label: "Worse",
    short:
      "Tasks whose Task success dropped vs the baseline (fewer tries passed). The verdict counts these.",
    long: "Not the same as Any eval worse, which counts rows where any eval score went down.",
    aliases: ["got worse", "worse tasks", "regression"],
  },
  anyEvalWorse: {
    label: "Any eval worse",
    short:
      "Rows where any eval score went down vs the baseline, for any run in view (beyond noise).",
    long: "Wider than the verdict's worse, which only counts tasks whose Task success dropped.",
    aliases: ["regressed", "eval regressed", "any eval"],
  },
  aligned: {
    label: "Aligned steps",
    short:
      "Steps that match between the two runs. `Aligned 11 of 25 steps · 14 folded` = 14 matching steps are hidden so the differences stand out.",
    aliases: ["folded", "alignment", "matching steps"],
  },
  onlyFails: {
    label: "Only one run fails",
    short:
      "`Only #2B fails` = the other runs pass this task; the cause is likely in #2B.",
    aliases: ["only fails", "fails alone"],
  },
  runBar: {
    label: "Run bar",
    short:
      "The row of run tabs above the results: which runs are in view, the baseline first. Add, remove or reorder runs there.",
    aliases: ["run tabs", "runs in view"],
  },
  whyColumn: {
    label: "Why column",
    short:
      "One line per row saying why it is in Any eval worse / better: which eval moved, by how much, for which run.",
    aliases: ["why", "reason column"],
  },
  pairwise: {
    label: "Pairwise review",
    short:
      "Judge two runs' answers to the same task side by side: A better, tie or B better. Votes feed the human review score.",
    aliases: ["a/b review", "side by side review", "better?"],
  },
  blind: {
    label: "Blind",
    short:
      "Hides which run is which (A / B) while you rate, so the label cannot sway the vote.",
    aliases: ["blind review", "hide names"],
  },
  costPerSolved: {
    label: "Cost per solved task",
    short:
      "Total cost of a run divided by the tasks it solved. The fairest price of quality: a cheap run that solves little costs more per solved task.",
    aliases: ["cost per solved", "best value", "value"],
  },
  // ------------------------------------------------- IT-R2 G2 P11 (additive)
  bestValue: {
    label: "Best value",
    short:
      "The run with the lowest cost per solved task among runs that solve almost as much as the best one (within 10 pts) and that no run beats on both cost and success.",
    long: "The cheapest run is named apart when it solves much less: cheap is not good value.",
    aliases: ["value", "cheapest good run"],
  },
  bothRan: {
    label: "Tasks both ran",
    short:
      "A comparison only counts tasks both runs have results for. Tasks one run skipped are left out of the percentages and the better / worse counts.",
    aliases: ["both ran", "denominator", "shared tasks"],
  },
  rowChecks: {
    label: "Row checks",
    short:
      'Checks written for one row, e.g. "Callback booked" or "Refund issued once". They run only on that row, next to the dataset\'s evaluations.',
    aliases: ["row check", "per-row checks", "end-state checks"],
  },
  carriedFrom: {
    label: "Carried from",
    short:
      "Reused from an earlier run (e.g. #2B) because this row did not change. It was not run again, so it costs nothing and cannot get better or worse.",
    aliases: ["carried over", "reused from"],
  },
  liveMock: {
    label: "live / mock",
    short:
      "live: the run called the real tools (in a sandbox). mock: tool calls were answered from recorded results, so nothing was changed anywhere.",
    aliases: ["live", "mock", "mocked tools", "recorded tools"],
  },
  lanes: {
    label: "Lanes",
    short:
      "One column per run, side by side, so you can read the same task step by step across runs.",
    aliases: ["lane", "columns per run"],
  },
  mapKey: {
    label: "Map / Key",
    short:
      "Map: a strip of the whole conversation, where each run diverges. Key: what the colours and marks mean.",
    aliases: ["map", "key", "legend", "minimap"],
  },
  keptTalking: {
    label: "Kept talking",
    short:
      "The agent went on speaking while the caller tried to interrupt (barge-in not handled).",
    aliases: ["interruption", "talked over", "barge-in"],
  },
  pairDiffers: {
    label: "Pass/fail differs",
    short:
      "Tasks where the runs disagree outright: at least one run passes every try and at least one fails every try. Better / worse counts more: a task going from 1 of 3 to 3 of 3 is better without differing.",
    aliases: ["differ", "disagree", "pass fail differs"],
  },
  // IT-R5 G2 P5 (rule 37): `Level` reads as a warning; the plain words are
  // `Too close to call`. `level` stays as a key so old links still resolve.
  level: {
    label: "Too close to call",
    short:
      "The difference in success is within noise: with this many tasks and tries it could be chance.",
    aliases: ["tie", "within noise", "level with", "level"],
  },
  tooCloseToCall: {
    label: "Too close to call",
    short:
      "The difference in success is within noise: with this many tasks and tries it could be chance.",
    long: "Its 95% range holds 0. Run more tasks or more tries to tell the two apart.",
    aliases: ["tie", "level", "not significant"],
  },
  isolate: {
    label: "Isolate",
    short:
      "Re-run with one thing changed (model, prompt or a tool) so the difference can only come from that change.",
    aliases: ["isolate a change", "one change"],
  },
  paths: {
    label: "Paths",
    short:
      "The routes runs took through the agent: which steps and tools, in which order, and how often each route passed.",
    aliases: ["path", "routes", "trajectories"],
  },
  matrix: {
    label: "Matrix",
    short:
      "Every task (rows) by every run (columns), with each try's pass or fail, so one look shows where runs disagree.",
    aliases: ["grid", "task matrix"],
  },
  tries: {
    label: "Tries",
    short:
      "How many times each task was run. 3 tries show whether a pass is reliable or luck.",
    aliases: ["try", "trials", "attempts", "repeats"],
  },
  newest: {
    label: "Newest",
    short: "The most recent run of this agent on the dataset.",
    aliases: ["latest", "new"],
  },
  previousVersion: {
    label: "Previous version",
    short:
      "The version of the same agent that ran before this one. A change reads against it: #7B vs #7A.",
    aliases: ["previous", "older version"],
  },
  // IT-R4 G2 (rule 33): keys other groups wrap on first use.
  noRunReliable: {
    label: "No run reliable",
    short:
      "No run solved its tasks on every try. Look at pass^3 per run to see which comes closest.",
    aliases: ["not reliable", "pass^3 zero"],
    docs: EXPERIMENTS_DOCS,
  },
  p50: {
    label: "p50",
    short:
      "The middle value: half the tries took less time than this, half took more.",
    aliases: ["median", "typical time"],
  },
  // IT-R5 G2 S1: the keys every surface may wrap in <Term>.
  p90: {
    label: "p90",
    short:
      "9 of 10 tries took less time than this; p50 is the typical one, p90 shows the slow ones.",
    aliases: ["90th percentile", "slow tail"],
  },
  endStateChecks: {
    label: "End-state checks",
    short:
      "Checks run on the environment after the agent stops (was the refund issued, is the ticket closed). A task passes when every check holds.",
    aliases: ["end state", "state checks", "assertions"],
  },
  dryRun: {
    label: "Dry run",
    short:
      "Runs the agent on one row without saving a run or scoring the dataset, so you can see what it does before you spend on every row.",
    aliases: ["try on one row", "test run"],
  },
  startState: {
    label: "Start state",
    short:
      "What the environment holds before the agent starts (files, records, tool data). Every try starts from the same state.",
    aliases: ["initial state", "fixture", "seed"],
  },
  asTasks: {
    label: "As tasks",
    short:
      "Each row becomes a task the agent runs in its environment, checked by its end state.",
    aliases: ["task suite", "import as tasks"],
    docs: DATASET_DOCS,
  },
  asConversations: {
    label: "As conversations",
    short:
      "Each row is a recorded conversation; runs replay or score the turns instead of acting in an environment.",
    aliases: ["sessions", "import as conversations"],
    docs: DATASET_DOCS,
  },
  splits: {
    label: "Splits",
    short:
      "Named subsets of the rows (for example hard, edge cases). Filter or run on one split.",
    aliases: ["split", "subset"],
    docs: DATASET_DOCS,
  },
  expectedAnswers: {
    label: "Expected answers",
    short:
      "The answer each row should get. Evals compare a run's output with it.",
    aliases: ["ground truth", "reference", "expected output"],
    docs: EVAL_DOCS,
  },
  wrapLines: {
    label: "Wrap lines",
    short: "Show long cell text on several lines instead of cutting it off.",
    aliases: ["wrap", "line wrap"],
  },
  matchedByMeaning: {
    label: "Matched by meaning",
    short:
      "Two answers count as the same when they say the same thing in different words (a model judges it), not only when the text is identical.",
    aliases: ["semantic match", "meaning"],
    docs: EVAL_DOCS,
  },
});

export const GLOSSARY_KEYS = Object.freeze(Object.keys(GLOSSARY));

/** The entry for a key, or null. */
export const glossaryEntry = (k) => GLOSSARY[k] ?? null;

const norm = (s) =>
  String(s ?? "")
    .toLowerCase()
    .trim();

/**
 * Entries matching a query on key, label, aliases or text, best first
 * (label/alias hits before text hits). Empty query → every entry.
 * @returns {{key: string, entry: GlossaryEntry}[]}
 */
export function searchGlossary(query) {
  const q = norm(query)
    .replace(/^what (does|is|are)\s+/, "")
    .replace(/\s+mean\??$/, "")
    .replace(/\?$/, "")
    .trim();
  const all = GLOSSARY_KEYS.filter((key) => !GLOSSARY[key].absent).map(
    (key) => ({ key, entry: GLOSSARY[key] }),
  );
  if (!q) return all;
  const scored = all
    .map(({ key, entry }) => {
      const names = [key, entry.label, ...(entry.aliases ?? [])].map(norm);
      let score = 0;
      if (names.some((n) => n === q)) score = 3;
      else if (names.some((n) => n.startsWith(q) || q.startsWith(n))) score = 2;
      else if (names.some((n) => n.includes(q))) score = 1.5;
      else if (norm(`${entry.short} ${entry.long ?? ""}`).includes(q))
        score = 1;
      return { key, entry, score };
    })
    .filter((x) => x.score > 0);
  scored.sort((a, b) => b.score - a.score);
  return scored.map(({ key, entry }) => ({ key, entry }));
}
