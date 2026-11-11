import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { ROW_HEIGHT } from "src/theme/sizes";
import MetricOutcomeBadge from "./MetricOutcomeBadge";
import { DEFAULT_LABELS, isBooleanMetric } from "./builderSpec";
import { formatMetricValue } from "./formatMetricValue";
import { inputLabel, previewNeeds, unitInputs } from "./metricPresentation";
import RecipeSentence from "./MetricSentence";

const grouped = new Intl.NumberFormat(undefined, {
  maximumFractionDigits: 6,
});
// Input values read like the rest of the page: numbers keep digit grouping.
const scalar = (value) =>
  value == null
    ? "No value"
    : typeof value === "boolean"
      ? String(value)
      : typeof value === "number" && Number.isFinite(value)
        ? grouped.format(value)
        : value === ""
          ? "Empty text"
          : String(value);
const count = (value) => new Intl.NumberFormat().format(value);
const isCount = (value) => Number.isSafeInteger(value) && value >= 0;

// Which units the preview read and which it left out, from the counts the
// server returned for the total. Nothing is reconstructed from the rate.
// unit_count is every settled unit read, including those without a value;
// valid_count is the units with a value, which the result rests on. An
// invalid sampling probability withholds the whole value (status
// invalid_probability), so it is said as that, not as "left out".
function basisLines(point, noun) {
  const conversations = noun === "conversations";
  const units = conversations ? "finished conversations" : noun;
  const lines = [];
  if (isCount(point?.unit_count)) {
    if (point.status === "ok" && isCount(point.valid_count))
      lines.push(
        point.valid_count === point.unit_count
          ? `Based on all ${count(point.unit_count)} ${units}.`
          : `Based on ${count(point.valid_count)} of ${count(point.unit_count)} ${units}.`,
      );
    else lines.push(`Read ${count(point.unit_count)} ${units}.`);
  }
  if (
    isCount(point?.invalid_probability_count) &&
    point.invalid_probability_count > 0
  )
    lines.push(
      `${count(point.invalid_probability_count)} had an invalid sampling probability, so no value is shown.`,
    );
  lines.push(
    isCount(point?.missing_count) && point.missing_count > 0
      ? `${count(point.missing_count)} had no value and are left out, not counted as 0.`
      : conversations
        ? "Conversations still in progress, or without a value, are left out, not counted as 0."
        : `${noun.charAt(0).toUpperCase()}${noun.slice(1)} without a value are left out, not counted as 0.`,
  );
  return lines;
}

const ellipsis = {
  display: "block",
  minWidth: 0,
  whiteSpace: "nowrap",
  overflow: "hidden",
  textOverflow: "ellipsis",
};

export default function MetricPreviewPanel({
  preview,
  form,
  sourceLabels,
  idleMessage,
  mode = "new",
}) {
  const { state, data, retry } = preview;
  const boolean = isBooleanMetric(form);
  const labels = { ...DEFAULT_LABELS, ...form.value_labels };
  // A custom formula names its inputs by alias, so the alias is the label.
  const labelFor = (alias) =>
    !form.kind || form.kind === "custom"
      ? alias
      : inputLabel(alias, { inputs: form.inputs, sourceLabels });
  // The ungrouped row is a total; never substitute the first grouped result.
  const point = data?.results?.find((row) => row.group_key === "");
  // The response's own metadata wins; the form only fills what it left out,
  // so the hero reads the same as the list and the metric page.
  const metadata = {
    ...data,
    unit: data?.unit || form.unit || "",
    value_format: data?.value_format || form.value_format,
    value_type: "number",
  };
  const ok =
    state === "ready" && data?.point_status === "ok" && point?.status === "ok";
  const value = ok
    ? formatMetricValue(point.value, metadata, undefined, { display: true })
    : null;
  const unrounded = ok ? formatMetricValue(point.value, metadata) : null;
  const noun =
    data?.unit_noun || (form.level === "span" ? "spans" : "conversations");
  // A unit's value shares the hero's unit and digit grouping. The percent
  // proof (rate_eligible) belongs to the total, so it is never rescaled.
  const sampleValue = (sample) =>
    typeof sample.value_number === "number"
      ? formatMetricValue(sample.value_number, {
          ...metadata,
          value_format: "number",
          rate_eligible: false,
        }) ?? scalar(sample.value_number)
      : scalar(sample.value_text);
  const countSummary =
    boolean &&
    !data?.weighted &&
    point?.status === "ok" &&
    Number.isFinite(point.numerator) &&
    Number.isFinite(point.denominator);
  const loading = ["loading", "busy_retrying"].includes(state);
  const failed = !["idle", "loading", "busy_retrying", "ready"].includes(state);
  const hasSentence = Boolean(form.inputs?.length);
  const samples = data?.sample_units?.slice(0, 20) || [];
  return (
    <Stack
      component="aside"
      aria-label="Metric preview"
      spacing={2.5}
      sx={{ px: 3, py: 2.5, minHeight: "100%" }}
    >
      <Stack direction="row" alignItems="baseline" spacing={1}>
        <Typography component="h2" variant="subtitle1" fontWeight={600}>
          Live preview
        </Typography>
        <Typography variant="caption" color="text.secondary">
          Last 7 days ·{" "}
          {form.level === "span" ? "spans" : "finished conversations"}
        </Typography>
      </Stack>
      <Box
        aria-live="polite"
        aria-busy={loading}
        sx={{
          p: 2,
          border: 1,
          borderColor: "divider",
          borderRadius: 1,
          minHeight: 132,
        }}
      >
        {hasSentence && (
          <Typography
            variant="body2"
            color="text.secondary"
            sx={{ mb: 1, overflowWrap: "anywhere" }}
          >
            <RecipeSentence recipe={form} labelFor={labelFor} />
          </Typography>
        )}
        {state === "idle" && (
          // The icon sits on the message's line, so it reads as one notice
          // under the sentence rather than a loose mark between the two.
          <Stack
            direction="row"
            alignItems="center"
            gap={1}
            sx={{ color: "text.secondary" }}
          >
            <Iconify
              icon="solar:graph-up-linear"
              width={16}
              sx={{ flexShrink: 0 }}
            />
            <Typography variant="body2" color="text.secondary">
              {idleMessage || previewNeeds(form, labelFor)}
            </Typography>
          </Stack>
        )}
        {loading && (
          <Stack direction="row" alignItems="center" gap={1} sx={{ py: 1 }}>
            <CircularProgress size={14} color="inherit" />
            <Typography variant="body2" color="text.secondary">
              {state === "busy_retrying"
                ? "Preview busy. Retrying once…"
                : "Updating preview…"}
            </Typography>
          </Stack>
        )}
        {failed && (
          <Alert
            severity="warning"
            action={
              <Button color="inherit" onClick={retry}>
                Retry
              </Button>
            }
          >
            {state === "busy"
              ? "Preview busy. Try again shortly."
              : state === "superseded"
                ? "Preview was superseded. Retry this definition."
                : "Preview unavailable. Your definition has not been changed."}
          </Alert>
        )}
        {state === "ready" && (
          <Stack spacing={0.5}>
            <Typography
              component="p"
              variant={value != null ? "display" : "title_md"}
              color={value != null ? "text.primary" : "text.secondary"}
              // Rounded for reading; the unrounded value is on hover.
              title={unrounded && unrounded !== value ? unrounded : undefined}
              sx={{ fontVariantNumeric: "tabular-nums" }}
            >
              {value ??
                (data?.point_status === "no_data" ? "No data" : "No value")}
            </Typography>
            {countSummary && (
              <Typography
                variant="body2"
                color="text.primary"
                sx={{ fontVariantNumeric: "tabular-nums" }}
              >
                {count(point.numerator)} of {count(point.denominator)}{" "}
                {data.unit_noun} match
              </Typography>
            )}
            {value == null && (
              <Typography variant="body2" color="text.secondary">
                {data?.point_status === "no_data"
                  ? "No settled units in this window."
                  : `A current value is not available${data?.point_status ? ` (${data.point_status.replaceAll("_", " ")})` : ""}.`}
              </Typography>
            )}
            {/* In place of the server's one fixed summary sentence: what the
                preview read and what it left out, in plain words. */}
            {point && (
              <Stack spacing={0.25} sx={{ pt: 0.5 }}>
                {basisLines(point, noun).map((line) => (
                  <Typography
                    key={line}
                    variant="caption"
                    color="text.secondary"
                    sx={{ fontVariantNumeric: "tabular-nums" }}
                  >
                    {line}
                  </Typography>
                ))}
              </Stack>
            )}
          </Stack>
        )}
      </Box>
      {state === "ready" && samples.length > 0 && (
        <Box>
          <Typography variant="subtitle2" sx={{ mb: 0.5 }}>
            Sample {data.unit_noun}
          </Typography>
          <Table
            size="small"
            aria-label="Preview sample inputs"
            sx={{ tableLayout: "fixed" }}
          >
            <TableHead>
              <TableRow>
                <TableCell sx={{ width: "34%" }}>Unit</TableCell>
                <TableCell>Inputs</TableCell>
                <TableCell
                  align={boolean ? "left" : "right"}
                  sx={{ width: 128 }}
                >
                  {boolean ? "Result" : "Value"}
                </TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {samples.map((sample) => (
                <TableRow
                  key={sample.unit_id}
                  sx={{ height: ROW_HEIGHT.default }}
                >
                  <TableCell>
                    <Tooltip title={sample.unit_id} placement="top-start">
                      <Typography
                        component="span"
                        variant="body2"
                        tabIndex={0}
                        sx={{
                          ...ellipsis,
                          fontFamily: "monospace",
                          color: "text.secondary",
                        }}
                      >
                        {sample.unit_id}
                      </Typography>
                    </Tooltip>
                  </TableCell>
                  <TableCell>
                    {unitInputs(sample).map(([alias, inputValue]) => (
                      <Typography
                        key={alias}
                        variant="body2"
                        component="div"
                        title={`${labelFor(alias)}: ${scalar(inputValue)}`}
                        sx={{
                          ...ellipsis,
                          fontVariantNumeric: "tabular-nums",
                          color:
                            inputValue == null
                              ? "text.secondary"
                              : "text.primary",
                        }}
                      >
                        {labelFor(alias)}: {scalar(inputValue)}
                      </Typography>
                    ))}
                  </TableCell>
                  <TableCell align={boolean ? "left" : "right"}>
                    {sample.status !== "value" ||
                    (boolean && typeof sample.value_bool !== "boolean") ? (
                      <MetricOutcomeBadge
                        reason={sample.no_value_reason || undefined}
                      />
                    ) : boolean ? (
                      <MetricOutcomeBadge
                        outcome={sample.value_bool}
                        label={labels[sample.value_bool ? "match" : "other"]}
                      />
                    ) : (
                      <Typography
                        component="span"
                        variant="body2"
                        title={sampleValue(sample)}
                        sx={{
                          ...ellipsis,
                          fontVariantNumeric: "tabular-nums",
                        }}
                      >
                        {sampleValue(sample)}
                      </Typography>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Box>
      )}
      <Stack direction="row" gap={0.75} alignItems="flex-start">
        <Iconify
          icon="solar:info-circle-linear"
          width={14}
          sx={{ color: "text.disabled", mt: "1px", flexShrink: 0 }}
        />
        <Typography variant="caption" color="text.secondary">
          {mode === "edit"
            ? "This preview is not saved. Saving queues a recalculation; the active version stays visible until its replacement is ready."
            : "This preview is not saved. Saving starts the metric’s first calculation; its values appear on the metric page when it finishes."}
        </Typography>
      </Stack>
    </Stack>
  );
}
MetricPreviewPanel.propTypes = {
  preview: PropTypes.object.isRequired,
  form: PropTypes.object.isRequired,
  sourceLabels: PropTypes.objectOf(PropTypes.string),
  // Overrides the idle copy when the page knows more than the form, e.g. a
  // legacy metric that must be rebuilt first.
  idleMessage: PropTypes.string,
  // "new" before the first save, "edit" when an active version may exist.
  mode: PropTypes.oneOf(["new", "edit"]),
};
