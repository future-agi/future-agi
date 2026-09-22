import { BUILD_TONES } from "../../buildEnvironment/buildTones";

// The statuses whose dot animates — a run in one of these phases is still
// moving, so the chip breathes.
export const LIVE_STATUSES = ["running", "booting", "grading"];

// Stable hook for the pulsing dot so callers (and tests) can target it without
// depending on emotion's generated class name.
export const PULSING_DOT_CLASS = "sim-status-dot--pulse";

// The run status vocabulary. Colours come from BUILD_TONES so this module holds
// no raw hex. `flaky` and `unmeasured` are deliberately not red: a flaky
// scenario disagreed with itself (the scenario is the problem, not the agent),
// and an unmeasured run means nothing upstream of the agent worked, so there is
// no verdict to blame the agent for.
export const STATUS_META = {
  queued: { color: BUILD_TONES.zinc, label: "Queued" },
  booting: { color: BUILD_TONES.amber, label: "Provisioning" },
  running: { color: BUILD_TONES.blue, label: "Running" },
  grading: { color: BUILD_TONES.accent, label: "Grading" },
  passed: { color: BUILD_TONES.green, label: "Passed" },
  flaky: { color: BUILD_TONES.amberBright, label: "Flaky" },
  unmeasured: { color: BUILD_TONES.ash, label: "Not measured" },
  completed: { color: BUILD_TONES.zinc, label: "Completed" },
  failed: { color: BUILD_TONES.red, label: "Failed" },
  error: { color: BUILD_TONES.orange, label: "Error" },
  cancelled: { color: BUILD_TONES.zinc, label: "Cancelled" },
};

// Run identity colours, indexed by a run's place in the sequence (its ordinal
// minus one, wrapped). Keyed off the ordinal — not the list position — so a run
// keeps the same letter and colour on the history list, the detail header and a
// comparison. Ported from the designer's `RUN_COLORS`, mapped onto BUILD_TONES
// so this module holds no raw hex.
export const RUN_COLORS = [
  BUILD_TONES.accent, // #7857FC
  BUILD_TONES.blue, // #2563EB
  BUILD_TONES.green, // #16A34A
  BUILD_TONES.amber, // #CA8A04
  BUILD_TONES.orange, // #EA580C
  BUILD_TONES.pink, // #DB2777
  BUILD_TONES.tealDeep, // #0D9488
  BUILD_TONES.indigo, // #4F46E5
];

// The identity colour for a run at a given 1-based ordinal. A missing or
// non-finite ordinal falls back to the first colour, so a caller never gets
// `undefined` (which would crash `alpha()` when fed to an sx colour).
export const runColor = (ordinal) => {
  const n = Number.isFinite(ordinal) ? ordinal : 1;
  return RUN_COLORS[(Math.max(1, n) - 1) % RUN_COLORS.length];
};

// Pre-flight estimate model. Ported verbatim from the designer's RunsPanel:
// duration grows with the scenario count (floored so a tiny suite still reads
// as a couple of minutes), concurrency is fixed, cost is a flat per-scenario
// rate. Kept as named constants so the arithmetic is not buried inline.
export const MINUTES_PER_SCENARIO = 0.7;
export const MIN_DURATION_MINUTES = 2;
export const RUN_CONCURRENCY = 4;
export const COST_PER_SCENARIO = 0.08;

export const estimatedMinutes = (count) =>
  Math.max(MIN_DURATION_MINUTES, Math.ceil(count * MINUTES_PER_SCENARIO));

export const estimatedCost = (count) => (count * COST_PER_SCENARIO).toFixed(2);

// The colour a pre-flight item's status line takes: green when satisfied, amber
// when satisfied-but-worth-a-look (an optional slot left empty), red when it
// blocks the run.
export const PREFLIGHT_STATE_COLORS = {
  ok: BUILD_TONES.green,
  warn: BUILD_TONES.amber,
  blocked: BUILD_TONES.red,
};

// The pass bar's two segments — the share that passed vs. the share that did
// not — so the row carries no raw hex.
export const PASS_BAR_COLORS = { passed: BUILD_TONES.green, failed: BUILD_TONES.red };

// Copy for the Runs tab. Held here (not inline) so the panel, the rows and the
// tests share one source of truth.
export const RUNS_COPY = {
  title: "Run simulation",
  subtitle: (name) => `Every task runs in its own clean copy of ${name}.`,
  preflight: "Pre-flight",
  start: "Start simulation",
  fix: "Fix",
  labels: {
    environment: "Environment",
    agent: "Agent",
    scenarios: "Scenarios",
    evals: "Evals",
  },
  agentConnected: "Connected agent",
  agentNotConnected: "Not connected",
  connectionVerified: "Connection verified",
  required: "Required",
  optional: "Optional",
  tasks: (n) => `${n} tasks`,
  critical: (n) => `${n} critical`,
  applied: (n) => `${n} applied`,
  estimate: {
    duration: "Est. duration",
    concurrency: "Concurrency",
    cost: "Est. cost",
    parallel: `${RUN_CONCURRENCY} parallel`,
  },
  history: (n) => `Run history (${n})`,
  started: "Started",
  empty: {
    title: "No runs yet",
    body: "Start a simulation above and you'll be able to watch every task execute live.",
  },
};
