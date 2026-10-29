import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  MenuItem,
  Stack,
  Typography,
} from "@mui/material";
import TextField from "./MetricField";
import MetricResults from "./MetricResults";
import { formatMetricTime, localTimeZone } from "./metricTime";

export default function MetricRunDialog({
  open,
  metricName,
  definitionVersion,
  inspectedVersion,
  onClose,
  busy,
  readOnly,
  dirty,
  start,
  end,
  sourceRun,
  filterField,
  filterValue,
  onChange,
  onPreview,
  onRun,
  preview,
  error,
}) {
  const validWindow =
    Number.isFinite(new Date(start).getTime()) &&
    Number.isFinite(new Date(end).getTime()) &&
    new Date(start) < new Date(end) &&
    new Date(end) <= new Date() &&
    new Date(end) - new Date(start) <= 31 * 86400000;
  const blocked = busy || readOnly || dirty || !validWindow;
  return (
    <Dialog
      open={open}
      onClose={busy ? undefined : onClose}
      fullWidth
      maxWidth="sm"
      aria-labelledby="metric-run-title"
    >
      <DialogTitle id="metric-run-title">Run metric</DialogTitle>
      <DialogContent>
        <Stack gap={2}>
          <Typography variant="subtitle2" sx={{ overflowWrap: "anywhere" }}>
            {metricName} · definition version {definitionVersion}
          </Typography>
          <Typography variant="body2" color="text.secondary">
            This run uses the current saved definition, version{" "}
            {definitionVersion}.
          </Typography>
          {inspectedVersion != null &&
            inspectedVersion !== definitionVersion && (
              <Alert severity="info">
                You are inspecting a result from version {inspectedVersion}.
                This starts a new run of version {definitionVersion}, not a
                replay of that older definition.
              </Alert>
            )}
          <Typography variant="body2" color="text.secondary">
            Enter boundaries in local time ({localTimeZone()}); saved windows
            are shown in UTC. A closed window can cover at most 31 days.
          </Typography>
          {error && <Alert severity="error">{error}</Alert>}
          {dirty && (
            <Alert severity="warning">
              Save or discard definition changes before running.
            </Alert>
          )}
          <Stack direction={{ xs: "column", sm: "row" }} gap={2}>
            <TextField
              fullWidth
              type="datetime-local"
              label="Window start"
              value={start}
              helperText={formatMetricTime(start)}
              onChange={(e) => onChange("start", e.target.value)}
              inputProps={{ step: "any" }}
              InputLabelProps={{ shrink: true }}
              sx={{ minWidth: { xs: 0, sm: 0 } }}
            />
            <TextField
              fullWidth
              type="datetime-local"
              label="Window end"
              value={end}
              helperText={formatMetricTime(end)}
              onChange={(e) => onChange("end", e.target.value)}
              inputProps={{ step: "any" }}
              InputLabelProps={{ shrink: true }}
              sx={{ minWidth: { xs: 0, sm: 0 } }}
            />
          </Stack>
          {!validWindow && (
            <Alert severity="warning">
              Choose a valid closed window, with the end after the start and no
              more than 31 days apart.
            </Alert>
          )}
          <Typography variant="body2" sx={{ overflowWrap: "anywhere" }}>
            {sourceRun
              ? `Evaluation task: ${sourceRun}`
              : "All evaluation tasks"}{" "}
            ·{" "}
            {filterValue
              ? `${filterField} = ${filterValue}`
              : "No extra run filters"}
          </Typography>
          <Box component="details">
            <Box component="summary" sx={{ cursor: "pointer", mb: 1.5 }}>
              Run filters and source
            </Box>
            <Stack gap={2}>
              <TextField
                label="Evaluation task ID (optional)"
                value={sourceRun}
                onChange={(e) => onChange("sourceRun", e.target.value)}
                fullWidth
                helperText="Reads existing results from this task; does not execute evaluators."
              />
              <TextField
                select
                label="Run filter"
                value={filterField}
                onChange={(e) => onChange("filterField", e.target.value)}
              >
                {["model", "provider", "status", "name", "service_name"].map(
                  (field) => (
                    <MenuItem key={field} value={field}>
                      {field.replaceAll("_", " ")}
                    </MenuItem>
                  ),
                )}
              </TextField>
              <TextField
                fullWidth
                label="Exact filter value (optional)"
                value={filterValue}
                onChange={(e) => onChange("filterValue", e.target.value)}
              />
            </Stack>
          </Box>
          {preview && (
            <Box>
              <Alert severity="info">
                Preview only — inputs have not been frozen yet. The selected
                saved result is unchanged.
              </Alert>
              <MetricResults results={preview.results} rollup="overall" />
            </Box>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button disabled={busy} onClick={onClose}>
          Cancel
        </Button>
        <Button disabled={blocked} onClick={onPreview}>
          Preview window
        </Button>
        <Button variant="contained" disabled={blocked} onClick={onRun}>
          Run and persist
        </Button>
      </DialogActions>
    </Dialog>
  );
}
MetricRunDialog.propTypes = {
  open: PropTypes.bool,
  metricName: PropTypes.string,
  definitionVersion: PropTypes.number,
  inspectedVersion: PropTypes.number,
  onClose: PropTypes.func.isRequired,
  busy: PropTypes.bool,
  readOnly: PropTypes.bool,
  dirty: PropTypes.bool,
  start: PropTypes.string,
  end: PropTypes.string,
  sourceRun: PropTypes.string,
  filterField: PropTypes.string,
  filterValue: PropTypes.string,
  onChange: PropTypes.func.isRequired,
  onPreview: PropTypes.func.isRequired,
  onRun: PropTypes.func.isRequired,
  preview: PropTypes.object,
  error: PropTypes.string,
};
