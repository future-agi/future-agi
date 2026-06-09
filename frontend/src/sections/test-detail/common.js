import { format } from "date-fns";
import CallDetailCellRenderer from "./CellRenderers/CallDetailCellRenderer";
import ScoreCellRenderer from "./CellRenderers/ScoreCellRenderer";
import EvalCellRenderer from "./CellRenderers/EvalCellRenderer";
import _ from "lodash";
import axios from "src/utils/axios";
import { endpoints } from "src/utils/axios";
import ScenarioCellRenderer from "./CellRenderers/ScenarioCellRenderer";
import { useQuery } from "@tanstack/react-query";
import { canonicalKeys, objectCamelToSnake } from "src/utils/utils";
import { getAnnotationMetricFilterDefinition } from "src/utils/prototypeObserveUtils";
import ToolEvaluationCellRenderer from "./CellRenderers/ToolEvaluationCellRenderer";
import { getLabel } from "./PerformanceMetrics/common";
import EvalReasonCellRenderer from "./CellRenderers/EvalReasonCellRenderer";
import ToolReasonCellRenderer from "./CellRenderers/ToolReasonCellRenderer";
import {
  reorderMenuList,
  setMenuIcons,
} from "src/utils/MenuIconSet/setMeniIcons";
import { menuIcons } from "src/utils/MenuIconSet/svgIcons";
import ColumnCellRenderer from "./CellRenderers/ColumnCellRenderer";
import { useTestDetailStore, useTestExecutionStore } from "./states";
import { AGENT_TYPES } from "src/sections/agents/constants";
import useKpis from "src/hooks/useKpis";
import { useMemo } from "react";
import { LoadingHeader } from "./CellRenderers/ScenarioCellRenderer";

const menuOrder = [
  "Pin Column",
  "separator",
  // "Choose Columns",
  "Autosize This Column",
  "Autosize All Columns",
  "Reset Columns",
];

const getMainMenuItems = (params) => {
  const allMenuItems = setMenuIcons(params, ""); // Pass dataset name
  const menuItems = allMenuItems.slice(0);
  const newMenuOrder = [...menuOrder];
  // const menuItems = params.defaultItems.slice(0);
  const extraMenuItems = [];

  const column = params.column.colDef;

  if (
    column?.type === TestDetailColumnTypes.EVALUATION ||
    column?.type === TestDetailColumnTypes.TOOL_EVALUATION
  ) {
    const reasonColumnField = `${column.field}_reason`;
    const reasonColumn = params?.api?.getColumn(reasonColumnField);

    newMenuOrder.unshift(
      "Show Reasoning",
      "Hide Reasoning",
      "Configure Eval",
      "separator",
    );
    if (column?.type === TestDetailColumnTypes.EVALUATION) {
      extraMenuItems.push({
        name: "Configure Eval",
        action: () => {
          useTestDetailStore.getState().setConfigureEval({
            id: column?.id,
          });
        },
        icon: menuIcons["Configure Eval"],
      });
    }
    extraMenuItems.push({
      name: reasonColumn?.visible ? "Hide Reasoning" : "Show Reasoning",
      action: () => {
        params?.api?.setColumnsVisible(
          [reasonColumnField],
          !reasonColumn.visible,
        );
      },
      icon: menuIcons["Show Reasoning"],
    });
  }

  const mainMenuItems = [...extraMenuItems, ...menuItems];
  const separatorAfter = ["Configure Eval", "Delete Column", "Sort Descending"];

  const resetIndex = mainMenuItems.findIndex(
    (item) => item.name === "Reset Columns",
  );
  if (resetIndex !== -1) {
    const originalReset = mainMenuItems[resetIndex];
    mainMenuItems[resetIndex] = {
      ...originalReset,
      action: () => {
        // Reset column state
        params?.api?.resetColumnState();

        // Collapse all column groups
        params?.api?.forEachNode((node) => {
          if (node.isSelected && node.group) {
            node.setExpanded(false);
          }
        });

        // Collapse groups by setting openByDefault to false
        const columnDefs = params?.api?.getColumnDefs() || [];
        columnDefs.forEach((colDef) => {
          if (colDef.children) {
            params?.api?.setColumnGroupOpened(colDef.id, false);
          }
        });
      },
    };
  }

  return reorderMenuList(mainMenuItems, newMenuOrder, separatorAfter);
};

const TestDetailColumnTypes = {
  EVALUATION: "evaluation",
  SCENARIO_DATASET_COLUMN: "scenario_dataset_column",
  TOOL_EVALUATION: "tool_evaluation",
};

const TestDetailGroupingColumns = [
  TestDetailColumnTypes.SCENARIO_DATASET_COLUMN,
  TestDetailColumnTypes.EVALUATION,
  TestDetailColumnTypes.TOOL_EVALUATION,
];

const formatValues = (id) => {
  if (id === "timestamp") {
    return ({ value }) =>
      value ? format(new Date(value), "yyyy-MM-dd HH:mm:ss") : "-";
  } else if (id === "response_time") {
    return ({ value }) => {
      return value ? `${value}s` : "-";
    };
  } else if (id === "latency") {
    return ({ value }) => {
      return value ? `${value}ms` : "-";
    };
  } else if (id === "agent_talk_percentage") {
    return ({ value }) => {
      const num = value !== "" && value != null ? Number(value) : NaN;
      return Number.isFinite(num) ? `${Math.round(num)}%` : "-";
    };
  } else {
    return null;
  }
};

const getCellRenderer = (col) => {
  if (col.id === "call_details") {
    return CallDetailCellRenderer;
  } else if (col.id === "overall_score") {
    return ScoreCellRenderer;
  }
  // else if (col.id === "endedReason") {
  //   return EndedReasonCellRenderer;   //removed as per  the  discussion with team
  // }
  else if (col.type === TestDetailColumnTypes.EVALUATION) {
    return EvalCellRenderer;
  } else if (col.type === TestDetailColumnTypes.SCENARIO_DATASET_COLUMN) {
    return ScenarioCellRenderer;
  } else if (col.type === TestDetailColumnTypes.TOOL_EVALUATION) {
    return ToolEvaluationCellRenderer;
  } else {
    return null;
  }
};

const getCellStyle = (col) => {
  if (TestDetailGroupingColumns.includes(col.type)) {
    return {
      padding: "0px",
    };
  }
};

const getValueSelector = (col) => {
  if (col.id === "service_provider_call_id") {
    return (params) => params.data?.service_provider_call_id;
  } else if (col.id === "customer_call_id") {
    return (params) => params.data?.customer_call_id;
  } else if (col.type === TestDetailColumnTypes.EVALUATION) {
    return (params) => {
      const evalData = params.data?.eval_metrics?.[col.id] || {};
      return {
        ...evalData,
        overall_status: params.data?.overall_status,
        call_status: params.data?.status,
      };
    };
  } else if (col.type === TestDetailColumnTypes.SCENARIO_DATASET_COLUMN) {
    return (params) => params.data?.scenario_columns?.[col.id];
  } else if (col.type === TestDetailColumnTypes.TOOL_EVALUATION) {
    return (params) => params.data?.tool_outputs?.[col.column_name];
  } else if (col.id === "call_details") {
    return (params) => {
      return {
        transcript: params.data?.transcript,
        type: params.data?.type,
        customer_name: params?.data?.customer_name,
        status: params?.data?.status,
        ended_reason: params?.data?.ended_reason,
        scenario: params?.data?.scenario,
        duration: params?.data?.duration,
        agent_definition_used_name: params?.data?.agent_definition_used_name,
        simulator_agent_name: params?.data?.simulator_agent_name,
        call_type: params?.data?.call_type,
        turn_count: params?.data?.turn_count,
        simulation_call_type: params?.data?.simulation_call_type,
        start_time: params?.data?.timestamp,
        call_id: params?.data?.id,
        phone_number: params?.data?.phone_number,
        provider: params?.data?.provider,
      };
    };
  } else if (col.id === "latency") {
    return (params) => params.data?.avg_agent_latency;
  }
  //  else if (col.id === "endedReason") {
  //   return (params) => params.data?.endedReason || "-";
  // }
};

const mapFieldToName = (field) => {
  if (field === "overall_score") {
    return "CSAT";
  }
  if (field === "latency") {
    return "Agent Latency (ms)";
  }
  return null;
};

export const getTestRunDetailGridColumnDefs = (columnOrder) => {
  if (!columnOrder) {
    return [];
  }

  const columns = columnOrder;

  // Group columns by type
  const scenarioDatasetColumns = columns.filter(
    (c) => c.type === TestDetailColumnTypes.SCENARIO_DATASET_COLUMN,
  );
  const evaluationColumns = columns.filter(
    (c) => c.type === TestDetailColumnTypes.EVALUATION,
  );
  const toolEvaluationColumns = columns.filter(
    (c) => c.type === TestDetailColumnTypes.TOOL_EVALUATION,
  );

  const otherColumns = columns.filter(
    (c) => !TestDetailGroupingColumns.includes(c.type),
  );

  // Helper function to create column definition
  const createColumnDef = (c) => ({
    headerName: mapFieldToName(c.id) || c.column_name,
    field: c.id,
    valueFormatter: formatValues(c.id),
    cellRenderer: getCellRenderer(c),
    cellStyle: getCellStyle(c),
    valueGetter: getValueSelector(c),
    isVisible: true,
    id: c.id,
    mainMenuItems: getMainMenuItems,
    type: c.type,
    width: c?.id === "call_details" ? 300 : undefined,
    headerComponent: ColumnCellRenderer,
  });

  const createReasonColumnDef = (c) => {
    return {
      headerName: `${c?.column_name}_reason`,
      field: `${c?.id}_reason`,
      cellRenderer: EvalReasonCellRenderer,
      valueGetter: (params) => {
        const evalData = params.data?.eval_metrics?.[c.id] || {};
        return {
          ...evalData,
          overall_status: params.data?.overall_status,
          call_status: params.data?.status,
        };
      },
      cellStyle: { display: "block" },
      isVisible: false,
      hide: true,
      id: `${c?.id}_reason`,
      colId: `${c?.id}_reason`,
      width: 400,
      headerComponent: ColumnCellRenderer,
    };
  };

  const createToolReasonColumnDef = (c) => {
    return {
      headerName: `${c?.column_name}_reason`,
      field: `${c?.column_name}_reason`,
      cellRenderer: ToolReasonCellRenderer,
      valueGetter: (params) => params.data?.tool_outputs?.[c.column_name],
      cellStyle: { display: "block" },
      isVisible: false,
      hide: true,
      id: `${c?.column_name}_reason`,
      colId: `${c?.column_name}_reason`,
      width: 400,
      headerComponent: ColumnCellRenderer,
    };
  };

  const createToolColumnDef = (c) => ({
    headerName: mapFieldToName(c.id) || c.column_name,
    field: c.column_name,
    valueFormatter: formatValues(c.id),
    cellRenderer: getCellRenderer(c),
    cellStyle: getCellStyle(c),
    valueGetter: getValueSelector(c),
    isVisible: true,
    id: c.column_name,
    mainMenuItems: getMainMenuItems,
    type: c.type,
    headerComponent: ColumnCellRenderer,
  });
  const config = [];

  // Add other columns (ungrouped)
  config.push(...otherColumns.map(createColumnDef));

  // Add scenario dataset columns group (deduplicated by column name across scenarios)
  if (scenarioDatasetColumns.length > 0) {
    // Group columns by name to merge duplicates from different scenarios
    const columnsByName = new Map();
    scenarioDatasetColumns.forEach((col) => {
      const name = col.column_name;
      if (!columnsByName.has(name)) {
        columnsByName.set(name, []);
      }
      columnsByName.get(name).push(col);
    });

    const deduplicatedColumns = Array.from(columnsByName.values()).map(
      (cols) => {
        const primary = cols[0];
        if (cols.length === 1) {
          return createColumnDef(primary);
        }
        // Merge: use primary column def but override valueGetter to check all UUIDs
        const colIds = cols.map((c) => c.id);
        const def = createColumnDef(primary);
        def.valueGetter = (params) => {
          for (const id of colIds) {
            const val = params.data?.scenario_columns?.[id];
            if (val !== undefined && val !== null && val !== "") return val;
          }
          return null;
        };
        return def;
      },
    );

    config.push({
      headerName: "Scenario Information",
      isVisible: true,
      id: TestDetailColumnTypes.SCENARIO_DATASET_COLUMN,
      children: deduplicatedColumns,
    });
  }

  // Add evaluation columns group
  if (evaluationColumns.length > 0) {
    config.push({
      headerName: "Evaluation Metrics",
      isVisible: true,
      id: TestDetailColumnTypes.EVALUATION,
      children: evaluationColumns.reduce((acc, curr) => {
        acc.push(createColumnDef(curr));
        acc.push(createReasonColumnDef(curr));
        return acc;
      }, []),
    });
  }

  if (toolEvaluationColumns.length > 0) {
    config.push({
      headerName: "Tool Evaluation",
      isVisible: true,
      id: TestDetailColumnTypes.TOOL_EVALUATION,
      children: toolEvaluationColumns.reduce((acc, curr) => {
        acc.push(createToolColumnDef(curr));
        acc.push(createToolReasonColumnDef(curr));
        return acc;
      }, []),
    });
  }

  return config;
};

export const getTestRunDetailColumnQuery = (
  executionId,
  pageNumber,
  debouncedSearchQuery,
  validatedFilters,
  pageSize,
) => {
  return {
    queryKey: [
      "test-execution-detail-list",
      executionId,
      debouncedSearchQuery,
      validatedFilters,
      pageNumber + 1,
    ],
    queryFn: () =>
      axios.get(endpoints.testExecutions.list(executionId), {
        params: {
          page: pageNumber + 1,
          limit: 30,
          search: debouncedSearchQuery,
          filters: JSON.stringify(validatedFilters?.map(objectCamelToSnake)),
          ...(pageSize && { limit: pageSize }),
        },
      }),
  };
};

export const useGetTestRunDetailGridColumns = (executionId) => {
  const { data: testExecutions } = useQuery({
    ...getTestRunDetailColumnQuery(executionId, 0, "", []),
    enabled: false,
    select: (data) => data.data,
  });
  return testExecutions?.column_order ?? [];
};

const ExcludeColumnsDatatypes = [
  "json",
  "image",
  "document",
  "audio",
  "boolean",
  "float",
  "integer",
  "datetime",
  "array",
];

export const getTestRunDetailFilterDefinition = (columns) => {
  const evalColumns = [];
  const scenarioColumns = [];

  columns.forEach((col) => {
    if (col.type === "evaluation") {
      evalColumns.push(col);
    } else if (col.type === "scenario_dataset_column") {
      scenarioColumns.push(col);
    }
  });

  const existingFilters = [
    {
      propertyName: "Call Type",
      propertyId: "call_type",
      filterType: {
        type: "option",
        options: [
          {
            label: "Outbound",
            value: "Outbound",
          },
          {
            label: "Inbound",
            value: "Inbound",
          },
        ],
      },
    },
    {
      propertyName: "Status",
      propertyId: "status",
      filterType: {
        type: "option",
        options: [
          { label: "Completed", value: "completed" },
          { label: "Failed", value: "failed" },
          { label: "Pending", value: "pending" },
          { label: "Registered", value: "registered" },
          { label: "Ongoing", value: "ongoing" },
          { label: "Cancelled", value: "cancelled" },
          { label: "Analyzing", value: "analyzing" },
        ],
      },
    },
    {
      propertyName: "Timestamp",
      propertyId: "timestamp",
      filterType: {
        type: "date",
      },
    },
    {
      propertyId: "overall_score",
      propertyName: "Overall Score",
      filterType: {
        type: "number",
      },
    },
  ];

  const evalFilters = [];
  const scenarioFilters = [];

  evalColumns.forEach((col) => {
    let filterType = {};
    let extra = {};
    if (col.eval_config?.output === "Pass/Fail") {
      filterType = {
        type: "option",
        options: [
          { label: "Passed", value: "Passed" },
          { label: "Failed", value: "Failed" },
        ],
      };
    } else if (col.eval_config?.output === "score") {
      filterType = {
        type: "number",
      };
    } else if (col.eval_config?.output === "choices") {
      filterType = {
        type: "text",
      };
      extra = { defaultFilter: "contains" };
    }

    evalFilters.push({
      propertyId: col.id,
      propertyName: col.column_name,
      filterType: filterType,
      ...extra,
    });
  });

  scenarioColumns.forEach((col) => {
    if (ExcludeColumnsDatatypes.includes(col.data_type)) return;

    let filterType = {
      type: "text",
    };
    let extra = {};
    if (col.data_type === "text") {
      filterType = {
        type: "text",
      };
      extra = { defaultFilter: "contains" };
    } else if (col.data_type === "integer" || col.data_type === "float") {
      filterType = {
        type: "number",
      };
    } else if (col.data_type === "boolean") {
      filterType = {
        type: "boolean",
      };
    } else if (col.data_type === "datetime") {
      filterType = {
        type: "date",
      };
    }
    scenarioFilters.push({
      propertyId: `scenario_${col.scenario_id}_dataset_${col.id}`,
      propertyName: col.column_name,
      filterType: filterType,
      ...extra,
    });
  });

  if (evalFilters.length > 0) {
    existingFilters.push({
      propertyName: "Evaluation Metrics",
      stringConnector: "is",
      dependents: evalFilters,
    });
  }

  if (scenarioFilters.length > 0) {
    existingFilters.push({
      propertyName: "Scenario Information",
      stringConnector: "is",
      dependents: scenarioFilters,
    });
  }

  const annotationMetricDef = getAnnotationMetricFilterDefinition(columns);
  existingFilters.push(...annotationMetricDef);

  return existingFilters;
};
// Status constants
export const TestRunExecutionStatus = {
  PENDING: "pending",
  RUNNING: "running",
  COMPLETED: "completed",
  FAILED: "failed",
  CANCELLED: "cancelled",
  EVALUATING: "evaluating",
  CANCELLING: "cancelling",
  QUEUED: "queued",
  ONGOING: "ongoing",
  ANALYZING: "analyzing",
};

export const TestRunLoadingStatus = [
  TestRunExecutionStatus.PENDING,
  TestRunExecutionStatus.RUNNING,
  TestRunExecutionStatus.EVALUATING,
  TestRunExecutionStatus.CANCELLING,
];

// Per-call statuses for which eval data may still arrive. Used by cell
// renderers to show a skeleton loader instead of a "-" dash while a call is
// still in flight.
export const CallExecutionLoadingStatus = [
  TestRunExecutionStatus.PENDING,
  TestRunExecutionStatus.RUNNING,
  TestRunExecutionStatus.QUEUED,
  TestRunExecutionStatus.ONGOING,
  TestRunExecutionStatus.EVALUATING,
  TestRunExecutionStatus.ANALYZING,
  "registered",
];

export const TestRunErrorStatus = [
  TestRunExecutionStatus.FAILED,
  TestRunExecutionStatus.CANCELLED,
];

// Agent-specific metric keys - consistent naming structure
const AGENT_METRICS = {
  VOICE: [
    "avg_score",
    "avg_agent_latency",
    "avg_bot_wpm",
    "avg_stop_time_after_interruption",
    "avg_turn_count",
    "agent_talk_percentage",
    "customer_talk_percentage",
  ],
  CHAT: [
    "avg_total_tokens",
    "avg_input_tokens",
    "avg_output_tokens",
    "avg_chat_latency_ms",
    "avg_turn_count",
    "avg_csat_score",
  ],
};

const DETAILS_KEYS = {
  VOICE: ["total_calls", "connected_calls", "calls_connected_percentage"],
  CHAT: ["total_calls", "connected_calls", "calls_connected_percentage"],
};

// Keys to exclude from processing
const IGNORED_KEYS = [
  "scenario_graphs",
  "calls_attempted",
  "failed_calls",
  "avg_response",
  "avg_user_interruption_count",
  "avg_ai_interruption_rate",
  "avg_user_wpm",
  "avg_talk_ratio",
  "avg_user_interruption_rate",
  "avg_ai_interruption_count",
  "agent_type",
  "total_duration",
  "is_inbound",
];

function calculatePercentage(value, obj) {
  const total = Object.entries(obj)
    .filter(([k]) => k !== "choices")
    .reduce((sum, [, v]) => sum + (typeof v === "number" ? v : 0), 0);

  return total === 0 ? 0 : Math.round((value / total) * 100);
}

export const extractKpis = (data, agentType) => {
  const systemMetrics = {};
  const evalMetrics = {};
  const callDetails = {};
  const deterministicEvals = [];

  const isVoice = agentType === AGENT_TYPES.VOICE;

  const relevantMetrics = isVoice ? AGENT_METRICS.VOICE : AGENT_METRICS.CHAT;
  // Ignore keys conditionally based on agentType
  const newIgnored = isVoice
    ? [...IGNORED_KEYS, ...AGENT_METRICS.CHAT, ...DETAILS_KEYS.CHAT]
    : [...IGNORED_KEYS, ...AGENT_METRICS.VOICE, ...DETAILS_KEYS.VOICE];
  const detailsKeys = isVoice ? DETAILS_KEYS.VOICE : DETAILS_KEYS.CHAT;

  // Process data keys. Iterate canonical snake_case keys only — the axios
  // interceptor adds enumerable camelCase aliases on every response, and
  // a plain `Object.keys` walk would double-count every metric AND slip
  // alias keys past the snake_case filter lists (IGNORED_KEYS /
  // AGENT_METRICS), causing entries like "scenarioGraphs" and "avgScore"
  // to render as extra eval cards.
  canonicalKeys(data || {}).forEach((key) => {
    // Categorize by key type
    if (relevantMetrics.includes(key)) {
      systemMetrics[key] = data[key];
    } else if (detailsKeys.includes(key)) {
      callDetails[key] = data[key];
    } else if (newIgnored.includes(key)) {
      return;
    } else if (typeof data[key] !== "object") {
      evalMetrics[key] = data[key];
    } else {
      deterministicEvals.push({
        id: key,
        title: getLabel(key),
        data: (data[key]?.choices || [])
          .map((choice) => {
            const value = data[key][choice] ?? 0;
            return value === 0
              ? null
              : {
                  name: choice,
                  value: calculatePercentage(value, data[key]),
                };
          })
          .filter(Boolean),
      });
    }
  });

  if (
    systemMetrics?.agent_talk_percentage !== undefined &&
    systemMetrics?.customer_talk_percentage !== undefined
  ) {
    systemMetrics["talk_ratio"] =
      `${Math.round(systemMetrics?.agent_talk_percentage)}/${Math.round(systemMetrics?.customer_talk_percentage)}`;
    delete systemMetrics?.agent_talk_percentage;
    delete systemMetrics?.customer_talk_percentage;
  }
  if (
    systemMetrics?.connected_calls !== undefined &&
    systemMetrics?.calls_attempted !== undefined
  ) {
    systemMetrics.percentage_attempted = Math.round(
      (systemMetrics?.connected_calls / systemMetrics?.calls_attempted) * 100,
    );
  }

  return {
    systemMetrics,
    evalMetrics,
    callDetails,
    deterministicEvals,
  };
};

export const RerunTestOptions = {
  EVAL_ONLY: "eval_only",
  CALL_AND_EVAL: "call_and_eval",
};

export const ToolEvalStatus = {
  RUNNING: "running",
  FAILED: "failed",
  COMPLETED: "completed",
};

export function replaceToolOutputIdsWithNames(data) {
  return data.map((call) => {
    if (call.tool_outputs && typeof call.tool_outputs === "object") {
      const newToolOutputs = {};

      Object.entries(call.tool_outputs).forEach(([id, details]) => {
        const name = details?.name || id;
        newToolOutputs[name] = details;
      });

      call.tool_outputs = newToolOutputs;
    }
    return call;
  });
}

export const getScenarioColumnIds = (columnOrder) => {
  const scenarioColumns = columnOrder.reduce((acc, curr) => {
    if (curr.type === TestDetailColumnTypes.SCENARIO_DATASET_COLUMN) {
      acc.push(curr.scenario_id);
    }
    return acc;
  }, []);
  return Array.from(new Set(scenarioColumns));
};

export const getSelectedCallExecutionIds = () => {
  const selectedFixableRecommendations =
    useTestDetailStore.getState().selectedFixableRecommendations;
  const selectedNonFixableRecommendations =
    useTestDetailStore.getState().selectedNonFixableRecommendations;

  const selectedCallExecutionIdsSet = new Set();

  selectedFixableRecommendations.forEach((recommendation) => {
    recommendation.callExecutionIds.forEach((callExecutionId) => {
      selectedCallExecutionIdsSet.add(callExecutionId);
    });
  });
  selectedNonFixableRecommendations.forEach((recommendation) => {
    recommendation.callExecutionIds.forEach((callExecutionId) => {
      selectedCallExecutionIdsSet.add(callExecutionId);
    });
  });

  return Array.from(selectedCallExecutionIdsSet);
};

export const getSelectedCallExecutionIdsFilter = () => {
  const selectedCallExecutionIds = getSelectedCallExecutionIds();

  if (selectedCallExecutionIds.length > 0) {
    // if we have any selected call execution ids from fix my agent we need to add it to filter
    return [
      {
        column_id: "call_execution_id",
        filter_config: {
          filter_op: "in",
          filter_type: "list",
          filter_value: selectedCallExecutionIds,
        },
      },
    ];
  }

  return [];
};
export const getLoadingStateWithRespectiveStatus = (
  status,
  simulationCallType,
) => {
  const normalizedStatus = status?.toLowerCase();
  const callLabel = simulationCallType === AGENT_TYPES.CHAT ? "Chat" : "Call";

  const pendingStatuses = [
    TestRunExecutionStatus.PENDING,
    TestRunExecutionStatus.QUEUED,
  ];

  if (pendingStatuses.includes(normalizedStatus)) {
    return {
      isCallInProgress: true,
      message: `${callLabel} has not been picked up yet`,
    };
  }

  if (normalizedStatus === TestRunExecutionStatus.ONGOING) {
    return {
      isCallInProgress: true,
      message: `${callLabel} is in progress`,
    };
  }

  return {
    isCallInProgress: false,
    message: null,
  };
};

export const columnOptions = [
  { key: "latency", label: "Latency", visible: true },
  { key: "tokens", label: "Tokens", visible: true },
  { key: "cost", label: "Cost", visible: true },
  { key: "evals", label: "Evals", visible: true },
  { key: "annotations", label: "Annotations", visible: true },
  { key: "events", label: "Events", visible: true },
];

// tabs.ts

export const getTabsBasedOnAgentType = ({ agentType, testId, executionId }) => {
  const tabs = [
    {
      id: "runs",
      title: agentType === AGENT_TYPES.CHAT ? "Chat Details" : "Call Details",
      path: `/dashboard/simulate/test/${testId}/${executionId}/call-details`,
      icon:
        agentType === AGENT_TYPES.CHAT
          ? "/assets/icons/ic_chat_single.svg"
          : "/assets/icons/usage-summary/ic_phone.svg",
    },
    {
      id: "analytics",
      title: "Analytics",
      path: `/dashboard/simulate/test/${testId}/${executionId}/analytics`,
      icon: "/assets/icons/usage-summary/ic_bar_signal.svg",
    },
    {
      id: "optimization_runs",
      title: "Optimization Runs",
      path: `/dashboard/simulate/test/${testId}/${executionId}/optimization_runs`,
      icon: "/assets/icons/navbar/ic_optimize.svg",
    },
  ];

  return tabs;
};

export const getCompareBaselineTooltipTitle = (status) => {
  if (status === TestRunExecutionStatus.ONGOING) {
    return "This chat is in progress. Please wait for it to complete before comparing with baseline chat.";
  }
  if (status === TestRunExecutionStatus.PENDING) {
    return "This chat has not been picked up yet. Please wait for it to be picked up before comparing with baseline chat.";
  }
  if (status === TestRunExecutionStatus.FAILED) {
    return "This chat has failed. Please try again.";
  }
  if (status === TestRunExecutionStatus.CANCELLED) {
    return "This chat has been cancelled. Please try again.";
  }
  return "";
};

export const useFixMyAgentBlocked = (executionId) => {
  const { data: kpis, isPending } = useKpis(executionId);
  const { status } = useTestExecutionStore();

  const { callDetails } = useMemo(() => {
    return extractKpis(kpis, kpis?.agent_type);
  }, [kpis]);

  if (isPending || status === null) {
    return {
      disabled: true,
      reason: "",
    };
  }

  // the test run is still running and we should ke the fix my agent blocked
  if (TestRunLoadingStatus.includes(status)) {
    return {
      disabled: true,
      reason:
        "The test run is still running, you can optimize your agent after the test run is completed",
    };
  } else if (
    status === TestRunExecutionStatus.COMPLETED ||
    status === TestRunExecutionStatus.CANCELLED
  ) {
    if (callDetails.connectedCalls < 15) {
      return {
        disabled: true,
        reason: "You need at least 15 connected calls to optimize your agent",
      };
    }
  }

  return {
    disabled: false,
    reason: "",
  };
};

export const TEST_RUN_DETAIL_DEFAULT_PLACEHOLDER_COLUMNS = [
  {
    headerComponent: LoadingHeader,
    field: "loading1",
    flex: 1,
    minWidth: 150,
  },
  {
    headerComponent: LoadingHeader,
    field: "loading2",
    flex: 1,
    minWidth: 150,
  },
  {
    headerComponent: LoadingHeader,
    field: "loading3",
    flex: 1,
    minWidth: 150,
  },
  {
    headerComponent: LoadingHeader,
    field: "loading4",
    flex: 1,
    minWidth: 150,
  },
  {
    headerComponent: LoadingHeader,
    field: "loading5",
    flex: 1,
    minWidth: 150,
  },
];
