// Stream A copy (SKILL-TRACES §2.1 and §2.2, UX-06). Every runtime component,
// including the Observe ones in lane ST9, imports its words from here. A load
// is never "use": this module never says "Used", "using" or "in use", and it
// never says "Error rate", "In scope" or "unrecognised digest".

const formatter = new Intl.NumberFormat("en-US");

export const plural = (count, one, many = `${one}s`) =>
  `${formatter.format(count)} ${count === 1 ? one : many}`;
export const formatCount = (value) =>
  value === null || value === undefined ? "—" : formatter.format(value);
export const formatPercent = (rate) => {
  if (rate === null || rate === undefined || !Number.isFinite(Number(rate))) return "—";
  const value = Number(rate) * 100;
  const rounded = value >= 10 ? Math.round(value) : Math.round(value * 10) / 10;
  return `${rounded}%`;
};
export const formatMs = (ms) => {
  if (ms === null || ms === undefined || !Number.isFinite(Number(ms))) return "—";
  const value = Number(ms);
  return value >= 1000 ? `${(value / 1000).toFixed(value >= 10000 ? 0 : 1)} s` : `${Math.round(value)} ms`;
};
export const formatCost = (value) => {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "—";
  const number = Number(value);
  if (number === 0) return "$0";
  return number < 0.01 ? `$${number.toPrecision(2)}` : `$${number.toFixed(number < 1 ? 3 : 2)}`;
};
export const versionName = (version) => (version === null || version === undefined ? "Unknown version" : `v${version}`);

// §2.2 surface names.
export const RUNTIME_TAB = "Runtime";
export const LOADED_BY = "Loaded by";
export const SKILLS_LOADED = "Skills loaded";
export const SKILLS_LOADED_AT_RUNTIME = "Skills loaded at runtime";
export const LINKED_NO_LOADS = "Linked, no loads recorded";
export const LOADED_7_DAYS = "Loaded (7 days)";
export const LINKED_CONTEXTS = "Linked contexts";
export const ACTIVITY_TAB = "Activity";
export const RETRIEVAL_FETCHES = "Retrieval (fetches)";

// §2.1 facts.
export const FETCHED = "Fetched";
export const LINKED = "Linked";
export const LOADED = "Loaded";
export const LOAD_FAILED = "Load failed";
export const NO_LOADS_RECORDED = "No loads recorded";
export const RAN_AFTER_LOADING = "Ran after loading";
export const FAILED_REQUESTS = "Failed requests";
export const CONTENTS_MISMATCH = "Contents don't match the published version";
export const EVIDENCE = Object.freeze({
  delivered: "Given to the app",
  agent_read: "Read by the agent",
  declared: "Reported by the app",
});
export const askedFor = (selector) => `Asked for ${selector}`;
export const askedForVersion = (version) => `Asked for v${version}`;
export const labelNow = (label) => `${label} now`;
export const NOT_LINKED_HERE = "not linked here";
export const LINKED_BADGE = "linked";

// Metric names.
export const METRIC = Object.freeze({
  agent: "Agent",
  skill: "Skill",
  version: "Version",
  traces: "Traces",
  agents: "Agents",
  failedRequests: FAILED_REQUESTS,
  p95: "p95",
  p50: "p50",
  costPerTrace: "Cost per trace",
  tokens: "Tokens per trace",
  loadTime: "Load time",
  cachedShare: "Cached",
  costAfterLoading: "Cost after loading",
  askedFor: "Asked for",
  lastLoaded: "Last loaded",
});
const METRIC_PHRASE = { failed_requests: "failed requests", latency_p95: "p95 latency", traces: "traces", cost_per_trace: "cost per trace" };
/** "failed requests", "p95 latency", "<eval> pass rate". */
export const metricPhrase = (metric, evalName) => {
  if (typeof metric === "string" && metric.startsWith("eval:")) return `${evalName || "eval"} pass rate`;
  return METRIC_PHRASE[metric] || String(metric || "").replace(/_/g, " ");
};
/** Which way "worse" goes for a metric: rates and latency rise, pass rates fall. */
export const worseDirection = (metric) => (typeof metric === "string" && metric.startsWith("eval:") ? "down" : "up");

// Window controls.
export const WINDOW_OPTIONS = Object.freeze([
  { value: "24h", label: "24 hours", hours: 24 },
  { value: "7d", label: "7 days", hours: 24 * 7 },
  { value: "30d", label: "30 days", hours: 24 * 30 },
]);
export const DEFAULT_WINDOW = "7d";
export const WINDOW_LABEL = "Window";
export const PROJECTS_LABEL = "Projects";
export const COLUMNS_LABEL = "Columns";
export const windowPhrase = (value) => {
  const option = WINDOW_OPTIONS.find((item) => item.value === value);
  return option ? `the last ${option.label}` : "this window";
};

// Lead sentence (§7.3 item 3).
export const leadSentence = ({ subject, agents, projects, compare }) => {
  if (!agents) return null;
  const first = `${subject} is loaded by ${plural(agents, "agent")} in ${plural(projects || 1, "project")}.`;
  if (!compare || !compare.agents) return first;
  return `${first} On the ${plural(compare.agents, "agent")} that ran both, ${metricPhrase(compare.metric)} ${compare.metric === "failed_requests" ? "are" : "is"} ${compare.aText} on v${compare.versionA} and ${compare.bText} on v${compare.versionB}.`;
};

// Failed loads and contents (§7.3 items 1 and 2, UX-04).
const ERROR_TYPE = { integrity: "integrity", authorization: "authorization", lease: "lease", not_found: "not found", other: "other" };
export const errorTypeName = (type) => ERROR_TYPE[type] || "other";
export const ERROR_TYPE_DETAIL = Object.freeze({
  integrity: "integrity check",
  authorization: "not authorized",
  lease: "lease expired",
  not_found: "not found",
  other: "other error",
});
export const failedLoadsSentence = ({ total, name, windowValue, byErrorType = {} }) => {
  const parts = Object.entries(byErrorType)
    .filter(([, count]) => count > 0)
    .sort((a, b) => b[1] - a[1])
    .map(([type, count]) => `${formatCount(count)} ${errorTypeName(type)}`);
  return `${plural(total, "load")} of ${name} failed in ${windowPhrase(windowValue)}${parts.length ? ` (${parts.join(", ")})` : ""}.`;
};
export const MATCHED_BY_NAME = "Matched by the name the app reported.";
export const mismatchSentence = (loads) =>
  `${plural(loads, "load")} had contents that don't match the published version. They're left out of the numbers below.`;
export const OPEN_THOSE_TRACES = "Open those traces";
export const failedLoadChip = (name, errorType) =>
  `${name} · load failed${errorType ? ` · ${ERROR_TYPE_DETAIL[errorType] || ERROR_TYPE_DETAIL.other}` : ""}`;

// Loaded by table (§7.3).
export const versionsWithNoLoads = (count) => `${plural(count, "version")} with no loads recorded`;
export const OPEN_SKILL_VERSION = "Open skill version";
export const COPY_LINK = "Copy link";
export const LINK_COPIED = "Link copied";
export const COPY_FAILED = "Couldn't copy the link.";
export const unnamedAgent = (project) => `Unnamed agent in ${project || "this project"}`;
export const NAME_IT = "Name it";
export const NAME_IT_LEAD = "Give this agent a name so its loads get their own row. One line does it:";
export const NAME_IT_SNIPPETS = Object.freeze([
  { label: "Python", code: 'with agent_scope(agent="refund-agent"):\n    ...' },
  { label: "Any language", code: "OTEL_SERVICE_NAME=refund-agent" },
]);
export const CONNECTED_APP_BADGE = "Connected app";
export const lowSampleCount = (count, total) => `${formatCount(count)} of ${formatCount(total)}`;
export const lowSampleTitle = (traces) => `Low sample: ${plural(traces, "trace")}. No change is shown below 30 traces.`;
export const evalCell = (mean, coverage) =>
  `${mean === null || mean === undefined ? "—" : Number(mean).toFixed(2)} · ${formatPercent(coverage)} evaluated`;
export const askedForMix = (requestedLabels = {}, requestedVersions = {}) => {
  const entries = [
    ...Object.entries(requestedLabels).map(([label, count]) => [`asked for ${label}`, count]),
    ...Object.entries(requestedVersions).map(([version, count]) => [`asked for v${version}`, count]),
  ];
  const total = entries.reduce((sum, [, count]) => sum + count, 0);
  if (!total) return "—";
  const text = entries
    .sort((a, b) => b[1] - a[1])
    .map(([phrase, count]) => `${phrase} ${formatPercent(count / total)}`)
    .join(" · ");
  return text.charAt(0).toUpperCase() + text.slice(1);
};
export const projectLine = ({ projectName, traces, failedRequests }) =>
  `${projectName} · ${plural(traces ?? 0, "trace")}${failedRequests === null || failedRequests === undefined ? "" : ` · ${formatCount(typeof failedRequests === "object" ? failedRequests.count : failedRequests)} failed`}`;
export const CHOOSE_PROJECT = (what) => `Choose a project for ${what}`;
export const VIEWING_ROW = "Viewing";
export const PRODUCTION_ROW = "production now";

// About this data (§7.3 item 7).
export const ABOUT_THIS_DATA = "About this data";
export const ABOUT_LINES = Object.freeze([
  "A trace can load several skills and versions, so these groups overlap. Numbers are never added across rows.",
  "Comparisons show what happened after a change, not what caused it.",
  "A load is recorded on the first read of any file of a skill version inside a trace.",
  "A failed request is a trace whose root span ended with an error.",
]);

// Move state (§7.3 header, UX-01).
export const hoursLeft = (hours) => `${Math.max(0, Math.ceil(hours))} h left`;
export const moveChipText = ({ verdict, toVersion, metric, evalName, agents, hoursElapsed, hoursTotal, hoursRemaining }) => {
  const version = `v${toVersion}`;
  if (verdict === "watching") return `Watching ${version} · ${hoursLeft(hoursRemaining)}`;
  const phrase = metric ? `${metricPhrase(metric, evalName)} ${worseDirection(metric)}` : "worse";
  if (verdict === "worse_so_far") {
    return `${version}: ${phrase} so far on ${plural(agents, "agent")} · ${Math.floor(hoursElapsed)} of ${Math.round(hoursTotal)} h`;
  }
  if (verdict === "worse") return `${version}: ${phrase} on ${plural(agents, "agent")}`;
  return null;
};
export const libraryMoveText = ({ verdict, toVersion, metric, evalName }) => {
  if (verdict === "watching") return `v${toVersion} · watching`;
  if (verdict === "worse_so_far" || verdict === "worse") {
    return metric ? `v${toVersion} · ${metricPhrase(metric, evalName)} ${worseDirection(metric)}` : `v${toVersion} · worse after the move`;
  }
  return null;
};

// Move card (§7.3, UX-05).
export const moveTitle = ({ label = "production", fromVersion, toVersion, windowHours }) =>
  `${label.charAt(0).toUpperCase()}${label.slice(1)} moved from ${fromVersion == null ? "no version" : `v${fromVersion}`} to v${toVersion} · ${plural(windowHours, "hour")} each side`;
export const contextReleaseTitle = ({ fromVersion, toVersion, windowHours }) =>
  `Released v${toVersion} (was ${fromVersion == null ? "unreleased" : `v${fromVersion}`}) · ${plural(windowHours, "hour")} each side`;
export const beforeCut = ({ toVersion, date }) => `The before period starts at the earlier move to v${toVersion} on ${date}.`;
export const collecting = ({ hoursElapsed, hoursTotal, traces }) =>
  `Collecting: ${Math.floor(hoursElapsed)} of ${Math.round(hoursTotal)} hours, ${plural(traces ?? 0, "trace")} so far`;
export const soFar = ({ hoursElapsed, hoursTotal }) => `so far · ${Math.floor(hoursElapsed)} of ${Math.round(hoursTotal)} h`;
export const SAME_AGENTS = "Same agents before and after";
export const sameAgentsLead = (agents) => `On the ${plural(agents, "agent")} that ran before and after the move`;
export const controlLine = ({ version, agents }) => `v${version} in the same period, ${plural(agents, "agent")}`;
export const ALL_AGENTS = "All agents";
export const BY_AGENT = "By agent";
export const ADOPTION = "Who moved";
export const BEFORE = "Before";
export const AFTER = "After";
export const reviewFailed = (count) => `Review ${plural(count, "failed request")}`;
export const rollBackTo = (version) => `Roll back to v${version}`;
export const VERDICT_TEXT = Object.freeze({
  watching: "Watching",
  worse_so_far: "Worse so far after the move",
  worse: "Worse after the move",
  better: "Better after the move",
  no_change: "No change after the move",
  not_enough_data: "Not enough traces to compare",
});
export const notMovedText = ({ reason, fromVersion, toVersion, contextName, cached }) => {
  switch (reason) {
    case "got_new":
      return `Got v${toVersion}`;
    case "asked_for_version":
      return `Still on v${fromVersion}: the app asks for v${fromVersion}`;
    case "pinned_in_context":
      return `Still on v${fromVersion}: pinned in ${contextName || "its agent context"}`;
    case "waiting_for_context_release":
      return `Still on v${fromVersion}: waiting for a ${contextName || "context"} release`;
    case "got_old_after_move":
      return `Got v${fromVersion} after the move${cached ? ", from a cached copy" : ""}`;
    case "not_seen":
    default:
      return "Not seen after this move";
  }
};
export const OPEN_CONTEXT = "Open context";
export const deltaText = (delta, metric) => {
  if (delta === null || delta === undefined || !Number.isFinite(Number(delta))) return null;
  const value = Number(delta);
  if (value === 0) return "no change";
  const arrow = value > 0 ? "↑" : "↓";
  if (metric === "failed_requests" || (typeof metric === "string" && metric.startsWith("eval:"))) {
    return `${arrow} ${Math.abs(value * 100).toFixed(1)} pts`;
  }
  return `${arrow} ${Math.abs(value * 100).toFixed(0)}%`;
};
export const changedSkillLine = ({ name, fromVersion, toVersion, change }) => {
  if (change === "added") return `${name}: added at v${toVersion}`;
  if (change === "removed") return `${name}: removed (was v${fromVersion})`;
  return `${name}: v${fromVersion} → v${toVersion}`;
};
export const CHANGED_SKILLS = "Skills that changed in this release";
export const SERIES_TITLE = (metric) => `${metricPhrase(metric).replace(/^./, (c) => c.toUpperCase())} by version`;
export const MOVE_MARKER = "Move";

// Receipt (§7.3 "Links into the card").
export const receiptLoads = ({ agents, verdictText }) =>
  `Loaded by ${plural(agents, "agent")} since the move${verdictText ? ` · ${verdictText}` : ""}`;
export const HOW_IT_WENT = "How it went";

// Diagnosis (§7.6, UX-07).
export const DIAGNOSIS = Object.freeze({
  noTracingProject: "Loads are recorded in traces. This workspace has no tracing project yet.",
  neverFetched: (kind) => `Nobody has fetched this ${kind} yet.`,
  olderSdk: ({ count, kind, days, minVersion }) =>
    `${plural(count, "consumer")} fetched this ${kind} in the last ${days} days. None can record loads: they use an SDK older than ${minVersion || "the first version that records loads"}.`,
  tracingUnavailable: ({ count, kind, days, off, noOtel }) => {
    const parts = [
      off ? `${formatCount(off)} ${off === 1 ? "has" : "have"} tracing switched off` : null,
      noOtel ? `${formatCount(noOtel)} ${noOtel === 1 ? "has" : "have"} no OpenTelemetry set up` : null,
    ].filter(Boolean);
    return `${plural(count, "consumer")} fetched this ${kind} in the last ${days} days. ${parts.join(", ")}.`.replace(/^./, (c) => c.toUpperCase());
  },
  nonSdk: ({ fetches, channels }) =>
    `Fetched ${fetches ? `${plural(fetches, "time")} ` : ""}through ${channels}. Loads are recorded only from the Python or TypeScript SDK.`,
  awaiting: "Consumers can record loads, but none arrived in this window.",
  partial: ({ agents, others, kind }) =>
    `${agents > 0 ? `Loads recorded from ${plural(agents, "agent")}` : "Some consumers can record loads"}. ${plural(others, "other consumer")} fetched this ${kind} and can't record loads.`,
  noReadableProjects: (kind) => `You can't open the projects where this ${kind} is loaded. Ask a workspace admin for access.`,
  unknown: "Loads haven't been recorded here yet, and the reason couldn't be worked out from the fetches.",
  linkedNotRead: "Other skills in this context recorded loads in this window. No app read this one.",
  lowSample: "Rows with fewer than 30 traces show their counts in grey, with no change.",
  noEvals: "No evals ran on these traces.",
  rootMissing: (count) => `${plural(count, "trace")} ${count === 1 ? "has" : "have"} no root span yet and ${count === 1 ? "isn't" : "aren't"} counted.`,
  projectCap: ({ shown, total }) => `Showing ${formatCount(shown)} of ${formatCount(total)} projects.`,
  unreadable: (count) => `${plural(count, "project")} you can't open ${count === 1 ? "isn't" : "aren't"} included.`,
  readBudget: "This took too long. Try a shorter window.",
});
export const DIAGNOSIS_ACTIONS = Object.freeze({
  setUpTracing: "Set up tracing",
  install: "Install",
  upgrade: "Upgrade steps",
  fixTracing: "How to turn it on",
  docs: "Docs",
  checkTracing: "Check tracing",
  seeWhich: "See which",
  chooseProjects: "Choose projects",
  retry: "Retry",
  setUpEval: "Set up an eval",
});
export const TRACING_FIX = Object.freeze({
  off: "Remove FI_SKILLS_TRACING=off from the app's environment.",
  no_otel: "Call traceAI register() (or set up any OpenTelemetry tracer provider) before the app reads skills.",
  load_only: "Tracing records loads only. Call agent_scope() to stamp every span.",
});
export const CHANNEL_NAMES = Object.freeze({ api: "API", mcp: "MCP", download: "download", sdk: "SDK" });
export const consumerLine = ({ label, channel, sdkVersion, tracingMode }) =>
  [label || "Unnamed consumer", CHANNEL_NAMES[channel] || channel, sdkVersion ? `SDK ${sdkVersion}` : null, tracingMode ? `tracing ${tracingMode.replace("_", " ")}` : null]
    .filter(Boolean)
    .join(" · ");
export const TRACE_SKILLS_DOCS = "https://docs.futureagi.com/docs/skills/trace-skills";

// States (§7.7, UX-15).
export const RUNTIME_ERROR = "Could not load runtime data. Your skill and versions are unchanged.";
export const CONTEXT_RUNTIME_ERROR = "Could not load runtime data. Your agent context and versions are unchanged.";
export const RUNTIME_UNAVAILABLE = "Runtime data is temporarily unavailable.";
export const LOADING_RUNTIME = "Loading runtime data";
export const CANT_OPEN_PROJECT = "You can't open this project.";
export const COULDNT_LOAD = "Couldn't load";
export const CANT_RECORD_YET = "Can't record loads yet";
export const MOVE_CARD_ERROR = "Couldn't load this move.";

// Check tracing (§7.3 Install, UX-14).
export const CHECK_TRACING = "Check tracing";
export const CHECK_AGAIN = "Check again";
export const STOP_WAITING = "Stop waiting";
export const waitingFor = (elapsed) => `Waiting for the next load… ${elapsed}`;
export const received = ({ name, version, agent, ago }) =>
  `Received ${name} v${version}${agent ? ` from ${agent}` : ""} ${ago}`;
export const NO_LOAD_IN_10 = "No load arrived in 10 minutes";
export const lastFetchReason = ({ sdkVersion, language, tracingMode, minVersion }) => {
  if (tracingMode === "off") return `The last fetch came from ${language || "an SDK"} with tracing switched off. ${TRACING_FIX.off}`;
  if (tracingMode === "no_otel") return `The last fetch came from ${language || "an SDK"} with no OpenTelemetry set up. ${TRACING_FIX.no_otel}`;
  if (sdkVersion) {
    return `The last fetch came from ${language ? `${language} ` : ""}${sdkVersion}, which can't record loads. Upgrade to ${minVersion || "the latest version"} or later.`;
  }
  return null;
};
export const CHECK_TRACING_VIEWER = "Only people who can install can check tracing.";
export const CHECK_TRACING_LEAD = "Run your app once, then check that its load arrives here.";

// Agent panel (§7.5, UX-10).
export const AGENT_PANEL_TITLE = "Agent";
export const CLOSE_AGENT_PANEL = "Close agent panel";
export const SERVICE = "Service";
export const PROJECT = "Project";
export const CONTEXT = "Agent context";
export const agentLoadedLead = (count) => `${plural(count, "skill version")} loaded in this window`;

// Linked skills column and footer (§7.4, UX-09).
export const tracesCount = (count) => plural(count, "trace");
export const loadedAsOther = ({ loaded, linked }) =>
  `Loaded as ${loaded.map((version) => `v${version}`).join(", ")}; this context links v${linked}`;
export const LOADED_NOT_LINKED = "Loaded but not linked here";

// §2.2 and §9.1: words a new component must never render.
export const FORBIDDEN_RUNTIME_WORDS = Object.freeze([
  /\bused\b/i,
  /\busing\b/i,
  /\bin use\b/i,
  /\buses\b/i,
  /error rate/i,
  /in scope/i,
  /unrecognised digest/i,
  /\bunused\b/i,
  /\b0 agents\b/i,
]);
export const forbiddenRuntimeWords = (text) =>
  FORBIDDEN_RUNTIME_WORDS.filter((pattern) => pattern.test(String(text || ""))).map(String);
