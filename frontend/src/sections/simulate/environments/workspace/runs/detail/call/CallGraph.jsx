import PropTypes from "prop-types";
import { useLayoutEffect, useRef, useState } from "react";
import { alpha, useTheme } from "@mui/material/styles";
import { Box, IconButton, Stack, Typography } from "@mui/material";

import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";

// Vel's trajectory drawing: what the agent did, in order, with the step it
// skipped and the branch it had no reason to take hanging off it. Solid is
// what happened; dashed is what didn't. Ported without the baseline-diff mode,
// which needs a second run's graph.

const NODE_W = 196;
const NODE_H = 46;
const ROW_H = 96;
const COL_GAP = 44;
const PAD = 16;
const R = 9;

const KIND = {
  /* eslint-disable no-restricted-syntax -- ds: data colour */
  start: { color: "#7857FC" },
  tool: { color: "#2563EB" },
  check: { color: "#CA8A04" },
  end: { color: "#16A34A" },
  skipped: { color: "#C2603F", dash: "5 4" },
  alternate: { color: "#9AA0A6", dash: "5 4" },
  /* eslint-enable no-restricted-syntax */
};

// eslint-disable-next-line no-restricted-syntax -- ds: data colour
const STATUS_RING = { fail: "#DC2626", warn: "#CA8A04" };

const clip = (text, max) =>
  text.length > max ? `${text.slice(0, max - 1)}…` : text;

export default function CallGraph({ spine = [], branches = [] }) {
  const theme = useTheme();
  const dark = theme.palette.mode === "dark";
  const wrapRef = useRef(null);
  const [zoom, setZoom] = useState(null);
  const [fit, setFit] = useState(1);

  // One column per branch, so a branch's connector never crosses another's node.
  const placed = branches.map((b, bi) => ({
    ...b,
    col: bi + 1,
    nodes: b.nodes.map((n, i) => ({ ...n, row: (b.after ?? 0) + 1 + i })),
  }));
  const branchCols = placed.length;
  const forkRow = branchCols
    ? Math.min(...placed.map((b) => b.after ?? 0))
    : null;

  const colX = (col) => PAD + col * (NODE_W + COL_GAP);
  const centredX = PAD + (NODE_W + COL_GAP) / 2;
  // Above the first fork both paths are one story, so they sit centred.
  const xOfSpine = (row) => (branchCols && row <= forkRow ? centredX : colX(0));
  const yOf = (row) => PAD + row * ROW_H;

  const lastRow = Math.max(
    spine.length - 1,
    ...placed.flatMap((b) => b.nodes.map((n) => n.row)),
    0,
  );
  const width = PAD * 2 + NODE_W * (1 + branchCols) + COL_GAP * branchCols;
  const height = PAD * 2 + NODE_H + lastRow * ROW_H;
  const scale = zoom ?? fit;
  const toneOf = (n) => KIND[n.kind] || KIND.tool;

  useLayoutEffect(() => {
    const el = wrapRef.current;
    if (!el) return undefined;
    const measure = () => {
      const avail = el.clientWidth - 46;
      setFit(avail > 0 ? Math.min(1, Math.max(0.45, avail / width)) : 1);
    };
    measure();
    if (typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [width]);

  const gap = (a, b) => {
    if (a?.at == null || b?.at == null) return null;
    const d = b.at - a.at;
    return d >= 0.6 ? `${d.toFixed(1)}s` : null;
  };

  const edge = ({ x1, y1, x2, y2, color, dash, label, key }) => {
    const midY = y1 + (ROW_H - NODE_H) / 2;
    const straight = Math.abs(x1 - x2) < 1;
    const dir = x2 > x1 ? 1 : -1;
    const d = straight
      ? `M ${x1} ${y1} V ${y2 - 7}`
      : `M ${x1} ${y1} V ${midY - R} Q ${x1} ${midY} ${x1 + R * dir} ${midY} ` +
        `H ${x2 - R * dir} Q ${x2} ${midY} ${x2} ${midY + R} V ${y2 - 7}`;
    return (
      <g key={key}>
        <path
          d={d}
          fill="none"
          stroke={color}
          strokeWidth={1.4}
          strokeDasharray={dash || undefined}
        />
        <path
          d={`M ${x2 - 4} ${y2 - 6} L ${x2} ${y2 - 1} L ${x2 + 4} ${y2 - 6}`}
          fill="none"
          stroke={color}
          strokeWidth={1.4}
        />
        {label && (
          <>
            <rect
              x={x1 + 5}
              y={midY - 17}
              width={label.length * 6 + 8}
              height={14}
              rx={4}
              fill={theme.palette.background.paper}
            />
            <text
              x={x1 + 9}
              y={midY - 6}
              style={{
                font: "500 10px ui-monospace, Menlo, monospace",
                fill: theme.palette.text.disabled,
              }}
            >
              {label}
            </text>
          </>
        )}
      </g>
    );
  };

  const node = (n, x, row) => {
    const tone = toneOf(n);
    const ring = STATUS_RING[n.status];
    const y = yOf(row);
    return (
      <g key={`${n.id}-${row}`}>
        <title>{n.sub ? `${n.label}: ${n.sub}` : n.label}</title>
        <rect
          x={x}
          y={y}
          width={NODE_W}
          height={NODE_H}
          rx={12}
          fill={
            tone.dash
              ? alpha(tone.color, dark ? 0.1 : 0.05)
              : theme.palette.background.paper
          }
          stroke={ring || tone.color}
          strokeWidth={n.kind === "end" ? 1.75 : 1.25}
          strokeDasharray={tone.dash || undefined}
        />
        <circle cx={x + 14} cy={y + NODE_H / 2} r={3.5} fill={tone.color} />
        <text
          x={x + 26}
          y={y + 20}
          style={{
            font: "600 12px ui-monospace, Menlo, monospace",
            fill: theme.palette.text.primary,
          }}
        >
          {clip(n.label, 20)}
        </text>
        {n.sub && (
          <text
            x={x + 26}
            y={y + 34}
            style={{
              font: "400 10.5px ui-monospace, Menlo, monospace",
              fill: theme.palette.text.disabled,
            }}
          >
            {clip(n.sub, 26)}
          </text>
        )}
      </g>
    );
  };

  if (!spine.length) return null;

  const zoomButtons = [
    {
      icon: "solar:add-linear",
      title: "Zoom in",
      onClick: () => setZoom((z) => Math.min(2, (z ?? fit) + 0.2)),
    },
    {
      icon: "solar:minus-linear",
      title: "Zoom out",
      onClick: () => setZoom((z) => Math.max(0.4, (z ?? fit) - 0.2)),
    },
    {
      icon: "solar:maximize-square-minimalistic-linear",
      title: "Fit to width",
      onClick: () => setZoom(null),
    },
  ];

  return (
    <Box data-testid="call-graph" sx={{ position: "relative" }}>
      <Box ref={wrapRef} sx={{ overflow: "auto", py: 1 }}>
        <svg
          viewBox={`0 0 ${width} ${height}`}
          width={width * scale}
          height={height * scale}
          style={{ display: "block", margin: "0 auto" }}
        >
          {spine.slice(0, -1).map((n, i) =>
            edge({
              x1: xOfSpine(i) + NODE_W / 2,
              y1: yOf(i) + NODE_H,
              x2: xOfSpine(i + 1) + NODE_W / 2,
              y2: yOf(i + 1),
              color: toneOf(spine[i + 1]).color,
              label: gap(n, spine[i + 1]),
              key: `spine-${i}`,
            }),
          )}
          {placed.map((b) =>
            b.nodes.map((m, i) =>
              edge({
                x1:
                  i === 0
                    ? xOfSpine(b.after ?? 0) + NODE_W / 2
                    : colX(b.col) + NODE_W / 2,
                y1:
                  i === 0
                    ? yOf(b.after ?? 0) + NODE_H
                    : yOf(b.nodes[i - 1].row) + NODE_H,
                x2: colX(b.col) + NODE_W / 2,
                y2: yOf(m.row),
                color: (KIND[m.kind] || KIND.alternate).color,
                dash: "5 4",
                key: `branch-${m.id}-${i}`,
              }),
            ),
          )}
          {spine.map((n, i) => node(n, xOfSpine(i), i))}
          {placed.map((b) => b.nodes.map((m) => node(m, colX(b.col), m.row)))}
        </svg>
      </Box>
      <Stack
        sx={{
          position: "absolute",
          right: 8,
          bottom: 8,
          borderRadius: 1,
          overflow: "hidden",
          border: "1px solid",
          borderColor: "divider",
          bgcolor: "background.paper",
        }}
      >
        {zoomButtons.map((b) => (
          <CustomTooltip
            key={b.icon}
            show
            arrow
            size="small"
            placement="left"
            title={b.title}
          >
            <IconButton size="small" aria-label={b.title} onClick={b.onClick}>
              <Iconify
                icon={b.icon}
                width={14}
                sx={{ color: "text.subtitle" }}
              />
            </IconButton>
          </CustomTooltip>
        ))}
      </Stack>
    </Box>
  );
}
CallGraph.propTypes = {
  spine: PropTypes.array,
  branches: PropTypes.array,
};

const LEGEND = [
  { label: "Start", color: KIND.start.color },
  { label: "Tool call", color: KIND.tool.color },
  { label: "Rule applied", color: KIND.check.color },
  { label: "Ended", color: KIND.end.color },
  { label: "Expected, never called", color: KIND.skipped.color, dash: true },
  { label: "Branch not taken", color: KIND.alternate.color, dash: true },
];

export function GraphLegend() {
  return (
    <Stack
      direction="row"
      spacing={1.75}
      sx={{ pb: 1.5, flexWrap: "wrap", rowGap: 0.5 }}
    >
      {LEGEND.map((l) => (
        <Stack
          key={l.label}
          direction="row"
          alignItems="center"
          spacing={0.625}
        >
          <Box
            sx={{
              width: 14,
              height: 9,
              borderRadius: 1,
              flexShrink: 0,
              border: "1px solid",
              borderColor: l.color,
              borderStyle: l.dash ? "dashed" : "solid",
            }}
          />
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            {l.label}
          </Typography>
        </Stack>
      ))}
    </Stack>
  );
}
