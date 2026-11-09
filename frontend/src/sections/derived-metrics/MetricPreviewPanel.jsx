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
  Typography,
} from "@mui/material";
import { DEFAULT_LABELS, isBooleanMetric } from "./builderSpec";
import { formatMetricValue } from "./formatMetricValue";

const scalar = (value) =>
  value == null
    ? "No value"
    : typeof value === "boolean"
      ? String(value)
      : value === ""
        ? "Empty text"
        : String(value);
export default function MetricPreviewPanel({ preview, form }) {
  const { state, data, retry } = preview;
  const boolean = isBooleanMetric(form);
  const labels = { ...DEFAULT_LABELS, ...form.value_labels };
  // The ungrouped row is a total; never substitute the first grouped result.
  const point = data?.results?.find((row) => row.group_key === "");
  const value =
    state === "ready" && data?.point_status === "ok" && point?.status === "ok"
      ? formatMetricValue(point.value, { ...data, value_type: "number" })
      : null;
  const countSummary =
    boolean &&
    !data?.weighted &&
    point?.status === "ok" &&
    Number.isFinite(point.numerator) &&
    Number.isFinite(point.denominator);
  const loading = ["loading", "busy_retrying"].includes(state);
  const failed = !["idle", "loading", "busy_retrying", "ready"].includes(state);
  return (
    <Stack
      component="aside"
      aria-label="Metric preview"
      spacing={2.5}
      sx={{ p: 3, height: "100%", bgcolor: "background.neutral" }}
    >
      <Box>
        <Typography component="h2" variant="subtitle2">
          Live preview
        </Typography>
        <Typography variant="caption" color="text.secondary">
          Last 7 days · settled units
        </Typography>
      </Box>
      <Box aria-live="polite" aria-busy={loading}>
        {state === "idle" && (
          <Typography variant="body2" color="text.secondary">
            Choose a project, source and valid calculation to preview.
          </Typography>
        )}
        {loading && (
          <Stack direction="row" alignItems="center" gap={1}>
            <CircularProgress size={16} color="inherit" />
            <Typography variant="body2">
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
          <Stack gap={1}>
            <Typography
              variant="h3"
              component="p"
              sx={{ fontWeight: 600, fontVariantNumeric: "tabular-nums" }}
            >
              {value ??
                (data?.point_status === "no_data" ? "No data" : "No value")}
            </Typography>
            {countSummary && (
              <Typography variant="body2">
                {point.numerator} of {point.denominator} {data.unit_noun} match
              </Typography>
            )}
            {data?.summary && (
              <Typography variant="body2" color="text.secondary">
                {data.summary}
              </Typography>
            )}
            {value == null && (
              <Typography variant="body2" color="text.secondary">
                {data?.point_status === "no_data"
                  ? "No settled units in this window."
                  : `A current value is not available${data?.point_status ? ` (${data.point_status.replaceAll("_", " ")})` : ""}.`}
              </Typography>
            )}
          </Stack>
        )}
      </Box>
      {state === "ready" && data?.sample_units?.length > 0 && (
        <Box sx={{ overflowX: "auto" }}>
          <Typography variant="subtitle2" sx={{ mb: 1 }}>
            Sample {data.unit_noun}
          </Typography>
          <Table size="small" aria-label="Preview sample inputs">
            <TableHead>
              <TableRow>
                <TableCell>Unit</TableCell>
                <TableCell>Inputs</TableCell>
                <TableCell>{boolean ? "Counts" : "Value"}</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {data.sample_units.slice(0, 20).map((sample) => (
                <TableRow key={sample.unit_id}>
                  <TableCell sx={{ maxWidth: 150, overflowWrap: "anywhere" }}>
                    {sample.unit_id}
                  </TableCell>
                  <TableCell sx={{ maxWidth: 220, overflowWrap: "anywhere" }}>
                    {Object.entries({
                      ...sample.inputs,
                      ...sample.inputs_bool,
                      ...sample.inputs_text,
                    }).map(([alias, inputValue]) => (
                      <Typography key={alias} variant="caption" component="div">
                        {form.inputs?.find((input) => input.alias === alias)
                          ?.attribute_key || alias}
                        : {scalar(inputValue)}
                      </Typography>
                    ))}
                  </TableCell>
                  <TableCell>
                    {sample.status !== "value" ? (
                      <>
                        <Typography variant="body2">No value</Typography>
                        <Typography variant="caption" color="text.secondary">
                          {sample.no_value_reason?.replaceAll("_", " ")}
                        </Typography>
                      </>
                    ) : boolean ? (
                      typeof sample.value_bool === "boolean" ? (
                        labels[sample.value_bool ? "match" : "other"]
                      ) : (
                        "No value"
                      )
                    ) : (
                      scalar(sample.value_number ?? sample.value_text)
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Box>
      )}
      <Typography variant="caption" color="text.secondary">
        This preview is not saved history. Saving queues recalculation; the
        active version stays visible until its replacement is ready.
      </Typography>
    </Stack>
  );
}
MetricPreviewPanel.propTypes = {
  preview: PropTypes.object.isRequired,
  form: PropTypes.object.isRequired,
};
