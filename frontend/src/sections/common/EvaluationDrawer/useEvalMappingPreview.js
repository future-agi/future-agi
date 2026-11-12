import { useQueries } from "@tanstack/react-query";
import axios from "src/utils/axios";

export const EVAL_MAPPING_PREVIEW = "/model-hub/eval-mapping-preview/";

/** Read saved inputs only. Never send preview requests to the execution API. */
export function useEvalMappingPreview({ evals, datasetId, rowId }) {
  return useQueries({
    queries: (evals ?? []).map((evaluation) => {
      const templateId =
        evaluation?.templateId ?? evaluation?.template_id ?? evaluation?.evalId;
      const body = {
        dataset_id: datasetId,
        template_id: templateId,
        row_ids: [rowId],
        config: {
          ...(evaluation?.config ?? {}),
          mapping: evaluation?.config?.mapping ?? evaluation?.mapping ?? {},
        },
        ...(evaluation?.pinned_version_id !== undefined
          ? { pinned_version_id: evaluation.pinned_version_id }
          : {}),
      };
      return {
        queryKey: ["eval-mapping-preview", JSON.stringify(body)],
        queryFn: () =>
          axios.post(EVAL_MAPPING_PREVIEW, body).then((response) => {
            const row = response.data.result.rows.find(
              (item) => item.row_id === rowId,
            );
            if (!row) throw new Error("Sample row was not returned");
            return row;
          }),
        enabled: Boolean(datasetId && rowId && templateId),
        retry: false,
      };
    }),
  });
}
