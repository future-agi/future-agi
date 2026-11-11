import PropTypes from "prop-types";
import { Box, Stack, Tooltip, Typography } from "@mui/material";

import { metricCopy, metricCountCopy } from "./copy";
import { pointValueState, unitValueState } from "./unitValueState";
import { formatMetricInterval, formatMetricValue } from "./formatMetricValue";

/**
 * Display-only. `source="unit"` takes MetricUnitValueSerializer plus `field`
 * from MetricFieldMetadataSerializer. `source="point"` takes MetricPointSerializer.
 * The caller owns requests, feature gating, and loading/error/stale lifecycle.
 * No hydrated-list-envelope aliases or inferred API fields are accepted here.
 */
export default function MetricValueCell({
  value,
  field,
  source = "unit",
  loading = false,
  error = false,
  stale = false,
  valueMode = "materialized",
  locale,
  dense = false,
}) {
  const options = { loading, error, stale, valueMode };
  const state =
    source === "point"
      ? pointValueState(value, options)
      : unitValueState(value, field, options);
  const hasValue = state.state === "value" || state.state === "stale";
  const metadata =
    source === "point" ? { ...value, value_type: "number" } : field;
  // A point is an aggregate in a headline position (a list cell, the
  // previous period), so it is rounded for reading like the KPI.
  const display = source === "point";
  const formatted = hasValue
    ? formatMetricValue(state.value, metadata, locale, { display })
    : null;
  // The unrounded value, on hover, when rounding changed what shows.
  const unrounded =
    display && hasValue
      ? formatMetricValue(state.value, metadata, locale)
      : null;
  const estimated = hasValue && source === "point" && value.weighted === true;
  const isStale = state.state === "stale";
  const explanation = [
    estimated ? metricCopy.estimateExplanation : "",
    isStale ? metricCopy.staleExplanation : "",
    !hasValue ? metricCopy.missingExplanation : "",
  ]
    .filter(Boolean)
    .join(" ");
  const showCounts = source === "point" || field?.projection === true;
  const counts = showCounts
    ? metricCountCopy(
        value,
        source === "point" ? value?.unit_noun : undefined,
        locale,
      )
    : [];
  const label = hasValue ? formatted : state.label;
  // Dense rows (lists) keep every returned line, one line each; the full text
  // stays in the DOM and in a native title for anything that is cut off.
  const lineSx = dense
    ? {
        display: "block",
        whiteSpace: "nowrap",
        overflow: "hidden",
        textOverflow: "ellipsis",
      }
    : {};

  return (
    <Stack
      component="span"
      spacing={dense ? 0.25 : 0.5}
      data-value-state={state.state}
      aria-busy={loading || undefined}
      sx={{ minWidth: 0, color: "text.primary", overflowWrap: "anywhere" }}
    >
      <Tooltip title={explanation} describeChild>
        <Box
          component="span"
          tabIndex={explanation ? 0 : undefined}
          sx={{
            display: "inline-flex",
            alignItems: "baseline",
            flexWrap: "wrap",
            gap: 0.5,
            width: "fit-content",
            maxWidth: "100%",
            borderRadius: 0.5,
            "&:focus-visible": {
              outline: "2px solid",
              outlineColor: "text.primary",
              outlineOffset: 2,
            },
          }}
        >
          {estimated && (
            <Box component="span" aria-hidden="true">
              ≈
            </Box>
          )}
          <Typography
            component="span"
            variant="body2"
            title={unrounded && unrounded !== formatted ? unrounded : undefined}
            sx={{
              fontWeight: hasValue ? 600 : 400,
              color: hasValue ? "text.primary" : "text.secondary",
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {label}
          </Typography>
          {estimated && (
            <Typography
              component="span"
              variant="caption"
              color="text.secondary"
            >
              {metricCopy.estimate}
            </Typography>
          )}
          {isStale && (
            <Typography
              component="span"
              variant="caption"
              color="text.secondary"
            >
              · {metricCopy.stale}
            </Typography>
          )}
        </Box>
      </Tooltip>
      {hasValue && source === "point" && (
        <Typography
          component="span"
          variant="caption"
          color="text.secondary"
          title={
            dense ? formatMetricInterval(value, locale, { display }) : undefined
          }
          sx={{ fontVariantNumeric: "tabular-nums", ...lineSx }}
        >
          {formatMetricInterval(value, locale, { display })}
        </Typography>
      )}
      {!loading &&
        !error &&
        counts.map((line) => (
          <Typography
            component="span"
            variant="caption"
            color="text.secondary"
            key={line}
            title={dense ? line : undefined}
            sx={{ fontVariantNumeric: "tabular-nums", ...lineSx }}
          >
            {line}
          </Typography>
        ))}
    </Stack>
  );
}

MetricValueCell.propTypes = {
  value: PropTypes.object,
  field: PropTypes.shape({
    value_type: PropTypes.oneOf(["number", "boolean", "text"]),
    projection: PropTypes.bool,
    value_format: PropTypes.string,
    unit: PropTypes.string,
    value_labels: PropTypes.objectOf(PropTypes.string),
  }),
  source: PropTypes.oneOf(["unit", "point"]),
  loading: PropTypes.bool,
  error: PropTypes.bool,
  stale: PropTypes.bool,
  valueMode: PropTypes.oneOf(["materialized", "on_demand"]),
  locale: PropTypes.string,
  dense: PropTypes.bool,
};
