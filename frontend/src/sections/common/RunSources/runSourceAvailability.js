// Which run sources this workspace can create and run, as the server reports
// them (GET /api/capabilities/ `experiment_run_sources`,
// tfc/capabilities/contracts.py ExperimentRunSourcesSerializer). Pure: no
// React. useRunSourceAvailability.js reads the report for the picker.
//
//   availability  {status, workspaceId, sources: {kind: {available, reason}}}
//
// Already running sources (HTTP endpoint, A2A agent, MCP tool) can be picked
// only where the server says it can run them; until it answers, they show
// greyed with a reason (pickReason), never hidden.
// Prompt and Agent graph stay as they were until the server says no (create
// and run still check), and then show the server's reason.

export const RUN_SOURCE_KINDS = Object.freeze([
  "prompt",
  "agent",
  "http",
  "a2a",
  "mcp_tool",
]);

/** The kinds behind the Already running card. */
export const RUNNING_SOURCE_KINDS = Object.freeze(["http", "a2a", "mcp_tool"]);

export const AVAILABILITY_STATUS = Object.freeze({
  LOADING: "loading",
  READY: "ready",
  FAILED: "failed",
});

/** A failed read is tried again this often, quietly (no toast). */
export const QUIET_RETRY_MS = 15000;

// The report names a reason for every source it refuses; this covers an
// entry without one.
export const FALLBACK_REASON = "Not available in this workspace.";

// An Already running source stays on screen, greyed, while the report loads,
// after a failed read or with no report (the wording shipped before the
// report existed).
export const UNREPORTED_REASON = "Not available in this deployment";

// A part this release does not build says so in these words (owner rule;
// a local constant). The picker uses it for its held options (catalog.js)
// and, while the server has given no reason of its own, for the A2A agent
// card and the MCP server tool: this release creates neither (the server
// says "... not built in this release" once it answers).
export const NOT_IN_RELEASE_REASON = "Not available in this release";
const UNBUILT_RUNNING_KINDS = Object.freeze(["a2a", "mcp_tool"]);

const unknown = (status) =>
  Object.freeze({ status, workspaceId: null, sources: Object.freeze({}) });

/** Not known yet. */
export const LOADING_AVAILABILITY = unknown(AVAILABILITY_STATUS.LOADING);
/** The read failed, or the server sent no report. */
export const FAILED_AVAILABILITY = unknown(AVAILABILITY_STATUS.FAILED);

const isEntry = (entry) => entry !== null && typeof entry === "object";

function sourceOf(entry) {
  if (entry.available === true)
    return Object.freeze({ available: true, reason: null });
  const reason = typeof entry.reason === "string" ? entry.reason.trim() : "";
  return Object.freeze({ available: false, reason: reason || FALLBACK_REASON });
}

/**
 * The availability in a capabilities body's `experiment_run_sources`, or null
 * when there is no report. Only a literal `available: true` offers a source;
 * a kind the report leaves out stays unknown.
 */
export function availabilityOfReport(report) {
  const sources = report?.sources;
  if (!isEntry(sources)) return null;
  return Object.freeze({
    status: AVAILABILITY_STATUS.READY,
    workspaceId:
      typeof report.workspace_id === "string" ? report.workspace_id : null,
    sources: Object.freeze(
      Object.fromEntries(
        RUN_SOURCE_KINDS.filter((kind) => isEntry(sources[kind])).map(
          (kind) => [kind, sourceOf(sources[kind])],
        ),
      ),
    ),
  });
}

const reported = (kind, availability) =>
  availability?.status === AVAILABILITY_STATUS.READY
    ? availability.sources?.[kind] ?? null
    : null;

/**
 * Whether a run kind can be picked here. Already running kinds need the
 * server's yes; Prompt and Agent graph need only no refusal; kinds the report
 * does not cover (code, harness, traces, ...) keep their behaviour.
 */
export function sourceAvailable(kind, availability) {
  const entry = reported(kind, availability);
  if (RUNNING_SOURCE_KINDS.includes(kind)) return entry?.available === true;
  return entry ? entry.available : true;
}

/** The server's plain reason a kind cannot run here, or null. */
export function sourceUnavailableReason(kind, availability) {
  const entry = reported(kind, availability);
  return entry && !entry.available ? entry.reason : null;
}

/**
 * Why a source shown in the picker cannot be picked, or null. Already
 * running kinds are always shown: without the server's yes they carry its
 * reason, or, while there is no report, NOT_IN_RELEASE_REASON (A2A, MCP
 * tool) or UNREPORTED_REASON (HTTP endpoint). Other kinds follow
 * sourceUnavailableReason.
 */
export function pickReason(kind, availability) {
  if (!RUNNING_SOURCE_KINDS.includes(kind))
    return sourceUnavailableReason(kind, availability);
  if (sourceAvailable(kind, availability)) return null;
  return (
    sourceUnavailableReason(kind, availability) ??
    (UNBUILT_RUNNING_KINDS.includes(kind)
      ? NOT_IN_RELEASE_REASON
      : UNREPORTED_REASON)
  );
}

/**
 * Why the Already running card cannot be picked, or null when the server
 * runs at least one of its kinds: the server's reason when it refused them
 * all with one reason, else UNREPORTED_REASON.
 */
export function runningCardReason(availability) {
  if (RUNNING_SOURCE_KINDS.some((kind) => sourceAvailable(kind, availability)))
    return null;
  const reasons = new Set(
    RUNNING_SOURCE_KINDS.map((kind) =>
      sourceUnavailableReason(kind, availability),
    ),
  );
  const [only] = [...reasons];
  return reasons.size === 1 && only ? only : UNREPORTED_REASON;
}
