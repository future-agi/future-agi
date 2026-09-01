// AgentPath — Sankey-style flow view of an agent trace's span types.
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
import CustomTooltip from "src/components/tooltip";
import FullscreenGraphDialog from "./FullscreenGraphDialog";
import {
  PAD,
  COL_WIDTH,
  BAR_WIDTH,
  NODE_GAP,
  LABEL_W,
  MIN_ZOOM,
  MAX_ZOOM,
  VIEWPORT_H,
  INITIAL_MIN_ZOOM,
  nodeHeightFor,
  computeSankeyLayout,
  computeNaturalSize,
} from "./agentPathUtils";

const SankeyChart = ({ layout, width, height, zoom, onNodeClick, theme }) => {
  const { columns, flows, maxSpans } = layout;

  const padding = PAD;
  const colGap = COL_WIDTH;
  const barWidth = BAR_WIDTH;
  const nodeGap = NODE_GAP;
  const labelW = LABEL_W;

  const nodePositions = new Map();
  columns.forEach((col, colIdx) => {
    const x = padding.left + colIdx * colGap;
    let yOffset = padding.top;
    col.nodes.forEach((node) => {
      const h = nodeHeightFor(node, maxSpans);
      nodePositions.set(node.id, { x, y: yOffset, h, color: node.color, node });
      yOffset += h + nodeGap;
    });
  });

  const flowPaths = flows.map((flow, idx) => {
    const src = nodePositions.get(flow.source);
    const tgt = nodePositions.get(flow.target);
    if (!src || !tgt) return null;

    const srcTotal = flows
      .filter((f) => f.source === flow.source)
      .reduce((s, f) => s + f.count, 0);
    const tgtTotal = flows
      .filter((f) => f.target === flow.target)
      .reduce((s, f) => s + f.count, 0);

    const srcBandH = Math.max(4, (flow.count / srcTotal) * src.h);
    const tgtBandH = Math.max(4, (flow.count / tgtTotal) * tgt.h);

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

  const nodeElements = [];
  nodePositions.forEach(({ x, y, h, color, node }, id) => {
    const tooltipContent = (
      <div style={{ lineHeight: 1.3 }}>
        <div style={{ fontSize: 12, fontWeight: 600, color: color.text }}>
          {node.name}
        </div>
        <div style={{ fontSize: 10, color: "var(--text-disabled)" }}>
          {(node.span_count || 0).toLocaleString()} spans
        </div>
      </div>
    );
    nodeElements.push(
      <g
        key={id}
        className="agent-path-node"
        style={{ cursor: onNodeClick ? "pointer" : "default" }}
        onClick={() => onNodeClick?.(node)}
      >
        <rect
          x={x - 4}
          y={y - 2}
          width={barWidth + labelW + 14}
          height={h + 4}
          rx={4}
          fill="transparent"
          className="agent-path-hover-bg"
        />
        <rect
          x={x}
          y={y}
          width={barWidth}
          height={h}
          rx={3}
          fill={color.bar}
          className="agent-path-bar"
        />
        <foreignObject
          x={x + barWidth + 6}
          y={y}
          width={labelW}
          height={h}
          style={{ overflow: "visible", pointerEvents: "auto" }}
        >
          <CustomTooltip
            show
            size="small"
            title={tooltipContent}
            placement="top"
            arrow
          >
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                justifyContent: "center",
                height: h,
              }}
            >
              <div style={{ maxWidth: labelW, lineHeight: 1.25 }}>
                <div
                  style={{
                    fontSize: 12,
                    fontWeight: 600,
                    color: color.text,
                    whiteSpace: "nowrap",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                  }}
                >
                  {node.name}
                </div>
                <div
                  style={{
                    fontSize: 10,
                    color: "var(--text-disabled)",
                    whiteSpace: "nowrap",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                  }}
                >
                  {(node.span_count || 0).toLocaleString()} spans
                </div>
              </div>
            </div>
          </CustomTooltip>
        </foreignObject>
      </g>,
    );
  });

  return (
    <svg
      width={Math.round(width * zoom)}
      height={Math.round(height * zoom)}
      viewBox={`0 0 ${width} ${height}`}
      style={{ display: "block" }}
    >
      <style>{`
        .agent-path-node:hover .agent-path-hover-bg { fill: ${theme ? alpha(theme.palette.text.primary, 0.04) : "rgba(0,0,0,0.03)"}; }
        .agent-path-node:hover .agent-path-bar { filter: brightness(0.9); transform-origin: center; }
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
  zoom: PropTypes.number,
  onNodeClick: PropTypes.func,
  theme: PropTypes.object,
};

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
  const natural = useMemo(() => computeNaturalSize(layout), [layout]);

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

  const viewportHeight = isFullscreen
    ? Math.max(containerHeight, 320)
    : VIEWPORT_H;

  const fitZoom = useMemo(() => {
    const availW = Math.max(1, containerWidth - 16);
    const availH = Math.max(1, viewportHeight - 16);
    const z = Math.min(availW / natural.width, availH / natural.height);
    return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, Number.isFinite(z) ? z : 1));
  }, [containerWidth, viewportHeight, natural.width, natural.height]);

  const initialZoom = useMemo(
    () => Math.min(1, Math.max(INITIAL_MIN_ZOOM, fitZoom)),
    [fitZoom],
  );

  // Re-fit only on structural change, not on resize / 10s refresh — so a
  // manual zoom sticks.
  const structKey = useMemo(() => {
    if (!layout?.columns) return "";
    return `${layout.columns.length}:${layout.columns.reduce(
      (s, c) => s + c.nodes.length,
      0,
    )}`;
  }, [layout]);
  const didAutoFitRef = useRef(false);
  useEffect(() => {
    didAutoFitRef.current = false;
  }, [structKey]);
  useEffect(() => {
    if (didAutoFitRef.current || !layout || containerWidth <= 1) return;
    setZoom(initialZoom);
    didAutoFitRef.current = true;
  }, [layout, containerWidth, initialZoom]);

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

  const chartEl = (
    <SankeyChart
      layout={layout}
      width={natural.width}
      height={natural.height}
      zoom={zoom}
      onNodeClick={onNodeClick}
      theme={theme}
    />
  );

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
          ? {
              height: isCollapsed ? collapsedToolbarHeight : "100%",
              width: "100%",
            }
          : {
              mx: 2,
              my: 1,
              minHeight: isCollapsed ? collapsedToolbarHeight : undefined,
            }),
      }}
    >
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
            onClick={() => setZoom((value) => Math.min(MAX_ZOOM, value + 0.2))}
            sx={btnSx}
          >
            <Iconify icon="mdi:plus" width={14} />
          </IconButton>
          <IconButton
            size="small"
            title="Zoom out"
            onClick={() => setZoom((value) => Math.max(MIN_ZOOM, value - 0.2))}
            sx={btnSx}
          >
            <Iconify icon="mdi:minus" width={14} />
          </IconButton>
          <IconButton
            size="small"
            title="Fit to view"
            onClick={() => setZoom(fitZoom)}
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

      {/* Fullscreen needs a bounded flex child to scroll; MUI Collapse sizes to
          content, so only use it for the inline strip. */}
      {isFullscreen ? (
        !isCollapsed && (
          <Box sx={{ flex: 1, minHeight: 0, overflow: "auto" }}>{chartEl}</Box>
        )
      ) : (
        <Collapse in={!isCollapsed} timeout="auto" sx={{ minHeight: 0 }}>
          <Box sx={{ height: VIEWPORT_H, overflow: "auto" }}>{chartEl}</Box>
        </Collapse>
      )}
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
