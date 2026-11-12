import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { REFRESH_STATUSES } from "./classicExperimentColumns";

// Experiments the runs view cannot show in full: the experiments list marks
// each row with `opens_in_full_results`, the same rule the experiment detail
// answers with (one backend function: a snapshot identity and a counted run
// for every live variant). A row it marks `false` belongs here; a row without
// the field is never guessed into this group. The field is read from the
// list's raw rows, so it works before the generated contracts list it.

// The list is read in pages of this size and filtered here, then shown
// PAGE_SIZE rows at a time, so every page of the group is full and its page
// count is true. Past the cap the read fails and says so; it never shows a
// partial list as the whole one.
export const READ_PAGE_SIZE = 200;
export const MAX_READ_PAGES = 50;

export const otherExperimentsKey = (datasetId) => [
  "experiment-list",
  datasetId,
  "opens-in-original-results",
];

export const isOtherExperiment = (row) => row?.opens_in_full_results === false;

export const otherExperimentsOf = (rows) =>
  (Array.isArray(rows) ? rows : []).filter(isOtherExperiment);

/** Every row of the dataset's experiments list, newest first. */
export async function readDatasetExperiments(datasetId, { signal } = {}) {
  const rows = [];
  for (let page = 1; page <= MAX_READ_PAGES; page += 1) {
    const response = await axios.get(
      endpoints.develop.experiment.experimentListPaginated,
      {
        params: { dataset_id: datasetId, page, limit: READ_PAGE_SIZE },
        signal,
      },
    );
    const body = response?.data;
    if (!Array.isArray(body?.results)) {
      throw new Error("The experiments list answered without its rows.");
    }
    rows.push(...body.results);
    if (!body.next || body.results.length === 0) return rows;
  }
  throw new Error(
    `This dataset has more than ${MAX_READ_PAGES * READ_PAGE_SIZE} experiments, too many to list here.`,
  );
}

const hasRunningOther = (rows) =>
  otherExperimentsOf(rows).some((row) => REFRESH_STATUSES.has(row.status));

/**
 * The dataset's experiments that open in the original results view.
 * @returns {{rows: object[], count: number|null, isPending: boolean,
 *   isError: boolean, error: unknown, refetch: Function}}
 *   count is null until a read has answered.
 */
export function useOtherExperiments(datasetId, { enabled = true } = {}) {
  const query = useQuery({
    queryKey: otherExperimentsKey(datasetId),
    queryFn: ({ signal }) => readDatasetExperiments(datasetId, { signal }),
    select: otherExperimentsOf,
    enabled: Boolean(datasetId) && enabled,
    refetchOnWindowFocus: false,
    // As the list does: read again every 10 s while one of them runs.
    refetchInterval: (q) => (hasRunningOther(q.state.data) ? 10000 : false),
    // The group shows its own error state with Retry.
    meta: { errorHandled: true },
  });
  const rows = query.data ?? [];
  return {
    rows,
    count: query.data ? rows.length : null,
    isPending: query.isPending,
    isError: query.isError,
    error: query.error,
    refetch: query.refetch,
  };
}
