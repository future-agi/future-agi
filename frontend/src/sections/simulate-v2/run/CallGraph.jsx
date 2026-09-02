import PropTypes from "prop-types";
import { useLayoutEffect, useRef, useState } from "react";
import { alpha, useTheme } from "@mui/material/styles";
import { Box, Stack, Typography, IconButton, Tooltip } from "@mui/material";
import Iconify from "src/components/iconify";

/**
 * The trajectory.
 *
 * A transcript is read top to bottom and says what was said. This says what the
 * agent *did*: where the episode started, every tool it called and what came
 * back, the moment a rule had to be applied, the branch it could have taken and
 * didn't, the step it should have taken and didn't, and how the call ended.
 *
 * Two kinds of line, and the difference matters. Solid is what happened.
 * Dashed is what didn't — and dashed splits again into a step the scenario
 * needed and the agent skipped (red: a finding) and a branch the agent had no
 * reason to take (grey: context). Collapsing those two is how a graph starts
 * lying about the run it is drawing.
 *
 * With a baseline set, colour switches meaning: solid nodes are then shared /
 * only-this-run, and the baseline's own detour appears as a third dashed
 * branch. One colour scale at a time — a node that is simultaneously "a tool
 * call" and "added since v2" cannot be both blue and green.
 */

const NODE_W = 196;
const NODE_H = 46;
const ROW_H = 96;
const COL_GAP = 44;
const PAD = 16;
const R = 9;

/* What a node is, when nothing is being compared. */
const KIND = {
  start: { color: "#7857FC", dot: "#7857FC" },
  tool: { color: "#2563EB", dot: "#2563EB" },
  check: { color: "#CA8A04", dot: "#CA8A04" },
  end: { color: "#16A34A", dot: "#16A34A" },
  skipped: { color: "#C2603F", dot: "#C2603F", dash: "5 4" },
  alternate: { color: "#9AA0A6", dot: "#9AA0A6", dash: "5 4" },
  baseline: { color: "#C2603F", dot: "#C2603F", dash: "5 4" },
};

/* What a node is, once there is a baseline to read it against. */
const DIFF = {
  shared: { color: "#9AA0A6" },
  added: { color: "#16A34A" },
};

const STATUS_RING = { fail: "#DC2626", warn: "#CA8A04" };

export default function CallGraph({ spine = [], branches = [], diff, focus, onNodeClick, sx }) {
  const theme = useTheme();
  const dark = theme.palette.mode === "dark";
  /*
    Fitted to the column it lands in, not to a number picked at build time. A
    trajectory with two branches is three columns wide and these panels are
    narrow — opening on a graph you immediately have to scroll sideways is a
    graph nobody reads. Manual zoom takes over the moment it is touched.
  */
  const wrapRef = useRef(null);
  const [zoom, setZoom] = useState(null);
  const [fit, setFit] = useState(1);

  /*
    One column per branch. Stacking two branches in the same column meant the
    second one's connector ran straight through the first one's node — which is
    the single fastest way to make a graph unreadable. The panel scrolls
    sideways instead.
  */
  const placed = branches.map((b, bi) => ({
    ...b,
    col: bi + 1,
    nodes: b.nodes.map((n, i) => ({ ...n, row: (b.after ?? 0) + 1 + i })),
  }));

  const branchCols = placed.length;
  const twoCols = branchCols > 0;
  const forkRow = twoCols ? Math.min(...placed.map((b) => b.after ?? 0)) : null;

  const colX = (col) => PAD + col * (NODE_W + COL_GAP);
  const centredX = PAD + (NODE_W + COL_GAP) / 2;
  /* Above the first fork the two paths are one story, so those nodes sit
     centred over the split rather than pretending to belong to the left. */
  const xOfSpine = (row) => (twoCols && row <= forkRow ? centredX : colX(0));
  const yOf = (row) => PAD + row * ROW_H;

  const lastRow = Math.max(spine.length - 1, ...placed.flatMap((b) => b.nodes.map((n) => n.row)), 0);
  const width = PAD * 2 + NODE_W * (1 + branchCols) + COL_GAP * branchCols;
  const height = PAD * 2 + NODE_H + lastRow * ROW_H;
  const scale = zoom ?? fit;

  const toneOf = (node) => {
    if (diff && node.diffKind) return DIFF[node.diffKind] || DIFF.shared;
    return KIND[node.kind] || KIND.tool;
  };

  /** One edge, with the gap between the two steps written on it. */
  const edge = ({ x1, y1, x2, y2, color, dash, label, key }) => {
    const midY = y1 + (ROW_H - NODE_H) / 2;
    const straight = Math.abs(x1 - x2) < 1;
    const dir = x2 > x1 ? 1 : -1;
    const d = straight
      ? `M ${x1} ${y1} V ${y2 - 7}`
      : `M ${x1} ${y1} V ${midY - R} Q ${x1} ${midY} ${x1 + R * dir} ${midY} `
        + `H ${x2 - R * dir} Q ${x2} ${midY} ${x2} ${midY + R} V ${y2 - 7}`;
    return (
      <g key={key}>
        <path d={d} fill="none" stroke={color} strokeWidth={1.4} strokeDasharray={dash || undefined} />
        <path
          d={`M ${x2 - 4} ${y2 - 6} L ${x2} ${y2 - 1} L ${x2 + 4} ${y2 - 6}`}
          fill="none" stroke={color} strokeWidth={1.4}
        />
        {label && (
          <>
            {/* The line would otherwise run straight through the text. */}
            <rect
              x={(straight ? x1 : x1) + 5} y={midY - 17} width={label.length * 6 + 8} height={14} rx={4}
              fill={theme.palette.background.paper}
            />
            <text
              x={(straight ? x1 : x1) + 9} y={midY - 6}
              style={{ font: "500 10px ui-monospace, Menlo, monospace", fill: theme.palette.text.disabled }}
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
    const dash = KIND[n.kind]?.dash;
    const ring = !diff && STATUS_RING[n.status];
    const y = yOf(row);
    /* Arrived here from a diagnosis that named this step. Marked, because
       "open the trace" and "open the trace at the step I was talking about"
       are different amounts of help. */
    const lit = focus && n.id === focus;
    return (
      <g
        key={`${n.id}-${row}`}
        onClick={() => onNodeClick?.(n)}
        style={{ cursor: onNodeClick ? "pointer" : "default" }}
      >
        {lit && (
          <rect
            x={x - 5} y={y - 5} width={NODE_W + 10} height={NODE_H + 10} rx={16}
            fill="none" stroke="#7857FC" strokeWidth={1.5} strokeDasharray="4 3" opacity={0.9}
          />
        )}
        <title>{n.hint || `${n.label} — ${n.sub || ""}`}</title>
        <rect
          x={x} y={y} width={NODE_W} height={NODE_H} rx={12}
          fill={dash ? alpha(tone.color, dark ? 0.1 : 0.05) : theme.palette.background.paper}
          stroke={ring || tone.color}
          strokeWidth={n.kind === "end" ? 1.75 : 1.25}
          strokeDasharray={dash || undefined}
          style={dark ? undefined : { filter: "drop-shadow(0 1px 1.5px rgba(16,24,40,.06))" }}
        />
        {/* What kind of step this is, without spending a whole row on a word. */}
        <circle cx={x + 14} cy={y + NODE_H / 2} r={3.5} fill={tone.color} />
        <text
          x={x + 26} y={y + 20}
          style={{ font: "600 12px ui-monospace, Menlo, monospace", fill: tone.color }}
        >
          {n.label.length > 20 ? `${n.label.slice(0, 19)}…` : n.label}
        </text>
        {n.sub && (
          <text
            x={x + 26} y={y + 34}
            style={{ font: "400 10.5px ui-monospace, Menlo, monospace", fill: theme.palette.text.disabled }}
          >
            {n.sub.length > 26 ? `${n.sub.slice(0, 25)}…` : n.sub}
          </text>
        )}
      </g>
    );
  };

  /* Measured rather than guessed: the same graph sits in a 430px column and in
     a full-width one, and only the DOM knows which. */
  useLayoutEffect(() => {
    const el = wrapRef.current;
    if (!el) return undefined;
    const measure = () => {
      /* Room reserved for the zoom cluster, so the last column never sits
         underneath it. */
      const avail = el.clientWidth - 46;
      setFit(avail > 0 ? Math.min(1, Math.max(0.45, avail / width)) : 1);
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [width]);

  const gap = (a, b) => {
    if (a?.at == null || b?.at == null) return null;
    const d = b.at - a.at;
    return d >= 0.6 ? `${d.toFixed(1)}s` : null;
  };

  /* After the hooks, never before them: an early return above a useLayoutEffect
     changes the hook order between renders. */
  if (!spine.length) {
    return (
      <Box sx={{ p: 3, textAlign: "center", ...sx }}>
        <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
          No path — this scenario did not run here.
        </Typography>
      </Box>
    );
  }

  return (
    <Box sx={{ position: "relative", ...sx }}>
      <Box ref={wrapRef} sx={{ overflow: "auto", py: 1 }}>
        <Box sx={{ width: width * scale, height: height * scale, transition: "width .15s, height .15s" }}>
          <svg
            viewBox={`0 0 ${width} ${height}`}
            width={width * scale}
            height={height * scale}
            style={{ display: "block", margin: "0 auto" }}
          >
            {/* what happened */}
            {spine.slice(0, -1).map((n, i) => edge({
              x1: xOfSpine(i) + NODE_W / 2,
              y1: yOf(i) + NODE_H,
              x2: xOfSpine(i + 1) + NODE_W / 2,
              y2: yOf(i + 1),
              color: toneOf(spine[i + 1]).color,
              label: gap(n, spine[i + 1]),
              key: `spine-${i}`,
            }))}

            {/* what didn't */}
            {placed.map((b) => b.nodes.map((m, i) => edge({
              x1: i === 0 ? xOfSpine(b.after ?? 0) + NODE_W / 2 : colX(b.col) + NODE_W / 2,
              y1: i === 0 ? yOf(b.after ?? 0) + NODE_H : yOf(b.nodes[i - 1].row) + NODE_H,
              x2: colX(b.col) + NODE_W / 2,
              y2: yOf(m.row),
              color: (KIND[m.kind] || KIND.alternate).color,
              dash: "5 4",
              key: `branch-${m.id}-${i}`,
            })))}

            {spine.map((n, i) => node(n, xOfSpine(i), i))}
            {placed.map((b) => b.nodes.map((m) => node(m, colX(b.col), m.row)))}
          </svg>
        </Box>
      </Box>

      <Stack
        sx={{
          position: "absolute", right: 8, bottom: 8,
          borderRadius: 1, overflow: "hidden",
          border: "1px solid", borderColor: "divider", bgcolor: "background.paper",
        }}
      >
        {[
          { icon: "mingcute:add-line", title: "Zoom in", onClick: () => setZoom((z) => Math.min(2, (z ?? fit) + 0.2)) },
          { icon: "mingcute:minimize-line", title: "Zoom out", onClick: () => setZoom((z) => Math.max(0.4, (z ?? fit) - 0.2)) },
          { icon: "solar:maximize-square-minimalistic-linear", title: "Fit to width", onClick: () => setZoom(null) },
        ].map((b) => (
          <Tooltip key={b.icon} arrow placement="left" title={b.title}>
            <IconButton size="small" onClick={b.onClick} sx={{ borderRadius: 0 }}>
              <Iconify icon={b.icon} width={14} sx={{ color: "text.subtitle" }} />
            </IconButton>
          </Tooltip>
        ))}
      </Stack>
    </Box>
  );
}

CallGraph.propTypes = {
  spine: PropTypes.array,
  branches: PropTypes.array,
  diff: PropTypes.bool,
  focus: PropTypes.string,
  onNodeClick: PropTypes.func,
  sx: PropTypes.object,
};

/**
 * What the drawing means — and it means two different things depending on
 * whether a baseline is in play, so the legend says which one is on.
 */
export function GraphLegend({ diff }) {
  const solid = diff
    ? [
      { label: "Shared with baseline", color: DIFF.shared.color },
      { label: "Only this run", color: DIFF.added.color },
    ]
    : [
      { label: "Start", color: KIND.start.color },
      { label: "Tool call", color: KIND.tool.color },
      { label: "Rule applied", color: KIND.check.color },
      { label: "Ended", color: KIND.end.color },
    ];

  const dashed = [
    { label: "Expected, never called", color: KIND.skipped.color },
    { label: "Branch not taken", color: KIND.alternate.color },
    ...(diff ? [{ label: "Baseline went here", color: KIND.baseline.color }] : []),
  ];

  return (
    <Stack direction="row" spacing={1.75} sx={{ px: 2, pb: 1.5, flexWrap: "wrap", rowGap: 0.5 }}>
      {[...solid.map((l) => ({ ...l, dash: false })), ...dashed.map((l) => ({ ...l, dash: true }))].map((l) => (
        <Stack key={l.label} direction="row" alignItems="center" spacing={0.625}>
          <Box
            sx={{
              width: 14, height: 9, borderRadius: 1, flexShrink: 0,
              border: "1px solid", borderColor: l.color,
              borderStyle: l.dash ? "dashed" : "solid",
            }}
          />
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{l.label}</Typography>
        </Stack>
      ))}
    </Stack>
  );
}

GraphLegend.propTypes = { diff: PropTypes.bool };
