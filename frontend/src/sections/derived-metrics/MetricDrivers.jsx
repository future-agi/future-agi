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
  Tooltip,
  Typography,
} from "@mui/material";
import EmptyState from "src/components/empty-state";
import Iconify from "src/components/iconify";
import { useMetricDrivers, useMetricFields, useMetricKpi } from "./api";
import { formatMetricValue } from "./formatMetricValue";
import { coveragePending, formatMetricWindow } from "./metricTime";
import { forwardPendingCopy } from "./copy";
import { unitValueState } from "./unitValueState";
import {
  formatMetricDifference,
  matchingMetricDriverField,
  sameMetricRead,
} from "./metricPageValues";
import MetricValueCell from "./MetricValueCell";
import MetricOutcomeBadge from "./MetricOutcomeBadge";
import MetricStatusLine, { MetricReadNotice } from "./MetricStatusLine";
import { inputLabel, unitInputs } from "./metricPresentation";

const TracesDrawer = lazy(
  () => import("src/sections/projects/TracesDrawer/TracesDrawer"),
);
const TaskRunTraceDrawer = lazy(
  () => import("src/sections/tasks/components/TaskRunTraceDrawer"),
);
const unitHeader = {
  conversation: "Conversation",
  trace: "Trace",
  span: "Span",
};
const ellipsis = {
  display: "block",
  minWidth: 0,
  whiteSpace: "nowrap",
  overflow: "hidden",
  textOverflow: "ellipsis",
};
const contextFor = {
  conversation: "sessions",
  span: "spans",
  user: "users",
  trace: "traces",
};

export default function MetricDrivers({ metric, scope, window }) {
  const definition = metric?.definition;
  const [cursors, setCursors] = useState([null]);
  const [page, setPage] = useState(0);
  const [selected, setSelected] = useState(null);
  const waiting = coveragePending(window, definition);
  const supported =
    Boolean(definition) &&
    !["user", "span"].includes(definition.level) &&
    !waiting;
  const args = {
    scope,
    metricId: metric?.id,
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
  // Retain only the selected source's identity, not masked metric data. Keep
  // this keyed drawer mounted so refetches do not reset its session or tab.
  const sourceOpen = Boolean(
    metric && valid.some((row) => row.unit_id === selected?.unitId),
  );
  const sourceDrawer = selected && (
    <Suspense
      key="source"
      fallback={
        sourceOpen && <Typography role="status">Opening source…</Typography>
      }
    >
      {selected.level === "conversation" ? (
        <TracesDrawer
          key={selected.unitId}
          open={sourceOpen}
          onClose={() => setSelected(null)}
          rowData={{ session_id: selected.unitId }}
          navigationContext={{
            workspace_id: scope.workspaceId,
            project_id: selected.projectId,
            filters: [],
            sort_params: [],
          }}
        />
      ) : (
        <TaskRunTraceDrawer
          open={sourceOpen}
          onClose={() => setSelected(null)}
          projectId={selected.projectId}
          traceId={selected.unitId}
        />
      )}
    </Suspense>
  );
  if (!metric) return <Stack spacing={2}>{sourceDrawer}</Stack>;
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
      <Box sx={{ border: 1, borderColor: "divider", borderRadius: 1 }}>
        <EmptyState
          variant="no-results"
          icon="solar:clock-circle-linear"
          title="Not calculated yet"
          description="Drivers need an active definition. Once its first calculation completes, the units pulling the value down are ranked here."
        />
      </Box>
    );
  if (waiting)
    return (
      <Box sx={{ border: 1, borderColor: "divider", borderRadius: 1 }}>
        <EmptyState
          variant="no-results"
          icon="solar:clock-circle-linear"
          title="Not calculated yet"
          description={forwardPendingCopy(definition)}
        />
      </Box>
    );
  const canOpen = ["conversation", "trace"].includes(definition.level);
  const inputs = definition.field_spec?.inputs?.length
    ? definition.field_spec.inputs
    : definition.inputs || [];
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
      <MetricReadNotice query={query} label="Drivers" definition={definition} />
      <MetricReadNotice query={fields} label="Field metadata" />
      {query.data && fields.data && (
        <>
          {!field ? (
            <Alert severity="warning">
              {candidateField
                ? "Field metadata changed. Reload the metric before inspecting driver values."
                : "Driver field metadata is unavailable. Values cannot be displayed safely."}
            </Alert>
          ) : valid.length ? (
            <TableContainer>
              <Table
                size="small"
                aria-label="Metric drivers"
                sx={{ tableLayout: "fixed" }}
              >
                <TableHead>
                  <TableRow>
                    <TableCell sx={{ width: "32%" }}>
                      {unitHeader[definition.level] || "Unit"}
                    </TableCell>
                    <TableCell sx={{ width: "20%" }}>Value</TableCell>
                    <TableCell>Inputs</TableCell>
                    <TableCell align="right" sx={{ width: "20%" }}>
                      Influence
                    </TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {valid.map((row) => (
                    <TableRow key={row.unit_id} hover>
                      <TableCell>
                        <Tooltip title={row.unit_id} placement="top-start">
                          {canOpen ? (
                            <Button
                              color="inherit"
                              size="small"
                              endIcon={
                                <Iconify
                                  icon="solar:arrow-right-up-linear"
                                  width={14}
                                />
                              }
                              sx={{
                                maxWidth: "100%",
                                minWidth: 0,
                                px: 0.5,
                                ml: -0.5,
                                justifyContent: "flex-start",
                                fontFamily: "monospace",
                                typography: "s1",
                                "& .MuiButton-endIcon": {
                                  flexShrink: 0,
                                  color: "text.disabled",
                                },
                              }}
                              onClick={() =>
                                setSelected({
                                  unitId: row.unit_id,
                                  level: definition.level,
                                  projectId: metric.project_id,
                                })
                              }
                            >
                              <Box component="span" sx={ellipsis}>
                                {row.unit_id}
                              </Box>
                            </Button>
                          ) : (
                            <Typography
                              component="span"
                              variant="body2"
                              tabIndex={0}
                              sx={{ ...ellipsis, fontFamily: "monospace" }}
                            >
                              {row.unit_id}
                            </Typography>
                          )}
                        </Tooltip>
                      </TableCell>
                      <TableCell>
                        {field.value_type === "boolean" ? (
                          // Every row here has a settled value (filtered above),
                          // shown as the same outcome pill as the preview.
                          <MetricOutcomeBadge
                            outcome={unitValueState(row, field).value}
                            label={formatMetricValue(
                              unitValueState(row, field).value,
                              field,
                            )}
                          />
                        ) : (
                          <MetricValueCell value={row} field={field} />
                        )}
                      </TableCell>
                      <TableCell>
                        {unitInputs(row).map(([alias, value]) => (
                          <Typography
                            variant="body2"
                            component="div"
                            key={alias}
                            sx={{ overflowWrap: "anywhere" }}
                          >
                            {inputLabel(alias, { inputs })}:{" "}
                            {value == null ? "No value" : String(value)}
                          </Typography>
                        ))}
                      </TableCell>
                      <TableCell
                        align="right"
                        sx={{ fontVariantNumeric: "tabular-nums" }}
                      >
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
            <Box sx={{ border: 1, borderColor: "divider", borderRadius: 1 }}>
              {/* Only the returned drivers are known here, never every unit
                  in the window, so the copy claims nothing broader. */}
              <EmptyState
                variant="no-results"
                icon="solar:sort-vertical-linear"
                {...(query.data.next_cursor
                  ? {
                      title: "No drivers on this page",
                      description:
                        "No unit on this page has a settled value. Use Next to see more.",
                    }
                  : page > 0
                    ? {
                        title: "No more drivers",
                        description:
                          "You have reached the end of the list. Use Previous to go back.",
                      }
                    : {
                        title: "No drivers in this window",
                        description:
                          "No unit pulling the value down has a settled value in this window. Try a longer window.",
                      })}
              />
            </Box>
          )}
          <Stack spacing={0.5}>
            <Typography variant="caption" color="text.secondary">
              Open units and units without a value are excluded, not counted as
              failures. Source telemetry may have expired since calculation.
            </Typography>
            {excluded > 0 && field && (
              <Typography variant="caption" color="text.secondary">
                {excluded} open or no-value units excluded. Sampled-out units
                have no value: not evaluated.
              </Typography>
            )}
            {!comparable && valid.length > 0 && (
              <Typography variant="caption" color="text.secondary">
                Influence units could not be verified against the same committed
                KPI. No effects are shown.
              </Typography>
            )}
          </Stack>
          <Stack
            direction="row"
            alignItems="flex-start"
            justifyContent="space-between"
            spacing={3}
            sx={{ pt: 1.5, borderTop: 1, borderColor: "divider" }}
          >
            <Typography variant="caption" color="text.secondary">
              {formatMetricWindow(query.data.effective_window)} ·{" "}
              {query.data.effective_window.rounding}
            </Typography>
            <Box sx={{ flexShrink: 0, maxWidth: "50%" }}>
              <MetricStatusLine
                provenance={query.data.provenance}
                definition={definition}
                completeThrough={query.data.complete_through}
                partial={query.data.partial}
              />
            </Box>
          </Stack>
        </>
      )}
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="flex-end"
        spacing={1}
      >
        <Typography variant="caption" color="text.secondary" sx={{ mr: 0.5 }}>
          Page {page + 1}
        </Typography>
        <Button
          color="inherit"
          variant="outlined"
          size="small"
          aria-label="Previous drivers"
          startIcon={<Iconify icon="solar:alt-arrow-left-linear" width={16} />}
          disabled={page === 0 || query.readState === "loading"}
          onClick={() => {
            setSelected(null);
            setPage(page - 1);
          }}
        >
          Previous
        </Button>
        <Button
          color="inherit"
          variant="outlined"
          size="small"
          aria-label="Next drivers"
          endIcon={<Iconify icon="solar:alt-arrow-right-linear" width={16} />}
          disabled={!query.data?.next_cursor}
          onClick={() => {
            setSelected(null);
            setCursors([...cursors.slice(0, page + 1), query.data.next_cursor]);
            setPage(page + 1);
          }}
        >
          Next
        </Button>
      </Stack>
      {sourceDrawer}
    </Stack>
  );
}
MetricDrivers.propTypes = {
  metric: PropTypes.object,
  scope: PropTypes.object.isRequired,
  window: PropTypes.object.isRequired,
};
