import React from "react";
import { Box } from "@mui/material";
import StatusChip from "src/components/custom-status-chip/CustomStatusChip";
import VersionCellRenderer from "src/sections/workbench/createPrompt/Metrics/CellRenderers/VersionCellRenderer";
import LabelCellRenderer from "src/sections/workbench/createPrompt/Metrics/CellRenderers/LabelCellRenderer";
import { interpolateColorTokenBasedOnScore } from "src/utils/utils";
import { normalizeEvalCellValue } from "src/sections/develop-detail/DataTab/common";
import { RENDERER_CONFIG, CELL_TYPES } from "./common";
import {
  EvaluationCell,
  AnnotationCell,
  LatencyCell,
  CostCell,
  TokenCell,
  DefaultCell,
  TimestampCell,
  TagsCell,
  ObservationLevelsCell,
} from "./index";
import { useNavigationHandlers } from "./useNavigationHandlers";

const CustomTraceRenderer = (params) => {
  const column = params.colDef.col;
  const colId = column?.id;
  const value = params.value;
  const data = params.data;

  const alignRight = RENDERER_CONFIG.alignRightItems.includes(column?.name);
  const isReason = column?.sourceField === "reason";
  const isEval = column?.groupBy === "Evaluation Metrics" && !isReason;
  const isAnnotation = column?.groupBy === "Annotation Metrics";

  const projectId = data?.project_id;
  const traceIdFromRow = data?.trace_id;
  const traceIdFromCell = value;

  const { handleTraceClick, handleSpanClick } = useNavigationHandlers(
    projectId,
    traceIdFromRow,
  );

  // Get background color for evaluation cells. LLM evals may pass {score, choice}
  // (object or Python-repr string) — extract the numeric score before color lookup.
  let evalNumericScore = NaN;
  if (isEval) {
    const normalized = normalizeEvalCellValue(value);
    evalNumericScore =
      normalized && typeof normalized === "object" && !Array.isArray(normalized)
        ? typeof normalized.score === "number"
          ? normalized.score
          : NaN
        : parseFloat(normalized);
  }
  const { bgcolor: backgroundColor = "", color = "" } = isEval
    ? interpolateColorTokenBasedOnScore(evalNumericScore, 100) || {}
    : {};

  // Special column renderers
  if (colId === CELL_TYPES.PROMPT_VERSION) {
    return (
      <VersionCellRenderer
        value={value}
        applyQuickFilters={params.applyQuickFilters}
        column={column}
      />
    );
  }

  if (colId === CELL_TYPES.LABELS) {
    return <LabelCellRenderer value={value} />;
  }
  if (colId === CELL_TYPES.STATUS && value) {
    return (
      <Box paddingX={1.5}>
        <StatusChip label={value} status={value} />
      </Box>
    );
  }

  if (colId === "observation_levels") {
    return <ObservationLevelsCell data={data} />;
  }

  if (RENDERER_CONFIG.timestampColumns?.includes(colId)) {
    return <TimestampCell value={value} />;
  }

  if (RENDERER_CONFIG.tagColumns?.includes(colId)) {
    return <TagsCell value={value} />;
  }

  if (isEval && column?.outputType === "Pass/Fail") {
    return (
      <div style={{ height: "100%", width: "100%", padding: 0, margin: 0 }}>
        <EvaluationCell value={value} column={column} />
      </div>
    );
  }

  if (isEval) {
    return <EvaluationCell value={value} column={column} />;
  }

  if (isAnnotation) {
    return <AnnotationCell value={value} column={column} />;
  }

  if (RENDERER_CONFIG.latencyColumns.includes(colId)) {
    return <LatencyCell value={value} />;
  }

  if (RENDERER_CONFIG.costColumns.includes(colId)) {
    return <CostCell value={value} data={data} />;
  }

  if (RENDERER_CONFIG.tokenColumns.includes(colId)) {
    return <TokenCell value={value} data={data} />;
  }

  // Default cell renderer
  return (
    <DefaultCell
      value={value}
      column={column}
      backgroundColor={backgroundColor}
      color={color}
      alignRight={alignRight}
      applyQuickFilters={params.applyQuickFilters}
      onCellClick={() => {
        if (colId === CELL_TYPES.TRACE_ID) {
          handleTraceClick(traceIdFromCell);
        }
        if (colId === CELL_TYPES.SPAN_ID) {
          handleSpanClick(value);
        }
      }}
    />
  );
};

export default CustomTraceRenderer;
