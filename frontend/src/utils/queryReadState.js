import {
  AGGREGATION_POLL_BACKOFF_FACTOR,
  AGGREGATION_POLL_INITIAL_DELAY_MS,
  AGGREGATION_POLL_MAX_ATTEMPTS as CONFIGURED_AGGREGATION_POLL_MAX_ATTEMPTS,
  AGGREGATION_POLL_MAX_CONSECUTIVE_FAILURES as CONFIGURED_AGGREGATION_POLL_MAX_CONSECUTIVE_FAILURES,
  AGGREGATION_POLL_MAX_DELAY_MS,
  AGGREGATION_POLL_TIMEOUT_MS as CONFIGURED_AGGREGATION_POLL_TIMEOUT_MS,
  AGGREGATION_REQUEST_TIMEOUT_MS as CONFIGURED_AGGREGATION_REQUEST_TIMEOUT_MS,
} from "src/config/runtime_limits";
import { isGridApiLive } from "src/utils/gridApi";

export const QUERY_READ_RETRY_MESSAGE =
  "Some results could not be loaded. Please try again.";

export const QUERY_READ_SAMPLED_MESSAGE = "Showing the newest matching rows.";

export const FILTER_VALUE_SAMPLED_MESSAGE =
  "Showing configured or recent suggestions only. Enter an exact value.";

export const FILTER_VALUE_UNAVAILABLE_MESSAGE =
  "Suggestions are temporarily unavailable. Enter an exact value or retry.";

export const ATTRIBUTE_LOOKUP_SAMPLED_MESSAGE =
  "Recent matching attributes are shown. Enter an exact attribute name if needed.";

export const ATTRIBUTE_LOOKUP_UNAVAILABLE_MESSAGE =
  "Attribute suggestions are temporarily unavailable. Enter an exact attribute name.";

export const QUERY_FAILED_RETRY_MESSAGE =
  "We couldn't load this data. Please retry in a moment.";

export const AGGREGATION_PREPARING_MESSAGE = "Loading results…";

export const AGGREGATION_POLLING_PAUSED_MESSAGE =
  "Still preparing exact results. Refresh to check again.";

export const GRAPH_LOADING_MESSAGE = "Loading graph data…";

const payloadCandidates = (payload) => {
  const responseData =
    payload?.data && !Array.isArray(payload.data) ? payload.data : null;
  const candidates = [
    payload,
    responseData,
    payload?.result,
    responseData?.result,
    payload?.metadata,
    payload?.result?.metadata,
    responseData?.metadata,
    responseData?.result?.metadata,
  ]
    .flatMap((candidate) =>
      Array.isArray(candidate) ? candidate : [candidate],
    )
    .filter(Boolean);

  return candidates.flatMap((candidate) => [
    candidate,
    ...(candidate?.metadata ? [candidate.metadata] : []),
    ...(Array.isArray(candidate?.metrics) ? candidate.metrics : []),
  ]);
};

const hasBoundedReadMetadata = (candidate) =>
  Object.keys(candidate || {}).some(
    (key) =>
      key === "query_complete" ||
      key === "query_status" ||
      key === "query_sampled" ||
      key.startsWith("query_error_") ||
      key.startsWith("query_sample_") ||
      key.startsWith("query_sampling_"),
  );

const hasValidStatusPair = (candidate) => {
  if (!hasBoundedReadMetadata(candidate)) return true;

  const status = candidate?.query_status;
  const complete = candidate?.query_complete;
  if (typeof complete !== "boolean") return false;

  if (status === "complete") {
    return (
      complete === true &&
      !candidate?.query_error_code &&
      candidate?.query_sampled !== true
    );
  }
  if (status === "sampled") {
    return complete === false && candidate?.query_sampled !== false;
  }
  if (status === "pending") {
    return (
      complete === false &&
      candidate?.query_sampled === false &&
      (candidate?.query_refreshing === true ||
        candidate?.query_refresh_failed === true)
    );
  }
  if (status === "degraded") return complete === false;
  return false;
};

const isPendingAggregationCandidate = (candidate) =>
  candidate?.query_complete === false &&
  candidate?.query_status === "pending" &&
  candidate?.query_sampled === false &&
  (candidate?.query_refreshing === true ||
    candidate?.query_refresh_failed === true);

const hasCompleteSamplingCoverage = (candidate) => {
  const planned = candidate?.query_sampling_strata;
  const hasCompletedStrata =
    candidate?.query_complete === false &&
    Boolean(candidate?.query_sampling_strategy) &&
    Number.isInteger(planned) &&
    planned > 0 &&
    candidate?.query_sampling_strata_completed === planned;
  const hasBoundedDashboardSample =
    candidate?.query_complete === false &&
    candidate?.query_error_code === "sample_limit" &&
    candidate?.query_sampling_strategy ===
      "bounded_physical_rows_per_time_bucket" &&
    Number.isInteger(candidate?.query_sampling_interval_seconds) &&
    candidate.query_sampling_interval_seconds > 0 &&
    Number.isInteger(candidate?.query_sample_limit) &&
    candidate.query_sample_limit > 0 &&
    Number.isInteger(candidate?.query_sample_per_bucket) &&
    candidate.query_sample_per_bucket > 0 &&
    candidate.query_sample_per_bucket <= candidate.query_sample_limit;
  return hasCompletedStrata || hasBoundedDashboardSample;
};

/**
 * Interpret the bounded-read metadata returned by tracing APIs.
 *
 * Older deployments do not return this metadata. Treating an absent marker as
 * complete preserves their existing empty-state behaviour during rollout.
 */
export function getQueryReadState(payload, { isError = false } = {}) {
  if (isError) return "error";

  const candidates = payloadCandidates(payload);
  if (candidates.some((candidate) => candidate?.queryReadState === "error")) {
    return "error";
  }

  const invalidMetadata = candidates.some(
    (candidate) => !hasValidStatusPair(candidate),
  );

  const sampledCandidates = candidates.filter(
    (candidate) => candidate?.query_status === "sampled",
  );
  const sampled =
    sampledCandidates.length > 0 &&
    sampledCandidates.every(hasCompleteSamplingCoverage);
  const invalidSample = sampledCandidates.some(
    (candidate) => !hasCompleteSamplingCoverage(candidate),
  );
  const degraded = candidates.some(
    (candidate) =>
      candidate?.query_status === "degraded" ||
      (candidate?.query_complete === false &&
        candidate?.query_status !== "sampled") ||
      candidate?.queryReadState === "degraded",
  );

  if (invalidMetadata || degraded || invalidSample) return "degraded";
  return sampled ? "sampled" : "complete";
}

/**
 * Aggregation endpoints publish an explicit exactness contract. Unlike list
 * reads, they must fail closed when that contract is missing: an unmarked
 * payload is never safe to chart as an exact aggregate.
 */
export function getExactAggregationReadState(
  payload,
  { isError = false } = {},
) {
  if (isError) return "error";

  const responseData =
    payload?.data && !Array.isArray(payload.data) ? payload.data : null;
  const authoritativeValue =
    responseData?.result ?? payload?.result ?? responseData ?? payload;
  let authoritativeCandidates = Array.isArray(authoritativeValue)
    ? authoritativeValue.filter(Boolean)
    : [authoritativeValue].filter(Boolean);

  // Some array-shaped aggregation endpoints attach the contract to the
  // response wrapper when the result itself is empty.
  if (authoritativeCandidates.length === 0) {
    authoritativeCandidates = [responseData, payload].filter(
      hasBoundedReadMetadata,
    );
  }
  if (
    authoritativeCandidates.length === 0 ||
    authoritativeCandidates.some(
      (candidate) =>
        !hasBoundedReadMetadata(candidate) || !hasValidStatusPair(candidate),
    )
  ) {
    return "degraded";
  }

  if (authoritativeCandidates.some(isPendingAggregationCandidate)) {
    return authoritativeCandidates.every(
      (candidate) =>
        isPendingAggregationCandidate(candidate) ||
        (candidate?.query_complete === true &&
          candidate?.query_status === "complete" &&
          candidate?.query_sampled === false &&
          !candidate?.query_error_code),
    )
      ? "pending"
      : "degraded";
  }

  const authoritativeSampled = authoritativeCandidates.filter(
    (candidate) => candidate?.query_status === "sampled",
  );
  if (authoritativeSampled.length > 0) {
    return authoritativeSampled.length === authoritativeCandidates.length &&
      authoritativeSampled.every(hasCompleteSamplingCoverage)
      ? "sampled"
      : "degraded";
  }
  if (
    authoritativeCandidates.some(
      (candidate) =>
        candidate?.query_complete !== true ||
        candidate?.query_status !== "complete" ||
        candidate?.query_sampled !== false ||
        candidate?.query_error_code,
    )
  ) {
    return "degraded";
  }

  const boundedCandidates = payloadCandidates(payload).filter(
    hasBoundedReadMetadata,
  );
  if (boundedCandidates.some((candidate) => !hasValidStatusPair(candidate))) {
    return "degraded";
  }
  if (
    boundedCandidates.some(
      (candidate) =>
        candidate?.query_status === "degraded" ||
        (candidate?.query_complete === false &&
          candidate?.query_status !== "sampled" &&
          candidate?.query_status !== "pending"),
    )
  ) {
    return "degraded";
  }

  if (boundedCandidates.some(isPendingAggregationCandidate)) return "pending";

  const sampledCandidates = boundedCandidates.filter(
    (candidate) => candidate?.query_status === "sampled",
  );
  if (sampledCandidates.length > 0) {
    return sampledCandidates.every(hasCompleteSamplingCoverage)
      ? "sampled"
      : "degraded";
  }

  return "complete";
}

/**
 * Queued exact reads keep rendering their prior exact snapshot, when one
 * exists, while the client polls the ordinary (non-refresh) request. A failed
 * queued job is terminal until the user explicitly asks to refresh again.
 */
export function getAggregationRefreshState(payload) {
  const candidates = payloadCandidates(payload);
  return {
    isRefreshing: candidates.some(
      (candidate) => candidate?.query_refreshing === true,
    ),
    refreshFailed: candidates.some(
      (candidate) => candidate?.query_refresh_failed === true,
    ),
  };
}

export const AGGREGATION_POLL_MAX_ATTEMPTS =
  CONFIGURED_AGGREGATION_POLL_MAX_ATTEMPTS;
// Polling has its own environment-backed wall. Individual requests remain
// bounded by the shorter transport timeout, while a healthy server-owned job
// can outlive one interactive HTTP window.
export const AGGREGATION_POLL_TIMEOUT_MS =
  CONFIGURED_AGGREGATION_POLL_TIMEOUT_MS;
export const AGGREGATION_POLL_MAX_CONSECUTIVE_FAILURES =
  CONFIGURED_AGGREGATION_POLL_MAX_CONSECUTIVE_FAILURES;
// The transport ceiling is independently configurable so deployments can
// leave measured response headroom above their server-side analytics wall.
export const AGGREGATION_REQUEST_TIMEOUT_MS =
  CONFIGURED_AGGREGATION_REQUEST_TIMEOUT_MS;

const aggregationRequestError = (code) => {
  const error = new Error("Exact aggregation request did not complete");
  error.code = code;
  return error;
};

/**
 * Bound one transport attempt independently of the server-side polling
 * contract. Promise callbacks that arrive after the deadline (or after the
 * caller moved to a new request generation) are deliberately discarded.
 */
export function awaitAggregationRequestWithDeadline(
  requestOrFactory,
  {
    timeoutMs,
    signal: upstreamSignal,
    isCurrent = () => true,
    onTimeout = () => {},
  } = {},
) {
  const deadlineMs = Math.max(Number(timeoutMs) || 0, 0);

  return new Promise((resolve, reject) => {
    let settled = false;
    let timer = null;
    const controller = new AbortController();
    const abortFromUpstream = () => {
      const reason =
        upstreamSignal?.reason ||
        new DOMException(
          "Exact aggregation request was cancelled",
          "AbortError",
        );
      controller.abort(reason);
      finish(reject, reason);
    };
    const cleanup = () => {
      if (timer !== null) globalThis.clearTimeout(timer);
      upstreamSignal?.removeEventListener("abort", abortFromUpstream);
    };
    const finish = (callback, value) => {
      if (settled) return;
      settled = true;
      cleanup();
      callback(value);
    };

    if (upstreamSignal?.aborted) {
      abortFromUpstream();
      return;
    }
    upstreamSignal?.addEventListener("abort", abortFromUpstream, {
      once: true,
    });

    timer = globalThis.setTimeout(() => {
      if (settled) return;
      if (isCurrent()) onTimeout();
      const error = aggregationRequestError("aggregation_request_timeout");
      finish(reject, error);
      controller.abort(error);
    }, deadlineMs);

    let request;
    try {
      request =
        typeof requestOrFactory === "function"
          ? requestOrFactory(controller.signal)
          : requestOrFactory;
    } catch (error) {
      finish(reject, error);
      return;
    }

    Promise.resolve(request).then(
      (response) => {
        if (settled) return;
        if (!isCurrent()) {
          finish(
            reject,
            aggregationRequestError("aggregation_request_superseded"),
          );
          return;
        }
        finish(resolve, response);
      },
      (error) => {
        if (!isCurrent()) {
          finish(
            reject,
            aggregationRequestError("aggregation_request_superseded"),
          );
          return;
        }
        finish(reject, error);
      },
    );
  });
}

/** Environment-backed exponential polling cadence with a bounded cap. */
export function getAggregationPollDelay(attempt = 0) {
  const boundedAttempt = Math.min(
    Math.max(Number.isInteger(attempt) ? attempt : 0, 0),
    AGGREGATION_POLL_MAX_ATTEMPTS - 1,
  );
  return Math.min(
    AGGREGATION_POLL_INITIAL_DELAY_MS *
      AGGREGATION_POLL_BACKOFF_FACTOR ** boundedAttempt,
    AGGREGATION_POLL_MAX_DELAY_MS,
  );
}

/**
 * Exact snapshot jobs are allowed to queue beyond one HTTP request deadline.
 * The elapsed polling limit covers slow jobs while the attempt limit also
 * bounds unexpectedly fast loops.
 */
export function isAggregationPollBudgetExhausted({
  attempt = 0,
  startedAt,
  now = Date.now(),
} = {}) {
  const attempts = Number.isInteger(attempt) ? Math.max(attempt, 0) : 0;
  const elapsed = Number.isFinite(startedAt)
    ? Math.max(Number(now) - startedAt, 0)
    : 0;
  return (
    attempts >= AGGREGATION_POLL_MAX_ATTEMPTS ||
    elapsed >= AGGREGATION_POLL_TIMEOUT_MS
  );
}

/**
 * Own the finite browser-side lifecycle of one exact-aggregation job.
 *
 * Every aggregation surface used to keep its own attempt refs and, in
 * practice, most of them forgot to apply the elapsed/attempt budget.  This
 * controller keeps those rules in one place.  It is intentionally framework
 * agnostic so React Query hooks, mutation-driven dashboard widgets, and unit
 * tests all use identical semantics.
 */
export function createAggregationPollController({
  now = () => Date.now(),
} = {}) {
  let attempt = 0;
  let startedAt = null;
  let consecutiveFailures = 0;
  let active = false;
  let exhausted = false;
  let terminationReason = null;

  const reset = () => {
    attempt = 0;
    startedAt = null;
    consecutiveFailures = 0;
    active = false;
    exhausted = false;
    terminationReason = null;
  };

  const start = () => {
    // Exhaustion is terminal for this observation window.  Only an explicit
    // reset (the user-facing Refresh/Retry action) may start a fresh budget.
    if (exhausted) return false;
    if (!active) {
      attempt = 0;
      startedAt = now();
      consecutiveFailures = 0;
    }
    active = true;
    return true;
  };

  const stop = () => {
    attempt = 0;
    startedAt = null;
    consecutiveFailures = 0;
    active = false;
    terminationReason = null;
  };

  const exhaust = (reason = "terminated") => {
    active = false;
    exhausted = true;
    terminationReason = reason;
    return false;
  };

  const remainingMs = (capMs = AGGREGATION_POLL_TIMEOUT_MS) => {
    if (!active || startedAt === null || exhausted) return 0;
    const elapsed = Math.max(now() - startedAt, 0);
    const actionRemaining = Math.max(AGGREGATION_POLL_TIMEOUT_MS - elapsed, 0);
    const numericCap = Number(capMs);
    const finiteCap = Number.isFinite(numericCap)
      ? Math.max(numericCap, 0)
      : AGGREGATION_POLL_TIMEOUT_MS;
    return Math.floor(Math.min(actionRemaining, finiteCap));
  };

  const nextDelay = () => {
    if (!active) return false;
    const currentTime = now();
    if (
      isAggregationPollBudgetExhausted({
        attempt,
        startedAt,
        now: currentTime,
      })
    ) {
      return exhaust("poll_budget");
    }

    const delay = getAggregationPollDelay(attempt);
    // Do not schedule a request whose timer itself crosses the elapsed-time
    // ceiling.  The server-owned job remains intact and an explicit refresh
    // can start a fresh bounded observation window.
    if (currentTime + delay - startedAt >= AGGREGATION_POLL_TIMEOUT_MS) {
      return exhaust("poll_budget");
    }
    return delay;
  };

  // React Query may evaluate `refetchInterval` multiple times while settling
  // one response. Count the actual poll request, not interval calculations.
  const recordAttempt = () => {
    if (!active || exhausted) return false;
    attempt += 1;
    return true;
  };

  const recordSuccess = () => {
    consecutiveFailures = 0;
  };

  const recordFailure = () => {
    if (!active) return false;
    consecutiveFailures += 1;
    if (consecutiveFailures >= AGGREGATION_POLL_MAX_CONSECUTIVE_FAILURES) {
      return exhaust("transport_failures");
    }
    return true;
  };

  return {
    reset,
    start,
    stop,
    terminate: exhaust,
    nextDelay,
    remainingMs,
    recordAttempt,
    recordSuccess,
    recordFailure,
    isActive: () => active,
    isExhausted: () => exhausted,
    getTerminationReason: () => terminationReason,
    snapshot: () => ({
      attempt,
      startedAt,
      consecutiveFailures,
      active,
      exhausted,
      terminationReason,
    }),
  };
}

export function getQueryReadMessage(state) {
  if (state === "sampled") return QUERY_READ_SAMPLED_MESSAGE;
  if (state === "degraded") return QUERY_READ_RETRY_MESSAGE;
  if (state === "error") return QUERY_FAILED_RETRY_MESSAGE;
  return null;
}

/**
 * Exact aggregation responses may be served from a persisted snapshot. The
 * backend exposes the time that exact computation completed as an ISO-8601
 * `query_completed_at` field. Missing or malformed timestamps stay absent;
 * completion labels must never substitute the browser's clock.
 */
export function getQueryCompletedAt(payload) {
  const raw = payloadCandidates(payload)
    .map((candidate) => candidate?.query_completed_at)
    .find(Boolean);
  const parsed = raw ? new Date(raw) : null;
  if (parsed && !Number.isNaN(parsed.getTime())) return parsed;
  return null;
}

/**
 * Filter-value pages have a deliberately smaller sampling contract than graph
 * responses. A valid sampled page carries the status triplet plus its values,
 * but no graph-specific stratum/per-bucket metadata. Keep this endpoint-aware
 * interpretation separate so graph rendering continues to fail closed when
 * coverage metadata is missing.
 */
export function getFilterValueReadState(payload, { isError = false } = {}) {
  if (isError) return "error";

  const result = payload?.result ?? payload;
  if (
    Array.isArray(result?.values) &&
    result?.query_complete === false &&
    result?.query_status === "sampled" &&
    result?.query_error_code === "sample_limit"
  ) {
    return "sampled";
  }

  return getQueryReadState(payload);
}

export function getFilterValueReadMessage(state) {
  if (state === "sampled") return FILTER_VALUE_SAMPLED_MESSAGE;
  if (state === "degraded" || state === "error") {
    return FILTER_VALUE_UNAVAILABLE_MESSAGE;
  }
  return null;
}

export function getAttributeLookupMessage(state) {
  if (state === "sampled") return ATTRIBUTE_LOOKUP_SAMPLED_MESSAGE;
  if (state === "degraded" || state === "error") {
    return ATTRIBUTE_LOOKUP_UNAVAILABLE_MESSAGE;
  }
  return null;
}

/**
 * Return graph points only when the bounded read is exact.
 *
 * The backend contract keeps incomplete samples out of `data`, but this is a
 * client-side safety boundary as well: a stale or regressed backend response
 * must not be charted as exact traffic/count/cost/token/latency data merely
 * because it carries points alongside `query_complete: false`.
 */
export function getExactGraphData(payload) {
  if (getExactAggregationReadState(payload) !== "complete") return [];

  const data = payload?.data;
  return Array.isArray(data) ? data : [];
}

/**
 * Return points that are safe to chart as exact data or as an explicitly
 * labelled bounded sample. Unlabelled incomplete and degraded responses remain
 * non-renderable.
 */
export function getRenderableGraphData(payload) {
  const state = getQueryReadState(payload);
  if (state !== "complete" && state !== "sampled") return [];

  const data = payload?.data ?? payload?.result?.data;
  return Array.isArray(data) ? data : [];
}

/**
 * Preserve AG Grid's server-side failure semantics while displaying the
 * sanitized read-error overlay. A failed page must never be reported as an
 * empty successful dataset because that can truncate pagination state.
 */
export function failServerSideGridRead(params) {
  if (params?.api && !isGridApiLive(params.api)) return false;
  params?.fail?.();
  params?.api?.showNoRowsOverlay?.();
  return true;
}
