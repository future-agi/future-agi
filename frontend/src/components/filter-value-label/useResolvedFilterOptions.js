import { useMemo } from "react";
import { useDashboardFilterValues } from "src/hooks/useDashboards";

const CUSTOM_ATTRIBUTE_VALUE_TYPES = new Set([
  "string",
  "number",
  "boolean",
  "array",
  "map",
  "json",
]);

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

export function shouldShowFilterValueContinuation({ hasNextPage }) {
  // TanStack v5 reports a failed next-page request through the query's broad
  // `isError` flag as well as `isFetchNextPageError`. The signed cursor is
  // still valid and loaded values are retained, so global error state must not
  // hide the one control that can retry that exact continuation.
  return Boolean(hasNextPage);
}

export function filterValuesUseBackendSearch(filter) {
  const backendType = getFilterBackendType(filter);
  const evalOutputType = filter?.outputType?.toUpperCase() || "";
  const isEvalWithStaticOptions =
    backendType === "eval_metric" &&
    ["PASS_FAIL", "CHOICE", "CHOICES"].includes(evalOutputType);
  return (
    !isEvalWithStaticOptions &&
    [
      "custom_attribute",
      "system_metric",
      "annotation_metric",
      "eval_metric",
    ].includes(backendType)
  );
}

const configuredChoiceIsMissing = (value) =>
  value === null || value === undefined || value === "";

const canonicalJsonValue = (value) => {
  if (Array.isArray(value)) return value.map(canonicalJsonValue);
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((key) => [key, canonicalJsonValue(value[key])]),
    );
  }
  return value;
};

const configuredChoiceIdentity = (value) => {
  const valueType = Array.isArray(value) ? "array" : typeof value;
  return `${valueType}:${JSON.stringify(canonicalJsonValue(value))}`;
};

export function normalizeConfiguredFilterOptions(choices = []) {
  const seen = new Set();
  const options = [];

  choices.forEach((choice) => {
    const isOption =
      choice !== null && typeof choice === "object" && !Array.isArray(choice);
    let value = choice;
    let label = choice;
    if (isOption) {
      value = choice.value;
      if (configuredChoiceIsMissing(value)) value = choice.label;
      if (configuredChoiceIsMissing(value)) value = choice.name;

      label = choice.label;
      if (configuredChoiceIsMissing(label)) label = choice.name;
      if (configuredChoiceIsMissing(label)) label = value;
    }
    if (configuredChoiceIsMissing(value)) return;

    const identity = configuredChoiceIdentity(value);
    if (seen.has(identity)) return;
    seen.add(identity);
    options.push({ value, label: String(label) });
  });

  return options;
}

export function useResolvedFilterOptions(
  filter,
  source,
  enabled = true,
  search = "",
  searchGesture = search,
) {
  const backendType = getFilterBackendType(filter);
  const backendSource = ["all", "both"].includes(source) ? "traces" : source;
  const evalOutputType = filter?.outputType?.toUpperCase() || "";
  const isEvalWithStaticOptions =
    backendType === "eval_metric" &&
    ["PASS_FAIL", "CHOICE", "CHOICES"].includes(evalOutputType);

  // Cursor-backed system and custom-attribute vocabularies can span many
  // pages. Client-filtering page one makes a real older value look absent, so
  // send the settled query to the authoritative cursor for both families.
  const usesBackendSearch = filterValuesUseBackendSearch(filter);
  const requestedAttributeType = String(
    filter?.dataType || filter?.data_type || "",
  ).toLowerCase();
  const observedAttributeTypes = Array.isArray(filter?.attributeTypes)
    ? [
        ...new Set(
          filter.attributeTypes.filter((valueType) =>
            ["string", "number", "boolean"].includes(valueType),
          ),
        ),
      ]
    : [];
  const readsMixedScalarMembership =
    ["contains", "not_contains"].includes(filter?.operator) &&
    observedAttributeTypes.length > 1 &&
    observedAttributeTypes.length === filter.attributeTypes.length;
  const attributeType =
    backendType === "custom_attribute" &&
    CUSTOM_ATTRIBUTE_VALUE_TYPES.has(requestedAttributeType) &&
    !readsMixedScalarMembership
      ? requestedAttributeType
      : undefined;

  const valueQuery = useDashboardFilterValues({
    propertyId: filter?.registryId || filter?.property_id || filter?.propertyId,
    metricName: filter?.id || "",
    metricType: backendType,
    projectIds: [],
    source: backendSource || "traces",
    search: usesBackendSearch ? search : "",
    searchGesture: usesBackendSearch ? searchGesture : "",
    // Every tracing/voice consumer must enter the signed-cursor route. Omitting
    // page_size silently selects the legacy finite-sample branch, which has no
    // read-more contract and owns a longer ClickHouse wall.
    pageSize: 10,
    // A key can carry several JSON value families. Pin value discovery to the
    // family selected in the cursor inventory so array member suggestions do
    // not get stringified or mixed with scalar/map values.
    attributeType,
    enabled: enabled && !isEvalWithStaticOptions,
  });
  const { data: fetchedOptions = [], isLoading } = valueQuery;

  const options = useMemo(() => {
    if (isEvalWithStaticOptions) {
      if (evalOutputType === "PASS_FAIL") {
        return [
          { value: "Passed", label: "Passed" },
          { value: "Failed", label: "Failed" },
        ];
      }
      if (
        ["CHOICE", "CHOICES"].includes(evalOutputType) &&
        filter?.choices?.length
      ) {
        return normalizeConfiguredFilterOptions(filter.choices);
      }
    }
    return fetchedOptions;
  }, [
    isEvalWithStaticOptions,
    evalOutputType,
    fetchedOptions,
    filter?.choices,
  ]);

  return {
    options,
    isLoading,
    isError: valueQuery.isError,
    queryReadState: valueQuery.queryReadState,
    fetchNextPage: valueQuery.fetchNextPage,
    hasNextPage: valueQuery.hasNextPage,
    continuationKey: valueQuery.continuationKey,
    isFetchingNextPage: valueQuery.isFetchingNextPage,
    isFetchNextPageError: valueQuery.isFetchNextPageError,
    cursorChainStopped: valueQuery.cursorChainStopped,
    retryFreshPage: valueQuery.retryFreshPage,
    isRetryingFreshPage: valueQuery.isRetryingFreshPage,
    refetch: valueQuery.refetch,
  };
}
