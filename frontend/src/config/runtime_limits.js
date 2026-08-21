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
  9_000,
  { minimum: 1_000, maximum: 60_000 },
);

export const ANALYTICS_REQUEST_TIMEOUT_MS = readBoundedRuntimeInteger(
  "VITE_ANALYTICS_REQUEST_TIMEOUT_MS",
  9_500,
  { minimum: 1_000, maximum: 60_000 },
);

export const AGGREGATION_REQUEST_TIMEOUT_MS = readBoundedRuntimeInteger(
  "VITE_AGGREGATION_REQUEST_TIMEOUT_MS",
  9_800,
  { minimum: 1_000, maximum: 60_000 },
);

export const FILTER_VALUE_REQUEST_TIMEOUT_MS = readBoundedRuntimeInteger(
  "VITE_FILTER_VALUE_REQUEST_TIMEOUT_MS",
  4_800,
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
  12,
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
