import { useMemo } from "react";
import { useDashboardFilterValues } from "src/hooks/useDashboards";

const getFilterBackendType = (filter) => {
  const map = {
    system: "system_metric",
    eval_metric: "eval_metric",
    annotation: "annotation_metric",
    custom_attribute: "custom_attribute",
    custom_column: "custom_column",
  };
  return map[filter?.type] || filter?.type || "system_metric";
};

export function useResolvedFilterOptions(filter, source, enabled = true) {
  const backendType = getFilterBackendType(filter);
  const evalOutputType = filter?.outputType?.toUpperCase() || "";
  const isEvalWithStaticOptions =
    backendType === "eval_metric" &&
    (evalOutputType === "PASS_FAIL" || evalOutputType === "CHOICES");

  const { data: fetchedOptions = [], isLoading } = useDashboardFilterValues({
    metricName: filter?.id || "",
    metricType: backendType,
    projectIds: [],
    source: source || "traces",
    enabled: enabled && !isEvalWithStaticOptions,
  });

  const options = useMemo(() => {
    if (isEvalWithStaticOptions) {
      if (evalOutputType === "PASS_FAIL") {
        return [
          { value: "Passed", label: "Passed" },
          { value: "Failed", label: "Failed" },
        ];
      }
      if (evalOutputType === "CHOICES" && filter?.choices?.length) {
        return filter.choices.map((c) => ({
          value: typeof c === "string" ? c : c.value || c.label || String(c),
          label: typeof c === "string" ? c : c.label || c.value || String(c),
        }));
      }
    }
    return fetchedOptions;
  }, [
    isEvalWithStaticOptions,
    evalOutputType,
    fetchedOptions,
    filter?.choices,
  ]);

  return { options, isLoading };
}
