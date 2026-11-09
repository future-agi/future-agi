import React from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import { alpha, useTheme } from "@mui/material/styles";
import CustomTooltip from "src/components/tooltip";

const tabular = { fontVariantNumeric: "tabular-nums" };

/**
 * Pass / Fail / Error per run as one stacked bar per run (status colours,
 * always with the counts as text so identity is never colour-alone).
 */
function OutcomeBars({ bins, runs, colors }) {
  const theme = useTheme();
  const tone = {
    Pass: theme.palette.green[500],
    Fail: theme.palette.red[500],
    Error: theme.palette.grey[400],
  };
  return (
    <Stack gap={0.75}>
      {runs.map((run) => {
        const parts = bins.map((b) => ({
          label: b.label,
          n: b.counts?.[run.run_id] ?? 0,
        }));
        const total = parts.reduce((s, p) => s + p.n, 0) || 1;
        return (
          <Stack key={run.run_id} direction="row" alignItems="center" gap={1}>
            <Box
              sx={{
                width: 3,
                height: 14,
                borderRadius: 1,
                bgcolor: colors?.[run.run_id]?.solid ?? "text.disabled",
                flexShrink: 0,
              }}
            />
            <Typography
              typography="s3"
              color="text.secondary"
              sx={{ width: 28, ...tabular }}
            >
              {run.label}
            </Typography>
            <CustomTooltip
              show
              size="small"
              placement="top"
              arrow
              title={parts.map((p) => `${p.label} ${p.n}`).join(" · ")}
            >
              <Stack
                direction="row"
                sx={{ flex: 1, height: 10, gap: "2px", minWidth: 0 }}
                // L2 G2 (a11y #9a): a labelled bar is an image to AT.
                role="img"
                aria-label={`${run.label}: ${parts.map((p) => `${p.n} ${p.label}`).join(", ")}`}
              >
                {parts
                  .filter((p) => p.n > 0)
                  .map((p) => (
                    <Box
                      key={p.label}
                      sx={{
                        width: `${(p.n / total) * 100}%`,
                        bgcolor: tone[p.label] ?? theme.palette.grey[400],
                        borderRadius: "2px",
                      }}
                    />
                  ))}
              </Stack>
            </CustomTooltip>
            <Typography
              typography="s3"
              color="text.secondary"
              sx={{
                width: 92,
                textAlign: "right",
                whiteSpace: "nowrap",
                ...tabular,
              }}
            >
              {parts
                .filter((p) => p.label !== "Error" || p.n > 0)
                .map((p) => p.n)
                .join(" · ")}
            </Typography>
          </Stack>
        );
      })}
      <Stack direction="row" gap={1.5} sx={{ pl: 5 }}>
        {bins
          .filter(
            (b) =>
              b.label !== "Error" || runs.some((r) => b.counts?.[r.run_id]),
          )
          .map((b) => (
            <Stack key={b.label} direction="row" alignItems="center" gap={0.5}>
              <Box
                sx={{
                  width: 8,
                  height: 8,
                  borderRadius: "2px",
                  bgcolor: tone[b.label],
                }}
              />
              <Typography typography="s3" color="text.secondary">
                {b.label}
              </Typography>
            </Stack>
          ))}
      </Stack>
    </Stack>
  );
}

OutcomeBars.propTypes = {
  bins: PropTypes.array,
  runs: PropTypes.array,
  colors: PropTypes.object,
};

/**
 * MiniHistogram — 10-bin distribution per run (grouped, in run colours) with
 * a dashed threshold line ("Pass ≥ 0.70"), or Pass/Fail/Error bars for
 * pass/fail metrics. Hover a bin for its counts.
 */
const MiniHistogram = ({
  histogram,
  runs,
  colors,
  threshold,
  thresholdLabel,
  formatX = (v) => String(v),
  height = 64,
}) => {
  const theme = useTheme();
  const bins = histogram?.bins ?? [];
  if (!bins.length || !runs?.length) return null;
  if (histogram.kind === "categorical")
    return <OutcomeBars bins={bins} runs={runs} colors={colors} />;

  const max = Math.max(
    1,
    ...bins.flatMap((b) => runs.map((r) => b.counts?.[r.run_id] ?? 0)),
  );
  const lo = bins[0].x0;
  const hi = bins[bins.length - 1].x1;
  const thresholdPct =
    threshold != null && hi > lo ? ((threshold - lo) / (hi - lo)) * 100 : null;

  return (
    <Box sx={{ position: "relative", pt: thresholdPct != null ? 1.75 : 0 }}>
      <Stack
        direction="row"
        alignItems="flex-end"
        sx={{
          height,
          gap: "2px",
          borderBottom: "1px solid",
          borderColor: "divider",
          position: "relative",
        }}
      >
        {bins.map((b) => (
          <CustomTooltip
            key={`${b.x0}-${b.x1}`}
            show
            size="small"
            placement="top"
            arrow
            title={
              <Stack gap={0.25}>
                <Typography typography="s3" fontWeight={600}>
                  {formatX(b.x0)} – {formatX(b.x1)}
                </Typography>
                {runs.map((r) => (
                  <Typography key={r.run_id} typography="s3" sx={tabular}>
                    {r.label} · {b.counts?.[r.run_id] ?? 0} rows
                  </Typography>
                ))}
              </Stack>
            }
          >
            <Stack
              direction="row"
              alignItems="flex-end"
              sx={{
                flex: 1,
                height: "100%",
                gap: "1px",
                px: "1px",
                borderRadius: "2px",
                "&:hover": { bgcolor: "action.hover" },
              }}
            >
              {runs.map((r) => {
                const n = b.counts?.[r.run_id] ?? 0;
                return (
                  <Box
                    key={r.run_id}
                    sx={{
                      flex: 1,
                      height: n ? `${Math.max(4, (n / max) * 100)}%` : 0,
                      bgcolor:
                        colors?.[r.run_id]?.solid ??
                        alpha(theme.palette.text.primary, 0.4),
                      borderRadius: "2px 2px 0 0",
                    }}
                  />
                );
              })}
            </Stack>
          </CustomTooltip>
        ))}
        {thresholdPct != null && (
          <Box
            sx={{
              position: "absolute",
              left: `${thresholdPct}%`,
              top: -14,
              bottom: 0,
              borderLeft: "1.5px dashed",
              borderColor: "text.secondary",
              pointerEvents: "none",
            }}
          >
            <Typography
              typography="s3"
              color="text.secondary"
              sx={{
                position: "absolute",
                top: -2,
                left: 4,
                whiteSpace: "nowrap",
                lineHeight: "12px",
              }}
            >
              {thresholdLabel}
            </Typography>
          </Box>
        )}
      </Stack>
      <Stack direction="row" justifyContent="space-between" sx={{ mt: 0.25 }}>
        <Typography typography="s3" color="text.disabled" sx={tabular}>
          {formatX(lo)}
        </Typography>
        <Typography typography="s3" color="text.disabled" sx={tabular}>
          {formatX(hi)}
        </Typography>
      </Stack>
    </Box>
  );
};

MiniHistogram.propTypes = {
  histogram: PropTypes.shape({
    kind: PropTypes.string,
    bins: PropTypes.array,
  }),
  runs: PropTypes.arrayOf(
    PropTypes.shape({ run_id: PropTypes.string, label: PropTypes.string }),
  ),
  colors: PropTypes.object,
  threshold: PropTypes.number,
  thresholdLabel: PropTypes.string,
  formatX: PropTypes.func,
  height: PropTypes.number,
};

export default MiniHistogram;
