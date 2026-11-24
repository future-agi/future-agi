import { LIFECYCLE_COPY } from "./scenarios.constants";

// The same rule the backend's runnable() applies: Active and Kept as written run, and so does a
// case on an install with no validation at all.
const RUNNABLE = new Set(["active", "kept_as_written"]);
const NO_VALIDATION = "validation_unavailable";

const TONES = {
  draft: "warning",
  kept_as_written: "warning",
  archived: "neutral",
  runs_unvalidated: "neutral",
};

export const isRunnable = (lifecycle) =>
  !lifecycle ||
  RUNNABLE.has(lifecycle.state) ||
  lifecycle.reason === NO_VALIDATION;

const hoverOf = (lifecycle) =>
  lifecycle.warning_text ||
  lifecycle.reason_text ||
  (lifecycle.legacy ? LIFECYCLE_COPY.legacyHover : "");

// The mark a row and the drawer show for its state, or null when the state needs no mark.
export const lifecycleChip = (lifecycle) => {
  if (!lifecycle) return null;
  const key =
    lifecycle.reason === NO_VALIDATION ? "runs_unvalidated" : lifecycle.state;
  if (!LIFECYCLE_COPY.states[key]) return null;
  return {
    key,
    label: LIFECYCLE_COPY.states[key],
    tone: TONES[key],
    hover: hoverOf(lifecycle),
  };
};

// A wrong-way example counts as caught only when it really ran: a step that could not run here
// (`assumed`) leaves no verdict, as the backend's proof reads it.
const resultOf = (control) => {
  if (!control.ran || control.assumed?.length) return "not_run";
  if (control.failed === true) return "caught";
  if (control.failed === false) return "not_caught";
  return "not_run";
};

export const wrongWayRows = (lifecycle) => {
  const ran = lifecycle?.proof?.controls ?? [];
  if (ran.length) {
    return ran.map((one) => ({
      name: one.name,
      why: one.why,
      result: resultOf(one),
    }));
  }
  return (lifecycle?.negative_controls ?? []).map((one) => ({
    name: one.name,
    why: one.why,
    result: "not_run",
  }));
};

// Whether a running validation (the running-validation answer) is proving this row: the cases it names, or, when it
// names none, every case but the archived ones.
export const isBeingValidated = (row, running) => {
  if (!running?.job_id || !row?.lifecycle) return false;
  if (running.scenario_keys)
    return running.scenario_keys.includes(row.scenarioKey);
  return row.lifecycle.state !== "archived";
};

// Proved, but a check reads evidence this agent doesn't give, so that part isn't measured when it runs.
export const isNotMeasured = (lifecycle) =>
  lifecycle?.warning === "evidence_not_captured";

// Only rules and files are checked against the environment; other sources (a chat, a person) have no state.
const CHECKED_SOURCES = new Set(["rule", "file"]);

// Whether a cited rule or file was found, changed since, or couldn't be confirmed; null for other sources.
export const citationState = (source, lifecycle) => {
  if (!CHECKED_SOURCES.has(source?.kind)) return null;
  if (!source.resolved) return "unconfirmed";
  if ((lifecycle?.source_changed ?? []).includes(source.ref)) return "changed";
  return "confirmed";
};

export const countsLine = (counts) => {
  if (!counts) return null;
  const legacy = counts.legacy_without_control ?? 0;
  return (
    LIFECYCLE_COPY.counts(counts) +
    (legacy > 0 ? LIFECYCLE_COPY.countsLegacy(legacy) : "")
  );
};
