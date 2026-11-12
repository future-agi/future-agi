// Quick tests (R2-SPEC §0.4): unlisted runs started from the Iterate dock.
// The leaderboard hides them; its header says `1 quick test · Show`, and
// `quick_tests=1` in the URL shows them. R1 returns them only with
// `include=quick_tests`; the key shares the R1 prefix, so the dock's
// invalidation after a run refreshes the count.
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { datasetRunsParams } from "src/api/develop/runs";
import { R1_CONFIG_INCLUDE, runConfigsKey } from "src/api/develop/r2";

export const QUICK_TESTS_PARAM = "quick_tests";

/** Quick tests of a dataset's experiments (pure filter; exported for tests). */
export const quickTestsOf = (runs) =>
  (runs ?? []).filter(
    (r) =>
      r.listed === false &&
      (r.kind === "quick_test" || r.source?.kind === "quick_test"),
  );

export default function useQuickTests(datasetId) {
  const params = datasetRunsParams({
    include: `${R1_CONFIG_INCLUDE},quick_tests`,
  });
  return useQuery({
    queryKey: runConfigsKey(datasetId, params),
    queryFn: () =>
      axios.get(endpoints.develop.datasetRuns.list(datasetId), { params }),
    select: (res) => quickTestsOf(res.data?.result?.results),
    enabled: Boolean(datasetId),
  });
}
