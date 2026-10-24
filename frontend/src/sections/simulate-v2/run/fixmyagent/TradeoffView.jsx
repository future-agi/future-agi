import PropTypes from "prop-types";
import { useMemo } from "react";
import { useTheme } from "@mui/material/styles";
import { Box, Button, Stack, Tooltip, Typography } from "@mui/material";
import { candidatesOf, frontierOf, projectedHeld } from "./tradeoffs";
import { COST_NOISE, PER_CONVERSATION } from "../../_mock/optimizationRuns";

const W = 640;
const H = 220;
const PAD = { l: 40, r: 16, t: 14, b: 30 };
const signed = (n) => `${n > 0 ? "+" : n < 0 ? "−" : "±"}${Math.abs(n)}%`;

/**
 * Quality against cost, with latency alongside — every candidate the search
 * tried, the frontier nothing else beats on all three, and the agent's cost
 * limit. The search's pick is the best-scoring candidate inside the limits;
 * choosing another ships that one instead.
 */
export default function TradeoffView({ record, chosen, onChoose }) {
  const theme = useTheme();
  const result = record.result;
  const candidates = useMemo(() => candidatesOf(record), [record]);
  const frontier = useMemo(() => frontierOf(candidates), [candidates]);
  const onFrontier = new Set(frontier.map((c) => c.n));
  const pick = result?.winner?.n;
  const current = chosen || pick;
  const finalistNs = new Set((result?.finalists || []).map((f) => f.n));
  /* The cost limit as a change on today: a fixed amount read against
     today's cost per conversation, "No increase" within run-to-run noise. */
  const lim = record.limits || {};
  const today = PER_CONVERSATION[record.surface === "voice" ? "voice" : "chat"];
  const ceiling =
    lim.costMode === "abs" && lim.costCeilingAbs != null
      ? Math.round((lim.costCeilingAbs / today - 1) * 1000) / 10
      : (lim.costCeiling ?? 0) <= 0
        ? COST_NOISE
        : lim.costCeiling;
  const costMeasured = record.costMeasured !== false;

  if (!candidates.length) return null;
  const xs = [...candidates.map((c) => c.cost), ceiling, 0];
  const ys = candidates.map((c) => c.score);
  const [x0, x1] = [Math.min(...xs) - 1, Math.max(...xs) + 1];
  const [y0, y1] = [Math.min(...ys, result.base) - 3, Math.max(...ys) + 3];
  const X = (v) => PAD.l + ((v - x0) / (x1 - x0)) * (W - PAD.l - PAD.r);
  const Y = (v) => H - PAD.b - ((v - y0) / (y1 - y0)) * (H - PAD.t - PAD.b);
  const line = frontier.map((c) => `${X(c.cost)},${Y(c.score)}`).join(" ");
  const ink = theme.palette.text.primary;
  const faint = theme.palette.text.disabled;

  return (
    <Box>
      <Typography
        sx={{ px: 2, pt: 1.5, typography: "s2", color: "text.secondary" }}
      >
        {`Every run by target score and cost per conversation. The line joins the best trade-offs — nothing else is at least as good on quality, cost and latency.${costMeasured ? " Runs past the dashed line break the cost limit." : " This agent reports no cost, so the cost limit can't be checked."} Choosing a run other than the search's pick means verifying it before it can ship.`}
      </Typography>
      <Box sx={{ px: 2, pt: 1 }}>
        <svg
          viewBox={`0 0 ${W} ${H}`}
          width="100%"
          style={{ display: "block", maxHeight: 260 }}
        >
          {/* axes */}
          <line
            x1={PAD.l}
            y1={H - PAD.b}
            x2={W - PAD.r}
            y2={H - PAD.b}
            stroke={theme.palette.divider}
          />
          <line
            x1={PAD.l}
            y1={PAD.t}
            x2={PAD.l}
            y2={H - PAD.b}
            stroke={theme.palette.divider}
          />
          <text
            x={W - PAD.r}
            y={H - 8}
            textAnchor="end"
            fontSize="12"
            fill={faint}
          >
            cost per conversation →
          </text>
          <text x={PAD.l + 4} y={PAD.t + 8} fontSize="12" fill={faint}>
            target score ↑
          </text>
          {[y0 + 3, Math.round((y0 + y1) / 2), y1 - 3].map((v) => (
            <text
              key={v}
              x={PAD.l - 6}
              y={Y(v) + 3}
              textAnchor="end"
              fontSize="12"
              fill={faint}
            >{`${Math.round(v)}%`}</text>
          ))}
          {[Math.ceil(x0), 0, ceiling]
            .filter((v, i, a) => a.indexOf(v) === i)
            .map((v) => (
              <text
                key={v}
                x={X(v)}
                y={H - PAD.b + 14}
                textAnchor="middle"
                fontSize="12"
                fill={faint}
              >
                {signed(v)}
              </text>
            ))}
          {/* the starting point and the cost limit */}
          <line
            x1={PAD.l}
            y1={Y(result.base)}
            x2={W - PAD.r}
            y2={Y(result.base)}
            stroke={faint}
            strokeDasharray="2 4"
          />
          <text
            x={W - PAD.r}
            y={Y(result.base) - 4}
            textAnchor="end"
            fontSize="12"
            fill={faint}
          >
            today
          </text>
          <line
            x1={X(ceiling)}
            y1={PAD.t}
            x2={X(ceiling)}
            y2={H - PAD.b}
            stroke={faint}
            strokeDasharray="4 3"
          />
          <text
            x={X(ceiling) + 4}
            y={PAD.t + 8}
            fontSize="12"
            fill={faint}
          >{`cost limit ${signed(ceiling)}`}</text>
          {/* the frontier */}
          {frontier.length > 1 && (
            <polyline
              points={line}
              fill="none"
              stroke={ink}
              strokeOpacity={0.5}
              strokeWidth={1.25}
            />
          )}
          {candidates.map((c) => {
            const on = onFrontier.has(c.n);
            const isCurrent = c.n === current;
            return (
              <g key={c.n}>
                <circle
                  cx={X(c.cost)}
                  cy={Y(c.score)}
                  r={isCurrent ? 6 : on ? 4.5 : 3.5}
                  // eslint-disable-next-line no-restricted-syntax -- ds: data colour
                  fill={isCurrent ? "#7857FC" : on ? ink : "transparent"}
                  // eslint-disable-next-line no-restricted-syntax -- ds: data colour
                  stroke={isCurrent ? "#7857FC" : c.eligible ? ink : faint}
                  strokeOpacity={c.eligible ? 0.8 : 0.6}
                />
                {(on || isCurrent) && (
                  <text
                    x={X(c.cost) + 8}
                    y={Y(c.score) + 3}
                    fontSize="12"
                    // eslint-disable-next-line no-restricted-syntax -- ds: data colour
                    fill={isCurrent ? "#7857FC" : ink}
                  >{`R${c.n}`}</text>
                )}
              </g>
            );
          })}
        </svg>
      </Box>

      {/* Nothing to trade when one candidate wins on every measure — said, not left implied. */}
      {frontier.length === 1 && frontier[0].n === pick && (
        <Typography
          sx={{ px: 2, pt: 1, typography: "s2", color: "text.secondary" }}
        >
          {`Nothing to trade here: Run ${pick} scores highest and costs least among the runs that keep every constraint.`}
        </Typography>
      )}

      {/* the frontier, as choices */}
      <Box sx={{ mt: 1, borderTop: "1px solid", borderColor: "divider" }}>
        {frontier.map((c) => {
          const isPick = c.n === pick;
          const isCurrent = c.n === current;
          const held = projectedHeld(result, c);
          return (
            <Stack
              key={c.n}
              direction="row"
              alignItems="center"
              spacing={2}
              sx={{
                px: 2,
                py: 1,
                "& + &": { borderTop: "1px solid", borderColor: "divider" },
              }}
            >
              <Typography
                sx={{
                  typography: "s2",
                  fontWeight: 600,
                  width: 64,
                  flexShrink: 0,
                }}
              >{`Run ${c.n}`}</Typography>
              <Typography
                sx={{
                  typography: "s2",
                  width: 120,
                  flexShrink: 0,
                  fontVariantNumeric: "tabular-nums",
                }}
              >{`${c.score}% target`}</Typography>
              <Tooltip
                arrow
                title={
                  isPick || finalistNs.has(c.n)
                    ? "Measured on the held-back set."
                    : "Projected from the search's pick until you verify this run."
                }
              >
                <Typography
                  sx={{
                    typography: "s2",
                    color: "text.secondary",
                    width: 140,
                    flexShrink: 0,
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  {held == null
                    ? "—"
                    : `${isPick || finalistNs.has(c.n) ? "" : "≈"}${held}% held back`}
                </Typography>
              </Tooltip>
              <Typography
                sx={{
                  typography: "s2",
                  color: "text.secondary",
                  width: 90,
                  flexShrink: 0,
                  fontVariantNumeric: "tabular-nums",
                }}
              >{`cost ${signed(c.cost)}`}</Typography>
              <Typography
                sx={{
                  typography: "s2",
                  color: "text.secondary",
                  width: 110,
                  flexShrink: 0,
                  fontVariantNumeric: "tabular-nums",
                }}
              >{`latency ${signed(c.latency)}`}</Typography>
              <Typography
                noWrap
                sx={{
                  typography: "s3",
                  color: "text.tertiary",
                  flex: 1,
                  minWidth: 0,
                }}
              >
                {c.proposals.map((p) => p.title).join(" · ") || "no change"}
              </Typography>
              {isCurrent ? (
                <Typography
                  sx={{
                    typography: "s3",
                    fontWeight: 600,
                    color: "text.secondary",
                    flexShrink: 0,
                    width: 92,
                    textAlign: "right",
                  }}
                >
                  {isPick ? "Search's pick" : "Chosen"}
                </Typography>
              ) : (
                <Button
                  size="small"
                  onClick={() => onChoose(isPick ? null : c.n)}
                  sx={{ color: "text.secondary", flexShrink: 0, width: 92 }}
                >
                  {isPick ? "Back to pick" : "Choose this"}
                </Button>
              )}
            </Stack>
          );
        })}
      </Box>
    </Box>
  );
}

TradeoffView.propTypes = {
  record: PropTypes.object,
  chosen: PropTypes.number,
  onChoose: PropTypes.func,
};
