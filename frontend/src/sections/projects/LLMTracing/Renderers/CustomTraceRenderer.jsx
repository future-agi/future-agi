import React from "react";
import { Box, ButtonBase, Tooltip } from "@mui/material";
import { useNavigate } from "react-router-dom";
import StatusChip from "src/components/custom-status-chip/CustomStatusChip";
import StatusBadge from "src/components/status-badge";
import { paths } from "src/routes/paths";
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
  const navigate = useNavigate();
  const column = params.colDef.context?.sourceColumn;
  const colId = column?.id;
  const value = params.value;
  const data = params.data;

  const alignRight = RENDERER_CONFIG.alignRightItems.includes(column?.name);
  const isReason = column?.sourceField === "reason";
  const isEval = column?.groupBy === "Evaluation Metrics" && !isReason;
  const isAnnotation = column?.groupBy === "Annotation Metrics";

  // Span-level cells carry a single span's eval (Pass/Fail is 0 or 100);
  // trace/voice cells are aggregated (Pass/Fail arrives as an averaged rate).
  const isSpanLevel = params.context?.entityType === "span";

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
        <StatusChip
          label={value}
          status={value}
          // TH-4088: neutral status text on the span list measured below 4.5:1 against the
          // chip background with the shared `text.disabled` token. Only the span list opts
          // into the readable token; the trace list and other consumers are unchanged.
          neutralTextColor={isSpanLevel ? "text.secondary" : undefined}
        />
      </Box>
    );
  }

  if (colId === "issues") {
    const list = Array.isArray(value) ? value : [];
    if (!list.length) return null;
    const first = list[0];
    const countLabel =
      list.length === 1 ? "1 issue" : `${list.length} issues`;
    const tooltipText = list
      .map((iss, idx) => `${idx + 1}. ${iss.title || "Error Feed issue"}`)
      .join("\n");
    return (
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          height: "100%",
          px: 1,
        }}
      >
        <Tooltip
          title={<Box sx={{ whiteSpace: "pre-line" }}>{tooltipText}</Box>}
          arrow
          placement="top"
        >
          <ButtonBase
            onClick={(e) => {
              e.stopPropagation();
              if (first?.cluster_id) {
                navigate(paths.dashboard.errorFeed.detail(first.cluster_id));
              }
            }}
            sx={{ borderRadius: "4px" }}
          >
            <StatusBadge role="danger" label={countLabel} size="sm" />
          </ButtonBase>
        </Tooltip>
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
    return (
      <TagsCell
        value={value}
        traceId={data?.trace_id}
        spanId={data?.span_id}
        entityType={params.context?.entityType}
        canEditTags={params.context?.canEditTags}
        // This grid is AG-Grid server-side, not React Query, so the popover's
        // cache invalidation can't refresh it — pull the saved tags via the
        // grid api instead.
        onTagsUpdated={() => params.api?.refreshServerSide()}
      />
    );
  }

  if (isEval && column?.outputType === "Pass/Fail") {
    return (
      <div style={{ height: "100%", width: "100%", padding: 0, margin: 0 }}>
        <EvaluationCell
          value={value}
          column={column}
          isSpanLevel={isSpanLevel}
        />
      </div>
    );
  }

  if (isEval) {
    return (
      <EvaluationCell value={value} column={column} isSpanLevel={isSpanLevel} />
    );
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
        if (params.context?.disableCellNavigation) return;
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
