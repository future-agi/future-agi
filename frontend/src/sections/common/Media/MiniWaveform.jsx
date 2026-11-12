// A small waveform drawn as SVG from `peaks` (≤ 200 buckets, 0–1): no
// wavesurfer, no audio decode, so a grid of them costs nothing. Played
// progress is drawn in the strong colour; the rest at ≥ 3:1 contrast in
// light and dark.
import React, { useMemo } from "react";
import PropTypes from "prop-types";
import { useTheme } from "@mui/material";

/** Resample peaks to `n` bars (max of each bucket). Exported for tests. */
export function barsOf(peaks, n) {
  const xs = (peaks ?? []).filter((v) => Number.isFinite(v));
  if (!xs.length || n <= 0) return [];
  const out = [];
  for (let i = 0; i < n; i += 1) {
    const a = Math.floor((i * xs.length) / n);
    const b = Math.max(a + 1, Math.floor(((i + 1) * xs.length) / n));
    let m = 0;
    for (let j = a; j < b && j < xs.length; j += 1) m = Math.max(m, xs[j]);
    out.push(Math.max(0, Math.min(1, m)));
  }
  return out;
}

export default function MiniWaveform({
  peaks,
  width = 80,
  height = 20,
  progress = 0,
  color,
  barWidth = 2,
  gap = 1,
}) {
  const theme = useTheme();
  const dark = theme.palette.mode === "dark";
  const n = Math.max(1, Math.floor(width / (barWidth + gap)));
  const bars = useMemo(() => barsOf(peaks, n), [peaks, n]);
  const played = color ?? theme.palette.primary.main;
  // ≥ 3:1 against the paper in both themes (slate-500 / slate-400).
  const rest = dark ? "#94A3B8" : "#64748B";
  const mid = height / 2;
  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label="Waveform"
      data-mini-waveform=""
      style={{ display: "block", flexShrink: 0 }}
    >
      {bars.length ? (
        bars.map((v, i) => {
          const h = Math.max(1.5, v * height);
          const x = i * (barWidth + gap);
          return (
            <rect
              // eslint-disable-next-line react/no-array-index-key
              key={i}
              x={x}
              y={mid - h / 2}
              width={barWidth}
              height={h}
              rx={barWidth / 2}
              fill={i / bars.length < progress ? played : rest}
            />
          );
        })
      ) : (
        <line
          x1={0}
          x2={width}
          y1={mid}
          y2={mid}
          stroke={rest}
          strokeDasharray="2 2"
        />
      )}
    </svg>
  );
}

MiniWaveform.propTypes = {
  peaks: PropTypes.arrayOf(PropTypes.number),
  width: PropTypes.number,
  height: PropTypes.number,
  progress: PropTypes.number,
  color: PropTypes.string,
  barWidth: PropTypes.number,
  gap: PropTypes.number,
};
