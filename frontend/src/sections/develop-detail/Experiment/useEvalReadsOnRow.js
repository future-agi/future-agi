// W1-5 (DS-04): what each eval card reads on the Data step's sample row,
// as the one scorer resolves it: eval-playground with
// `row_context.reads_only` answers the eval's `inputs_used` (the same
// resolution every result cell records), with no judge call. The table
// never re-derives an eval's inputs from the card.
import { useQueries } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

/**
 * @param {{evals: object[], datasetId: string, rowId: string}} p  evals:
 *   the form's userEvalMetrics cards
 * @returns {Array<{data?: {inputs_used: object, skipped: string|null},
 *   isLoading: boolean, isError: boolean}>} one query per card
 */
export function useEvalReadsOnRow({ evals, datasetId, rowId }) {
  return useQueries({
    queries: (evals ?? []).map((e) => {
      const templateId = e?.templateId ?? e?.template_id ?? e?.evalId;
      const config = {
        ...(e?.config ?? {}),
        mapping: e?.config?.mapping ?? e?.mapping ?? {},
      };
      return {
        queryKey: [
          "eval-reads-on-row",
          datasetId,
          rowId,
          templateId,
          JSON.stringify(config),
          e?.model ?? null,
        ],
        queryFn: () =>
          axios
            .post(endpoints.develop.eval.evalPlayground, {
              template_id: templateId,
              model: e?.model || "turing_large",
              config,
              row_context: {
                source: "experiment_create",
                dataset_id: datasetId,
                row_id: rowId,
                reads_only: true,
              },
            })
            .then((res) => res.data?.result ?? null),
        enabled: Boolean(datasetId && rowId && templateId),
      };
    }),
  });
}
