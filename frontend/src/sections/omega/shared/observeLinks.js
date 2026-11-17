import { addDays, startOfDay, sub } from "date-fns";
import { formatDate } from "src/utils/report-utils";
import { serializeFilterListForApi } from "src/api/contracts/filter-contract";

// Kept in sync with LLMTracing/common.js. The shared layer must not import
// the feature section just to reuse URL parameter constants or filter rows.
export const OBSERVE_LINK_FILTER_PARAM = "observeLinkFilter";
export const OBSERVE_ORIGIN_PARAM = "observeFrom";
export const OBSERVE_RETURN_PARAM = "observeReturnTo";
export const OBSERVE_TRACE_TAB = "traces";
export const OBSERVE_SELECTED_TAB = "trace";
export const OBSERVE_DATE_FILTER_PARAM = "primaryTraceDateFilter";

const PROJECT_ID_RE = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/;
const DEPLOYMENT_ID_RE = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/;
const ATTRIBUTE_RE = /^[A-Za-z0-9_.:-]{1,255}$/;
const ORIGIN_LABEL_MAX = 120;

const FILTER_FOR_ERRORS = Object.freeze({
  column_id: "status",
  filter_config: {
    filter_type: "text",
    filter_op: "equals",
    filter_value: "ERROR",
  },
});

const WINDOW_DURATIONS = Object.freeze({
  "7d": { days: 7 },
  "30d": { days: 30 },
  "3m": { months: 3 },
  "6m": { months: 6 },
  "12m": { months: 12 },
});

const floorToUtcHour = (date) =>
  new Date(Math.floor(date.getTime() / (60 * 60 * 1000)) * (60 * 60 * 1000));

const filterForAttribute = (attribute, value, displayName) => ({
  column_id: attribute,
  display_name: displayName,
  property_id: `custom_attribute:${attribute}`,
  filter_config: {
    filter_type: "text",
    filter_op: "equals",
    filter_value: value,
    col_type: "SPAN_ATTRIBUTE",
  },
});

const validIdentifier = (value, pattern) =>
  typeof value === "string" && pattern.test(value);

const revisionValue = (value) => {
  if (Number.isSafeInteger(value) && value > 0) return String(value);
  if (typeof value === "string" && /^[1-9]\d*$/.test(value)) {
    const parsed = Number(value);
    if (Number.isSafeInteger(parsed)) return value;
  }
  return null;
};

const safePathSegment = (value) => {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  if (
    !trimmed ||
    trimmed.length > 512 ||
    /[\\/?#]/.test(trimmed) ||
    [...trimmed].some(
      (character) =>
        character.charCodeAt(0) < 0x20 || character.charCodeAt(0) === 0x7f,
    )
  ) {
    return null;
  }
  return trimmed;
};

/**
 * Build the persisted filter rows used by Observe's trace list.
 *
 * These are the same API-shaped rows accepted by `hydrateStoredFilterList`,
 * rather than a second link-only format. That keeps a deep link visible in
 * the filter chip strip and makes removing a chip behave like a normal edit.
 */
export function deploymentObserveFilters({ deploymentId, revision } = {}) {
  if (!validIdentifier(deploymentId, DEPLOYMENT_ID_RE)) return [];

  const rows = [
    filterForAttribute("omega.deployment.id", deploymentId, "Deployment ID"),
  ];
  const revisionText = revisionValue(revision);
  if (revisionText) {
    rows.push(
      filterForAttribute("omega.deployment.revision", revisionText, "Revision"),
    );
  }
  return rows;
}

function sameOriginPath(value, location) {
  if (typeof value !== "string" || !value.trim()) return null;
  const raw = value.trim();
  const base = location?.origin;
  if (!base || raw.startsWith("//") || raw.includes("\\")) return null;
  try {
    const parsed = new URL(raw, base);
    if (
      parsed.origin !== base ||
      parsed.username ||
      parsed.password ||
      !parsed.pathname.startsWith("/")
    ) {
      return null;
    }
    return `${parsed.pathname}${parsed.search}${parsed.hash}`;
  } catch {
    return null;
  }
}

function safeOriginLabel(value) {
  if (typeof value !== "string") return null;
  const label = value.trim().replace(/\s+/g, " ");
  return label && label.length <= ORIGIN_LABEL_MAX ? label : null;
}

function validProjectId(value) {
  return validIdentifier(value, PROJECT_ID_RE);
}

function validAttributeName(value) {
  return validIdentifier(value, ATTRIBUTE_RE);
}

function normalizeDateFilter(value) {
  let candidate = value;
  if (typeof candidate === "string") {
    try {
      candidate = JSON.parse(candidate);
    } catch {
      return null;
    }
  }
  if (!candidate || typeof candidate !== "object") return null;
  if (
    !Array.isArray(candidate.dateFilter) ||
    candidate.dateFilter.length !== 2 ||
    candidate.dateFilter.some(
      (bound) => typeof bound !== "string" || !bound.trim(),
    )
  ) {
    return null;
  }
  return {
    ...candidate,
    dateFilter: candidate.dateFilter.map((bound) => bound.trim()),
    dateOption: candidate.dateOption || "Custom",
  };
}

function dateFilterForWindow(window) {
  if (typeof window !== "string") return normalizeDateFilter(window);
  const normalized = window.trim().toLowerCase();
  if (normalized === "24h") {
    const end = new Date();
    return {
      dateFilter: [formatDate(sub(end, { hours: 24 })), formatDate(end)],
      dateOption: "Custom",
    };
  }
  if (normalized === "today") {
    const start = startOfDay(new Date());
    return {
      dateFilter: [formatDate(start), formatDate(addDays(start, 1))],
      dateOption: "Today",
    };
  }
  const duration = WINDOW_DURATIONS[normalized];
  if (!duration) return null;
  const now = new Date();
  const start = floorToUtcHour(sub(now, duration));
  const end = addDays(startOfDay(now), 1);
  return {
    dateFilter: [formatDate(start), formatDate(end)],
    dateOption: normalized.toUpperCase(),
  };
}

function rowKey(row) {
  return JSON.stringify(row);
}

function mergedObserveFilters({
  deploymentId,
  revision,
  filters,
  includeErrors,
}) {
  const rows = [
    ...deploymentObserveFilters({ deploymentId, revision }),
    ...(Array.isArray(filters) ? filters : []),
    ...(includeErrors ? [FILTER_FOR_ERRORS] : []),
  ];
  const seen = new Set();
  return rows.filter((row) => {
    const key = rowKey(row);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function defaultOriginLabel({ deploymentName, deploymentId, revision }) {
  const rawName = safeOriginLabel(deploymentName) || deploymentId;
  const name = rawName?.replace(/^deploy\//, "");
  const revisionText = revisionValue(revision);
  if (name && revisionText)
    return `${name} · revision ${revisionText} in Deployments`;
  if (name) return `${name} in Deployments`;
  return "Deployments";
}

function setOriginParams(
  params,
  { origin, returnTo, location, deploymentName, deploymentId, revision },
) {
  const backPath = sameOriginPath(returnTo, location);
  const label =
    origin === undefined
      ? safeOriginLabel(
          defaultOriginLabel({ deploymentName, deploymentId, revision }),
        )
      : safeOriginLabel(origin);
  if (backPath && label) {
    params.set(OBSERVE_ORIGIN_PARAM, label);
    params.set(OBSERVE_RETURN_PARAM, backPath);
  }
}

function traceListPath(projectId) {
  return `/dashboard/observe/${encodeURIComponent(projectId)}/llm-tracing`;
}

function canonicalRows(rows, { allowEmpty = false } = {}) {
  try {
    const canonical = serializeFilterListForApi(rows);
    return allowEmpty || canonical.length > 0 ? canonical : null;
  } catch {
    return null;
  }
}

function addFilterParams(params, rows, { allowEmpty = false } = {}) {
  const canonical = canonicalRows(rows, { allowEmpty });
  if (!canonical) return false;
  if (canonical.length > 0) {
    params.set(OBSERVE_LINK_FILTER_PARAM, JSON.stringify(canonical));
  }
  return true;
}

/**
 * Return a same-origin Observe URL for a deployment, revision, error set or run.
 *
 * `filters` are additive: deployment and revision scope is always retained,
 * then caller-provided rows and the optional error row are appended. This is
 * important for Overview cells because replacing the scope would make an
 * Errors link show failures from the entire Observe project.
 */
export function buildObserveLink({
  projectId,
  deploymentId,
  deploymentName,
  revision,
  returnTo,
  origin,
  location = typeof window === "undefined" ? null : window.location,
  filters,
  includeErrors = false,
  errors = false,
  runId,
  traceId,
  primaryTraceDateFilter,
  dateFilter,
  // Bound under another name: a parameter called `window` would shadow the
  // global in `location`'s default above and throw (temporal dead zone)
  // whenever a caller leaves `location` out.
  window: windowOption,
} = {}) {
  if (!validProjectId(projectId)) return null;
  if (!validIdentifier(deploymentId, DEPLOYMENT_ID_RE)) return null;

  const safeTraceId = safePathSegment(traceId);
  if (traceId !== undefined && !safeTraceId) return null;
  const safeRunId = safePathSegment(runId);
  if (runId !== undefined && !safeRunId) return null;

  const effectiveErrors = includeErrors || errors;
  const rows = mergedObserveFilters({
    deploymentId,
    revision,
    filters,
    includeErrors: effectiveErrors,
  });
  const params = new URLSearchParams();
  const dateValue = dateFilterForWindow(
    primaryTraceDateFilter ?? dateFilter ?? windowOption,
  );

  if (safeTraceId) {
    setOriginParams(params, {
      origin,
      returnTo,
      location,
      deploymentName,
      deploymentId,
      revision,
    });
    const query = params.toString();
    return `/dashboard/observe/${encodeURIComponent(projectId)}/trace/${encodeURIComponent(safeTraceId)}${query ? `?${query}` : ""}`;
  }

  if (!validProjectId(projectId)) return null;
  params.set("tab", OBSERVE_TRACE_TAB);
  params.set("selectedTab", OBSERVE_SELECTED_TAB);
  if (dateValue)
    params.set(OBSERVE_DATE_FILTER_PARAM, JSON.stringify(dateValue));

  const listRows = safeRunId
    ? [...rows, filterForAttribute("omega.run.id", safeRunId, "Run ID")]
    : rows;
  if (!addFilterParams(params, listRows, { allowEmpty: Boolean(safeTraceId) }))
    return null;

  setOriginParams(params, {
    origin,
    returnTo,
    location,
    deploymentName,
    deploymentId,
    revision,
  });
  return `${traceListPath(projectId)}?${params.toString()}`;
}

export function buildObserveErrorLink(options = {}) {
  return buildObserveLink({ ...options, includeErrors: true });
}

export function buildObserveRunLink(options = {}) {
  return buildObserveLink(options);
}

export function buildObserveTraceLink(options = {}) {
  return buildObserveLink(options);
}

/** Build the Tasks → Create URL prefilled with the same deployment scope. */
export function buildAddEvalsLink({
  projectId,
  deploymentId,
  revision,
  filters,
  returnTo,
  location = typeof window === "undefined" ? null : window.location,
} = {}) {
  if (!validProjectId(projectId)) return null;
  if (!validIdentifier(deploymentId, DEPLOYMENT_ID_RE)) return null;
  const rows = mergedObserveFilters({ deploymentId, revision, filters });
  const canonical = canonicalRows(rows);
  if (!canonical || canonical.length === 0) return null;
  const params = new URLSearchParams({
    project: projectId,
    filters: JSON.stringify(canonical),
  });
  const backPath = sameOriginPath(returnTo, location);
  if (backPath) params.set("returnTo", backPath);
  return `/dashboard/tasks/create?${params.toString()}`;
}

/** Build the Error Feed link for a summary's new issue. */
export function buildObserveIssueLink(issueId) {
  const safeIssueId = safePathSegment(issueId);
  return safeIssueId
    ? `/dashboard/error-feed/${encodeURIComponent(safeIssueId)}`
    : null;
}

/**
 * Parse the origin/back pair on an Observe URL. Returns null unless both
 * values are present and the return path resolves to the current origin.
 */
export function parseObserveOrigin({
  search = typeof window === "undefined" ? "" : window.location.search,
  location = typeof window === "undefined" ? null : window.location,
} = {}) {
  const params = new URLSearchParams(search || "");
  const origin = safeOriginLabel(params.get(OBSERVE_ORIGIN_PARAM));
  const returnTo = sameOriginPath(params.get(OBSERVE_RETURN_PARAM), location);
  if (!origin || !returnTo) return null;
  return { origin, returnTo };
}

export function observeLinkForTarget(target, options = {}) {
  if (!target || typeof target !== "object") return null;
  const deploymentId =
    options.deploymentId ?? target.deploymentId ?? target.deployment_id;
  const deploymentName =
    options.deploymentName ??
    target.deploymentName ??
    target.deployment_name ??
    target.projectName ??
    target.project_name;
  return buildObserveLink({
    ...options,
    projectId: options.projectId ?? target.projectId ?? target.project_id,
    deploymentId,
    deploymentName,
  });
}

export const observeLinkContract = Object.freeze({
  filterParameter: OBSERVE_LINK_FILTER_PARAM,
  dateParameter: OBSERVE_DATE_FILTER_PARAM,
  originParameter: OBSERVE_ORIGIN_PARAM,
  returnParameter: OBSERVE_RETURN_PARAM,
  attributes: Object.freeze([
    "omega.deployment.id",
    "omega.deployment.revision",
    "omega.run.id",
  ]),
  validateAttributeName: validAttributeName,
});
