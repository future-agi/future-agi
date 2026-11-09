// KH integration (H-1 · EV-07): which eval template, and which of its versions, an
// eval column's "Open in Evals" opens. Reads only existing endpoints:
//   GET develops/{id}/get_evals_list/?eval_type=user  (the drawer's query key)
//   GET eval-templates/{id}/versions/                 (the Evals page's key)
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { paramsSerializer } from "src/utils/utils";
import { getUserEvalListKey } from "src/sections/common/EvaluationDrawer/getEvalsList";
import { useEvalVersions } from "src/sections/evals/hooks/useEvalVersions";
import { evalLinkOf, resolveTemplateId } from "./evalLinks";

const USER_EVALS = { eval_type: "user" };
// Hover cards and headers mount many at once; one read per dataset serves
// them all for this long (the drawer's own query polls while it is open).
const SAVED_EVALS_STALE_MS = 30 * 1000;

/** The dataset's saved evals, on the Evaluate drawer's query key. */
function useSavedEvals(datasetId, enabled) {
  return useQuery({
    queryKey: getUserEvalListKey("dataset", datasetId, USER_EVALS),
    queryFn: () =>
      axios.get(endpoints.develop.eval.getEvalsList(datasetId), {
        params: USER_EVALS,
        paramsSerializer: paramsSerializer(),
      }),
    select: (res) => res?.data?.result?.evals ?? [],
    enabled: Boolean(enabled && datasetId),
    staleTime: SAVED_EVALS_STALE_MS,
  });
}

/**
 * @returns {{templateId: string|null}} def.template_id, else the dataset's
 * saved evals (metric_key / user eval id), else null (no link).
 */
export function useEvalTemplateRef({ datasetId, metricKey, def } = {}) {
  const own = def?.template_id ?? null;
  const { data: saved } = useSavedEvals(datasetId, !own);
  return {
    templateId:
      own ?? resolveTemplateId({ def, metricKey, datasetEvals: saved }),
  };
}

/**
 * The checked link for an eval column at a version, or null when the column
 * has no template. {href, tooltip, missing}
 */
export function useEvalLink({ datasetId, metricKey, def, name, version }) {
  const { templateId } = useEvalTemplateRef({ datasetId, metricKey, def });
  const { data: versionsData } = useEvalVersions(templateId);
  return evalLinkOf({
    templateId,
    version,
    name: name ?? def?.name,
    versions: versionsData?.versions ?? null,
  });
}
