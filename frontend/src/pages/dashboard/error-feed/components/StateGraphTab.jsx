import React, { useMemo } from "react";
import { Box, Skeleton, Stack, Typography, alpha, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";
import { useGetTraceDetail } from "src/api/project/trace-detail";
import { useErrorFeedDetail } from "src/api/errorFeed/error-feed";

function extractSteps(spanTree) {
  if (!spanTree?.length) return [];
  const steps = [];
  const seen = new Set();
  const walk = (entries) => {
    for (const entry of entries || []) {
      const s =
        entry?.observation_span || entry?.observationSpan || entry || {};
      const name =
        s.name || s.observation_type || s.observationType || "unknown";
      const type = s.observation_type || s.observationType || "unknown";
      if (!seen.has(name)) {
        seen.add(name);
        steps.push({ name, type, status: s.status });
      }
      if (entry.children?.length) walk(entry.children);
    }
  };
  walk(spanTree);
  return steps;
}

function computeDivergence(failSteps, successSteps) {
  const failNames = new Set(failSteps.map((s) => s.name));
  const successNames = new Set(successSteps.map((s) => s.name));
  return {
    shared: failSteps.filter((s) => successNames.has(s.name)),
    failOnly: failSteps.filter((s) => !successNames.has(s.name)),
    successOnly: successSteps.filter((s) => !failNames.has(s.name)),
  };
}

const NODE_H = 42;
const NODE_PAD = 50; // icon + padding
const CHAR_W = 7.5; // approx width per character at 11px font
const Y_GAP = 65;

function nodeW(name) {
  return Math.max(140, (name || "").length * CHAR_W + NODE_PAD);
}
const TYPE_ICONS = {
  agent: "🤖",
  llm: "💬",
  tool: "🔧",
  generation: "💬",
  retriever: "📄",
  chain: "🔗",
  workflow: "⚡",
  unknown: "⚙️",
};

function FlowNode({ x, y, name, type, color }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const w = nodeW(name);
  return (
    <Box
      sx={{
        position: "absolute",
        left: x - w / 2,
        top: y,
        width: w,
        height: NODE_H,
        borderRadius: "8px",
        bgcolor: isDark ? alpha("#fff", 0.03) : alpha("#000", 0.02),
        border: "1.5px solid",
        borderColor: color ? alpha(color, 0.5) : "divider",
        display: "flex",
        alignItems: "center",
        px: 1.25,
        gap: 0.75,
        transition: "all 0.15s ease",
        "&:hover": {
          bgcolor: isDark ? alpha("#fff", 0.06) : alpha("#000", 0.04),
          transform: "translateY(-1px)",
        },
      }}
    >
      <Box
        sx={{
          width: 22,
          height: 22,
          borderRadius: "5px",
          bgcolor: isDark ? alpha("#fff", 0.05) : alpha("#000", 0.04),
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          fontSize: "11px",
          flexShrink: 0,
        }}
      >
        {TYPE_ICONS[type] || TYPE_ICONS.unknown}
      </Box>
      <Typography
        fontSize="11px"
        fontWeight={600}
        color="text.primary"
        noWrap
        title={name}
        sx={{ lineHeight: 1.3 }}
      >
        {name}
      </Typography>
    </Box>
  );
}
FlowNode.propTypes = {
  x: PropTypes.number,
  y: PropTypes.number,
  name: PropTypes.string,
  type: PropTypes.string,
  color: PropTypes.string,
};

function SvgEdge({ x1, y1, x2, y2, color }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const stroke =
    color || (isDark ? "rgba(255,255,255,0.2)" : "rgba(0,0,0,0.15)");
  const isStraight = Math.abs(x1 - x2) < 5;
  const pathD = isStraight
    ? `M ${x1} ${y1} L ${x2} ${y2}`
    : `M ${x1} ${y1} C ${x1} ${y1 + 25}, ${x2} ${y2 - 25}, ${x2} ${y2}`;
  const mid = `a${x1 | 0}${y1 | 0}${x2 | 0}${y2 | 0}`;
  return (
    <g>
      <defs>
        <marker
          id={mid}
          markerWidth="8"
          markerHeight="8"
          refX="7"
          refY="4"
          orient="auto"
        >
          <path d="M 0 0 L 8 4 L 0 8 Z" fill={stroke} />
        </marker>
      </defs>
      <path
        d={pathD}
        fill="none"
        stroke={stroke}
        strokeWidth="1.5"
        markerEnd={`url(#${mid})`}
      />
    </g>
  );
}
SvgEdge.propTypes = {
  x1: PropTypes.number,
  y1: PropTypes.number,
  x2: PropTypes.number,
  y2: PropTypes.number,
  color: PropTypes.string,
};

function ForkLabel({ x, y, label, color }) {
  return (
    <Box
      sx={{
        position: "absolute",
        left: x - 40,
        top: y - 22,
        display: "flex",
        alignItems: "center",
        gap: 0.5,
        zIndex: 2,
      }}
    >
      <Box
        sx={{
          width: 7,
          height: 7,
          borderRadius: "50%",
          bgcolor: color,
          border: "2px solid",
          borderColor: "background.paper",
        }}
      />
      <Typography
        sx={{
          fontSize: "10px",
          fontWeight: 700,
          color,
          whiteSpace: "nowrap",
          textShadow: "0 1px 3px rgba(0,0,0,0.5)",
        }}
      >
        {label}
      </Typography>
    </Box>
  );
}
ForkLabel.propTypes = {
  x: PropTypes.number,
  y: PropTypes.number,
  label: PropTypes.string,
  color: PropTypes.string,
};

function SectionCard({ title, icon, children }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        overflow: "hidden",
        bgcolor: isDark ? alpha("#fff", 0.02) : "background.paper",
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        gap={0.75}
        sx={{
          px: 1.75,
          py: 1.1,
          borderBottom: "1px solid",
          borderColor: "divider",
          bgcolor: isDark ? alpha("#fff", 0.025) : alpha("#000", 0.018),
        }}
      >
        {icon && (
          <Iconify icon={icon} width={14} sx={{ color: "text.disabled" }} />
        )}
        <Typography
          fontSize="11px"
          fontWeight={600}
          color="text.secondary"
          sx={{ textTransform: "uppercase", letterSpacing: "0.06em" }}
        >
          {title}
        </Typography>
      </Stack>
      <Box sx={{ p: 1.75 }}>{children}</Box>
    </Box>
  );
}
SectionCard.propTypes = {
  title: PropTypes.string,
  icon: PropTypes.string,
  children: PropTypes.node,
};

function DivergenceDiagram({ failSteps, successSteps }) {
  const { shared, failOnly, successOnly } = useMemo(
    () => computeDivergence(failSteps, successSteps),
    [failSteps, successSteps],
  );

  // Compute max widths for layout
  const maxSharedW =
    shared.length > 0 ? Math.max(...shared.map((s) => nodeW(s.name))) : 140;
  const maxFailW =
    failOnly.length > 0 ? Math.max(...failOnly.map((s) => nodeW(s.name))) : 140;
  const maxSuccessW =
    failOnly.length > 0
      ? Math.max(...successOnly.map((s) => nodeW(s.name)))
      : 140;
  const forkOffset = Math.max(
    maxFailW / 2 + maxSuccessW / 2 + 80,
    maxSharedW / 2 + maxFailW / 2 + 60,
  );
  const centerX = forkOffset + maxFailW / 2;

  const allNodes = [];
  const svgEdges = [];
  let y = 10;

  for (const step of shared) {
    allNodes.push({ x: centerX, y, ...step });
    y += Y_GAP;
  }
  for (let i = 0; i < shared.length - 1; i++) {
    svgEdges.push({
      x1: centerX,
      y1: allNodes[i].y + NODE_H,
      x2: centerX,
      y2: allNodes[i + 1].y,
    });
  }

  const forkY = y;
  const lastSharedY =
    shared.length > 0 ? allNodes[allNodes.length - 1].y + NODE_H : 0;

  const failX = centerX - forkOffset;
  let failY = forkY;
  const fsi = allNodes.length;
  for (const step of failOnly) {
    allNodes.push({ x: failX, y: failY, ...step, color: "#DB2F2D" });
    failY += Y_GAP;
  }
  if (failOnly.length > 0 && shared.length > 0)
    svgEdges.push({
      x1: centerX,
      y1: lastSharedY,
      x2: failX,
      y2: forkY,
      color: alpha("#DB2F2D", 0.6),
    });
  for (let i = fsi; i < fsi + failOnly.length - 1; i++)
    svgEdges.push({
      x1: failX,
      y1: allNodes[i].y + NODE_H,
      x2: failX,
      y2: allNodes[i + 1].y,
      color: alpha("#DB2F2D", 0.5),
    });

  const successX = centerX + forkOffset;
  let successY = forkY;
  const ssi = allNodes.length;
  for (const step of successOnly) {
    allNodes.push({ x: successX, y: successY, ...step, color: "#5ACE6D" });
    successY += Y_GAP;
  }
  if (successOnly.length > 0 && shared.length > 0)
    svgEdges.push({
      x1: centerX,
      y1: lastSharedY,
      x2: successX,
      y2: forkY,
      color: alpha("#5ACE6D", 0.6),
    });
  for (let i = ssi; i < ssi + successOnly.length - 1; i++)
    svgEdges.push({
      x1: successX,
      y1: allNodes[i].y + NODE_H,
      x2: successX,
      y2: allNodes[i + 1].y,
      color: alpha("#5ACE6D", 0.5),
    });

  const canvasH = Math.max(failY, successY, forkY) + 20;
  const canvasW =
    centerX + forkOffset + Math.max(maxSuccessW, maxFailW) / 2 + 40;

  if (!allNodes.length)
    return (
      <Typography
        fontSize="12px"
        color="text.disabled"
        sx={{ py: 3, textAlign: "center" }}
      >
        No span data available
      </Typography>
    );

  return (
    <Box sx={{ display: "flex", justifyContent: "center" }}>
      <Box sx={{ position: "relative", overflow: "auto", minHeight: canvasH }}>
        <svg
          width={canvasW}
          height={canvasH}
          style={{
            position: "absolute",
            top: 0,
            left: 0,
            pointerEvents: "none",
            overflow: "visible",
          }}
        >
          {svgEdges.map((e, i) => (
            <SvgEdge key={i} {...e} />
          ))}
        </svg>
        <Box sx={{ position: "relative", width: canvasW, height: canvasH }}>
          {allNodes.map((n, i) => (
            <FlowNode key={`${n.name}-${i}`} {...n} />
          ))}
          {shared.length > 0 && failOnly.length > 0 && (
            <ForkLabel x={failX} y={forkY} label="Fail path" color="#DB2F2D" />
          )}
          {shared.length > 0 && successOnly.length > 0 && (
            <ForkLabel
              x={successX}
              y={forkY}
              label="Success path"
              color="#5ACE6D"
            />
          )}
        </Box>
      </Box>
    </Box>
  );
}
DivergenceDiagram.propTypes = {
  failSteps: PropTypes.array,
  successSteps: PropTypes.array,
};

export default function StateGraphTab({ error }) {
  const clusterId = error?.clusterId;
  const { data: detail, isLoading: isDetailLoading } =
    useErrorFeedDetail(clusterId);
  const failTraceId = detail?.representativeTrace?.traceId;
  const successTraceId = detail?.successTrace?.traceId;
  const { data: failData, isLoading: isFailLoading } =
    useGetTraceDetail(failTraceId);
  const { data: successData, isLoading: isSuccessLoading } =
    useGetTraceDetail(successTraceId);
  const isLoading = Boolean(
    isDetailLoading ||
      (failTraceId && isFailLoading && !failData) ||
      (successTraceId && isSuccessLoading && !successData),
  );
  const failSteps = useMemo(
    () =>
      extractSteps(failData?.observation_spans || failData?.observationSpans),
    [failData],
  );
  const successSteps = useMemo(
    () =>
      extractSteps(
        successData?.observation_spans || successData?.observationSpans,
      ),
    [successData],
  );

  if (isLoading) {
    return (
      <Stack gap={2}>
        <Skeleton
          variant="rectangular"
          height={420}
          sx={{ borderRadius: "8px" }}
        />
      </Stack>
    );
  }

  if (!failTraceId && !successTraceId) {
    return (
      <Stack alignItems="center" justifyContent="center" sx={{ py: 8 }}>
        <Iconify
          icon="mdi:graph-outline"
          width={40}
          sx={{ color: "text.disabled", mb: 1.5 }}
        />
        <Typography fontSize="13px" color="text.disabled">
          No trace data available for state graph yet.
        </Typography>
      </Stack>
    );
  }

  return (
    <Stack gap={2}>
      <SectionCard title="Agent Decision Flow" icon="mdi:graph-outline">
        <Stack direction="row" alignItems="center" gap={2} mb={1.5}>
          <Stack direction="row" alignItems="center" gap={0.6}>
            <Box
              sx={{
                width: 20,
                height: 2,
                bgcolor: "text.disabled",
                borderRadius: 1,
              }}
            />
            <Typography fontSize="11px" color="text.disabled">
              Shared path
            </Typography>
          </Stack>
          <Stack direction="row" alignItems="center" gap={0.6}>
            <Box
              sx={{ width: 20, height: 2, bgcolor: "#DB2F2D", borderRadius: 1 }}
            />
            <Typography fontSize="11px" color="text.disabled">
              Failing path
            </Typography>
          </Stack>
          <Stack direction="row" alignItems="center" gap={0.6}>
            <Box
              sx={{ width: 20, height: 2, bgcolor: "#5ACE6D", borderRadius: 1 }}
            />
            <Typography fontSize="11px" color="text.disabled">
              Working path
            </Typography>
          </Stack>
        </Stack>
        <Stack direction="row" gap={2} mb={1}>
          {failTraceId && (
            <Stack direction="row" alignItems="center" gap={0.5}>
              <Box
                sx={{
                  width: 8,
                  height: 8,
                  borderRadius: "50%",
                  bgcolor: "#DB2F2D",
                }}
              />
              <Typography
                fontSize="11px"
                fontWeight={600}
                color="text.secondary"
              >
                Failing: {failTraceId?.slice(0, 8)}…
              </Typography>
            </Stack>
          )}
          {successTraceId && (
            <Stack direction="row" alignItems="center" gap={0.5}>
              <Box
                sx={{
                  width: 8,
                  height: 8,
                  borderRadius: "50%",
                  bgcolor: "#5ACE6D",
                }}
              />
              <Typography
                fontSize="11px"
                fontWeight={600}
                color="text.secondary"
              >
                Working: {successTraceId?.slice(0, 8)}…
              </Typography>
            </Stack>
          )}
        </Stack>
        <DivergenceDiagram failSteps={failSteps} successSteps={successSteps} />
      </SectionCard>
    </Stack>
  );
}
StateGraphTab.propTypes = { error: PropTypes.object };
