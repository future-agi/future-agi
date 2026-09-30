import { BUILD_TONES } from "../../buildEnvironment/buildTones";

// The statuses whose dot animates — a run in one of these phases is still
// moving, so the chip breathes.
export const LIVE_STATUSES = ["running", "booting", "grading", "cancelling"];

// TestExecution lifecycle states that can still advance and need polling.
export const ACTIVE_EXECUTION_STATUSES = new Set([
  "pending",
  "running",
  "cancelling",
  "evaluating",
]);

// The subset a user can still stop — `cancelling` is already on its way out.
export const STOPPABLE_EXECUTION_STATUSES = new Set(["pending", "running", "evaluating"]);

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
  // The runs table's lifecycle "Completed" — green like the design. Kept apart from
  // `completed` above, which the run header uses for "finished with findings".
  finished: { color: BUILD_TONES.green, label: "Completed" },
  failed: { color: BUILD_TONES.red, label: "Failed" },
  error: { color: BUILD_TONES.orange, label: "Error" },
  // A stopped run until the backend confirms its sandbox is gone.
  cancelling: { color: BUILD_TONES.zinc, label: "Cancelling" },
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

// The pass bar's two segments — the share that passed vs. the share that did
// not — so the row carries no raw hex.
export const PASS_BAR_COLORS = { passed: BUILD_TONES.green, failed: BUILD_TONES.red };
