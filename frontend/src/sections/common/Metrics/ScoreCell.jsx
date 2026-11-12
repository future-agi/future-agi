import React from "react";
import PropTypes from "prop-types";
import { Box, IconButton, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { formatDelta, formatValue, heatColor } from "./metricFormat";
import { fmtScore } from "src/sections/common/format/fmt";

const MOVE_COLOR = {
  improved: "green.600",
  regressed: "red.600",
  unchanged: "text.disabled",
};

const isRate = (def) =>
  ["pass_fail", "thumbs", "pairwise"].includes(def?.output_type);

/** Per-row corner delta: `↑` / `↓` for a pass/fail flip, `+0.12` for a score. */
function cornerDelta(def, delta, movement) {
  if (!movement || movement === "unchanged" || movement === "not_comparable")
    return null;
  if (isRate(def) || delta == null || !Number.isFinite(delta))
    return movement === "improved" ? "↑" : "↓";
  return formatDelta(def, delta);
}

/**
 * ScoreCell — one run's score on one row (PRODUCT-SPEC §4, §3.10.7).
 * Heat background from the eval-cell scale (direction-aware), the value,
 * a corner delta coloured by movement, a flaky dot, a human-override icon,
 * an inline reason and a hover `Filter by this`. Fills its grid cell; give
 * the AG Grid cell `padding: 0`.
 *
 * Props (frozen):
 * @param {object} props.def  metric def
 * @param {number|null} props.value  per-row value (trial mean)
 * @param {string} [props.display]  API display text (`Fail`, `2/3`, `0.67`);
 *   formatted from `value` when absent
 * @param {number} [props.delta]  vs the baseline run on this row
 * @param {"improved"|"regressed"|"unchanged"|"not_comparable"} [props.movement]
 * @param {boolean|{std?: number, trials?: number}} [props.flaky]
 * @param {{display?: string, by?: string}|boolean} [props.override]  a human
 *   label disagrees with the eval
 * @param {string} [props.error]  eval error message (replaces the value)
 * @param {string} [props.reason]  judge reason
 * @param {boolean} [props.showReason]  reason inline (2 lines) under the value
 * @param {Function} [props.onFilter]  hover action "Filter by this"
 */
const ScoreCell = ({
  def,
  value,
  display,
  delta,
  movement,
  flaky,
  override,
  error,
  reason,
  showReason = false,
  onFilter,
}) => {
  const hasValue = value != null && Number.isFinite(value);
  const text = error
    ? "Error"
    : display ?? (hasValue ? formatValue(def, value, { agg: "value" }) : null);
  const bg = error
    ? "var(--score-red-light)"
    : hasValue
      ? heatColor(def, value)
      : null;
  const corner = error ? null : cornerDelta(def, delta, movement);
  const flakyStd = typeof flaky === "object" && flaky ? flaky.std : null;
  // W1-8: how unsure the row is (1 − |2p − 1|: 1 = an even split).
  const flakyScore =
    typeof flaky === "object" && flaky && Number.isFinite(flaky.flakiness)
      ? flaky.flakiness
      : null;

  return (
    <Box
      sx={{
        position: "relative",
        height: "100%",
        width: "100%",
        minHeight: 32,
        px: 1,
        py: 0.75,
        bgcolor: bg ?? "transparent",
        display: "flex",
        flexDirection: "column",
        gap: 0.25,
        overflow: "hidden",
        "& .score-cell-filter": { opacity: 0 },
        "&:hover .score-cell-filter": { opacity: 1 },
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        gap={0.75}
        sx={{ minWidth: 0 }}
      >
        {text == null ? (
          <Typography typography="s2" color="text.disabled" noWrap>
            Not scored
          </Typography>
        ) : (
          <CustomTooltip
            show={!!error}
            size="small"
            placement="top"
            arrow
            title={error ? `Eval error: ${error}` : ""}
          >
            <Typography
              typography="s2_1"
              color={error ? "red.700" : "text.primary"}
              fontWeight={error ? 500 : 400}
              noWrap
              sx={{ fontVariantNumeric: "tabular-nums", minWidth: 0 }}
            >
              {error ? `Error · ${error}` : text}
            </Typography>
          </CustomTooltip>
        )}
        {flaky && (
          <CustomTooltip
            show
            size="small"
            placement="top"
            arrow
            title={`Flaky: trials disagree${
              flakyScore != null ? ` · flakiness ${fmtScore(flakyScore)}` : ""
            }${flakyStd != null ? ` (σ ${fmtScore(flakyStd)})` : ""}`}
          >
            <Box
              aria-label="Flaky"
              sx={{
                width: 6,
                height: 6,
                borderRadius: "50%",
                bgcolor: "amber.500",
                flexShrink: 0,
              }}
            />
          </CustomTooltip>
        )}
        {override && (
          <CustomTooltip
            show
            size="small"
            placement="top"
            arrow
            title={
              typeof override === "object"
                ? `Human label: ${override.display ?? "disagrees"}${override.by ? ` · ${override.by}` : ""}`
                : "A human label disagrees with this eval"
            }
          >
            <Box
              component="span"
              aria-label="Human override"
              sx={{
                display: "inline-flex",
                color: "text.secondary",
                flexShrink: 0,
              }}
            >
              <Iconify icon="mdi:account-outline" width={14} />
            </Box>
          </CustomTooltip>
        )}
        <Box sx={{ flex: 1 }} />
        {onFilter && (
          <CustomTooltip
            show
            size="small"
            placement="top"
            arrow
            title="Filter by this"
          >
            <IconButton
              className="score-cell-filter"
              size="small"
              aria-label="Filter by this"
              onClick={(e) => {
                e.stopPropagation();
                onFilter();
              }}
              sx={{ p: 0.25, transition: "opacity 120ms" }}
            >
              <Iconify icon="material-symbols:filter-list-rounded" width={14} />
            </IconButton>
          </CustomTooltip>
        )}
        {corner && (
          <Typography
            typography="s3"
            fontWeight={600}
            sx={{
              color: MOVE_COLOR[movement] ?? "text.secondary",
              fontVariantNumeric: "tabular-nums",
              flexShrink: 0,
            }}
          >
            {corner}
          </Typography>
        )}
      </Stack>
      {showReason && reason && !error && (
        <Typography
          typography="s3"
          color="text.secondary"
          sx={{
            display: "-webkit-box",
            WebkitLineClamp: 2,
            WebkitBoxOrient: "vertical",
            overflow: "hidden",
            whiteSpace: "normal",
            wordBreak: "break-word",
          }}
        >
          {reason}
        </Typography>
      )}
    </Box>
  );
};

ScoreCell.propTypes = {
  def: PropTypes.object,
  value: PropTypes.number,
  display: PropTypes.string,
  delta: PropTypes.number,
  movement: PropTypes.oneOf([
    "improved",
    "regressed",
    "unchanged",
    "not_comparable",
    null,
  ]),
  flaky: PropTypes.oneOfType([PropTypes.bool, PropTypes.object]),
  override: PropTypes.oneOfType([PropTypes.bool, PropTypes.object]),
  error: PropTypes.string,
  reason: PropTypes.string,
  showReason: PropTypes.bool,
  onFilter: PropTypes.func,
};

export default ScoreCell;
