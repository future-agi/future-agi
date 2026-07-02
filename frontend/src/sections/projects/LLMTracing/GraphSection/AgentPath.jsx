/**
 * AgentPath — Sankey-like flow visualization showing span types with counts.
 * Each column is a "rank" in the execution flow (left → right).
 * Nodes have colored vertical bars proportional to span count.
 * Colored bands flow between connected nodes.
 */
import React, { useMemo, useRef, useState, useEffect } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Collapse,
  CircularProgress,
  IconButton,
  Typography,
  useTheme,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import FullscreenGraphDialog from "./FullscreenGraphDialog";

// Node type → color mapping (matches AgentGraph)
const TYPE_COLORS = {
  agent: {
    bar: "#c4b5fd",
    band: "#c4b5fd",
    text: "#7c3aed",
    icon: "mdi:robot-outline",
  },
  llm: { bar: "#93c5fd", band: "#93c5fd", text: "#2563eb", icon: "mdi:brain" },
  generation: {
    bar: "#93c5fd",
    band: "#93c5fd",
    text: "#2563eb",
    icon: "mdi:brain",
  },
  tool: {
    bar: "#86efac",
    band: "#86efac",
    text: "#16a34a",
    icon: "mdi:wrench-outline",
  },
  retriever: {
    bar: "#5eead4",
    band: "#5eead4",
    text: "#0d9488",
    icon: "mdi:magnify",
  },
  chain: {
    bar: "#f0abfc",
    band: "#f0abfc",
    text: "#c026d3",
    icon: "mdi:link-variant",
  },
  embedding: {
    bar: "#fdba74",
    band: "#fdba74",
    text: "#ea580c",
    icon: "mdi:vector-square",
  },
  guardrail: {
    bar: "#fca5a5",
    band: "#fca5a5",
    text: "#dc2626",
    icon: "mdi:shield-check-outline",
  },
  reranker: {
    bar: "#fca5a5",
    band: "#fca5a5",
    text: "#dc2626",
    icon: "mdi:sort-variant",
  },
  unknown: {
    bar: "#d1d5db",
    band: "#d1d5db",
    text: "#6b7280",
    icon: "mdi:help-circle-outline",
  },
};

const getColor = (type) =>
  TYPE_COLORS[type?.toLowerCase()] || TYPE_COLORS.unknown;

// ---------------------------------------------------------------------------
// Compute Sankey layout from graph data
// ---------------------------------------------------------------------------
const computeSankeyLayout = (graphData) => {
  if (!graphData?.nodes?.length || !graphData?.edges?.length) return null;

  const nodeMap = new Map();
  graphData.nodes.forEach((n) => {
    if (n.type !== "start" && n.type !== "end") {
      nodeMap.set(n.id, { ...n });
    }
  });

  // Build adjacency (excluding start/end sentinels)
  const outEdges = new Map(); // source -> [{target, count}]
  const inEdges = new Map(); // target -> [{source, count}]
  const targetSet = new Set();

  graphData.edges.forEach((e) => {
    if (e.isSelfLoop) return;
    if (!nodeMap.has(e.source) || !nodeMap.has(e.target)) return;

    if (!outEdges.has(e.source)) outEdges.set(e.source, []);
    outEdges
      .get(e.source)
      .push({ target: e.target, count: e.transitionCount || 1 });

    if (!inEdges.has(e.target)) inEdges.set(e.target, []);
    inEdges
      .get(e.target)
      .push({ source: e.source, count: e.transitionCount || 1 });

    targetSet.add(e.target);
  });

  // Assign ranks using BFS from roots
  const roots = [...nodeMap.keys()].filter((id) => !targetSet.has(id));
  if (roots.length === 0) {
    // Fallback: pick node with highest spanCount
    const sorted = [...nodeMap.values()].sort(
      (a, b) => (b.span_count || 0) - (a.span_count || 0),
    );
    if (sorted.length > 0) roots.push(sorted[0].id);
  }

  const rank = new Map();
  const queue = roots.map((id) => ({ id, r: 0 }));
  const visited = new Set();

  while (queue.length > 0) {
    const { id, r } = queue.shift();
    if (visited.has(id)) continue;
    visited.add(id);
    rank.set(id, Math.max(rank.get(id) || 0, r));

    const out = outEdges.get(id) || [];
    for (const { target } of out) {
      if (!visited.has(target)) {
        queue.push({ id: target, r: r + 1 });
      }
    }
  }

  // Add any unvisited nodes
  nodeMap.forEach((_, id) => {
    if (!rank.has(id)) rank.set(id, 0);
  });

  // Group nodes by rank
  const columns = new Map(); // rank -> [nodeId]
  rank.forEach((r, id) => {
    if (!columns.has(r)) columns.set(r, []);
    columns.get(r).push(id);
  });

  // Sort columns by rank, nodes within column by spanCount desc
  const sortedRanks = [...columns.keys()].sort((a, b) => a - b);
  sortedRanks.forEach((r) => {
    columns
      .get(r)
      .sort(
        (a, b) =>
          (nodeMap.get(b)?.spanCount || 0) - (nodeMap.get(a)?.spanCount || 0),
      );
  });

  // Find max span count for scaling
  let maxSpans = 0;
  nodeMap.forEach((n) => {
    maxSpans = Math.max(maxSpans, n.span_count || 0);
  });

  // Build layout data
  const layoutColumns = sortedRanks.map((r) => ({
    rank: r,
    nodes: columns.get(r).map((id) => ({
      ...nodeMap.get(id),
      id,
      color: getColor(nodeMap.get(id)?.type),
    })),
  }));

  // Build flow bands (edges between ranks)
  const flows = [];
  graphData.edges.forEach((e) => {
    if (e.isSelfLoop) return;
    if (!nodeMap.has(e.source) || !nodeMap.has(e.target)) return;
    const sourceNode = nodeMap.get(e.source);
    const targetNode = nodeMap.get(e.target);
    flows.push({
      source: e.source,
      target: e.target,
      count: e.transitionCount || 1,
      sourceColor: getColor(sourceNode?.type),
      targetColor: getColor(targetNode?.type),
    });
  });

  return { columns: layoutColumns, flows, maxSpans };
};

// ---------------------------------------------------------------------------
// SVG Sankey rendering
// ---------------------------------------------------------------------------
const SankeyChart = ({ layout, width, height, onNodeClick, theme }) => {
  const { columns, flows } = layout;

  const padding = { top: 10, bottom: 10, left: 20, right: 20 };
  const colGap = Math.max(
    60,
    (width - padding.left - padding.right) / (columns.length + 1),
  );
  const barWidth = 16;
  const minBarHeight = 20;
  const availableHeight = height - padding.top - padding.bottom;
  const nodeGap = 8;

  // Position nodes in each column
  const nodePositions = new Map(); // nodeId -> {x, y, h, color}

  columns.forEach((col, colIdx) => {
    const x = padding.left + colIdx * colGap;
    const totalSpans = col.nodes.reduce(
      (sum, n) => sum + (n.span_count || 1),
      0,
    );
    const totalGaps = Math.max(0, col.nodes.length - 1) * nodeGap;
    const scaleHeight = availableHeight - totalGaps;

    let yOffset = padding.top;
    col.nodes.forEach((node) => {
      const proportion = (node.span_count || 1) / totalSpans;
      const h = Math.max(minBarHeight, proportion * scaleHeight);

      nodePositions.set(node.id, {
        x,
        y: yOffset,
        h,
        color: node.color,
        node,
      });
      yOffset += h + nodeGap;
    });
  });

  // Build flow paths — curved bands between source and target bars
  const flowPaths = flows.map((flow, idx) => {
    const src = nodePositions.get(flow.source);
    const tgt = nodePositions.get(flow.target);
    if (!src || !tgt) return null;

    // Calculate band thickness proportional to flow count
    const srcTotal = flows
      .filter((f) => f.source === flow.source)
      .reduce((s, f) => s + f.count, 0);
    const tgtTotal = flows
      .filter((f) => f.target === flow.target)
      .reduce((s, f) => s + f.count, 0);

    const srcBandH = Math.max(4, (flow.count / srcTotal) * src.h);
    const tgtBandH = Math.max(4, (flow.count / tgtTotal) * tgt.h);

    // Calculate y offsets for stacking bands
    const srcFlowsBefore = flows
      .slice(0, idx)
      .filter((f) => f.source === flow.source);
    const tgtFlowsBefore = flows
      .slice(0, idx)
      .filter((f) => f.target === flow.target);

    const srcYOffset = srcFlowsBefore.reduce((sum, f) => {
      const t = flows
        .filter((ff) => ff.source === f.source)
        .reduce((s, ff) => s + ff.count, 0);
      return sum + Math.max(4, (f.count / t) * src.h);
    }, 0);

    const tgtYOffset = tgtFlowsBefore.reduce((sum, f) => {
      const t = flows
        .filter((ff) => ff.target === f.target)
        .reduce((s, ff) => s + ff.count, 0);
      return sum + Math.max(4, (f.count / t) * tgt.h);
    }, 0);

    const x0 = src.x + barWidth;
    const y0 = src.y + srcYOffset;
    const x1 = tgt.x;
    const y1 = tgt.y + tgtYOffset;
    const cpx = (x0 + x1) / 2;

    const d = [
      `M ${x0} ${y0}`,
      `C ${cpx} ${y0}, ${cpx} ${y1}, ${x1} ${y1}`,
      `L ${x1} ${y1 + tgtBandH}`,
      `C ${cpx} ${y1 + tgtBandH}, ${cpx} ${y0 + srcBandH}, ${x0} ${y0 + srcBandH}`,
      `Z`,
    ].join(" ");

    return (
      <path
        key={`flow-${idx}`}
        d={d}
        fill={flow.sourceColor.band}
        opacity={0.25}
        stroke="none"
      />
    );
  });

  // Render node bars and labels — with hover highlight + click
  const nodeElements = [];
  nodePositions.forEach(({ x, y, h, color, node }, id) => {
    nodeElements.push(
      <g
        key={id}
        style={{ cursor: onNodeClick ? "pointer" : "default" }}
        onClick={() => onNodeClick?.(node)}
      >
        {/* Hover target — invisible wider rect for easier hovering */}
        <rect
          x={x - 4}
          y={y - 2}
          width={barWidth + 130}
          height={h + 4}
          rx={4}
          fill="transparent"
          className="agent-path-hover-bg"
        />
        {/* Colored vertical bar */}
        <rect
          x={x}
          y={y}
          width={barWidth}
          height={h}
          rx={3}
          fill={color.bar}
          className="agent-path-bar"
        />
        {/* Node label */}
        <foreignObject
          x={x + barWidth + 6}
          y={y}
          width={120}
          height={h}
          style={{ overflow: "visible", pointerEvents: "none" }}
        >
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              justifyContent: "center",
              height: h,
              lineHeight: 1.2,
            }}
          >
            <span
              style={{
                fontSize: 12,
                fontWeight: 600,
                color: color.text,
                whiteSpace: "nowrap",
              }}
            >
              {node.name}
            </span>
            <span
              style={{
                fontSize: 10,
                color: "var(--text-disabled)",
                whiteSpace: "nowrap",
              }}
            >
              {(node.span_count || 0).toLocaleString()} spans
            </span>
          </div>
        </foreignObject>
      </g>,
    );
  });

  return (
    <svg width={width} height={height} style={{ display: "block" }}>
      <style>{`
        g:hover .agent-path-hover-bg { fill: ${theme ? alpha(theme.palette.text.primary, 0.04) : "rgba(0,0,0,0.03)"}; }
        g:hover .agent-path-bar { filter: brightness(0.9); transform-origin: center; }
      `}</style>
      <g>{flowPaths}</g>
      <g>{nodeElements}</g>
    </svg>
  );
};

SankeyChart.propTypes = {
  layout: PropTypes.object.isRequired,
  width: PropTypes.number.isRequired,
  height: PropTypes.number.isRequired,
  onNodeClick: PropTypes.func,
  theme: PropTypes.object,
};

// ---------------------------------------------------------------------------
// AgentPath component
// ---------------------------------------------------------------------------
const AgentPathInner = ({
  data,
  isLoading,
  onNodeClick,
  isFullscreen = false,
  onToggleFullscreen,
}) => {
  const theme = useTheme();
  const containerRef = useRef(null);
  const [containerWidth, setContainerWidth] = useState(900);
  const [containerHeight, setContainerHeight] = useState(200);
  const [isHovering, setIsHovering] = useState(false);
  const [isCollapsed, setIsCollapsed] = useState(false);
  const [zoom, setZoom] = useState(1);
  const layout = useMemo(() => computeSankeyLayout(data), [data]);

  useEffect(() => {
    if (!containerRef.current) return;
    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        setContainerWidth(entry.contentRect.width || 900);
        setContainerHeight(entry.contentRect.height || 200);
      }
    });
    observer.observe(containerRef.current);
    return () => observer.disconnect();
  }, []);

  if (isLoading) {
    return (
      <Box
        sx={{
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          height: 80,
        }}
      >
        <CircularProgress size={24} />
      </Box>
    );
  }

  if (!layout || layout.columns.length === 0) {
    return (
      <Box
        sx={{
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          height: 80,
        }}
      >
        <Typography color="text.secondary" variant="body2">
          No agent path data available for this time range
        </Typography>
      </Box>
    );
  }

  const chartHeight = isFullscreen ? Math.max(containerHeight, 320) : 200;
  const chartWidth = Math.max(320, Math.round(containerWidth * zoom));
  const scaledChartHeight = Math.max(120, Math.round(chartHeight * zoom));
  const controlInset = isFullscreen ? 24 : 8;
  const collapsedToolbarHeight = controlInset * 2 + 28;
  const showToolbar = isHovering || isFullscreen || isCollapsed;
  const btnSx = {
    p: 0.5,
    borderRadius: 0,
    borderRight: "1px solid",
    borderRightColor: "divider",
    "&:last-child": { borderRight: "none" },
    color: "text.secondary",
  };

  return (
    <Box
      ref={containerRef}
      onMouseEnter={() => setIsHovering(true)}
      onMouseLeave={() => setIsHovering(false)}
      sx={{
        display: "flex",
        flexDirection: "column",
        position: "relative",
        bgcolor: "background.paper",
        overflow: isCollapsed ? "visible" : "hidden",
        ...(isFullscreen
          ? { height: isCollapsed ? collapsedToolbarHeight : "100%", width: "100%" }
          : { mx: 2, my: 1, minHeight: isCollapsed ? collapsedToolbarHeight : undefined }),
      }}
    >
      {/* Zoom controls, top right (matches AgentGraph) */}
      {showToolbar && (
        <Box
          sx={{
            position: "absolute",
            top: controlInset,
            right: controlInset,
            zIndex: isFullscreen ? (t) => t.zIndex.modal + 1 : 10,
            display: "flex",
            bgcolor: "background.paper",
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "6px",
            boxShadow: (t) =>
              `0 1px 3px ${alpha(t.palette.common.black, 0.06)}`,
            overflow: "hidden",
          }}
        >
          <IconButton
            size="small"
            title="Zoom in"
            onClick={() => setZoom((value) => Math.min(2, value + 0.2))}
            sx={btnSx}
          >
            <Iconify icon="mdi:plus" width={14} />
          </IconButton>
          <IconButton
            size="small"
            title="Zoom out"
            onClick={() => setZoom((value) => Math.max(0.6, value - 0.2))}
            sx={btnSx}
          >
            <Iconify icon="mdi:minus" width={14} />
          </IconButton>
          <IconButton
            size="small"
            title="Fit"
            onClick={() => setZoom(1)}
            sx={btnSx}
          >
            <Iconify icon="mdi:crosshairs-gps" width={14} />
          </IconButton>
          <IconButton
            size="small"
            title={isFullscreen ? "Exit fullscreen" : "Fullscreen"}
            onClick={onToggleFullscreen}
            sx={btnSx}
          >
            <Iconify
              icon={isFullscreen ? "mdi:fullscreen-exit" : "mdi:fullscreen"}
              width={14}
            />
          </IconButton>
          <IconButton
            size="small"
            title={isCollapsed ? "Expand" : "Collapse"}
            onClick={() => setIsCollapsed((value) => !value)}
            sx={btnSx}
          >
            <Iconify
              icon={isCollapsed ? "mdi:chevron-up" : "mdi:chevron-down"}
              width={14}
            />
          </IconButton>
        </Box>
      )}

      <Collapse
        in={!isCollapsed}
        timeout="auto"
        sx={isFullscreen ? { flex: 1, minHeight: 0 } : { minHeight: 0 }}
      >
        <Box
          sx={{
            height: "100%",
            overflow: "auto",
          }}
        >
          <SankeyChart
            layout={layout}
            width={chartWidth}
            height={scaledChartHeight}
            onNodeClick={onNodeClick}
            theme={theme}
          />
        </Box>
      </Collapse>
    </Box>
  );
};

AgentPathInner.propTypes = {
  data: PropTypes.object,
  isLoading: PropTypes.bool,
  onNodeClick: PropTypes.func,
  isFullscreen: PropTypes.bool,
  onToggleFullscreen: PropTypes.func,
};

const AgentPath = (props) => (
  <FullscreenGraphDialog
    onNodeClick={props.onNodeClick}
    renderGraph={({ isFullscreen, onToggleFullscreen, onNodeClick }) => (
      <AgentPathInner
        {...props}
        isFullscreen={isFullscreen}
        onToggleFullscreen={onToggleFullscreen}
        onNodeClick={onNodeClick}
      />
    )}
  />
);

AgentPath.propTypes = {
  data: PropTypes.object,
  isLoading: PropTypes.bool,
  onNodeClick: PropTypes.func,
};

export default AgentPath;
