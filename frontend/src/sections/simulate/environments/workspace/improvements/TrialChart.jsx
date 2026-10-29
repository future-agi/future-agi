import PropTypes from "prop-types";
import { useLayoutEffect, useRef, useState } from "react";
import { alpha, useTheme } from "@mui/material/styles";
import { Box, Stack, Typography } from "@mui/material";

import { pct, pts } from "./fixRunView";

const DEFAULT_W = 720;
const H = 190;
const PAD = { l: 40, r: 44, t: 28, b: 24 };
const num = { fontVariantNumeric: "tabular-nums" };

// The design's trial chart (RunsOverview in trial mode), hand-built SVG: the
// target pass rate after each trial, against the rate before the change.
function useWidth() {
  const ref = useRef(null);
  const [width, setWidth] = useState(DEFAULT_W);
  useLayoutEffect(() => {
    if (!ref.current || typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(([entry]) => {
      const next = Math.round(entry.contentRect.width);
      if (next > 0) setWidth(next);
    });
    observer.observe(ref.current);
    return () => observer.disconnect();
  }, []);
  return [ref, width];
}

export default function TrialChart({ candidates }) {
  const theme = useTheme();
  const [ref, W] = useWidth();
  const trials = [...candidates].sort((a, b) => a.attempt - b.attempt);
  if (!trials.length) return null;
  const rates = trials.map((c) => pct(c.verdict.target_pass_rate[1]));
  const before = pct(trials[0].verdict.target_pass_rate[0]);
  const best = Math.max(...rates);
  const n = trials.length;
  const iw = W - PAD.l - PAD.r;
  const ih = H - PAD.t - PAD.b;
  const inset = n > 1 ? 18 : 0;
  const x = (i) =>
    n === 1 ? PAD.l + iw / 2 : PAD.l + inset + (i / (n - 1)) * (iw - inset * 2);
  const y = (v) => PAD.t + ih - (v / 100) * ih;
  const ink = theme.palette.text.primary;
  const paper = theme.palette.background.paper;
  const sub = theme.palette.text.disabled;
  const pick =
    trials.find((c) => c.outcome === "winner") || trials[rates.indexOf(best)];
  const gain = pts(pick.verdict.target_pass_rate);
  const lead = `pass rate on the target scenarios · Trial ${pick.attempt} · ${
    pick.outcome === "winner" ? "winner" : "best so far"
  } ${gain >= 0 ? "▲" : "▼"} ${Math.abs(gain)} pts vs the agent before the change, on the same scenarios`;
  return (
    <Box
      sx={{
        mb: 2,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        bgcolor: "background.paper",
        overflow: "hidden",
      }}
    >
      <Stack
        direction="row"
        alignItems="baseline"
        spacing={1}
        sx={{
          px: 2.5,
          py: 1.5,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <Typography sx={{ typography: "m2", fontWeight: 700, ...num }}>
          {pct(pick.verdict.target_pass_rate[1])}
          <Box
            component="span"
            sx={{ typography: "s2", color: "text.subtitle", ml: 0.25 }}
          >
            %
          </Box>
        </Typography>
        <Typography sx={{ typography: "s2", color: "text.secondary" }}>
          {lead}
        </Typography>
      </Stack>
      <Stack direction="row" alignItems="center" sx={{ px: 2.5, pt: 1.5 }}>
        <Typography sx={{ typography: "s3", color: "text.subtitle", flex: 1 }}>
          {`Each point is one finished trial, oldest to latest · ${n} ${n === 1 ? "trial" : "trials"} · ${before}% before the change`}
        </Typography>
        <Stack direction="row" alignItems="center" spacing={0.75}>
          <Box sx={{ width: 14, height: 2, bgcolor: "text.primary" }} />
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            Scenario pass rate
          </Typography>
        </Stack>
      </Stack>
      <Box ref={ref} sx={{ px: 2.5, pb: 1, width: "100%", minWidth: 0 }}>
        <svg
          width={W}
          height={H}
          role="img"
          aria-label="Target pass rate per trial"
          style={{ display: "block" }}
        >
          {[100, 50, 0].map((t) => (
            <g key={t}>
              <line
                x1={PAD.l}
                x2={W - PAD.r}
                y1={y(t)}
                y2={y(t)}
                stroke={alpha(ink, 0.07)}
              />
              <text
                x={W - PAD.r + 8}
                y={y(t) + 3.5}
                fontSize="10.5"
                fill={sub}
                style={num}
              >
                {`${t}%`}
              </text>
            </g>
          ))}
          <text
            x={PAD.l - 10}
            y={H - 6}
            textAnchor="end"
            fontSize="10.5"
            fill={sub}
          >
            Trial
          </text>
          <line
            x1={PAD.l}
            x2={W - PAD.r}
            y1={y(before)}
            y2={y(before)}
            stroke={alpha(ink, 0.35)}
            strokeDasharray="4 3"
          />
          <polyline
            points={rates.map((r, i) => `${x(i)},${y(r)}`).join(" ")}
            fill="none"
            stroke={alpha(ink, 0.9)}
            strokeWidth={1.5}
            strokeLinejoin="round"
          />
          {trials.map((c, i) => {
            const top = rates[i] === best;
            return (
              <g key={c.id} data-testid="trial-point">
                <circle
                  cx={x(i)}
                  cy={y(rates[i])}
                  r={top ? 5 : 3.5}
                  fill={top ? ink : paper}
                  stroke={ink}
                  strokeWidth={1.5}
                />
                <text
                  x={x(i)}
                  y={H - 6}
                  textAnchor="middle"
                  fontSize="10.5"
                  fill={sub}
                  style={num}
                >
                  {c.attempt}
                </text>
              </g>
            );
          })}
        </svg>
      </Box>
    </Box>
  );
}

TrialChart.propTypes = { candidates: PropTypes.array.isRequired };
