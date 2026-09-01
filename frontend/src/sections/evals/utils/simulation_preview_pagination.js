import {
  formatRuntimeSeconds,
  INTERACTIVE_REQUEST_TIMEOUT_MS,
  SIMULATION_PREVIEW_PAGE_SIZE as CONFIGURED_SIMULATION_PREVIEW_PAGE_SIZE,
} from "src/config/runtime_limits";

export const SIMULATION_PREVIEW_PAGE_SIZE =
  CONFIGURED_SIMULATION_PREVIEW_PAGE_SIZE;
export const SIMULATION_PREVIEW_HTTP_TIMEOUT_MS =
  INTERACTIVE_REQUEST_TIMEOUT_MS;

export class SimulationPreviewPageError extends Error {
  constructor(message) {
    super(message);
    this.name = "SimulationPreviewPageError";
  }
}

const fail = (message) => {
  throw new SimulationPreviewPageError(message);
};

export function mergeSimulationPreviewPage(
  payload,
  {
    previousItems = [],
    expectedSnapshotTotal = null,
    expectedSnapshotAt = null,
  } = {},
) {
  if (!payload || payload.exact !== true || !Array.isArray(payload.results)) {
    fail("The server did not return an exact simulation preview page.");
  }
  const total = payload.snapshot_total;
  const loadedThrough = payload.loaded_through;
  if (
    !Number.isSafeInteger(total) ||
    total < 0 ||
    !Number.isSafeInteger(loadedThrough) ||
    loadedThrough < 0 ||
    loadedThrough > total
  ) {
    fail("The simulation preview page has invalid snapshot metadata.");
  }
  if (expectedSnapshotTotal !== null && total !== expectedSnapshotTotal) {
    fail("The simulation preview snapshot changed while loading more rows.");
  }
  if (
    typeof payload.snapshot_at !== "string" ||
    !payload.snapshot_at ||
    (expectedSnapshotAt !== null && payload.snapshot_at !== expectedSnapshotAt)
  ) {
    fail("The simulation preview timestamp changed while loading more rows.");
  }
  if (typeof payload.has_more !== "boolean") {
    fail("The simulation preview continuation state is missing.");
  }
  if (payload.complete !== !payload.has_more) {
    fail("The simulation preview completion state is inconsistent.");
  }
  if (payload.has_more) {
    if (typeof payload.next_cursor !== "string" || !payload.next_cursor) {
      fail("The simulation preview continuation cursor is missing.");
    }
    if (loadedThrough >= total) {
      fail("The simulation preview incorrectly reports more rows.");
    }
    if (payload.results.length === 0) {
      fail("The simulation preview continuation page is empty.");
    }
  } else if (payload.next_cursor !== null || loadedThrough !== total) {
    fail("The simulation preview terminal page is incomplete.");
  }

  const combined = [...previousItems, ...payload.results];
  const ids = combined.map((item) => item?.id);
  if (
    ids.some((id) => typeof id !== "string" || !id) ||
    new Set(ids).size !== combined.length
  ) {
    fail("The simulation preview returned a duplicate or invalid row.");
  }
  if (combined.length !== loadedThrough) {
    fail("The simulation preview skipped one or more rows.");
  }

  return {
    items: combined,
    nextCursor: payload.next_cursor,
    hasMore: payload.has_more,
    snapshotTotal: total,
    complete: payload.complete,
    snapshotAt: payload.snapshot_at,
  };
}

export function simulationPreviewRequestError(error) {
  if (error instanceof SimulationPreviewPageError) {
    return {
      message: `${error.message} Restart the list to continue safely.`,
      restartRequired: true,
    };
  }
  // The shared Axios interceptor intentionally flattens HTTP errors to their
  // response body. Accept both that application shape and a raw Axios error so
  // a 409/404 cannot accidentally fall through to a generic retry loop.
  const responseData = error?.response?.data;
  const semanticError = responseData || error || {};
  const code = semanticError?.code;
  const status = error?.response?.status ?? error?.statusCode;
  const restartRequired = semanticError?.restart_required === true;
  if (code === "simulation_preview_snapshot_changed" || status === 409) {
    return {
      message:
        "This simulation changed while rows were loading. Restart the list to continue safely.",
      restartRequired: true,
    };
  }
  if (code === "simulation_preview_cursor_invalid" || restartRequired) {
    return {
      message:
        "This preview continuation expired. Restart the list to continue.",
      restartRequired: true,
    };
  }
  if (code === "simulation_preview_not_found" || status === 404) {
    return {
      message:
        "This simulation preview source is no longer available. Select another simulation or execution.",
      restartRequired: false,
      terminal: true,
    };
  }
  const transportCode =
    error?.transportCode || (responseData ? null : error?.code);
  if (transportCode === "ECONNABORTED" || transportCode === "ETIMEDOUT") {
    return {
      message: `The preview read exceeded ${formatRuntimeSeconds(
        SIMULATION_PREVIEW_HTTP_TIMEOUT_MS,
      )} seconds. Retry when the data service is ready.`,
      restartRequired: false,
      terminal: false,
    };
  }
  return {
    message:
      semanticError?.detail ||
      error?.message ||
      "The simulation preview could not be loaded. Retry.",
    restartRequired: false,
    terminal: false,
  };
}
