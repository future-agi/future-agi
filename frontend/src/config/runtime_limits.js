const buildConfig = import.meta.env || {};
const browserConfig =
  (typeof window !== "undefined" && window.__FUTURE_AGI_CONFIG__) || {};

/** Resolve a bounded integer from runtime config, build config, then default. */
export function readBoundedRuntimeInteger(
  name,
  defaultValue,
  { minimum, maximum, runtimeConfig = browserConfig, envConfig = buildConfig },
) {
  const parse = (rawValue) => {
    if (rawValue === undefined || rawValue === null || rawValue === "") {
      return null;
    }
    const value = Number(rawValue);
    return Number.isSafeInteger(value) && value >= minimum && value <= maximum
      ? value
      : null;
  };
  const fallback = parse(defaultValue);
  if (fallback === null) {
    throw new RangeError(`${name} has an invalid default`);
  }

  return parse(runtimeConfig[name]) ?? parse(envConfig[name]) ?? fallback;
}

/** Render an environment-backed millisecond wall without stale copy. */
export function formatRuntimeSeconds(milliseconds) {
  if (!Number.isSafeInteger(milliseconds) || milliseconds < 1) {
    throw new RangeError("milliseconds must be a positive safe integer");
  }
  return String(milliseconds / 1_000);
}

export const INTERACTIVE_REQUEST_TIMEOUT_MS = readBoundedRuntimeInteger(
  "VITE_INTERACTIVE_REQUEST_TIMEOUT_MS",
  30_000,
  { minimum: 1_000, maximum: 60_000 },
);

// Filter-mode queue adds commit one exact server-bounded batch at a time.
// Bound both the number of sequential continuations and their aggregate browser
// wall while allowing measured runtime tuning without rebuilding the frontend.
export const MAX_ADD_QUEUE_CONTINUATION_PAGES = readBoundedRuntimeInteger(
  "VITE_ADD_QUEUE_CONTINUATION_MAX_PAGES",
  100,
  { minimum: 1, maximum: 1_000 },
);

export const MAX_ADD_QUEUE_CONTINUATION_WALL_MS = readBoundedRuntimeInteger(
  "VITE_ADD_QUEUE_CONTINUATION_WALL_MS",
  10 * 60 * 1_000,
  { minimum: INTERACTIVE_REQUEST_TIMEOUT_MS, maximum: 60 * 60 * 1_000 },
);

export const ANALYTICS_REQUEST_TIMEOUT_MS = readBoundedRuntimeInteger(
  "VITE_ANALYTICS_REQUEST_TIMEOUT_MS",
  30_000,
  { minimum: 1_000, maximum: 60_000 },
);

export const AGGREGATION_REQUEST_TIMEOUT_MS = readBoundedRuntimeInteger(
  "VITE_AGGREGATION_REQUEST_TIMEOUT_MS",
  30_000,
  { minimum: 1_000, maximum: 60_000 },
);

export function readAggregationPollTimeout({
  requestTimeoutMs = AGGREGATION_REQUEST_TIMEOUT_MS,
  runtimeConfig = browserConfig,
  envConfig = buildConfig,
} = {}) {
  return readBoundedRuntimeInteger(
    "VITE_AGGREGATION_POLL_TIMEOUT_MS",
    220_000,
    {
      minimum: requestTimeoutMs,
      maximum: 600_000,
      runtimeConfig,
      envConfig,
    },
  );
}

// Exact reads are server-owned jobs. Keep each HTTP attempt interactive while
// allowing the browser to observe a healthy 180-second worker after the
// preceding 30-second foreground attempt and one final polling interval.
export const AGGREGATION_POLL_TIMEOUT_MS = readAggregationPollTimeout();

export const FILTER_VALUE_REQUEST_TIMEOUT_MS = readBoundedRuntimeInteger(
  "VITE_FILTER_VALUE_REQUEST_TIMEOUT_MS",
  30_000,
  { minimum: 100, maximum: 60_000 },
);

export const AGGREGATION_POLL_INITIAL_DELAY_MS = readBoundedRuntimeInteger(
  "VITE_AGGREGATION_POLL_INITIAL_DELAY_MS",
  1_000,
  { minimum: 100, maximum: 60_000 },
);

export const AGGREGATION_POLL_MAX_DELAY_MS = readBoundedRuntimeInteger(
  "VITE_AGGREGATION_POLL_MAX_DELAY_MS",
  Math.max(8_000, AGGREGATION_POLL_INITIAL_DELAY_MS),
  { minimum: AGGREGATION_POLL_INITIAL_DELAY_MS, maximum: 60_000 },
);

export const AGGREGATION_POLL_BACKOFF_FACTOR = readBoundedRuntimeInteger(
  "VITE_AGGREGATION_POLL_BACKOFF_FACTOR",
  2,
  { minimum: 1, maximum: 10 },
);

export const AGGREGATION_POLL_MAX_ATTEMPTS = readBoundedRuntimeInteger(
  "VITE_AGGREGATION_POLL_MAX_ATTEMPTS",
  32,
  { minimum: 1, maximum: 100 },
);

export const AGGREGATION_POLL_MAX_CONSECUTIVE_FAILURES =
  readBoundedRuntimeInteger(
    "VITE_AGGREGATION_POLL_MAX_CONSECUTIVE_FAILURES",
    3,
    { minimum: 1, maximum: 20 },
  );

export const CURSOR_MAX_EMPTY_CONTINUATIONS = readBoundedRuntimeInteger(
  "VITE_CURSOR_MAX_EMPTY_CONTINUATIONS",
  12,
  { minimum: 1, maximum: 128 },
);

// Cursor pages are sequential: page N returns the opaque cursor required by
// page N+1. Keep one datasource request in flight and retain only a small
// window of rendered blocks so long scroll sessions cannot grow the tab heap
// without bound. Both values remain runtime-overridable for measured tuning.
export const OBSERVE_GRID_MAX_CONCURRENT_REQUESTS = readBoundedRuntimeInteger(
  "VITE_OBSERVE_GRID_MAX_CONCURRENT_REQUESTS",
  1,
  { minimum: 1, maximum: 4 },
);

export const OBSERVE_GRID_MAX_BLOCKS_IN_CACHE = readBoundedRuntimeInteger(
  "VITE_OBSERVE_GRID_MAX_BLOCKS_IN_CACHE",
  5,
  { minimum: 1, maximum: 100 },
);

// Cursor tokens are much smaller than row payloads, but a tab that traverses
// an unbounded result set must not retain one token and transition edge per
// page forever. Keep a generous, runtime-tunable LRU of random-access
// checkpoints; an older evicted page fails closed and can be restarted from
// page one without allowing stale cursor branches to mix.
export const OBSERVE_CURSOR_MAX_CHECKPOINTS = readBoundedRuntimeInteger(
  "VITE_OBSERVE_CURSOR_MAX_CHECKPOINTS",
  4_096,
  { minimum: OBSERVE_GRID_MAX_BLOCKS_IN_CACHE + 1, maximum: 16_384 },
);

// A cursor page transition normally settles as soon as AG Grid paints a row
// from the target page. This wall is only a fail-safe for a cached grid block
// that never emits a usable render; an active HTTP read still owns its own
// request timeout and is never hidden by this fallback.
export const OBSERVE_PAGE_TRANSITION_MAX_WAIT_MS = readBoundedRuntimeInteger(
  "VITE_OBSERVE_PAGE_TRANSITION_MAX_WAIT_MS",
  60_000,
  { minimum: 1_000, maximum: 120_000 },
);

export const OBSERVE_LIST_CELL_PREVIEW_MAX_CHARS = readBoundedRuntimeInteger(
  "VITE_OBSERVE_LIST_CELL_PREVIEW_MAX_CHARS",
  16 * 1024,
  { minimum: 1_024, maximum: 1024 * 1024 },
);

export const CHUNK_IMPORT_TIMEOUT_MS = readBoundedRuntimeInteger(
  "VITE_CHUNK_IMPORT_TIMEOUT_MS",
  10_000,
  { minimum: 1_000, maximum: 120_000 },
);

export const CHUNK_IMPORT_MAX_RETRIES = readBoundedRuntimeInteger(
  "VITE_CHUNK_IMPORT_MAX_RETRIES",
  3,
  { minimum: 0, maximum: 10 },
);

export const CHUNK_IMPORT_RETRY_BASE_DELAY_MS = readBoundedRuntimeInteger(
  "VITE_CHUNK_IMPORT_RETRY_BASE_DELAY_MS",
  1_000,
  { minimum: 100, maximum: 60_000 },
);

export const INTERACTIVE_MAX_PAGE_SIZE = readBoundedRuntimeInteger(
  "VITE_INTERACTIVE_MAX_PAGE_SIZE",
  100,
  { minimum: 1, maximum: 500 },
);

// Observe trace/span lists use explicit cursor-backed page navigation. Keep
// the default small enough for an interactive render while allowing operators
// to tune it up to the shared interactive ceiling without rebuilding the app.
export const OBSERVE_LIST_DEFAULT_PAGE_SIZE = readBoundedRuntimeInteger(
  "VITE_OBSERVE_LIST_DEFAULT_PAGE_SIZE",
  Math.min(25, INTERACTIVE_MAX_PAGE_SIZE),
  { minimum: 1, maximum: INTERACTIVE_MAX_PAGE_SIZE },
);

export const OBSERVE_LIST_PAGE_SIZE_OPTIONS = Array.from(
  new Set(
    [10, 25, 50, OBSERVE_LIST_DEFAULT_PAGE_SIZE].filter(
      (size) => size <= INTERACTIVE_MAX_PAGE_SIZE,
    ),
  ),
).sort((left, right) => left - right);

export const PROPERTY_CATALOG_PAGE_SIZE = readBoundedRuntimeInteger(
  "VITE_PROPERTY_CATALOG_PAGE_SIZE",
  Math.min(50, INTERACTIVE_MAX_PAGE_SIZE),
  { minimum: 1, maximum: INTERACTIVE_MAX_PAGE_SIZE },
);

export const INTERACTIVE_TABLE_PAGE_SIZE = readBoundedRuntimeInteger(
  "VITE_INTERACTIVE_TABLE_PAGE_SIZE",
  Math.min(10, INTERACTIVE_MAX_PAGE_SIZE),
  { minimum: 1, maximum: INTERACTIVE_MAX_PAGE_SIZE },
);

export const PROPERTY_CATALOG_SEARCH_PAGE_SIZE = readBoundedRuntimeInteger(
  "VITE_PROPERTY_CATALOG_SEARCH_PAGE_SIZE",
  Math.min(20, PROPERTY_CATALOG_PAGE_SIZE),
  { minimum: 1, maximum: PROPERTY_CATALOG_PAGE_SIZE },
);

export const PROPERTY_CATALOG_COMPACT_PAGE_SIZE = readBoundedRuntimeInteger(
  "VITE_PROPERTY_CATALOG_COMPACT_PAGE_SIZE",
  Math.min(25, PROPERTY_CATALOG_PAGE_SIZE),
  { minimum: 1, maximum: PROPERTY_CATALOG_PAGE_SIZE },
);

export const PROPERTY_CATALOG_LEGACY_PAGE_SIZE = readBoundedRuntimeInteger(
  "VITE_PROPERTY_CATALOG_LEGACY_PAGE_SIZE",
  200,
  { minimum: 1, maximum: 200 },
);

export const PROPERTY_CATALOG_SEARCH_DEBOUNCE_MS = readBoundedRuntimeInteger(
  "VITE_PROPERTY_CATALOG_SEARCH_DEBOUNCE_MS",
  300,
  { minimum: 0, maximum: 5_000 },
);

// Must remain aligned with PROPERTY_CATALOG_MAX_SEARCH_BYTES on the API.
// Exact filter values have a separate, larger limit; this only bounds the
// server-side vocabulary-search prefix sent while a user is typing.
export const PROPERTY_CATALOG_SEARCH_MAX_UTF8_BYTES = readBoundedRuntimeInteger(
  "VITE_PROPERTY_CATALOG_SEARCH_MAX_UTF8_BYTES",
  512,
  { minimum: 1, maximum: 4_096 },
);

export const PROPERTY_PICKER_RENDER_BATCH_SIZE = readBoundedRuntimeInteger(
  "VITE_PROPERTY_PICKER_RENDER_BATCH_SIZE",
  500,
  { minimum: 1, maximum: 5_000 },
);

export const PROPERTY_PICKER_PREFETCH_MARGIN_PX = readBoundedRuntimeInteger(
  "VITE_PROPERTY_PICKER_PREFETCH_MARGIN_PX",
  48,
  { minimum: 0, maximum: 1_000 },
);

export const ATTRIBUTE_INVENTORY_SEARCH_DEBOUNCE_MS = readBoundedRuntimeInteger(
  "VITE_ATTRIBUTE_INVENTORY_SEARCH_DEBOUNCE_MS",
  350,
  { minimum: 0, maximum: 5_000 },
);

export const FILTER_VALUE_SEARCH_DEBOUNCE_MS = readBoundedRuntimeInteger(
  "VITE_FILTER_VALUE_SEARCH_DEBOUNCE_MS",
  500,
  { minimum: 0, maximum: 5_000 },
);

export const FILTER_AUTO_APPLY_DEBOUNCE_MS = readBoundedRuntimeInteger(
  "VITE_FILTER_AUTO_APPLY_DEBOUNCE_MS",
  350,
  { minimum: 0, maximum: 5_000 },
);

export const PROPERTY_CATALOG_STALE_TIME_MS = readBoundedRuntimeInteger(
  "VITE_PROPERTY_CATALOG_STALE_TIME_MS",
  60_000,
  { minimum: 0, maximum: 3_600_000 },
);

export const PROPERTY_CATALOG_CACHE_TIME_MS = readBoundedRuntimeInteger(
  "VITE_PROPERTY_CATALOG_CACHE_TIME_MS",
  Math.max(300_000, PROPERTY_CATALOG_STALE_TIME_MS),
  { minimum: PROPERTY_CATALOG_STALE_TIME_MS, maximum: 86_400_000 },
);

export const PROPERTY_CATALOG_LEGACY_STALE_TIME_MS = readBoundedRuntimeInteger(
  "VITE_PROPERTY_CATALOG_LEGACY_STALE_TIME_MS",
  300_000,
  { minimum: 0, maximum: 3_600_000 },
);

export const PROPERTY_CATALOG_LEGACY_CACHE_TIME_MS = readBoundedRuntimeInteger(
  "VITE_PROPERTY_CATALOG_LEGACY_CACHE_TIME_MS",
  Math.max(900_000, PROPERTY_CATALOG_LEGACY_STALE_TIME_MS),
  { minimum: PROPERTY_CATALOG_LEGACY_STALE_TIME_MS, maximum: 86_400_000 },
);

export const FILTER_VALUE_PAGE_SIZE = readBoundedRuntimeInteger(
  "VITE_FILTER_VALUE_PAGE_SIZE",
  Math.min(50, INTERACTIVE_MAX_PAGE_SIZE),
  { minimum: 1, maximum: INTERACTIVE_MAX_PAGE_SIZE },
);

export const FILTER_VALUE_MIN_VISIBLE_RESULTS = readBoundedRuntimeInteger(
  "VITE_FILTER_VALUE_MIN_VISIBLE_RESULTS",
  1,
  { minimum: 1, maximum: FILTER_VALUE_PAGE_SIZE },
);

export const FILTER_VALUE_STALE_TIME_MS = readBoundedRuntimeInteger(
  "VITE_FILTER_VALUE_STALE_TIME_MS",
  300_000,
  { minimum: 0, maximum: 3_600_000 },
);

export const FILTER_VALUE_CACHE_TIME_MS = readBoundedRuntimeInteger(
  "VITE_FILTER_VALUE_CACHE_TIME_MS",
  Math.max(900_000, FILTER_VALUE_STALE_TIME_MS),
  { minimum: FILTER_VALUE_STALE_TIME_MS, maximum: 86_400_000 },
);

export const AUTOMATION_RULE_LIST_PAGE_SIZE = readBoundedRuntimeInteger(
  "VITE_AUTOMATION_RULE_LIST_PAGE_SIZE",
  Math.min(25, INTERACTIVE_MAX_PAGE_SIZE),
  { minimum: 1, maximum: INTERACTIVE_MAX_PAGE_SIZE },
);

export const OBSERVE_PROJECT_PAGE_SIZE = readBoundedRuntimeInteger(
  "VITE_OBSERVE_PROJECT_PAGE_SIZE",
  Math.min(100, INTERACTIVE_MAX_PAGE_SIZE),
  { minimum: 1, maximum: INTERACTIVE_MAX_PAGE_SIZE },
);

export const EVAL_METRIC_MAX_WINDOW_DAYS = readBoundedRuntimeInteger(
  "VITE_EVAL_METRIC_MAX_WINDOW_DAYS",
  365,
  { minimum: 1, maximum: 3_660 },
);

export const SIMULATION_PREVIEW_MAX_PAGE_SIZE = readBoundedRuntimeInteger(
  "VITE_SIMULATION_PREVIEW_MAX_PAGE_SIZE",
  50,
  { minimum: 1, maximum: 500 },
);

export const SIMULATION_PREVIEW_PAGE_SIZE = readBoundedRuntimeInteger(
  "VITE_SIMULATION_PREVIEW_PAGE_SIZE",
  Math.min(50, SIMULATION_PREVIEW_MAX_PAGE_SIZE),
  { minimum: 1, maximum: SIMULATION_PREVIEW_MAX_PAGE_SIZE },
);

export const GROUND_TRUTH_DATASET_PAGE_SIZE = readBoundedRuntimeInteger(
  "VITE_GROUND_TRUTH_DATASET_PAGE_SIZE",
  100,
  { minimum: 1, maximum: 500 },
);

export const DATASET_ROW_ADJACENCY_MAX_ROWS = readBoundedRuntimeInteger(
  "VITE_DATASET_ROW_ADJACENCY_MAX_ROWS",
  50,
  { minimum: 1, maximum: 500 },
);
