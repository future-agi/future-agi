import { SIMULATION_PERSONA_FILTER_FIELDS } from "./utils/simulation-persona-filter-fields";

export const QUEUE_ROLES = {
  ANNOTATOR: "annotator",
  REVIEWER: "reviewer",
  MANAGER: "manager",
};

export const ROLE_PRIORITY = [
  QUEUE_ROLES.MANAGER,
  QUEUE_ROLES.REVIEWER,
  QUEUE_ROLES.ANNOTATOR,
];

export const queueRoleList = (member) => {
  if (!member) return [];
  if (Array.isArray(member?.roles) && member.roles.length > 0) {
    return member.roles;
  }
  return member?.role ? [member.role] : [QUEUE_ROLES.ANNOTATOR];
};

export const hasQueueRole = (member, role) =>
  queueRoleList(member).includes(role);

export const isQueueAnnotatorRole = (annotator) =>
  hasQueueRole(annotator, QUEUE_ROLES.ANNOTATOR);

export const queueViewerMembership = (queue) => {
  const viewerRoles =
    Array.isArray(queue?.viewer_roles) && queue.viewer_roles.length > 0
      ? queue.viewer_roles
      : queue?.viewer_role
        ? [queue.viewer_role]
        : [];

  if (viewerRoles.length === 0) return null;

  return {
    role: queue?.viewer_role || viewerRoles[0],
    roles: viewerRoles,
  };
};

export const canViewerAddItemsToQueue = (queue) =>
  hasQueueRole(queueViewerMembership(queue), QUEUE_ROLES.MANAGER);

// Allowed queue status transitions, mirroring the backend state machine
// (VALID_STATUS_TRANSITIONS in model_hub/models/annotation_queues.py). The
// update-status endpoint rejects anything not listed here, so the UI must only
// offer reachable targets — nothing transitions *to* "draft", for instance.
export const QUEUE_STATUS_TRANSITIONS = {
  draft: ["active"],
  active: ["paused", "completed"],
  paused: ["active", "completed"],
  completed: ["active", "paused"],
};

export const SOURCE_OPTIONS = [
  { value: "dataset_row", label: "Dataset Row" },
  { value: "trace", label: "Trace" },
  { value: "observation_span", label: "Span" },
  { value: "trace_session", label: "Session" },
  { value: "call_execution", label: "Simulation" },
];

export const TRIGGER_FREQUENCY_OPTIONS = [
  { value: "manual", label: "Manually" },
  { value: "hourly", label: "Every hour" },
  { value: "daily", label: "Daily" },
  { value: "weekly", label: "Weekly" },
  { value: "monthly", label: "Monthly" },
];

export const DEFAULT_FILTER = {
  column_id: "",
  filter_config: {
    filter_type: "",
    filter_op: "",
    filter_value: "",
  },
};

export const SIMULATION_RULE_FILTER_FIELDS = [
  {
    id: "status",
    name: "Status",
    category: "system",
    type: "categorical",
    choices: ["completed", "failed", "in_progress", "pending", "cancelled"],
  },
  ...SIMULATION_PERSONA_FILTER_FIELDS,
  {
    id: "agent_definition",
    name: "Agent Definition",
    category: "system",
    type: "text",
  },
  {
    id: "call_type",
    name: "Call Type",
    category: "system",
    type: "categorical",
    choices: ["voice", "text"],
  },
  {
    id: "simulation_call_type",
    name: "Simulation Call Type",
    category: "system",
    type: "text",
  },
  {
    id: "duration_seconds",
    name: "Duration",
    category: "system",
    type: "number",
  },
  {
    id: "overall_score",
    name: "Overall Score",
    category: "system",
    type: "number",
  },
  {
    id: "created_at",
    name: "Created At",
    category: "system",
    type: "date",
  },
];

export const SIMPLE_FILTER_CATEGORIES = [
  { key: "all", label: "All", icon: "mdi:view-grid-outline" },
  { key: "system", label: "System", icon: "mdi:tune-variant" },
  { key: "persona", label: "Persona", icon: "mdi:account-outline" },
];

export const SESSION_RULE_FILTER_FIELDS = [
  { id: "session_id", name: "Session ID", category: "system", type: "string" },
  {
    id: "first_message",
    name: "First Message",
    category: "system",
    type: "string",
  },
  {
    id: "last_message",
    name: "Last Message",
    category: "system",
    type: "string",
  },
  { id: "user_id", name: "User ID", category: "system", type: "string" },
  { id: "duration", name: "Duration", category: "system", type: "number" },
  { id: "total_cost", name: "Total Cost", category: "system", type: "number" },
  {
    id: "total_traces_count",
    name: "Total Traces",
    category: "system",
    type: "number",
  },
  { id: "start_time", name: "Start Time", category: "system", type: "date" },
  { id: "end_time", name: "End Time", category: "system", type: "date" },
];

export const MULTI_VALUE_OPS = new Set(["in", "not_in"]);
