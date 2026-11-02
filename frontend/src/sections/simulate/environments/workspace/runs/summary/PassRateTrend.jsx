import PropTypes from "prop-types";
import { useEffect, useMemo, useRef, useState } from "react";
import { Box, CircularProgress, Stack, Typography } from "@mui/material";
import { alpha, useTheme } from "@mui/material/styles";

const NUM = { fontVariantNumeric: "tabular-nums" };
const DASH = "-";
const H = 208;
const PAD = { l: 44, r: 56, t: 38, b: 24 };

// The pass rate of every finished run, oldest to latest, as a line (the same
// number the runs table prints), failed calls as faint bars beneath, agent
// versions marked across the top. Hover a point for its numbers; click to
// open the run. A focused eval draws its own dashed line.
export default function PassRateTrend({
  rows,
  focusEval,
  capped,
  cap,
  isLoading,
  onOpenRun,
}) {
  const theme = useTheme();
  const ref = useRef(null);
  const [w, setW] = useState(900);
  const [hover, setHover] = useState(null);
  useEffect(() => {
    if (!ref.current || typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(([e]) =>
      setW(Math.max(320, Math.round(e.contentRect.width))),
    );
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  const points = useMemo(
    () =>
      rows.map((r) => ({
        run: r,
        rate: r.passRate ?? 0,
        failing: r.failed ?? 0,
      })),
    [rows],
  );

  const legendText = { typography: "s3", color: "text.subtitle" };
  const caption = (
    <Stack
      direction="row"
      alignItems="center"
      spacing={2}
      sx={{ px: 2.5, pt: 1.5 }}
    >
      <Typography noWrap sx={{ ...legendText, flex: 1, minWidth: 0 }}>
        {`Each point is one finished run, oldest to latest${points.length > 1 ? ` · ${points.length} runs` : ""} · hover for its numbers, click to open`}
      </Typography>
      {capped && (
        <Typography noWrap sx={{ ...legendText, flexShrink: 0 }}>
          {`Showing the latest ${cap} runs`}
        </Typography>
      )}
      <Stack direction="row" alignItems="center" spacing={0.75}>
        <Box
          sx={{
            width: 12,
            height: 2,
            borderRadius: 1,
            bgcolor: "text.primary",
          }}
        />
        <Typography sx={legendText}>Pass rate</Typography>
      </Stack>
      <Stack direction="row" alignItems="center" spacing={0.75}>
        <Box
          sx={{
            width: 8,
            height: 8,
            borderRadius: "4px",
            bgcolor: (t) => alpha(t.palette.text.primary, 0.16),
          }}
        />
        <Typography sx={legendText}>Failed calls</Typography>
      </Stack>
      {focusEval && (
        <Stack direction="row" alignItems="center" spacing={0.75}>
          <Box
            sx={{
              width: 12,
              borderTop: "2px dashed",
              borderColor: focusEval.color,
            }}
          />
          <Typography noWrap sx={{ ...legendText, maxWidth: 160 }}>
            {focusEval.name}
          </Typography>
        </Stack>
      )}
    </Stack>
  );

  // The history comes in its own, slower query; until it lands, an empty
  // chart would claim no run has finished.
  if (!points.length && isLoading) {
    return (
      <Box ref={ref}>
        {caption}
        <Stack alignItems="center" sx={{ py: 3 }}>
          <CircularProgress size={20} aria-label="Loading runs" />
        </Stack>
      </Box>
    );
  }

  if (!points.length) {
    return (
      <Box ref={ref}>
        {caption}
        <Typography
          sx={{ px: 2.5, py: 3, typography: "s2", color: "text.subtitle" }}
        >
          No finished run yet. The line starts with the first one.
        </Typography>
      </Box>
    );
  }

  const n = points.length;
  const iw = w - PAD.l - PAD.r;
  const ih = H - PAD.t - PAD.b;
  const inset = n > 1 ? 18 : 0;
  const x = (i) =>
    n === 1 ? PAD.l + iw / 2 : PAD.l + inset + (i / (n - 1)) * (iw - inset * 2);
  const focusVals = focusEval
    ? points.map((p) => {
        const v = p.run.scores?.[focusEval.id];
        return v == null ? null : Math.round(v);
      })
    : [];
  const lowest = Math.min(
    ...points.map((p) => p.rate),
    ...focusVals.filter((v) => v != null),
  );
  const lo = Math.max(0, Math.floor((lowest - 10) / 20) * 20);
  const y = (v) => PAD.t + ih - ((v - lo) / (100 - lo)) * ih;
  const ticks = [];
  for (let t = 100; t >= lo; t -= (100 - lo) / 2) ticks.push(Math.round(t));
  const ink = theme.palette.text.primary;
  const paper = theme.palette.background.paper;
  const sub = theme.palette.text.disabled;
  const maxFail = Math.max(1, ...points.map((p) => p.failing));
  const gap = n > 1 ? (iw - inset * 2) / (n - 1) : iw;
  const bw = Math.max(5, Math.min(22, gap * 0.5));
  // Where the agent version changes: a dashed rule, its label across the top.
  const groups = [];
  points.forEach((p, i) => {
    if (!groups.length || groups[groups.length - 1].v !== p.run.agentVersion)
      groups.push({ v: p.run.agentVersion, from: i });
  });
  const step = Math.ceil(n / Math.max(1, Math.floor(iw / 40)));
  const hp = hover != null ? points[hover] : null;
  const line = (vals) =>
    vals
      .map((v, i) => (v == null ? null : `${x(i)},${y(v)}`))
      .filter(Boolean)
      .join(" ");
  const open = (run) => {
    if (onOpenRun && run.executionId) onOpenRun(run);
  };

  return (
    <Box ref={ref} sx={{ position: "relative", minWidth: 0 }}>
      {caption}
      <svg
        width={w}
        height={H}
        role="group"
        aria-label="Pass rate across runs"
        style={{ display: "block", maxWidth: "100%" }}
      >
        {ticks.map((t) => (
          <g key={t}>
            <line
              x1={PAD.l}
              x2={w - PAD.r}
              y1={y(t)}
              y2={y(t)}
              stroke={alpha(ink, 0.07)}
            />
            <text
              x={w - PAD.r + 8}
              y={y(t) + 3.5}
              fontSize="10.5"
              fill={sub}
              style={NUM}
            >{`${t}%`}</text>
          </g>
        ))}
        <text
          x={PAD.l - 10}
          y={H - 6}
          textAnchor="end"
          fontSize="10.5"
          fill={sub}
        >
          Run
        </text>
        <text
          x={w - 6}
          y={PAD.t - 14}
          textAnchor="end"
          fontSize="10.5"
          fill={sub}
        >
          Pass rate
        </text>
        {points.map((p, i) => {
          const bh = (p.failing / maxFail) * ih * 0.32;
          return (
            <rect
              key={`f${p.run.id}`}
              x={x(i) - bw / 2}
              y={PAD.t + ih - bh}
              width={bw}
              height={bh}
              rx={1.5}
              fill={alpha(ink, hover === i ? 0.22 : 0.12)}
            />
          );
        })}
        {groups.map((g) => {
          const at = g.from === 0 ? PAD.l : (x(g.from) + x(g.from - 1)) / 2;
          return (
            <g key={`v${g.from}`}>
              {g.from > 0 && (
                <line
                  x1={at}
                  x2={at}
                  y1={PAD.t - 18}
                  y2={PAD.t + ih}
                  stroke={alpha(ink, 0.18)}
                  strokeDasharray="3 4"
                />
              )}
              <text
                x={at + (g.from > 0 ? 6 : 0)}
                y={PAD.t - 14}
                fontSize="12"
                fill={alpha(ink, 0.55)}
              >
                {g.v ?? DASH}
              </text>
            </g>
          );
        })}
        {hp && (
          <line
            x1={x(hover)}
            x2={x(hover)}
            y1={PAD.t - 4}
            y2={PAD.t + ih}
            stroke={alpha(ink, 0.2)}
          />
        )}
        {focusEval && (
          <polyline
            points={line(focusVals)}
            fill="none"
            stroke={focusEval.color}
            strokeWidth={1.5}
            strokeDasharray="4 3"
          />
        )}
        <polyline
          points={line(points.map((p) => p.rate))}
          fill="none"
          stroke={alpha(ink, 0.9)}
          strokeWidth={1.5}
          strokeLinejoin="round"
        />
        {points.map((p, i) => {
          const last = i === n - 1;
          return (
            <g key={p.run.id}>
              <circle
                cx={x(i)}
                cy={y(p.rate)}
                r={last ? 5 : hover === i ? 4.5 : 3.5}
                fill={last ? ink : paper}
                stroke={ink}
                strokeWidth={1.5}
              />
              {last && hover == null && (
                <text
                  x={x(i)}
                  y={y(p.rate) - 11}
                  textAnchor={n > 1 ? "end" : "middle"}
                  fontSize="11.5"
                  fontWeight={600}
                  fill={ink}
                  style={NUM}
                >{`${p.rate}%`}</text>
              )}
              {(i % step === 0 || last) && (
                <text
                  x={x(i)}
                  y={H - 6}
                  textAnchor="middle"
                  fontSize="10.5"
                  fill={last ? alpha(ink, 0.8) : sub}
                  style={NUM}
                >
                  {p.run.ordinal}
                </text>
              )}
            </g>
          );
        })}
        {points.map((p, i) => (
          <rect
            key={`h${p.run.id}`}
            x={x(i) - Math.max(10, gap / 2)}
            y={PAD.t - 10}
            width={Math.max(20, gap)}
            height={ih + 30}
            fill="transparent"
            role="link"
            tabIndex={0}
            aria-label={`Open ${p.run.label}`}
            style={{ cursor: "pointer", outline: "none" }}
            onMouseEnter={() => setHover(i)}
            onMouseLeave={() => setHover(null)}
            onFocus={() => setHover(i)}
            onBlur={() => setHover(null)}
            onClick={() => open(p.run)}
            onKeyDown={(ev) => {
              if (ev.key === "Enter") open(p.run);
            }}
          />
        ))}
      </svg>
      {hp && (
        <Box
          sx={{
            position: "absolute",
            pointerEvents: "none",
            zIndex: 2,
            top: 44,
            left: Math.min(Math.max(x(hover) + 12, 8), w - 260),
            px: 1.25,
            py: 0.75,
            borderRadius: 1,
            border: "1px solid",
            borderColor: "divider",
            bgcolor: "background.paper",
            boxShadow: (t) => t.customShadows?.z8 || "none",
            whiteSpace: "nowrap",
          }}
        >
          <Typography sx={{ typography: "s2", ...NUM }}>
            <Box component="span" sx={{ fontWeight: "fontWeightSemiBold" }}>
              {hp.run.label}
            </Box>
            {` · ${hp.run.agentVersion ?? DASH} · ${hp.rate}% · ${hp.run.passed}/${hp.run.tasks} passed`}
            {focusEval && focusVals[hover] != null
              ? ` · ${focusEval.name} ${focusVals[hover]}%`
              : ""}
          </Typography>
        </Box>
      )}
    </Box>
  );
}

PassRateTrend.propTypes = {
  // Summary rows (summaryData.buildSummaryRow) of the finished runs, oldest first.
  rows: PropTypes.arrayOf(PropTypes.object).isRequired,
  // { id, name, color } of the eval drawn over the pass rate, if any.
  focusEval: PropTypes.shape({
    id: PropTypes.string,
    name: PropTypes.string,
    color: PropTypes.string,
  }),
  // Older runs were left off; the caption says the chart holds the latest `cap`.
  capped: PropTypes.bool,
  cap: PropTypes.number,
  // The run history is still loading.
  isLoading: PropTypes.bool,
  onOpenRun: PropTypes.func,
};
