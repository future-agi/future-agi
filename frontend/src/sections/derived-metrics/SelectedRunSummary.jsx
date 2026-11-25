import PropTypes from "prop-types";
import { Alert, Box, Stack, Typography } from "@mui/material";
import { formatMetricWindow, formatLocalMetricWindow } from "./metricTime";

const number = (value) =>
  value == null
    ? "—"
    : new Intl.NumberFormat(undefined, { maximumSignificantDigits: 6 }).format(
        value,
      );
const metricValue = (value) =>
  value == null
    ? "—"
    : new Intl.NumberFormat(
        undefined,
        Math.abs(value) >= 1e9 || (value !== 0 && Math.abs(value) < 0.0001)
          ? { maximumSignificantDigits: 6 }
          : { minimumFractionDigits: 2, maximumFractionDigits: 6 },
      ).format(value);

function meaning(run, result, evaluatorNames, compact) {
  const definition = run.definition;
  const input = definition?.inputs?.[0];
  const source =
    input?.source === "eval"
      ? evaluatorNames[input.config_id] || "the saved evaluation result"
      : input?.attribute_key ||
        input?.field?.replaceAll("_", " ") ||
        "the saved input";
  const formula = definition?.formula || "";
  const calculation = /^mean\(\w+\)$/.test(formula)
    ? `Average of ${source}`
    : /^sum\(\w+\)$/.test(formula)
      ? `Total of ${source}`
      : /^count_if\(/.test(formula)
        ? "Count of matching values"
        : /^ratio\(/.test(formula)
          ? `${metricValue(result.value)} = ${number(result.numerator)} ÷ ${number(result.denominator)} (${definition?.weighting === "inverse_probability" ? "weighted numerator total ÷ weighted denominator total" : "numerator total ÷ denominator total"})`
          : "Calculated from the saved inputs";
  const count = result?.valid_count;
  const unit = definition?.selection_unit || "unit";
  const scope =
    count == null
      ? ""
      : ` across ${number(count)} ${unit}${count === 1 ? "" : "s"}`;
  if (compact) return `${calculation}${scope}.`;
  return `${calculation}${scope}, from ${formatLocalMetricWindow(run)}.${definition?.weighting === "inverse_probability" ? " Inverse-probability weighted." : ""}`;
}

/** Only consumes the selected run's immutable recipe and committed result. */
export default function SelectedRunSummary({
  run,
  result,
  compact = false,
  readNotice = "",
  evaluatorNames = {},
  children,
  actions,
  detailsOpen,
  onDetailsToggle,
  detailsRef,
  intervalReason,
}) {
  const definition = run.definition;
  const interval = result?.uncertainty;
  const hasInterval =
    interval?.status === "ok" &&
    interval.ci_low != null &&
    interval.ci_high != null;
  const weighted = definition?.weighting === "inverse_probability";
  return (
    <Stack
      component="section"
      aria-label={compact ? "Saved result context" : "Selected saved run"}
      gap={compact ? 0.5 : 1.5}
      sx={{
        minWidth: 0,
        borderBottom: compact ? 0 : 1,
        borderColor: "divider",
        pb: compact ? 0 : 3,
        mt: compact ? 1 : 0,
        textAlign: "left",
        overflowWrap: "anywhere",
        color: "text.primary",
      }}
    >
      <Typography variant="subtitle2">
        {compact ? "Saved result" : "Selected saved run"} · version{" "}
        {run.metric_version}
      </Typography>
      {readNotice && <Alert severity="info">{readNotice}</Alert>}
      {!readNotice && ["queued", "running", "failed"].includes(run.status) && (
        <Alert severity={run.status === "failed" ? "warning" : "info"}>
          {run.result_id
            ? `Recompute ${run.status}. This is the last committed result, not a new result.`
            : `Run ${run.status}. No committed result yet.`}
        </Alert>
      )}
      {result ? (
        <>
          {!compact && (
            <Typography
              component="h2"
              sx={{
                fontSize: 24,
                lineHeight: "32px",
                fontWeight: 600,
                fontVariantNumeric: "tabular-nums",
              }}
            >
              {metricValue(result.value)}
            </Typography>
          )}
          {result.value == null ? (
            <Typography variant="body2">
              No value — {result.status?.replaceAll("_", " ") || "unavailable"}.
              Unavailable is not zero.
            </Typography>
          ) : (
            <>
              <Typography variant={compact ? "caption" : "body2"}>
                {meaning(run, result, evaluatorNames, compact)}
              </Typography>
              {!compact && (
                <Typography variant="body2">
                  {hasInterval
                    ? `95% interval ${number(interval.ci_low)} – ${number(interval.ci_high)}`
                    : "Point estimate only — 95% interval unavailable."}
                </Typography>
              )}
            </>
          )}
        </>
      ) : (
        <Typography variant="body2">
          {run.result_id
            ? "Select the overall rollup to inspect the committed value; unavailable reads are not zero."
            : "No committed result yet."}
        </Typography>
      )}
      {actions}
      <Box
        component="details"
        open={detailsOpen}
        onToggle={
          onDetailsToggle
            ? (e) => onDetailsToggle(e.currentTarget.open)
            : undefined
        }
        id={compact ? undefined : "saved-run-results"}
        ref={detailsRef}
        tabIndex={-1}
      >
        <Box component="summary" sx={{ cursor: "pointer", fontWeight: 500 }}>
          Details
        </Box>
        <Stack gap={1.5} sx={{ mt: 1.5 }}>
          <Typography variant={compact ? "caption" : "body2"}>
            Window (UTC): {formatMetricWindow(run)}
          </Typography>
          <Typography variant={compact ? "caption" : "body2"}>
            Exact saved boundaries: {run.window_start} to {run.window_end}
          </Typography>
          {run.source_run_id && (
            <Typography variant="body2">
              Evaluation task: {run.source_run_id}
            </Typography>
          )}
          {(definition?.filters?.length > 0 ||
            run.filters?.length > 0 ||
            definition?.cohort_key) && (
            <Typography variant="body2" color="text.secondary">
              {definition?.filters?.length || 0} definition filters ·{" "}
              {run.filters?.length || 0} run filters
              {definition?.cohort_key
                ? ` · Cohort: ${definition.cohort_key}`
                : ""}
              . Exact filters are in the saved definition below.
            </Typography>
          )}
          {(interval?.reason || intervalReason) && (
            <Typography variant="caption" color="text.secondary">
              {compact ? "No 95% interval: " : "Interval detail: "}
              {(interval?.reason || intervalReason).replaceAll("_", " ")}
            </Typography>
          )}
          {result && (
            <>
              <Typography variant={compact ? "caption" : "body2"}>
                {number(result.unit_count)}{" "}
                {definition?.selection_unit
                  ? `${definition.selection_unit} units`
                  : "units"}{" "}
                · {number(result.valid_count)} valid ·{" "}
                {number(result.missing_count)} missing inputs ·{" "}
                {number(result.invalid_probability_count)} invalid probability
              </Typography>
              <Typography variant={compact ? "caption" : "body2"}>
                {weighted ? "Weighted numerator total" : "Numerator total"}:{" "}
                {number(result.numerator)} ·{" "}
                {weighted ? "weighted denominator total" : "denominator total"}:{" "}
                {number(result.denominator)}.
              </Typography>
              <Typography variant="caption" color="text.secondary">
                Totals are formula contributions, not necessarily counts of
                people or sessions. Missing inputs are excluded, not replaced
                with zero; invalid probabilities invalidate the rollup. Inspect
                contributors for further exclusions.
              </Typography>
            </>
          )}
          {definition ? (
            <>
              <Typography variant="body2">
                {weighted
                  ? "Inverse-probability weighted"
                  : definition.weighting === "none"
                    ? "Unweighted"
                    : "Weighting unavailable"}{" "}
                · selection unit: {definition.selection_unit}
              </Typography>
              <Typography variant="subtitle2">
                Saved definition · version {definition.version}
              </Typography>
              <Typography
                component="pre"
                variant="body2"
                sx={{
                  whiteSpace: "pre-wrap",
                  overflowWrap: "anywhere",
                  fontFamily: "monospace",
                  m: 0,
                }}
              >
                {definition.formula}
              </Typography>
              <Typography variant="caption" color="text.secondary">
                For ratios, the formula defines numerator and denominator
                contributions per unit before aggregation.
              </Typography>
              <Typography variant="body2">
                Definition filters:{" "}
                {(definition.filters || [])
                  .map((filter) => `${filter.field} = ${filter.value}`)
                  .join(" · ") || "None"}
              </Typography>
              <Typography variant="body2">
                Frozen run filters:{" "}
                {(run.filters || [])
                  .map((filter) => `${filter.field} = ${filter.value}`)
                  .join(" · ") || "None"}
              </Typography>
              {definition.cohort_key && (
                <Typography variant="body2">
                  Cohort attribute: {definition.cohort_key}
                </Typography>
              )}
              {(definition.inputs || []).map((input) => (
                <Typography
                  key={input.alias}
                  variant="body2"
                  sx={{ overflowWrap: "anywhere" }}
                >
                  {input.alias}:{" "}
                  {input.source === "eval"
                    ? `${evaluatorNames[input.config_id] || "Saved evaluator (name unavailable; binding preserved)"} · evaluator ID: ${input.config_id} · hash ${input.config_hash || "legacy unversioned"}`
                    : `${input.field} ${input.attribute_key || ""}`}{" "}
                  · {input.reducer}
                </Typography>
              ))}
              <Typography
                variant="caption"
                sx={{ display: "block", overflowWrap: "anywhere" }}
              >
                Definition hash: {definition.definition_hash}
              </Typography>
            </>
          ) : (
            <Typography variant="body2">
              Saved definition unavailable. The current editor is not evidence
              of this run&apos;s formula or weighting.
            </Typography>
          )}
          {children}
        </Stack>
      </Box>
    </Stack>
  );
}
SelectedRunSummary.propTypes = {
  run: PropTypes.object.isRequired,
  result: PropTypes.object,
  compact: PropTypes.bool,
  readNotice: PropTypes.string,
  evaluatorNames: PropTypes.object,
  children: PropTypes.node,
  actions: PropTypes.node,
  detailsOpen: PropTypes.bool,
  onDetailsToggle: PropTypes.func,
  detailsRef: PropTypes.object,
  intervalReason: PropTypes.string,
};
