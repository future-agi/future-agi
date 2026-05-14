import React, { useMemo, useCallback } from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import WidgetRenderer from "./WidgetRenderer";
import { resolveBindings } from "./resolveBindings";
import useDynamicAnalysis from "./useDynamicAnalysis";
import useImagineStore from "./useImagineStore";

const DEFAULT_ROW_HEIGHT = 260;

/**
 * Grid canvas that renders multiple widgets from config.
 * Uses CSS Grid with 12 columns — each widget specifies position.
 *
 * Widgets with `dataBinding` are resolved against the current traceData,
 * making saved views dynamic across traces.
 */
export default function WidgetCanvas({
  widgets,
  traceData,
  emptyState,
  chatRef,
  traceId,
  projectId,
}) {
  // Cache lookup function for dynamic analysis widgets
  const analysisCache = useImagineStore((s) => s.analysisCache);
  const getAnalysisCache = useMemo(
    () =>
      traceId
        ? (widgetId) =>
            analysisCache[`${traceId}::${widgetId}`]?.content || null
        : null,
    [traceId, analysisCache],
  );

  // Resolve data bindings — converts dataBinding → config using live trace data
  const resolvedWidgets = useMemo(
    () => resolveBindings(widgets, traceData, getAnalysisCache),
    [widgets, traceData, getAnalysisCache],
  );

  // Watch for Falcon WS updates and cache analysis results
  const runAnalysis = useDynamicAnalysis(
    resolvedWidgets,
    traceData,
    chatRef,
    traceId,
    projectId,
  );

  // Handler for "Run Analysis" / "Rerun" button on dynamic widgets
  const handleRunAnalysis = useCallback(
    (widget) => runAnalysis(widget),
    [runAnalysis],
  );

  if (!resolvedWidgets?.length) {
    return (
      emptyState || (
        <Box
          sx={{
            height: "100%",
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            gap: 1.5,
            color: "text.disabled",
            px: 3,
          }}
        >
          <Iconify
            icon="mdi:creation-outline"
            width={48}
            sx={{ opacity: 0.3 }}
          />
          <Typography fontSize={14} fontWeight={500} color="text.secondary">
            Describe what you want to visualize
          </Typography>
          <Typography
            fontSize={12}
            color="text.disabled"
            textAlign="center"
            maxWidth={320}
          >
            Falcon will analyze your trace data and build interactive
            visualizations
          </Typography>
        </Box>
      )
    );
  }

  return (
    <Box
      sx={{
        display: "grid",
        gridTemplateColumns: "repeat(12, 1fr)",
        gridAutoRows: `minmax(${DEFAULT_ROW_HEIGHT}px, auto)`,
        gap: 1.5,
        p: 1.5,
        overflow: "auto",
        height: "100%",
      }}
    >
      {resolvedWidgets.map((widget) => {
        const pos = widget.position || {};
        const colStart = (pos.col ?? 0) + 1;
        const colSpan = pos.colSpan ?? 12;

        return (
          <Box
            key={widget.id}
            sx={{
              gridColumn: `${colStart} / span ${colSpan}`,
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 1,
              bgcolor: "background.paper",
              overflow: "hidden",
              minHeight: 0,
              display: "flex",
              flexDirection: "column",
              transition: "box-shadow 150ms",
              "&:hover": {
                boxShadow: "0 2px 8px rgba(0,0,0,0.06)",
              },
            }}
          >
            <WidgetRenderer
              widget={widget}
              traceData={traceData}
              onRunAnalysis={handleRunAnalysis}
            />
          </Box>
        );
      })}
    </Box>
  );
}

WidgetCanvas.propTypes = {
  widgets: PropTypes.arrayOf(
    PropTypes.shape({
      id: PropTypes.string.isRequired,
      type: PropTypes.string.isRequired,
    }),
  ),
  traceData: PropTypes.object,
  emptyState: PropTypes.node,
  chatRef: PropTypes.object,
  traceId: PropTypes.string,
  projectId: PropTypes.string,
};
