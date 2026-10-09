import axios, { endpoints } from "src/utils/axios";

// A run allows at most this many calls (scenarios × trials). It mirrors the
// backend's HARNESS_MAX_EXECUTIONS_PER_RUN default: a deployment that changes
// it still gets the backend's own refusal, so this only lets the re-run menu
// say so before the request goes out.
export const MAX_CALLS_PER_RUN = 200;

// The largest page the calls endpoint serves.
const PAGE_SIZE = 500;

/**
 * The scenarios behind a set of calls, once each. A re-run works per scenario
 * and repeats it `trials` times, so two trial rows of one scenario are one key.
 */
export const uniqueScenarioKeys = (rows) => [
  ...new Set(rows.map((row) => row?.sourceScenarioKey).filter(Boolean)),
];

/**
 * Every scenario key behind the calls matching `filters`, less the calls in
 * `excludedIds`. "Select all matching" never loads those rows, so they are
 * read here, flat (no grouping) and a page at a time.
 */
export async function listMatchingScenarioKeys(
  executionId,
  filters = {},
  excludedIds = [],
) {
  const excluded = new Set(excludedIds);
  const keys = new Set();
  let page = 1;
  let totalPages = 1;
  do {
    // eslint-disable-next-line no-await-in-loop
    const { data } = await axios.get(
      endpoints.runResultsV3.calls(executionId),
      {
        params: {
          page,
          page_size: PAGE_SIZE,
          search: "",
          filters: JSON.stringify(filters),
        },
      },
    );
    (data?.results ?? []).forEach((row) => {
      if (!excluded.has(row.id) && row.source_scenario_key)
        keys.add(row.source_scenario_key);
    });
    totalPages = data?.total_pages || 1;
    page += 1;
  } while (page <= totalPages);
  return [...keys];
}
