// The Call lens latency timeline (R4 lane M): x = each answered turn, one
// bar per lane (the run's colour), a dashed line at the Latency p90 target,
// ⚡ where the agent kept talking over the caller. Compact (56 px): total
// bars + target. Expanded (140 px): the STT · LLM · TTS split as tints of
// the run colour, dead air (> 2 s) hatched, tool-call ticks under the bars
// (red on error). Hover a turn for its numbers; click it to focus the turn.
import React, { useLayoutEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Box, alpha, useTheme } from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import { fmtDeltaMs, fmtMs } from "src/sections/common/format/fmt";
import { latencyTooltip } from "./lensModel";

export const COMPACT_H = 56;
export const EXPANDED_H = 140;

function useWidth() {
  const ref = useRef(null);
  const [w, setW] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    const measure = () => setW(el.clientWidth);
    measure();
    if (typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}

export default function LatencyTimeline({
  rows,
  lanes,
  names,
  thresholdMs,
  deadAirMs = 2000,
  expanded = false,
  focusK,
  onFocusRow,
}) {
  const theme = useTheme();
  const [ref, width] = useWidth();
  const H = expanded ? EXPANDED_H : COMPACT_H;
  const top = 12;
  const bottom = expanded ? 22 : 14;
  const plotH = H - top - bottom;
  const maxGap = Math.max(
    thresholdMs * 1.25,
    ...rows.flatMap((r) => r.lanes.map((l) => (l ? l.gap : 0))),
  );
  const y = (ms) => top + plotH - (ms / maxGap) * plotH;
  const n = rows.length || 1;
  const groupW = width ? width / n : 0;
  const nLanes = lanes.length;
  const barW = Math.max(3, Math.min(18, (groupW * 0.7) / nLanes));
  const gridColor = theme.palette.divider;
  const textColor = theme.palette.text.disabled;
  const hatchId = `hatch-${React.useId().replace(/:/g, "")}`;

  return (
    <Box
      ref={ref}
      data-latency-timeline={expanded ? "expanded" : "compact"}
      sx={{ width: "100%", height: H, position: "relative" }}
    >
      {width ? (
        <svg width={width} height={H} role="img" aria-label="Latency per turn">
          <defs>
            <pattern
              id={hatchId}
              width="4"
              height="4"
              patternUnits="userSpaceOnUse"
              patternTransform="rotate(45)"
            >
              <line
                x1="0"
                y1="0"
                x2="0"
                y2="4"
                stroke={theme.palette.text.primary}
                strokeWidth="1.5"
                strokeOpacity="0.45"
              />
            </pattern>
          </defs>
          <line
            x1={0}
            x2={width}
            y1={top + plotH}
            y2={top + plotH}
            stroke={gridColor}
          />
          <line
            x1={0}
            x2={width}
            y1={y(thresholdMs)}
            y2={y(thresholdMs)}
            stroke={theme.palette.warning.main}
            strokeDasharray="4 3"
            data-threshold={thresholdMs}
          />
          <text
            x={width - 2}
            y={y(thresholdMs) - 2}
            textAnchor="end"
            fontSize="10"
            fill={theme.palette.warning.dark}
          >
            {`p90 target ${fmtMs(thresholdMs)}`}
          </text>
          {rows.map((row, gi) => {
            const gx = gi * groupW;
            const focused = focusK === row.k;
            const x0 = gx + (groupW - barW * nLanes - (nLanes - 1) * 2) / 2;
            return (
              <CustomTooltip
                key={row.k}
                show
                size="small"
                arrow
                title={latencyTooltip(row, names, { fmtMs, fmtDeltaMs })}
              >
                <g
                  data-turn-group={row.n}
                  onClick={() => onFocusRow?.(row)}
                  style={{ cursor: "pointer" }}
                >
                  <rect
                    x={gx}
                    y={0}
                    width={groupW}
                    height={H}
                    fill={
                      focused
                        ? alpha(theme.palette.primary.main, 0.08)
                        : "transparent"
                    }
                  />
                  {row.lanes.map((l, li) => {
                    if (!l) return null;
                    const bx = x0 + li * (barW + 2);
                    const color =
                      lanes[li]?.color ?? theme.palette.text.secondary;
                    const yTop = y(l.gap);
                    const segs =
                      expanded && l.stage.ok
                        ? [
                            ["stt", 0.4],
                            ["llm", 0.7],
                            ["tts", 1],
                          ]
                        : null;
                    let acc = 0;
                    return (
                      <g
                        key={lanes[li]?.runId ?? li}
                        data-bar={`${row.n}:${li}`}
                      >
                        {segs ? (
                          segs.map(([k, a]) => {
                            const v = l.stage[k];
                            const y1 = y(acc + v);
                            const y2 = y(acc);
                            acc += v;
                            return (
                              <rect
                                key={k}
                                x={bx}
                                y={y1}
                                width={barW}
                                height={Math.max(0.5, y2 - y1)}
                                fill={alpha(color, a)}
                              />
                            );
                          })
                        ) : (
                          <rect
                            x={bx}
                            y={yTop}
                            width={barW}
                            height={Math.max(1, top + plotH - yTop)}
                            fill={color}
                            rx={1}
                          />
                        )}
                        {expanded && l.deadAir ? (
                          <rect
                            x={bx}
                            y={yTop}
                            width={barW}
                            height={Math.max(1, y(deadAirMs) - yTop)}
                            fill={`url(#${hatchId})`}
                            data-dead-air=""
                          />
                        ) : null}
                        {l.interrupted ? (
                          <text
                            x={bx + barW / 2}
                            y={Math.max(10, yTop - 2)}
                            textAnchor="middle"
                            fontSize="10"
                            fill={theme.palette.warning.dark}
                            data-kept-marker=""
                          >
                            ⚡
                          </text>
                        ) : null}
                        {expanded && l.tools.length ? (
                          <rect
                            x={bx}
                            y={top + plotH + 2}
                            width={barW}
                            height={3}
                            fill={
                              l.tools.some((t) => t.status === "error")
                                ? theme.palette.error.main
                                : theme.palette.text.secondary
                            }
                            data-tool-tick=""
                          />
                        ) : null}
                      </g>
                    );
                  })}
                  <text
                    x={gx + groupW / 2}
                    y={H - 2}
                    textAnchor="middle"
                    fontSize="10"
                    fill={focused ? theme.palette.primary.main : textColor}
                  >
                    {row.n}
                  </text>
                </g>
              </CustomTooltip>
            );
          })}
        </svg>
      ) : null}
    </Box>
  );
}

LatencyTimeline.propTypes = {
  rows: PropTypes.array.isRequired,
  lanes: PropTypes.array.isRequired,
  names: PropTypes.arrayOf(PropTypes.string).isRequired,
  thresholdMs: PropTypes.number.isRequired,
  deadAirMs: PropTypes.number,
  expanded: PropTypes.bool,
  focusK: PropTypes.number,
  onFocusRow: PropTypes.func,
};
