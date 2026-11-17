// The Add-data sheet's static vocabulary (R2-SPEC §7.3): sources, item
// shapes, include flags, expected-output choices and mapping recipes. Pure
// data; the sheet and mappingModel.js read it.

/** Source list, in sheet order. `importable` = previewed through IM1. */
export const SOURCES = Object.freeze([
  {
    id: "sessions",
    label: "Sessions",
    icon: "mdi:forum-outline",
    importable: true,
  },
  {
    id: "traces",
    label: "Traces",
    icon: "mdi:transit-connection-variant",
    importable: true,
  },
  {
    id: "queue",
    label: "Annotation queue",
    icon: "mdi:account-check-outline",
    importable: true,
  },
  // options-truth L5 G5: Simulation runs (nothing to import yet) and SDK /
  // API (the coming SDK's path; it printed a call this sheet never sends)
  // stay out until they are real.
  { id: "file", label: "File", icon: "mdi:file-upload-outline" },
]);

export const sourceById = (id) => SOURCES.find((s) => s.id === id) ?? null;

/** Session shapes (§7.3.2), segmented in this order. `kind` = row kind. */
export const SESSION_SHAPES = Object.freeze([
  {
    id: "conversation",
    label: "As conversations",
    kind: "conversation",
    explainer:
      "One row per session: the whole conversation with its tool calls. Good for replaying and judging whole conversations.",
    example: "chat-1052 → 6 turns · 3 tool calls",
  },
  {
    id: "task",
    label: "As tasks",
    kind: "task",
    explainer:
      "One row per session, as a task: the opening message and the customer's goal; a simulated customer plays the rest. Good for comparing agents and harnesses.",
    example:
      "chat-1052 → goal “Get a refund for the damaged blender on A-8830 without returning it.”",
  },
  {
    id: "per_turn",
    label: "One row per turn",
    kind: "row",
    explainer: "Each agent turn becomes a row with the history before it.",
    example: "chat-1052 turn 4 → history (3 turns) + the customer message",
  },
  {
    id: "failing_turn",
    label: "Only the failing turn",
    kind: "row",
    explainer: "The turn the session eval marked as failing, with its history.",
    example: "chat-1052 → turn 2 and the 1 turn before it",
  },
]);

/** Trace shapes (§7.3.3). All preview through IM1 `shape:"trace"`. */
export const TRACE_SHAPES = Object.freeze([
  {
    id: "per_trace",
    label: "One row per trace",
    kind: "row",
    explainer: "Root input and output of each trace become one row.",
  },
  {
    id: "per_span",
    label: "One row per span",
    kind: "row",
    filter: "name = respond",
    explainer:
      "Every span named respond becomes a row (filter: name = respond).",
  },
  {
    id: "trace_item",
    label: "Whole trace as a trace item",
    kind: "trace",
    explainer:
      "A frozen copy of the trace tree, for runs that score the recorded path only.",
  },
]);

/** Queue and simulation shapes (§7.3.7b). */
export const QUEUE_SHAPES = Object.freeze([
  {
    id: "conversation",
    label: "As rows",
    kind: "row",
    explainer:
      "Each completed item becomes a row; session items land as conversations.",
  },
]);
export const SIMULATION_SHAPES = Object.freeze([
  SESSION_SHAPES[0],
  {
    ...SESSION_SHAPES[1],
    explainer:
      "One row per call, as a task: the goal comes from the scenario; a simulated customer plays the rest.",
  },
]);

/**
 * IT-R2 G5 P3: why a shape is off for the target, said inline (not only in
 * a tooltip on a disabled control) with the action that unblocks it.
 */
export const SHAPE_DISABLED_REASONS = Object.freeze({
  taskNeedsSuite:
    "As tasks needs a task suite: a task carries a sandbox start state and checks, and this dataset has no columns for them. Import as conversations, or start a new task suite.",
});
export const SHAPE_UNBLOCK_LABELS = Object.freeze({
  taskNeedsSuite: "Import into a new task suite",
});

export function shapesFor(sourceKind) {
  if (sourceKind === "traces") return TRACE_SHAPES;
  if (sourceKind === "queue") return QUEUE_SHAPES;
  if (sourceKind === "simulation") return SIMULATION_SHAPES;
  return SESSION_SHAPES;
}

/** The IM1 `shape` for a sheet shape id. */
export function im1Shape(sourceKind, shapeId) {
  if (sourceKind === "traces") return "trace";
  if (sourceKind === "queue") return "conversation";
  return ["task", "conversation", "per_turn", "failing_turn"].includes(shapeId)
    ? shapeId
    : "conversation";
}

/** The default shape of a source for a target kind. */
export function defaultShape(sourceKind, targetKind) {
  if (sourceKind === "traces") return "per_trace";
  if (sourceKind === "queue") return "conversation";
  return targetKind === "task_suite" ? "task" : "conversation";
}

/** The dataset kind a new dataset gets for a shape. */
export function newDatasetKind(shapeId) {
  if (shapeId === "task") return "task_suite";
  if (shapeId === "conversation") return "conversations";
  return "rows";
}

/**
 * Include toggles (§7.3.3, Opik pattern) with a rough per-trace size in KB
 * for the estimate line. `on` = default.
 */
export const INCLUDE_FLAGS = Object.freeze([
  { id: "child_spans", label: "Child spans", kb: 6.2, on: true },
  { id: "tool_results", label: "Tool results", kb: 2.4, on: true },
  { id: "attachments", label: "Attachments", kb: 48, on: false },
  {
    id: "eval_scores",
    label: "Eval scores (frozen at import)",
    kb: 0.3,
    on: true,
  },
  { id: "annotations", label: "Annotations", kb: 0.2, on: false },
  { id: "error_feed", label: "Error Feed category", kb: 0.1, on: false },
  { id: "usage_cost", label: "Usage and cost", kb: 0.1, on: true },
  { id: "metadata", label: "Metadata", kb: 0.4, on: true },
]);

export const defaultIncludes = () =>
  Object.fromEntries(INCLUDE_FLAGS.map((f) => [f.id, f.on]));

/** Expected output, safely (§7.3.4). The first is the default. */
export const EXPECTED_OPTIONS = Object.freeze([
  { id: "empty", label: "Leave empty, needs review" },
  { id: "human_correction", label: "Human correction if any, else empty" },
  {
    id: "production_output",
    label: "Production reply (what the model said)",
  },
]);

export const PRODUCTION_WARNING =
  "This is what the model said, not what is right.";

/**
 * `Field ▾` options: the IM1 fields the server resolves (`from.field`).
 * `sources` = where the field exists.
 */
export const FIELD_OPTIONS = Object.freeze([
  {
    field: "first_customer_message",
    label: "First customer message",
    sources: ["sessions", "queue", "simulation", "traces"],
  },
  {
    field: "last_customer_message",
    label: "Last customer message",
    sources: ["sessions", "queue", "simulation"],
  },
  {
    field: "final_agent_reply",
    label: "Final agent reply",
    sources: ["sessions", "queue", "simulation", "traces"],
  },
  {
    field: "conversation",
    label: "Whole conversation",
    sources: ["sessions", "queue", "simulation"],
  },
  {
    field: "conversation_to_failing_turn",
    label: "Conversation up to the failing turn",
    sources: ["sessions", "queue", "simulation"],
  },
  {
    field: "tool_calls",
    label: "Tool calls",
    sources: ["sessions", "queue", "simulation"],
  },
  {
    field: "resolution_reason",
    label: "Resolution reason",
    sources: ["sessions", "queue", "simulation"],
  },
  { field: "trace_input", label: "Trace input", sources: ["traces"] },
  { field: "trace_output", label: "Trace output", sources: ["traces"] },
  {
    field: "retrieved_context",
    label: "Retrieved context",
    sources: ["sessions", "traces", "queue"],
  },
  {
    field: "human_correction",
    label: "Human correction, else empty",
    sources: ["sessions", "traces", "queue"],
  },
  {
    field: "evals",
    label: "Session evals (frozen)",
    sources: ["sessions", "queue", "simulation"],
  },
]);

export const fieldLabel = (field) =>
  FIELD_OPTIONS.find((f) => f.field === field)?.label ?? field;

/** Recipe chips (§7.3.1): one click sets the active row's `from`. */
export const RECIPES = Object.freeze([
  { label: "Last customer message", field: "last_customer_message" },
  {
    label: "Conversation up to the failing turn",
    field: "conversation_to_failing_turn",
  },
  { label: "Final agent reply", field: "final_agent_reply" },
  { label: "Tool calls", field: "tool_calls" },
  { label: "Retrieved context", field: "retrieved_context" },
  { label: "Human correction, else empty", field: "human_correction" },
  { label: "Session evals (frozen)", field: "evals" },
]);

/** Sessions filter choices (the pool is the last 7 days). */
export const RESOLUTION_FILTERS = Object.freeze([
  { id: "Fail", label: "Resolution = Fail" },
  { id: "any", label: "Any resolution" },
]);

export const WINDOW_LABEL = "last 7 days";

/** Above this many matches the source offers background / first / sample. */
export const LARGE_SELECTION = 200;
