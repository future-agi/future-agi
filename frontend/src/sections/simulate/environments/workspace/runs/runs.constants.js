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
  agentEndpoint: "Endpoint on file",
  required: "Required",
  optional: "Optional",
  tasks: (n) => `${n} tasks`,
  critical: (n) => `${n} critical`,
  applied: (n) => `${n} applied`,
  history: (n) => `Run history (${n})`,
  started: "Started",
  loading: "Loading run history…",
  loadMore: "Load older runs",
  loadingMore: "Loading…",
  empty: {
    title: "No runs yet",
    body: "Start a simulation above and you'll be able to watch every task execute live.",
  },
  error: {
    title: "Couldn't load the run history",
    body: "The executions for this environment didn't come back. Reload the page to try again.",
  },
};
