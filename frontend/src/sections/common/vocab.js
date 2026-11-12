// One vocabulary everywhere (R2-SPEC §0.3): rows, runs, evals. A task
// suite's items are rows; a `task` row shows a `Task` badge; deterministic
// evals are evals of type `Check` (chip `Check`, next to `Judge`).
//
// Frozen API (R2 manifest row 56): VOCAB, plural. R3 (G0, F3) adds the
// locked metric names and chip labels below; screens print these constants,
// never their own spelling (`Cost per task`, never `Cost`, `$ / row`).

export const VOCAB = Object.freeze({
  row: "row",
  rows: "rows",
  run: "run",
  eval: "eval",
  taskBadge: "Task",
  conversationBadge: "Conversation",
  checkChip: "Check",
  judgeChip: "Judge",
  // No status words (S1): kept (empty) because r2Vocab.test.js pins the key.
  preview: "",
  // R3 F3 locked names
  SUCCESS_RATE: "Success rate",
  RELIABLE: "Reliable on all 3 tries",
  RELIABLE_SHORT: "Reliable",
  FLAKY: "Flaky",
  COST_PER_TASK: "Cost per task",
  TOTAL_COST: "Total cost",
  TIME_PER_TASK: "Time per task",
  BASELINE: "Baseline",
  TRIAL: "trial",
  TRIALS: "trials",
  EXPERIMENT: "experiment",
  PTS: "pts",
  // Matrix / rail chips
  CHIP_ALL: "All",
  CHIP_DIFFER: "Differ",
  CHIP_FLAKY: "Flaky",
  CHIP_NO_RUN_RELIABLE: "No run reliable",
  CHIP_ALL_RELIABLE: "All reliable",
  // Divergence vocabulary (strings built by common/Align/milestones.js)
  FIRST_WRONG_STEP: "First wrong step",
  FIRST_DIFFERENCE: "First difference",
  // R4 F3 locked names
  YOUR_CODE: "Your code",
  AGENT_HARNESS: "Agent harness",
  VOICE_AGENT: "Voice agent",
  ALREADY_RUNNING: "Already running",
  ENDPOINT: "Endpoint",
  THIS_MACHINE: "This machine",
  CI: "CI",
  SANDBOX: "Future AGI sandbox",
  REMOTE_ENDPOINT: "Remote endpoint",
  MORE_PLACES: "More places to run",
  RUNNERS: "Runners",
  // IT-R1 G1 P3: plain labels (were `Trace + upload` / `Connect live`).
  TRACE_UPLOAD: "Run on your machine",
  // options-truth L1 C11 / L3 G10: no status words. Every way to run works
  // (the empty status keys are gone); OTel keeps its fact.
  CONNECT_LIVE: "Live connection",
  OTEL_PREVIEW: "OTel",
  SIMULATED: "Simulated",
  // options-truth L1 C11: no status footer (was the IT-R2 status line).
  PROTOTYPE_FOOTER: "",
  TRY_ON_ONE_ROW: "Try on 1 row",
  COPY_ONE_ROW_COMMAND: "Copy 1-row command",
  CREATE_AND_GET_COMMANDS: "Create experiment and get commands",
  RERUN_WITH_CHANGES: "Rerun with changes…",
  WHAT_THIS_RUN_USES: "What this run uses",
  WHERE_IT_RUNS: "Where it runs",
  ALLOW_TEAMMATES: "Allow teammates",
  TASK_COMPLETION: "Task completion",
  LATENCY_P50: "Latency p50",
  LATENCY_P90: "Latency p90",
  LATENCY_P99: "Latency p99",
  KEPT_TALKING: "Kept talking over the caller",
  CAPTURE_GATEWAY: "Model calls only (gateway)",
  CAPTURE_TRANSCRIPT: "Full steps (harness transcript)",
  CAPTURE_FINAL_ONLY: "Final result only (no transcript)",
  FULL_GRAPH: "Full graph",
  BASIC_GRAPH: "Basic graph",
  TEST_A_FIX: "Test a fix",
  LATEST_VERSION: "Latest version",
  PREVIOUS_VERSION: "Previous version",
  RUN_ON_THESE_ROWS: "Run on these rows",
  BEST_VALUE: "Best value",
});

/** The four Matrix / rail chip labels, in display order (after `All`). */
export const CHIP_LABELS = Object.freeze([
  VOCAB.CHIP_DIFFER,
  VOCAB.CHIP_FLAKY,
  VOCAB.CHIP_NO_RUN_RELIABLE,
  VOCAB.CHIP_ALL_RELIABLE,
]);

const IRREGULAR = {
  row: "rows",
  run: "runs",
  eval: "evals",
  trial: "trials",
  task: "tasks",
  try: "tries",
  reply: "replies",
};

/**
 * `1 row` / `248 rows` / `1,234 runs` (numbers en-US). Plural by `+s`
 * unless an explicit plural is given.
 * @param {number} n
 * @param {string} word  singular, e.g. `row`
 * @param {string} [pluralWord]  explicit plural (`entries`)
 */
export function plural(n, word, pluralWord) {
  const count = Number.isFinite(Number(n)) ? Number(n) : 0;
  const many = pluralWord ?? IRREGULAR[word] ?? `${word}s`;
  return `${count.toLocaleString("en-US")} ${count === 1 ? word : many}`;
}
