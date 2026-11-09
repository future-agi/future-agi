import PropTypes from "prop-types";
import { lazy, Suspense, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import { useMetricDrivers, useMetricFields, useMetricKpi } from "./api";
import { formatMetricValue } from "./formatMetricValue";
import { formatMetricWindow } from "./metricTime";
import { unitValueState } from "./unitValueState";
import {
  formatMetricDifference,
  matchingMetricDriverField,
  sameMetricRead,
} from "./metricPageValues";
import MetricValueCell from "./MetricValueCell";
import MetricStatusLine, { MetricReadNotice } from "./MetricStatusLine";

const TracesDrawer = lazy(
  () => import("src/sections/projects/TracesDrawer/TracesDrawer"),
);
const TaskRunTraceDrawer = lazy(
  () => import("src/sections/tasks/components/TaskRunTraceDrawer"),
);
const contextFor = {
  conversation: "sessions",
  span: "spans",
  user: "users",
  trace: "traces",
};

export default function MetricDrivers({ metric, scope, window }) {
  const definition = metric.definition;
  const [cursors, setCursors] = useState([null]);
  const [page, setPage] = useState(0);
  const [selected, setSelected] = useState(null);
  const supported =
    Boolean(definition) && !["user", "span"].includes(definition.level);
  const args = {
    scope,
    metricId: metric.id,
    version: definition?.version,
    enabled: supported,
  };
  const query = useMetricDrivers({
    ...args,
    params: {
      ...window,
      limit: 20,
      ...(cursors[page] && { after: cursors[page] }),
    },
  });
  const fields = useMetricFields({
    ...args,
    params: { context: contextFor[definition?.level] || "sessions" },
  });
  const kpi = useMetricKpi({ ...args, params: window });
  if (definition?.level === "user")
    return (
      <Alert severity="info">
        User-level metrics and drivers are not available yet.
      </Alert>
    );
  if (definition?.level === "span")
    return (
      <Alert severity="info">
        Span drivers and unit evidence are not available yet. KPI and trend
        remain available.
      </Alert>
    );
  if (!definition)
    return (
      <Typography color="text.secondary">
        Not calculated yet. Drivers need an active definition.
      </Typography>
    );
  const candidateField = fields.data?.results[0];
  const field = matchingMetricDriverField(candidateField, definition)
    ? candidateField
    : null;
  const rows = query.data?.results || [];
  // The server ranks failures. Defensively exclude open/missing/expired rows;
  // missing values must never be presented as failing conversations.
  const valid = field
    ? rows.filter(
        (row) =>
          unitValueState(row, field).state === "value" &&
          formatMetricValue(unitValueState(row, field).value, field) !== null,
      )
    : [];
  const excluded = rows.length - valid.length;
  const comparable = sameMetricRead(query.data, kpi.data);
  const canOpen = ["conversation", "trace"].includes(definition.level);
  return (
    <Stack spacing={2}>
      <Box>
        <Typography component="h2" variant="subtitle1" fontWeight={600}>
          Drivers
        </Typography>
        <Typography variant="body2" color="text.secondary">
          Units pulling the value down, ranked by their returned leave-one-out
          influence. Influence is the current value minus its value without that
          unit.
        </Typography>
      </Box>
      <Typography variant="body2" color="text.secondary">
        Open units and units without a value are excluded, not counted as
        failures. Source telemetry may have expired since calculation.
      </Typography>
      <MetricReadNotice query={query} label="Drivers" />
      <MetricReadNotice query={fields} label="Field metadata" />
      {query.data && fields.data && (
        <>
          <Typography variant="caption" color="text.secondary">
            {formatMetricWindow(query.data.effective_window)} ·{" "}
            {query.data.effective_window.rounding}
          </Typography>
          {!field ? (
            <Alert severity="warning">
              {candidateField
                ? "Field metadata changed. Reload the metric before inspecting driver values."
                : "Driver field metadata is unavailable. Values cannot be displayed safely."}
            </Alert>
          ) : valid.length ? (
            <TableContainer>
              <Table size="small" aria-label="Metric drivers">
                <TableHead>
                  <TableRow>
                    {["Unit", "Value", "Inputs", "Influence"].map((label) => (
                      <TableCell key={label}>{label}</TableCell>
                    ))}
                  </TableRow>
                </TableHead>
                <TableBody>
                  {valid.map((row) => (
                    <TableRow key={row.unit_id}>
                      <TableCell
                        sx={{ maxWidth: 300, overflowWrap: "anywhere" }}
                      >
                        {canOpen ? (
                          <Button
                            color="inherit"
                            size="small"
                            sx={{ textAlign: "left", overflowWrap: "anywhere" }}
                            onClick={() => setSelected(row.unit_id)}
                          >
                            {row.unit_id}
                          </Button>
                        ) : (
                          row.unit_id
                        )}
                      </TableCell>
                      <TableCell>
                        <MetricValueCell value={row} field={field} />
                      </TableCell>
                      <TableCell>
                        {Object.entries({
                          ...row.inputs,
                          ...row.inputs_bool,
                          ...row.inputs_text,
                        }).map(([alias, value]) => (
                          <Typography
                            variant="caption"
                            component="div"
                            key={alias}
                            sx={{ overflowWrap: "anywhere" }}
                          >
                            {alias}:{" "}
                            {value == null ? "No value" : String(value)}
                          </Typography>
                        ))}
                      </TableCell>
                      <TableCell sx={{ fontVariantNumeric: "tabular-nums" }}>
                        {comparable
                          ? formatMetricDifference(
                              row.influence,
                              kpi.data.current,
                            ) || "Not available"
                          : "Not available"}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          ) : (
            <Typography color="text.secondary">
              No drivers with a settled value in this window.
            </Typography>
          )}
          {excluded > 0 && field && (
            <Typography variant="body2" color="text.secondary">
              {excluded} open or no-value units excluded. Sampled-out units have
              no value: not evaluated.
            </Typography>
          )}
          {!comparable && valid.length > 0 && (
            <Typography variant="caption" color="text.secondary">
              Influence units could not be verified against the same committed
              KPI. No effects are shown.
            </Typography>
          )}
          <MetricStatusLine
            provenance={query.data.provenance}
            definition={definition}
          />
        </>
      )}
      <Stack direction="row" alignItems="center" spacing={2}>
        <Button
          color="inherit"
          disabled={page === 0 || query.readState === "loading"}
          onClick={() => {
            setSelected(null);
            setPage(page - 1);
          }}
        >
          Previous drivers
        </Button>
        <Typography variant="caption">Page {page + 1}</Typography>
        <Button
          color="inherit"
          disabled={!query.data?.next_cursor}
          onClick={() => {
            setSelected(null);
            setCursors([...cursors.slice(0, page + 1), query.data.next_cursor]);
            setPage(page + 1);
          }}
        >
          Next drivers
        </Button>
      </Stack>
      {selected && valid.some((row) => row.unit_id === selected) && (
        <Suspense
          fallback={<Typography role="status">Opening source…</Typography>}
        >
          {definition.level === "conversation" ? (
            <TracesDrawer
              key={selected}
              open
              onClose={() => setSelected(null)}
              rowData={{ session_id: selected }}
              navigationContext={{
                workspace_id: scope.workspaceId,
                project_id: metric.project_id,
                filters: [],
                sort_params: [],
              }}
            />
          ) : (
            <TaskRunTraceDrawer
              open
              onClose={() => setSelected(null)}
              projectId={metric.project_id}
              traceId={selected}
            />
          )}
        </Suspense>
      )}
    </Stack>
  );
}
MetricDrivers.propTypes = {
  metric: PropTypes.object.isRequired,
  scope: PropTypes.object.isRequired,
  window: PropTypes.object.isRequired,
};
