import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { getColumnConfig } from "src/sections/develop-detail/DataTab/common";
import axios, { endpoints } from "src/utils/axios";
import logger from "src/utils/logger";
import { modelHubDevelopsGetDatasetTableList } from "src/generated/api-contracts/api";

export const normalizeDatasetListItem = (dataset) => ({
  ...dataset,
  datasetId: dataset?.dataset_id ?? dataset?.datasetId ?? dataset?.id,
  datasetType: dataset?.dataset_type ?? dataset?.datasetType,
  modelType: dataset?.model_type ?? dataset?.modelType,
});

export const useDevelopDatasetList = (
  search_text,
  exclude,
  options,
  newPayload = {},
) => {
  const payload = {};
  const queryKey = ["develop", "dataset-name-list"];

  if (search_text?.length) {
    payload.search_text = search_text;
    queryKey.push(search_text);
  }

  if (exclude?.length) {
    payload.excluded_dataset = exclude;
    queryKey.push(exclude);
  }

  const addToPayloadAndKey = (key, value) => {
    if (value !== undefined && value !== null) {
      payload[key] = value;
      queryKey.push(key);
    }
  };

  Object.entries(newPayload).forEach(([key, value]) => {
    addToPayloadAndKey(key, value);
  });

  return useQuery({
    ...options,
    queryKey,
    queryFn: () =>
      axios.get(endpoints.develop.getDatasetList(), { params: payload }),
    select: (d) =>
      (d.data?.result?.datasets ?? []).map(normalizeDatasetListItem),
    staleTime: 30 * 1000, // 30 seconds stale time (reduced from 1 min)
    refetchOnWindowFocus: true, // Refetch when window regains focus
  });
};

export const useScenariosList = (search_text, options = {}) => {
  const params = {};
  const queryKey = ["scenarios"];

  if (search_text?.length) {
    params.search_text = search_text;
    queryKey.push(search_text);
  }

  return useQuery({
    ...options,
    queryKey,
    queryFn: () =>
      axios.get(endpoints.scenarios.list, {
        params: {
          limit: 20,
          search: search_text,
        },
      }),
    select: (d) => d.data?.results,
    staleTime: 30 * 1000,
    refetchOnWindowFocus: true,
  });
};

export const useRunPromptOptions = (options) => {
  return useQuery({
    ...options,
    queryKey: ["develop", "run-prompt-options"],
    queryFn: () => axios.get(endpoints.develop.runPrompt.runPromptOptions),
    select: (d) => d.data?.result,
    staleTime: 1 * 60 * 1000, // 1 min stale time
  });
};

export const useVoiceOptions = (options) => {
  return useQuery({
    queryKey: ["voice-options", options?.model],
    queryFn: () =>
      axios.get(endpoints.develop.runPrompt.voiceOptions, {
        params: {
          model: options?.model,
        },
      }),
    select: (d) => ({
      default: d.data?.result?.default_voice,
      voices: d.data?.result?.supported_voices?.map((voice) => ({
        value: voice?.id,
        label: voice?.name,
      })),
      isCustomAudio: d.data?.result?.custom_voice_supported,
    }),
    staleTime: 1 * 60 * 1000, // 1 min stale time
    enabled: Boolean(options?.enabled),
  });
};

export const useGetDatasetColumns = (dataset, options, params = {}) => {
  return useQuery({
    ...options,
    queryKey: ["get-dataset-columns", dataset, params],
    queryFn: () =>
      axios.get(endpoints.develop.getDatasetColumns(dataset), { params }),
    select: (d) => d.data?.result?.columns,
    staleTime: 1 * 60 * 1000, // 1 min stale time
  });
};

/**
 * Fetch JSON schemas for JSON-type columns in a dataset.
 * Used for autocomplete suggestions when accessing JSON properties with dot notation.
 *
 * @param {string} datasetId - The dataset UUID
 * @param {object} options - React Query options
 * @returns {object} Query result with JSON schemas keyed by column ID
 */
export const useGetJsonColumnSchema = (datasetId, options = {}) => {
  logger.debug(options);
  return useQuery({
    queryKey: ["json-column-schema", datasetId],
    queryFn: () => axios.get(endpoints.develop.getJsonColumnSchema(datasetId)),
    select: (d) => d.data?.result,
    enabled: Boolean(datasetId),
    staleTime: 5 * 60 * 1000, // 5 min stale time - schema doesn't change often
    ...options,
  });
};

export const useGetDatasetDetail = (datasetId, options = {}, params = {}) => {
  return useQuery({
    queryKey: ["get-dataset-detail", datasetId, params],
    queryFn: () => modelHubDevelopsGetDatasetTableList(datasetId, params),
    select: (d) => d?.data?.result || d?.result || d,
    enabled: Boolean(datasetId),
    staleTime: 60 * 1000,
    ...options,
  });
};

export const useEvaluationList = (
  search_text,
  exclude,
  options,
  newPayload = {},
) => {
  const payload = {};
  const queryKey = [""];

  if (search_text?.length) {
    payload.search_text = search_text;
    queryKey.push(search_text);
  }

  if (exclude?.length) {
    payload.excluded_dataset = exclude;
    queryKey.push(exclude);
  }

  const addToPayloadAndKey = (key, value) => {
    if (value !== undefined && value !== null) {
      payload[key] = value;
      queryKey.push(key);
    }
  };

  Object.entries(newPayload).forEach(([key, value]) => {
    addToPayloadAndKey(key, value);
  });

  return useQuery({
    ...options,
    queryKey,
    queryFn: () => axios.post(endpoints.develop.eval.getEvalNames, payload),
    select: (d) => d.data?.result,
    staleTime: 1 * 60 * 1000,
  });
};

export const getDatasetQueryKey = (
  datasetId,
  pageNumber,
  filters,
  sort,
  searchQuery,
) => {
  return ["dataset-detail", datasetId, pageNumber, filters, sort, searchQuery];
};

export const getDatasetQueryOptions = (
  datasetId,
  pageNumber,
  filters,
  sort,
  searchQuery,
  extra,
) => {
  const pageSize = extra?.pageSize || 30;
  return {
    queryKey: getDatasetQueryKey(
      datasetId,
      pageNumber,
      filters,
      sort,
      searchQuery,
    ),
    queryFn: () =>
      axios.get(endpoints.develop.getDatasetDetail(datasetId), {
        params: {
          current_page_index: pageNumber,
          filters: JSON.stringify(filters),
          sort: JSON.stringify(sort),
          ...(searchQuery && {
            search: JSON.stringify({
              key: searchQuery,
              type: ["text", "image", "audio"],
            }),
          }),
          page_size: pageSize,
        },
      }),
    staleTime: Infinity,
    ...extra,
  };
};

export const useDatasetColumnConfig = (
  dataset,
  includeSummary = false,
  shouldFetch = false,
  freshData = false,
) => {
  const { data: tableData } = useQuery(
    getDatasetQueryOptions(dataset, 0, [], [], "", {
      enabled: shouldFetch,
      ...(freshData && {
        staleTime: 0,
        refetchOnMount: true,
      }),
    }),
  );

  const columnConfig = useMemo(() => {
    return tableData?.data?.result?.column_config ?? [];
  }, [tableData?.data?.result?.column_config]);

  return useMemo(() => {
    const grouping = {};

    for (const eachCol of columnConfig) {
      if (
        eachCol?.source_id &&
        (eachCol?.origin_type === "evaluation" ||
          eachCol?.origin_type === "evaluation_reason") &&
        !includeSummary
      ) {
        if (!grouping[eachCol?.source_id]) {
          grouping[eachCol?.source_id] = [eachCol];
        } else {
          grouping[eachCol?.source_id].push(eachCol);
        }
      } else {
        grouping[eachCol?.id] = [eachCol];
      }
    }

    const columnMap = [];

    for (const [_, cols] of Object.entries(grouping)) {
      if (cols.length === 1) {
        const eachCol = cols[0];
        columnMap.push(
          getColumnConfig({
            eachCol,
            dataset,
          }),
        );
      } else {
        let eachCol = cols[0];
        let children = null;
        eachCol = cols.find((v) => v?.origin_type === "evaluation");
        children = cols.map((v) =>
          getColumnConfig({
            eachCol: v,
            dataset,
          }),
        );
        columnMap.push(
          getColumnConfig({
            eachCol,
            ...(children && { children }),
            dataset,
          }),
        );
      }
    }
    return columnMap;
  }, [columnConfig, dataset, includeSummary]);
};

export const useGetColumns = (dataset) => {
  const { data: tableData } = useQuery(
    getDatasetQueryOptions(dataset, 0, [], [], "", { enabled: false }),
  );

  const columnConfig = useMemo(() => {
    return tableData?.data?.result?.column_config ?? [];
  }, [tableData?.data?.result?.column_config]);

  return columnConfig;
};

export const useColumnInfo = (columnId, options = {}) => {
  return useQuery({
    queryFn: () =>
      axios.get(endpoints.develop.optimizeDevelop.columnInfo, {
        params: { column_id: columnId },
      }),
    queryKey: ["optimize-develop-column-info", columnId],
    enabled: Boolean(columnId?.length),
    select: (data) => data?.data?.result,
    ...options,
  });
};

/**
 * Hook to fetch derived variables for a prompt template.
 * Derived variables are extracted from JSON structured outputs.
 *
 * @param {string} promptId - The prompt template UUID
 * @param {object} options - Additional options
 * @param {string} options.version - Optional version filter
 * @param {string} options.columnName - Optional column name filter
 * @returns {object} Query result with derived variables
 */
export const useDerivedVariables = (promptId, options = {}) => {
  const { version, columnName, ...queryOptions } = options;

  const params = {};
  if (version) params.version = version;
  if (columnName) params.column_name = columnName;

  return useQuery({
    queryKey: ["derived-variables", promptId, version, columnName],
    queryFn: () =>
      axios.get(endpoints.develop.runPrompt.getDerivedVariables(promptId), {
        params,
      }),
    select: (d) => d.data?.result,
    enabled: Boolean(promptId),
    staleTime: 30 * 1000, // 30 seconds - variables can change on rerun
    ...queryOptions,
  });
};

/**
 * Hook to fetch all derived variables from all run prompt columns in a dataset.
 *
 * This aggregates derived variables from run prompt columns that produce JSON outputs,
 * making them available for use in other prompts, evals, and experiments.
 *
 * @param {string} datasetId - The dataset UUID
 * @param {object} options - Additional query options
 * @returns {object} Query result with aggregated derived variables
 */
export const useDatasetDerivedVariables = (datasetId, options = {}) => {
  return useQuery({
    queryKey: ["dataset-derived-variables", datasetId],
    queryFn: () =>
      axios.get(
        endpoints.develop.runPrompt.getDatasetDerivedVariables(datasetId),
      ),
    select: (d) => d.data?.result,
    enabled: Boolean(datasetId),
    staleTime: 30 * 1000, // 30 seconds - variables can change on rerun
    ...options,
  });
};

/**
 * Hook to fetch the schema for derived variables of a specific column.
 *
 * @param {string} promptId - The prompt template UUID
 * @param {string} columnName - The column name
 * @param {object} options - Additional options
 * @returns {object} Query result with schema details
 */
export const useDerivedVariableSchema = (
  promptId,
  columnName,
  options = {},
) => {
  const { version, ...queryOptions } = options;

  const params = {};
  if (version) params.version = version;

  return useQuery({
    queryKey: ["derived-variable-schema", promptId, columnName, version],
    queryFn: () =>
      axios.get(
        endpoints.develop.runPrompt.getDerivedVariableSchema(
          promptId,
          columnName,
        ),
        { params },
      ),
    select: (d) => d.data?.result,
    enabled: Boolean(promptId) && Boolean(columnName),
    staleTime: 30 * 1000,
    ...queryOptions,
  });
};

// NOTE: For variable autocomplete options, use getDropdownOptionsFromCols from
// src/sections/develop-detail/RunPrompt/common.js or the useVariableOptions hook
// from src/hooks/use-variable-options.js
