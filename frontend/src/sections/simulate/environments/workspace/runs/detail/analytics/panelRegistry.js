// The run analytics widgets: a stable id, the section it lives in, its
// default width and whether only voice runs have it. The layout hook stores
// ids only, so a saved arrangement survives any change to how a widget draws.
//
// `columns` is how many widget slots a section has side by side on wide
// screens; `defaultSpan` is measured in those slots.
export const SECTIONS = [
  { id: "breakdowns", label: "Breakdowns", columns: 4 },
  { id: "reliability", label: "Reliability", columns: 2 },
  { id: "evaluations", label: "Evaluations", columns: 2 },
  {
    id: "csat",
    label: "CSAT and provider scores",
    hint: "Scores the run already produced. No new cost.",
    columns: 2,
  },
  { id: "voice", label: "Voice latency SLOs", columns: 2 },
  { id: "trends", label: "Latency", columns: 2 },
  { id: "distribution", label: "Distribution", columns: 2 },
  { id: "failure_analysis", label: "Failure analysis", columns: 2 },
  { id: "tools", label: "Tools", columns: 2 },
  { id: "performance_tails", label: "Performance tails", columns: 2 },
  { id: "custom", label: "Custom widgets", columns: 2 },
];

const capital = (text) => text.charAt(0).toUpperCase() + text.slice(1);

// `title` takes the run's noun ("call" or "chat") where the wording follows it.
export const PANELS = [
  {
    id: "goal_outcome",
    section: "breakdowns",
    title: "Goal outcome breakdown",
  },
  {
    id: "disconnection",
    section: "breakdowns",
    title: (noun) => `How ${noun}s ended`,
  },
  {
    id: "provider_success",
    section: "breakdowns",
    title: "Provider's own success flag",
  },
  { id: "sentiment", section: "breakdowns", title: "Provider sentiment" },
  {
    id: "reliability",
    section: "reliability",
    title: "Reliability across trials",
    defaultSpan: 2,
  },
  {
    id: "evaluations",
    section: "evaluations",
    title: "Evaluations",
    defaultSpan: 2,
  },
  {
    id: "csat",
    section: "csat",
    title: "CSAT distribution (0–10)",
    defaultSpan: 2,
  },
  {
    id: "voice_slos",
    section: "voice",
    title: "Voice pipeline latency",
    voiceOnly: true,
  },
  // Chat runs record LLM cost too; there it sits with the other cost widget.
  {
    id: "pipeline_cost",
    section: "voice",
    chatSection: "performance_tails",
    title: "Cost breakdown by pipeline stage",
  },
  {
    id: "call_duration",
    section: "trends",
    title: (noun) => `${capital(noun)} duration`,
  },
  {
    id: "duration_percentiles",
    section: "trends",
    title: (noun) => `${capital(noun)} duration percentiles`,
  },
  { id: "task_latency", section: "trends", title: "Agent latency" },
  {
    id: "percentiles",
    section: "trends",
    title: "Agent response time percentiles",
  },
  {
    id: "response_time",
    section: "trends",
    title: "Agent response time per call",
    defaultSpan: 2,
  },
  {
    id: "distribution",
    section: "distribution",
    title: "Distribution summary",
    defaultSpan: 2,
  },
  {
    id: "attribution",
    section: "distribution",
    title: "Failure attribution",
    defaultSpan: 2,
  },
  {
    id: "risk",
    section: "failure_analysis",
    title: "Weakest scenarios",
    defaultSpan: 2,
  },
  { id: "tools_volume", section: "tools", title: "Tool call volume" },
  { id: "tools_failure", section: "tools", title: "Tool failure rate" },
  {
    id: "slowest",
    section: "performance_tails",
    title: (noun) => `Slowest ${noun}s`,
  },
  {
    id: "expensive",
    section: "performance_tails",
    title: (noun) => `Most expensive ${noun}s`,
  },
];

export const getPanelMeta = (id) =>
  PANELS.find((panel) => panel.id === id) || null;

export const sectionMeta = (id) =>
  SECTIONS.find((section) => section.id === id) || null;

export function panelTitle(id, noun = "call") {
  const title = getPanelMeta(id)?.title;
  if (typeof title === "function") return title(noun);
  return title || id;
}

export function panelSection(id, surface) {
  const meta = getPanelMeta(id);
  if (!meta) return "custom";
  return surface === "chat" && meta.chatSection
    ? meta.chatSection
    : meta.section;
}

export const panelSpan = (id) => getPanelMeta(id)?.defaultSpan || 1;

// Where a widget sits until it is moved: a built-in in its own section, a
// copy in its source's section, a query widget under Custom widgets.
// `widget` is the custom widget with this id, if it is one.
export const defaultSectionOf = (id, widget, surface) =>
  widget ? widget.section || "custom" : panelSection(id, surface);

// A widget's width until it is changed, in its section's `columns`: a
// built-in, a copy of one or an edited copy (`spanOf`) takes the built-in's
// width, any other query widget the whole row.
export const defaultSpanOf = (id, widget, columns) => {
  const builtin = widget ? widget.of || widget.spanOf : id;
  return Math.min(builtin ? panelSpan(builtin) : columns, columns);
};

export const sectionColumns = (id) => sectionMeta(id)?.columns || 2;

// Every built-in a run of this surface can show, in page order.
export const eligibleIds = (surface) =>
  PANELS.filter((panel) => !panel.voiceOnly || surface !== "chat").map(
    (panel) => panel.id,
  );
