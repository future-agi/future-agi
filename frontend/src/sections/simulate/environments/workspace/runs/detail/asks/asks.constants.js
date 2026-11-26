// Words for asking questions about a run's calls.

// What a question can read about each call (call_evidence.Kind). Audio is left
// out: a typed question reads text, and the backend refuses it.
export const ASK_KINDS = [
  { value: "transcript", label: "Transcript" },
  { value: "scenario", label: "Scenario" },
  { value: "agent_instructions", label: "Agent instructions" },
  { value: "tool_calls", label: "Tool calls" },
  { value: "check_results", label: "Check results" },
  { value: "world_state_start", label: "World at the start" },
  { value: "world_changes", label: "World changes" },
  { value: "world_state_end", label: "World at the end" },
  { value: "agent_traces", label: "Agent traces" },
];

export const kindLabel = (value) =>
  ASK_KINDS.find((one) => one.value === value)?.label ?? humanize(value);

// Why a call's evidence was not captured (call_evidence.Cause).
const CAUSES = {
  level_forbids: "not kept at this run's record level",
  not_uploaded: "never uploaded",
  no_record: "no record",
  world_not_recorded: "world not recorded",
  start_not_sealed: "start not sealed",
  no_whole_instructions: "instructions not kept whole",
  no_longer_kept: "no longer kept",
  not_readable_yet: "not readable yet",
  too_large: "too large to read",
};

export const causeLabel = (value) => CAUSES[value] ?? humanize(value);

// The result states, in the order they are counted. Errors are never fails.
export const COUNTED_STATES = [
  { key: "answered", label: "Answered" },
  { key: "unmeasured", label: "Unmeasured" },
  { key: "not_applicable", label: "Not applicable" },
  { key: "grading_error", label: "Grading error" },
  { key: "infrastructure_error", label: "Infrastructure error" },
  { key: "cancelled", label: "Cancelled" },
];

export const stateLabel = (value) =>
  value === "pending"
    ? "Pending"
    : COUNTED_STATES.find((one) => one.key === value)?.label ?? humanize(value);

export const ASK_STATUS = {
  running: "Running",
  finished: "Finished",
  stopped: "Stopped",
};

export function humanize(value) {
  if (!value) return "";
  const words = String(value).replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export const plural = (n, one, many = `${one}s`) =>
  `${Number(n).toLocaleString()} ${n === 1 ? one : many}`;

/** "About 4 credits", or what is known when no price can be given yet. */
export function costLabel(check) {
  const priced = check?.priced_by_use ?? [];
  const parts = [];
  if (check?.credits_estimate != null) {
    parts.push(`About ${plural(check.credits_estimate, "credit")}`);
  }
  if (priced.length) {
    parts.push(
      `${priced.join(", ")} ${priced.length === 1 ? "is" : "are"} billed on what ${priced.length === 1 ? "it uses" : "they use"}, so there is no price before it runs`,
    );
  }
  if (!parts.length) return "No credits charged";
  return parts.join(". ");
}
