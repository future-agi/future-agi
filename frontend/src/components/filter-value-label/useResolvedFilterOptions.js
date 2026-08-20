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

/**
 * True when the backend's labels are just the values — a caller that only
 * needs a label can then render the value directly instead of fetching the
 * whole value list (a workspace-wide span scan).
 *
 * Only `custom_attribute` qualifies, and deliberately so: its backend branch
 * returns {value: v, label: v} unconditionally, with no per-field exceptions
 * to track. system_metric is mostly identity too, but several of its fields
 * DO relabel (project/project_id -> project name, session -> display name),
 * the set differs per surface, and its scans are cheap anyway (narrow
 * columns, no attribute-map I/O) — not worth the misclassification risk of
 * rendering a raw id where a name belongs.
 */
export function filterLabelsMatchValues(filter) {
  return getFilterBackendType(filter) === "custom_attribute";
}

export function useResolvedFilterOptions(
  filter,
  source,
  enabled = true,
  search = "",
) {
  const backendType = getFilterBackendType(filter);
  const evalOutputType = filter?.outputType?.toUpperCase() || "";
  const isEvalWithStaticOptions =
    backendType === "eval_metric" &&
    (evalOutputType === "PASS_FAIL" || evalOutputType === "CHOICES");

  // Backend search is index-backed for custom attributes and can reach values
  // outside the default lookback, which client-side filtering of the fetched
  // page cannot. Other types keep filtering the fetched page client-side.
  const usesBackendSearch = backendType === "custom_attribute";

  const { data: fetchedOptions = [], isLoading } = useDashboardFilterValues({
    metricName: filter?.id || "",
    metricType: backendType,
    projectIds: [],
    source: source || "traces",
    search: usesBackendSearch ? search : "",
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
