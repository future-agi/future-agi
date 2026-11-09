// K-2 (RT-09): runs started with Record off, from the same R1 read as the
// quick tests link (`include=…,quick_tests`, the same key), so the footer
// makes one request for both and the dock's invalidation refreshes both.
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { datasetRunsParams } from "src/api/develop/runs";
import { R1_CONFIG_INCLUDE, runConfigsKey } from "src/api/develop/r2";
import { notRecordedOf } from "./recordModel";

export default function useNotRecordedRuns(datasetId) {
  const params = datasetRunsParams({
    include: `${R1_CONFIG_INCLUDE},quick_tests`,
  });
  return useQuery({
    queryKey: runConfigsKey(datasetId, params),
    queryFn: () =>
      axios.get(endpoints.develop.datasetRuns.list(datasetId), { params }),
    select: (res) => notRecordedOf(res.data?.result?.results),
    enabled: Boolean(datasetId),
  });
}
