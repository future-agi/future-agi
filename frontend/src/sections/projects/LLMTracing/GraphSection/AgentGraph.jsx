/**
 * AgentGraph — Aggregate agent DAG visualization
 *
 * Horizontal (LR) layout showing span type nodes with edges.
 *   - Nodes show: icon, name, span count
 *   - Hover tooltip: type, span count, avg latency, tokens, errors, error rate
 *   - Edges: thickness scaled by transition count, labels for high-count transitions
 *   - Start/Stop sentinel nodes as circles
 */
import React, { useMemo, useState, useEffect } from "react";
import PropTypes from "prop-types";
import {
  Box,
  CircularProgress,
  IconButton,
  Typography,
  useTheme,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import {
  ReactFlow,
  ReactFlowProvider,
  useNodesState,
  useEdgesState,
  useReactFlow,
  Handle,
  Position,
  MarkerType,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import FullscreenGraphDialog from "./FullscreenGraphDialog";
import { layoutGraph } from "./agentGraphLayout";

// ---------------------------------------------------------------------------
// Node type → color + icon
// ---------------------------------------------------------------------------
const TYPE_COLORS = {
  agent: { text: "#7c3aed", icon: "mdi:robot-outline", label: "Agent" },
  llm: { text: "#2563eb", icon: "mdi:brain", label: "LLM" },
  generation: { text: "#2563eb", icon: "mdi:brain", label: "Generation" },
  tool: { text: "#16a34a", icon: "mdi:wrench-outline", label: "Tool" },
  retriever: { text: "#0d9488", icon: "mdi:magnify", label: "Retriever" },
  chain: { text: "#c026d3", icon: "mdi:link-variant", label: "Chain" },
  embedding: { text: "#ea580c", icon: "mdi:vector-square", label: "Embedding" },
  guardrail: {
    text: "#dc2626",
    icon: "mdi:shield-check-outline",
    label: "Guardrail",
  },
  unknown: {
    text: "#6b7280",
    icon: "mdi:help-circle-outline",
    label: "Unknown",
  },
  start: { text: "#16a34a", icon: "mdi:play-circle-outline", label: "Start" },
  end: { text: "#dc2626", icon: "mdi:stop-circle-outline", label: "Stop" },
};

// Derive bg (alpha 0.08) and border (alpha 0.24) from the text color at runtime
const getColor = (type) => {
  const c = TYPE_COLORS[type] || TYPE_COLORS.unknown;
  return { ...c, bg: alpha(c.text, 0.08), border: alpha(c.text, 0.24) };
};

// Format milliseconds nicely
const fmtMs = (ms) => {
  if (ms == null || ms <= 0) return null;
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
};

// ---------------------------------------------------------------------------
// Custom node — shows icon + name + span count, rich tooltip on hover
// ---------------------------------------------------------------------------
const handleStyle = {
  background: "transparent",
  border: "none",
  width: 1,
  height: 1,
};

const AgentNode = ({ data }) => {
  const color = getColor(data.type);
  const isSentinel = data.type === "start" || data.type === "end";
  const isStart = data.type === "start";
  const isEnd = data.type === "end";
  const isVertical = data._direction === "TB";
  const errorRate =
    data.span_count > 0 && data.error_count > 0
      ? ((data.error_count / data.span_count) * 100).toFixed(1)
      : null;
  // Dim nodes that don't match the active filter
  const isDimmed = data._hasMatch === false;

  // Tooltip: name, duration/tokens side-by-side, cost, eval bars, annotations
  const evals = data.evals || [];
  const annotations = data.annotations || [];
  // Dedupe evals by name, average their scores
  const evalMap = {};
  evals.forEach((e) => {
    const name = e.eval_name || e.eval_config_id || "eval";
    const score =
      e.score ?? (e.result === true ? 100 : e.result === false ? 0 : null);
    if (score == null) return;
    if (!evalMap[name]) evalMap[name] = { name, total: 0, count: 0 };
    evalMap[name].total += score;
    evalMap[name].count += 1;
  });
  const evalSummary = Object.values(evalMap).map((e) => ({
    name: e.name,
    score: e.total / e.count,
  }));
  const lowEvals = evalSummary.filter((e) => e.score < 50);

  const tooltipContent = !isSentinel ? (
    <Box sx={{ minWidth: 200, maxWidth: 260 }}>
      {/* Header: icon + name */}
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 0.75,
          px: 1.25,
          py: 0.75,
          borderBottom: "1px solid rgba(255,255,255,0.1)",
        }}
      >
        <Iconify icon={color.icon} width={14} color={color.border} />
        <Typography sx={{ fontWeight: 600, fontSize: 12, color: "#fff" }}>
          {data.name}
        </Typography>
      </Box>

      {/* Metrics: Duration | Total Tokens */}
      <Box sx={{ display: "flex", gap: 2, px: 1.25, pt: 0.75 }}>
        <Box>
          <Typography
            sx={{
              fontSize: 10,
              color: "rgba(255,255,255,0.5)",
              fontWeight: 500,
            }}
          >
            Duration
          </Typography>
          <Typography sx={{ fontSize: 12, color: "#fff", fontWeight: 600 }}>
            {fmtMs(data.avg_latency_ms) || "0ms"}
          </Typography>
        </Box>
        <Box>
          <Typography
            sx={{
              fontSize: 10,
              color: "rgba(255,255,255,0.5)",
              fontWeight: 500,
            }}
          >
            Total Tokens
          </Typography>
          <Typography sx={{ fontSize: 12, color: "#fff", fontWeight: 600 }}>
            {data.total_tokens > 0
              ? `${data.total_tokens.toLocaleString()} tok`
              : "—"}
          </Typography>
        </Box>
      </Box>

      {/* Cost */}
      {(data.total_cost > 0 || data.avg_cost > 0) && (
        <Box sx={{ px: 1.25, pt: 0.25 }}>
          <Typography
            sx={{
              fontSize: 10,
              color: "rgba(255,255,255,0.5)",
              fontWeight: 500,
            }}
          >
            Estimated LLM cost
          </Typography>
          <Typography sx={{ fontSize: 12, color: "#fff", fontWeight: 600 }}>
            ${(data.total_cost || data.avg_cost || 0).toFixed(4)}
          </Typography>
        </Box>
      )}

      {/* Spans count */}
      {data.span_count > 1 && (
        <Box sx={{ px: 1.25, pt: 0.25 }}>
          <Typography
            sx={{
              fontSize: 10,
              color: "rgba(255,255,255,0.5)",
              fontWeight: 500,
            }}
          >
            Runs
          </Typography>
          <Typography sx={{ fontSize: 12, color: "#fff", fontWeight: 600 }}>
            {data.span_count}
          </Typography>
        </Box>
      )}

      {/* Errors */}
      {data.error_count > 0 && (
        <Box sx={{ px: 1.25, pt: 0.25 }}>
          <Typography
            sx={{
              fontSize: 10,
              color: "rgba(255,255,255,0.5)",
              fontWeight: 500,
            }}
          >
            Errors
          </Typography>
          <Typography sx={{ fontSize: 12, color: "#f87171", fontWeight: 600 }}>
            {data.error_count} {errorRate && `(${errorRate}%)`}
          </Typography>
        </Box>
      )}

      {/* Evals — all evals with progress bars */}
      {evalSummary.length > 0 && (
        <Box
          sx={{
            px: 1.25,
            pt: 0.75,
            pb: 0.5,
            borderTop: "1px solid rgba(255,255,255,0.1)",
            mt: 0.5,
          }}
        >
          <Typography
            sx={{
              fontSize: 10,
              color: "rgba(255,255,255,0.5)",
              fontWeight: 500,
              mb: 0.5,
            }}
          >
            {lowEvals.length > 0 ? "Low performance evals" : "Evals"}
          </Typography>
          {(lowEvals.length > 0 ? lowEvals : evalSummary)
            .slice(0, 4)
            .map((ev) => (
              <Box key={ev.name} sx={{ mb: 0.5 }}>
                <Box
                  sx={{
                    display: "flex",
                    justifyContent: "space-between",
                    mb: 0.15,
                  }}
                >
                  <Typography sx={{ fontSize: 10, color: "#fff" }}>
                    {ev.name}
                  </Typography>
                  <Typography
                    sx={{ fontSize: 10, color: "#fff", fontWeight: 600 }}
                  >
                    {Math.round(ev.score)}%
                  </Typography>
                </Box>
                <Box
                  sx={{
                    height: 4,
                    borderRadius: 2,
                    bgcolor: "rgba(255,255,255,0.15)",
                    overflow: "hidden",
                  }}
                >
                  <Box
                    sx={{
                      height: "100%",
                      width: `${Math.min(100, Math.max(0, ev.score))}%`,
                      borderRadius: 2,
                      bgcolor:
                        ev.score < 25
                          ? "#ef4444"
                          : ev.score < 50
                            ? "#f59e0b"
                            : "#22c55e",
                    }}
                  />
                </Box>
              </Box>
            ))}
        </Box>
      )}

      {/* Annotations */}
      {annotations.length > 0 && (
        <Box
          sx={{
            px: 1.25,
            pt: 0.5,
            pb: 0.5,
            borderTop: "1px solid rgba(255,255,255,0.1)",
            mt: evalSummary.length > 0 ? 0 : 0.5,
          }}
        >
          <Typography
            sx={{
              fontSize: 10,
              color: "rgba(255,255,255,0.5)",
              fontWeight: 500,
              mb: 0.25,
            }}
          >
            Annotations ({annotations.length})
          </Typography>
          {annotations.slice(0, 3).map((ann, i) => (
            <Box
              key={i}
              sx={{
                display: "flex",
                justifyContent: "space-between",
                py: 0.15,
              }}
            >
              <Typography sx={{ fontSize: 10, color: "#fff" }}>
                {ann.label_name ||
                  ann.annotation_label_name ||
                  `Annotation ${i + 1}`}
              </Typography>
              <Typography sx={{ fontSize: 10, color: "#fff", fontWeight: 600 }}>
                {ann.score != null ? `${ann.score}` : ann.value || "—"}
              </Typography>
            </Box>
          ))}
          {annotations.length > 3 && (
            <Typography
              sx={{ fontSize: 9, color: "rgba(255,255,255,0.4)", mt: 0.25 }}
            >
              +{annotations.length - 3} more
            </Typography>
          )}
        </Box>
      )}

      <Box sx={{ pb: 0.25 }} />
    </Box>
  ) : null;

  return (
    <>
      {/* Handles — direction-aware */}
      {!isStart && (
        <Handle
          type="target"
          position={isVertical ? Position.Top : Position.Left}
          style={handleStyle}
        />
      )}
      {!isEnd && (
        <Handle
          type="source"
          position={isVertical ? Position.Bottom : Position.Right}
          style={handleStyle}
        />
      )}

      <CustomTooltip
        show={!isSentinel}
        type="black"
        size="small"
        placement="top"
        title={tooltipContent}
      >
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 0.75,
            px: isSentinel ? 1 : 1.25,
            py: isSentinel ? 0.25 : 0.5,
            borderRadius: isSentinel ? "20px" : "8px",
            border: isSentinel ? "1.5px solid" : "2px solid",
            borderColor: color.border,
            bgcolor: isSentinel ? "transparent" : "background.paper",
            boxShadow: isSentinel
              ? "none"
              : (theme) =>
                  `0 1px 3px ${alpha(theme.palette.common.black, 0.06)}`,
            minWidth: isSentinel ? 44 : 80,
            justifyContent: isSentinel ? "center" : "flex-start",
            cursor: isSentinel ? "default" : "pointer",
            opacity: isDimmed ? 0.3 : 1,
            transition: "all 0.15s ease",
            "&:hover": isSentinel
              ? {}
              : {
                  boxShadow: (theme) =>
                    `0 0 0 2px ${color.border}, 0 2px 8px ${alpha(theme.palette.common.black, 0.1)}`,
                  transform: "scale(1.03)",
                },
          }}
        >
          <Iconify
            icon={color.icon}
            width={isSentinel ? 12 : 14}
            color={color.text}
          />
          <Box sx={{ overflow: "hidden" }}>
            <Typography
              noWrap
              sx={{
                fontWeight: 600,
                fontSize: isSentinel ? 10 : 12,
                color: color.text,
                lineHeight: 1.2,
                maxWidth: 100,
              }}
            >
              {data.name}
            </Typography>
            {/* Show span count directly on the node for non-sentinel */}
            {!isSentinel && data.span_count > 0 && (
              <Typography
                sx={{ fontSize: 9, color: "text.disabled", lineHeight: 1 }}
              >
                {data.span_count.toLocaleString()} spans
              </Typography>
            )}
          </Box>
        </Box>
      </CustomTooltip>
    </>
  );
};

AgentNode.propTypes = { data: PropTypes.object };

const nodeTypes = { agentNode: AgentNode };

// ---------------------------------------------------------------------------
// Build React Flow nodes + edges from API data
// ---------------------------------------------------------------------------
const buildFlowData = (graphData, direction = "LR", theme = null) => {
  if (!graphData?.nodes?.length) return { nodes: [], edges: [] };

  const nodeIdSet = new Set(graphData.nodes.map((n) => n.id));

  const flowNodes = graphData.nodes.map((node) => ({
    id: node.id,
    type: "agentNode",
    data: { ...node, _direction: direction },
    position: { x: 0, y: 0 },
    connectable: false,
  }));

  // Find max transition count for scaling edge thickness
  const maxTransitions = Math.max(
    1,
    ...graphData.edges.map((e) => e.transition_count || 1),
  );

  const flowEdges = graphData.edges
    .filter((edge) => nodeIdSet.has(edge.source) && nodeIdSet.has(edge.target))
    .map((edge, idx) => {
      const count = edge.transition_count || 1;
      // Scale thickness: 0.75px min, 2px max — subtle, not overpowering
      const thickness = 0.75 + (count / maxTransitions) * 1.25;

      const edgeColor = theme ? theme.palette.text.disabled : "#94a3b8";
      const strokeColor = theme ? theme.palette.divider : "#cbd5e1";
      const labelBgColor = theme ? theme.palette.background.paper : "#ffffff";

      return {
        id: `e-${idx}`,
        source: edge.source,
        target: edge.target,
        type: "default",
        animated: edge.is_self_loop || false,
        markerEnd: {
          type: MarkerType.ArrowClosed,
          color: edgeColor,
          width: 8,
          height: 8,
        },
        style: {
          stroke: strokeColor,
          strokeWidth: Math.round(thickness * 10) / 10,
        },
        // Show transition count on edges with > 1 transition
        ...(count > 1 && {
          label: `×${count}`,
          labelStyle: { fontSize: 9, fill: edgeColor, fontWeight: 500 },
          labelBgStyle: { fill: labelBgColor, fillOpacity: 0.9 },
          labelBgPadding: [3, 2],
        }),
      };
    });

  const layoutNodes = layoutGraph(flowNodes, flowEdges, direction);
  return { nodes: layoutNodes, edges: flowEdges };
};

// ---------------------------------------------------------------------------
// Zoom controls — top-right, appear on hover
// ---------------------------------------------------------------------------
const ZoomControls = ({ isFullscreen, onToggleFullscreen }) => {
  const { zoomIn, zoomOut, fitView } = useReactFlow();
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
      sx={{
        position: "absolute",
        top: 8,
        right: 8,
        zIndex: 10,
        display: "flex",
        bgcolor: "background.paper",
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "6px",
        boxShadow: (theme) =>
          `0 1px 3px ${alpha(theme.palette.common.black, 0.06)}`,
        overflow: "hidden",
      }}
    >
      <IconButton
        size="small"
        onClick={() => zoomIn({ duration: 200 })}
        sx={btnSx}
      >
        <Iconify icon="mdi:plus" width={14} />
      </IconButton>
      <IconButton
        size="small"
        onClick={() => zoomOut({ duration: 200 })}
        sx={btnSx}
      >
        <Iconify icon="mdi:minus" width={14} />
      </IconButton>
      <IconButton
        size="small"
        onClick={() => fitView({ duration: 300, padding: 0.3 })}
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
    </Box>
  );
};

ZoomControls.propTypes = {
  isFullscreen: PropTypes.bool,
  onToggleFullscreen: PropTypes.func,
};

// ---------------------------------------------------------------------------
// AgentGraph component
// ---------------------------------------------------------------------------
const AgentGraphInner = ({
  data,
  isLoading,
  isError,
  direction = "LR",
  onNodeClick,
  isFullscreen = false,
  onToggleFullscreen,
}) => {
  const theme = useTheme();
  const [isHovering, setIsHovering] = useState(false);
  const { nodes: layoutNodes, edges: layoutEdges } = useMemo(
    () => buildFlowData(data, direction, theme),
    [data, direction, theme],
  );

  const [nodes, setNodes, onNodesChange] = useNodesState(layoutNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(layoutEdges);

  useEffect(() => {
    setNodes(layoutNodes);
    setEdges(layoutEdges);
  }, [layoutNodes, layoutEdges, setNodes, setEdges]);

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
        <CircularProgress size={20} />
      </Box>
    );
  }

  if (isError) {
    return (
      <Box
        sx={{
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          height: 80,
          color: "text.disabled",
        }}
      >
        <Typography variant="body2" sx={{ fontSize: 13 }}>
          Failed to load agent graph
        </Typography>
      </Box>
    );
  }

  if (!data?.nodes?.length) {
    return (
      <Box
        sx={{
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          height: 80,
          color: "text.disabled",
        }}
      >
        <Typography variant="body2" sx={{ fontSize: 13 }}>
          No agent graph data available
        </Typography>
      </Box>
    );
  }

  return (
    <Box
      sx={{
        height: "100%",
        minHeight: 120,
        width: "100%",
        position: "relative",
        bgcolor: "background.paper",
      }}
      onMouseEnter={() => setIsHovering(true)}
      onMouseLeave={() => setIsHovering(false)}
    >
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        nodeTypes={nodeTypes}
        nodesConnectable={false}
        elementsSelectable={false}
        zoomOnScroll={false}
        onNodeClick={(_, node) => {
          if (
            node.data?.type !== "start" &&
            node.data?.type !== "end" &&
            onNodeClick
          ) {
            onNodeClick(node.data);
          }
        }}
        fitView
        fitViewOptions={{ padding: 0.2, maxZoom: 1.3 }}
        minZoom={0.3}
        maxZoom={2}
        proOptions={{ hideAttribution: true }}
      >
        {(isHovering || isFullscreen) && (
          <ZoomControls
            isFullscreen={isFullscreen}
            onToggleFullscreen={onToggleFullscreen}
          />
        )}
      </ReactFlow>
    </Box>
  );
};

AgentGraphInner.propTypes = {
  data: PropTypes.object,
  isLoading: PropTypes.bool,
  isError: PropTypes.bool,
  direction: PropTypes.oneOf(["LR", "TB"]),
  onNodeClick: PropTypes.func,
  isFullscreen: PropTypes.bool,
  onToggleFullscreen: PropTypes.func,
};

const AgentGraph = (props) => (
  <FullscreenGraphDialog
    onNodeClick={props.onNodeClick}
    renderGraph={({ isFullscreen, onToggleFullscreen, onNodeClick }) => (
      <ReactFlowProvider>
        <AgentGraphInner
          {...props}
          isFullscreen={isFullscreen}
          onToggleFullscreen={onToggleFullscreen}
          onNodeClick={onNodeClick}
        />
      </ReactFlowProvider>
    )}
  />
);

AgentGraph.propTypes = {
  data: PropTypes.object,
  isLoading: PropTypes.bool,
  isError: PropTypes.bool,
  direction: PropTypes.oneOf(["LR", "TB"]),
  onNodeClick: PropTypes.func,
};

export default AgentGraph;
