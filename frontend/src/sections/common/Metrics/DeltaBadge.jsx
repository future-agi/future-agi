import React from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import {
  LOW_SAMPLE_N,
  TONE_COLOR,
  deltaTone,
  formatCi,
  formatDelta,
} from "./metricFormat";

/**
 * DeltaBadge — a run's difference from the baseline run on one metric
 * (PRODUCT-SPEC §4, C5, M6). `+7.1 pts` in green/red by direction; grey
 * with no prefix glyph when the 95% CI includes 0 (within noise); a grey
 * `n=20` suffix below LOW_SAMPLE_N shared rows.
 * Tooltip: "Within noise · vs #3A on 236 shared examples · 95% CI ±3.8 pts".
 *
 * Props (frozen):
 * @param {object} props.def  metric def
 * @param {number|null} props.delta  candidate − baseline (metric units)
 * @param {number|null} [props.ci95]  CI half-width (same units)
 * @param {boolean} [props.significant]  from the API; false = within noise
 * @param {number} [props.n]  shared examples the delta is computed on
 * @param {string} [props.vsLabel]  baseline run label, `#3A`
 * @param {boolean} [props.partial]  the candidate is still running
 * @param {"s2"|"s3"} [props.typography="s2"]
 */
const DeltaBadge = ({
  def,
  delta,
  ci95,
  significant,
  n,
  vsLabel,
  partial = false,
  typography = "s2",
}) => {
  if (delta == null || !Number.isFinite(delta)) return null;
  const tone = deltaTone(def, delta, { significant });
  const lowSample = Number.isFinite(n) && n < LOW_SAMPLE_N;
  const ci = formatCi(def, ci95);

  const lines = [];
  if (significant === false)
    lines.push(ci ? `Within noise (95% CI ${ci})` : "Within noise");
  const vs = [
    vsLabel ? `vs ${vsLabel}` : "vs baseline run",
    Number.isFinite(n) ? `on ${n} shared examples` : null,
  ]
    .filter(Boolean)
    .join(" ");
  lines.push(significant === false || !ci ? vs : `${vs} · 95% CI ${ci}`);
  if (lowSample) lines.push(`Low sample: fewer than ${LOW_SAMPLE_N} rows`);
  if (partial) lines.push("Partial: the run is still in progress");

  return (
    <CustomTooltip
      show
      size="small"
      placement="top"
      arrow
      title={
        <Stack spacing={0.25}>
          {lines.map((l) => (
            <Typography key={l} typography="s2">
              {l}
            </Typography>
          ))}
        </Stack>
      }
    >
      <Box
        component="span"
        data-tone={tone}
        sx={{
          display: "inline-flex",
          alignItems: "baseline",
          gap: 0.5,
          whiteSpace: "nowrap",
          cursor: "default",
        }}
      >
        <Typography
          component="span"
          typography={typography}
          fontWeight="fontWeightMedium"
          sx={{
            color: TONE_COLOR[tone],
            fontVariantNumeric: "tabular-nums",
            opacity: partial ? 0.8 : 1,
          }}
        >
          {formatDelta(def, delta)}
        </Typography>
        {lowSample && (
          <Typography
            component="span"
            typography="s3"
            color="text.disabled"
            sx={{ fontVariantNumeric: "tabular-nums" }}
          >
            n={n}
          </Typography>
        )}
      </Box>
    </CustomTooltip>
  );
};

DeltaBadge.propTypes = {
  def: PropTypes.object,
  delta: PropTypes.number,
  ci95: PropTypes.number,
  significant: PropTypes.bool,
  n: PropTypes.number,
  vsLabel: PropTypes.string,
  partial: PropTypes.bool,
  typography: PropTypes.oneOf(["s1", "s2", "s3"]),
};

export default DeltaBadge;
