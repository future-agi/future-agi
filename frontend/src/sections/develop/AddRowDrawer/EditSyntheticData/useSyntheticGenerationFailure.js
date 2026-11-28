import { useQuery } from "@tanstack/react-query";
import { getDatasetQueryOptions } from "src/api/develop/develop-detail";
import { useEditSyntheticDataStore } from "./state";

// The reason the backend recorded for the last failed synthetic generation,
// or undefined. Only its fixed message is shown.
export const getSyntheticGenerationFailureReason = (result) => {
  const message = result?.dataset_config?.synthetic_generation_error?.message;
  return typeof message === "string" && message ? message : undefined;
};

// One answer to "did synthetic generation fail?" for every view of the
// dataset: the data tab overlay and the summary drawer's retry button.
export default function useSyntheticGenerationFailure(datasetId) {
  const failedToLoadData = useEditSyntheticDataStore(
    (state) => state.failedToGenerateData,
  );
  const { data: tableData } = useQuery(
    getDatasetQueryOptions(datasetId, 0, [], [], "", { enabled: false }),
  );
  const result = tableData?.data?.result;
  const reason = getSyntheticGenerationFailureReason(result);

  return {
    failed: Boolean(failedToLoadData || reason),
    reason,
    result,
  };
}
