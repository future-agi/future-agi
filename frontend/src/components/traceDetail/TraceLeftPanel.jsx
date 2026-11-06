import React, { useState, useCallback, useMemo, useRef } from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import AgentGraph from "src/sections/projects/LLMTracing/GraphSection/AgentGraph";
import TraceTreeV2 from "./TraceTreeV2";
import SpanTreeTimeline from "./SpanTreeTimeline";
import { buildTraceGraph } from "./buildTraceGraph";

// Memoized agent graph from span tree — uses buildTraceGraph for grouping.
// Click cycles through spans belonging to the clicked node.
export const TraceAgentGraph = React.memo(({ spans, onSelectSpan }) => {
  const graphData = useMemo(() => buildTraceGraph(spans), [spans]);
  const clickIndexRef = useRef({});

  const handleNodeClick = useCallback(
    (nodeId) => {
      const spanIds = graphData?.nodeToSpanIds?.[nodeId];
      if (!spanIds?.length || !onSelectSpan) return;
      const prev = clickIndexRef.current[nodeId] || 0;
      const idx = prev < spanIds.length ? prev : 0;
      onSelectSpan(spanIds[idx]);
      clickIndexRef.current[nodeId] = (idx + 1) % spanIds.length;
    },
    [graphData, onSelectSpan],
  );

  if (!graphData?.nodes?.length) return null;

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        overflow: "hidden",
      }}
    >
      <Typography
        variant="caption"
        fontWeight={600}
        color="text.secondary"
        sx={{
          px: 1.5,
          py: 0.5,
          flexShrink: 0,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        Agent Graph
      </Typography>
      <Box sx={{ flex: 1, minHeight: 0 }}>
        <AgentGraph
          data={graphData}
          isLoading={false}
          onNodeClick={handleNodeClick}
          direction="TB"
        />
      </Box>
    </Box>
  );
});

TraceAgentGraph.displayName = "TraceAgentGraph";
TraceAgentGraph.propTypes = {
  spans: PropTypes.array,
  onSelectSpan: PropTypes.func,
};

// Left panel with vertical resizable split: Tree/Timeline (top) + Agent Graph (bottom).
const LeftPanelSplit = ({
  leftPanelWidth,
  viewMode,
  spans,
  selectedSpanId,
  onSelectSpan,
  visibleMetrics,
  setVisibleMetrics,
  showAgentGraph,
  onRefresh,
}) => {
  const [topPercent, setTopPercent] = useState(showAgentGraph ? 50 : 100);
  const containerRef = useRef(null);

  const handleVDragStart = useCallback(
    (e) => {
      e.preventDefault();
      const startY = e.clientY;
      const startPct = topPercent;
      const container = containerRef.current;
      if (!container) return;
      const containerH = container.offsetHeight;

      const onMove = (moveE) => {
        const diff = moveE.clientY - startY;
        const newPct = startPct + (diff / containerH) * 100;
        setTopPercent(Math.min(85, Math.max(25, newPct)));
      };
      const onUp = () => {
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
      };
      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
    },
    [topPercent],
  );

  const showGraph = showAgentGraph && spans?.length > 0;

  return (
    <Box
      ref={containerRef}
      sx={{
        width: leftPanelWidth != null ? `${leftPanelWidth}%` : "100%",
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
        borderRight: "1px solid",
        borderColor: "divider",
        flexShrink: 0,
      }}
    >
      {/* Top: Trace Tree / Timeline */}
      <Box
        sx={{
          height: showGraph ? `${topPercent}%` : "100%",
          overflow: "auto",
          flexShrink: 0,
        }}
      >
        {viewMode === "tree" ? (
          <TraceTreeV2
            spans={spans}
            selectedSpanId={selectedSpanId}
            onSelectSpan={onSelectSpan}
            showMetrics={
              visibleMetrics?.latency ||
              visibleMetrics?.tokens ||
              visibleMetrics?.cost
            }
            visibleMetrics={visibleMetrics}
            onToggleMetrics={
              setVisibleMetrics
                ? () =>
                    setVisibleMetrics((prev) => {
                      const next = !(prev.latency || prev.tokens);
                      return { ...prev, latency: next, tokens: next };
                    })
                : undefined
            }
            onRefresh={onRefresh}
          />
        ) : (
          <SpanTreeTimeline
            spans={spans}
            selectedSpanId={selectedSpanId}
            onSelectSpan={onSelectSpan}
          />
        )}
      </Box>

      {/* Horizontal resizable divider */}
      {showGraph && (
        <Box
          onMouseDown={handleVDragStart}
          sx={{
            height: 6,
            cursor: "row-resize",
            flexShrink: 0,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            borderTop: "1px solid",
            borderBottom: "1px solid",
            borderColor: "divider",
            "&:hover": { bgcolor: "action.hover" },
          }}
        >
          <Iconify
            icon="solar:menu-dots-linear"
            width={16}
            color="text.disabled"
          />
        </Box>
      )}

      {/* Bottom: Agent Graph */}
      {showGraph && (
        <Box
          sx={{
            height: `${100 - topPercent}%`,
            overflow: "hidden",
            flexShrink: 0,
          }}
        >
          <TraceAgentGraph spans={spans} onSelectSpan={onSelectSpan} />
        </Box>
      )}
    </Box>
  );
};

LeftPanelSplit.propTypes = {
  leftPanelWidth: PropTypes.number,
  viewMode: PropTypes.string,
  spans: PropTypes.array,
  selectedSpanId: PropTypes.string,
  onSelectSpan: PropTypes.func,
  visibleMetrics: PropTypes.object,
  setVisibleMetrics: PropTypes.func,
  showAgentGraph: PropTypes.bool,
  onRefresh: PropTypes.func,
};

export default LeftPanelSplit;
