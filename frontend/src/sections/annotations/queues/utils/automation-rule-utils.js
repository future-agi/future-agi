import { format, isValid } from "date-fns";
import { buildApiFilterFromPanelRow } from "src/api/contracts/filter-contract";
import {
  LIST_FILTER_OPS,
  NO_VALUE_FILTER_OPS,
} from "src/api/contracts/filter-contract.generated";
import { getRandomId } from "src/utils/utils";
import { apiFilterHasValue } from "./filter-operators";
import { DEFAULT_FILTER } from "../constants";

export function getQueueScopeId(queue, key) {
  const value = queue?.[key];
  if (!value) return "";
  return typeof value === "object"
    ? value.id || value.datasetId || value.dataset_id
    : value;
}

export function getDatasetOptionId(dataset) {
  return dataset?.dataset_id || dataset?.datasetId || dataset?.id || "";
}

export function resolveRuleScopeId(queue, queueScopeId, selectedScopeId) {
  if (queue?.is_default) return selectedScopeId || queueScopeId;
  return queueScopeId || selectedScopeId;
}

export function isQueueScopeLocked(queue, queueScopeId) {
  return Boolean(queueScopeId) && !queue?.is_default;
}

export const makeDatasetDefaultFilter = () => ({
  id: getRandomId(),
  column_id: "",
  filter_config: {
    filter_type: "text",
    filter_op: "equals",
    filter_value: "",
  },
});

export const datasetFilterToCamel = (filter) => ({
  id: filter.id,
  columnId: filter.column_id || "",
  filterConfig: {
    filterType: filter.filter_config?.filter_type || "",
    filterOp: filter.filter_config?.filter_op || "",
    filterValue: filter.filter_config?.filter_value ?? "",
  },
});

export const datasetFilterToSnake = (filter) => ({
  id: filter.id,
  column_id: filter.columnId || "",
  filter_config: {
    filter_type: filter.filterConfig?.filterType || "",
    filter_op: filter.filterConfig?.filterOp || "",
    filter_value: filter.filterConfig?.filterValue ?? "",
  },
});

export const isDatasetFilterValid = (filter) => {
  const config = filter?.filter_config;
  if (!config || typeof filter.column_id !== "string") return false;
  const { filter_value: value, filter_op: op, filter_type: type } = config;
  if (filter.column_id.length === 0 || op === "" || type === "") return false;

  const isRange = op === "between" || op === "not_between";
  const hasValue = NO_VALUE_FILTER_OPS.includes(op)
    ? true
    : LIST_FILTER_OPS.includes(op)
      ? Array.isArray(value) && value.length > 0
      : value !== "";
  if (!hasValue) return false;

  if (type === "datetime") {
    return isRange ? isValid(value?.[0]) && isValid(value?.[1]) : isValid(value);
  }
  return isRange ? Array.isArray(value) && value.length === 2 : true;
};

export const formatDatasetDatetime = (value) =>
  Array.isArray(value)
    ? value.map((item) =>
        item ? format(new Date(item), "yyyy-MM-dd HH:mm:ss") : item,
      )
    : value
      ? format(new Date(value), "yyyy-MM-dd HH:mm:ss")
      : value;

export const transformDatasetFilter = (filter) => {
  const config = filter.filter_config || {};
  return buildApiFilterFromPanelRow({
    field: filter.column_id,
    fieldType: config.filter_type,
    operator: config.filter_op,
    value:
      config.filter_type === "datetime"
        ? formatDatasetDatetime(config.filter_value)
        : config.filter_value,
  });
};

export function defaultFiltersForSource(sourceType) {
  if (sourceType === "dataset_row") {
    return [makeDatasetDefaultFilter()];
  }
  return [{ ...DEFAULT_FILTER, id: getRandomId() }];
}

function filterWithValue(filter) {
  return apiFilterHasValue(filter);
}

export function getSubmittableFilters(filters) {
  // Drop rows that don't carry a value (or aren't a unary op like is_null).
  // Without this, a half-filled row with just a
  // columnId selected serialises into the API payload's `filter:` array
  // and the backend's evaluator silently match-everythings.
  return (filters || [])
    .filter(filterWithValue)
    .map(({ id, ...filter }) => filter);
}

function snakeFilterToUi(filter) {
  const config = filter?.filter_config || {};
  const filterType = config.filter_type || "";
  let filterValue = "filter_value" in config ? config.filter_value : "";
  if (filterType === "datetime") {
    filterValue = Array.isArray(filterValue)
      ? filterValue.map((value) => (value ? new Date(value) : value))
      : filterValue
        ? new Date(filterValue)
        : filterValue;
  }
  return {
    id: getRandomId(),
    column_id: filter?.column_id || "",
    display_name: filter?.display_name,
    filter_config: {
      filter_type: filterType,
      filter_op: config.filter_op || "",
      filter_value: filterValue,
      ...(config.col_type ? { col_type: config.col_type } : {}),
    },
  };
}

export function ruleConditionsToFilters(rule) {
  const sourceType = rule?.source_type || "trace";
  const filterPayload = rule?.conditions?.filter;
  if (Array.isArray(filterPayload) && filterPayload.length > 0) {
    return filterPayload.map(snakeFilterToUi);
  }
  const rules = rule?.conditions?.rules || [];
  if (rules.length === 0) return defaultFiltersForSource(sourceType);
  return rules.map((row) => ({
    id: getRandomId(),
    column_id: row.field || "",
    filter_config: {
      filter_type: "text",
      filter_op: row.op || "",
      filter_value: row.value ?? "",
    },
  }));
}

export function ruleConditionsToScope(rule) {
  return rule?.conditions?.scope || {};
}

export function buildConditionsForRule(sourceType, filters, scope, queue) {
  const queueProjectId = getQueueScopeId(queue, "project");
  const queueDatasetId = getQueueScopeId(queue, "dataset");
  const queueAgentId = getQueueScopeId(queue, "agent_definition");
  const nextScope = {};

  if (sourceType === "dataset_row") {
    const datasetId = resolveRuleScopeId(
      queue,
      queueDatasetId,
      scope.dataset_id,
    );
    if (datasetId) nextScope.dataset_id = datasetId;
    return {
      operator: "and",
      filter: filters.filter(isDatasetFilterValid).map(transformDatasetFilter),
      scope: nextScope,
    };
  }

  if (sourceType === "trace" || sourceType === "observation_span") {
    const projectId = resolveRuleScopeId(
      queue,
      queueProjectId,
      scope.project_id,
    );
    if (projectId) nextScope.project_id = projectId;
    if (sourceType === "trace") {
      nextScope.is_voice_call = !!scope.is_voice_call;
      nextScope.remove_simulation_calls = !!scope.remove_simulation_calls;
    }
    const apiFilters = getSubmittableFilters(filters);
    return {
      operator: "and",
      filter: apiFilters,
      scope: nextScope,
    };
  }

  if (sourceType === "trace_session") {
    const projectId = resolveRuleScopeId(
      queue,
      queueProjectId,
      scope.project_id,
    );
    if (projectId) nextScope.project_id = projectId;
    const apiFilters = getSubmittableFilters(filters);
    return {
      operator: "and",
      filter: apiFilters,
      scope: nextScope,
    };
  }

  if (sourceType === "call_execution") {
    const agentId = resolveRuleScopeId(queue, queueAgentId, scope.project_id);
    if (agentId) nextScope.project_id = agentId;
    const apiFilters = getSubmittableFilters(filters);
    return {
      operator: "and",
      filter: apiFilters,
      ...(Object.keys(nextScope).length ? { scope: nextScope } : {}),
    };
  }

  return {
    operator: "and",
    filter: getSubmittableFilters(filters),
    ...(Object.keys(nextScope).length ? { scope: nextScope } : {}),
  };
}

export function isScopeReady(sourceType, scope, queue) {
  if (sourceType === "dataset_row") {
    return Boolean(scope.dataset_id || getQueueScopeId(queue, "dataset"));
  }
  if (["trace", "observation_span", "trace_session"].includes(sourceType)) {
    return Boolean(scope.project_id || getQueueScopeId(queue, "project"));
  }
  if (sourceType === "call_execution") {
    return Boolean(
      scope.project_id || getQueueScopeId(queue, "agent_definition"),
    );
  }
  return true;
}

export function getRuleSubmitDisabledTooltipTitle(sourceType, scope, queue, name) {
  if (!name.trim()) return "Enter a rule name";
  if (!isScopeReady(sourceType, scope, queue)) {
    if (sourceType === "dataset_row") return "Choose a dataset";
    if (["trace", "observation_span", "trace_session"].includes(sourceType)) {
      return "Choose a project";
    }
    if (sourceType === "call_execution") return "Choose an agent definition";
  }
  return "";
}
