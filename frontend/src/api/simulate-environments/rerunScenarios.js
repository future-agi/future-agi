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
 * Every call matching `filters`, less the ones in `excludedIds`, and the
 * scenarios behind them. "Select all matching" never loads those rows, so they
 * are read here, flat (no grouping) and a page at a time. Only calls with a
 * scenario count, as only those can be ticked in the table.
 */
export async function listMatchingCalls(
  executionId,
  filters = {},
  excludedIds = [],
) {
  const excluded = new Set(excludedIds);
  const callIds = [];
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
      if (excluded.has(row.id) || !row.source_scenario_key) return;
      callIds.push(row.id);
      keys.add(row.source_scenario_key);
    });
    totalPages = data?.total_pages || 1;
    page += 1;
  } while (page <= totalPages);
  return { callIds, scenarioKeys: [...keys] };
}

/**
 * Grades the given calls again, in place on their run: no new calls and no
 * new run. The ids are always sent explicitly, as the endpoint's `select_all`
 * covers the whole run and knows nothing of the table's filters.
 */
export async function rerunCallEvals(executionId, callIds) {
  const { data } = await axios.post(
    endpoints.testExecutions.rerunExecution(executionId),
    {
      select_all: false,
      rerun_type: "eval_only",
      call_execution_ids: callIds,
    },
  );
  return data;
}
