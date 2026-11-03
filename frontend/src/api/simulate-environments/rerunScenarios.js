import { useMutation, useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import axios, { endpoints } from "src/utils/axios";

import {
  callDetailKeyPrefix,
  runAnalyticsKey,
  runResultsKey,
} from "./runEvals";

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
 * Every re-runnable call matching `filters`, less the calls in `excludedIds`,
 * as `{ id, sourceScenarioKey }`. "Select all matching" never loads those rows,
 * so they are read here, flat (no grouping) and a page at a time. A call with
 * no scenario can't be re-run either way, so it is left out.
 */
export async function listMatchingCalls(
  executionId,
  filters = {},
  excludedIds = [],
) {
  const excluded = new Set(excludedIds);
  const calls = [];
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
        calls.push({ id: row.id, sourceScenarioKey: row.source_scenario_key });
    });
    totalPages = data?.total_pages || 1;
    page += 1;
  } while (page <= totalPages);
  return calls;
}

/**
 * Makes chosen calls again inside their own run: each row is refilled with the
 * new result, and the backend keeps the earlier attempt. The run is running
 * again afterwards, so its header and table are refreshed to resume polling.
 */
export function useRerunCallsInRun(executionId) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (callIds) =>
      axios.post(endpoints.testExecutions.rerunExecution(executionId), {
        rerun_type: "call_and_eval",
        call_execution_ids: callIds,
      }),
    // The global handler toasts only `{ result }` errors; a run's refusal arrives
    // as `{ error, message }`, and the menu has already closed.
    onError: (error) => {
      if (error?.result) return;
      enqueueSnackbar(error?.message || "Couldn't re-run the selected calls", {
        variant: "error",
      });
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: runResultsKey(executionId) });
      queryClient.invalidateQueries({ queryKey: runAnalyticsKey(executionId) });
      queryClient.invalidateQueries({ queryKey: callDetailKeyPrefix });
    },
  });
}
