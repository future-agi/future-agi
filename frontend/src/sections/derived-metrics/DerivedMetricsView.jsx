import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  Box,
  Button,
  Chip,
  Divider,
  MenuItem,
  LinearProgress,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { useNavigate } from "react-router-dom";
import axios, { endpoints } from "src/utils/axios";
import { useWorkspace } from "src/contexts/WorkspaceContext";
import { useOrganization } from "src/contexts/OrganizationContext";
import { readObserveProjectPage } from "src/api/project/observe-project-list";
import { paths } from "src/routes/paths";
import useCanEditDashboard from "src/sections/dashboards/hooks/useCanEditDashboard";
import MetricInputs from "./MetricInputs";
import MetricResults from "./MetricResults";
import { initialForm, formFromMetric, metricRequestBody } from "./metricForm";

const localTime = (date) =>
  new Date(date.getTime() - date.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 16);
export default function DerivedMetricsView() {
  const { currentWorkspaceId, isReady } = useWorkspace();
  const { currentOrganizationId } = useOrganization();
  if (!isReady || !currentWorkspaceId || !currentOrganizationId) {
    return (
      <Box sx={{ p: 3 }}>
        <LinearProgress aria-label="Loading workspace" />
      </Box>
    );
  }
  const scope = `${currentOrganizationId}:${currentWorkspaceId}`;
  // Scope-keyed editor unmount prevents an old workspace's draft/run surviving a switch.
  return <MetricEditor key={scope} scope={scope} />;
}

function MetricEditor({ scope }) {
  const navigate = useNavigate();
  const cache = useQueryClient();
  const { isReadOnly } = useCanEditDashboard();
  const [form, setForm] = useState(initialForm);
  const [metric, setMetric] = useState(null);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [projectSearch, setProjectSearch] = useState("");
  const [projectPage, setProjectPage] = useState(0);
  const [before, setBefore] = useState("");
  const [runId, setRunId] = useState("");
  const [runBefore, setRunBefore] = useState("");
  const [pollUntil, setPollUntil] = useState(0);
  const [preview, setPreview] = useState(null);
  const [rollup, setRollup] = useState("overall");
  const [after, setAfter] = useState("");
  const [start, setStart] = useState(() =>
    localTime(new Date(Date.now() - 86400000)),
  );
  const [end, setEnd] = useState(() => localTime(new Date()));
  const [sourceRun, setSourceRun] = useState("");
  const [filterField, setFilterField] = useState("model");
  const [filterValue, setFilterValue] = useState("");
  const [dashboardName, setDashboardName] = useState("");
  const [monitorName, setMonitorName] = useState("");
  const [threshold, setThreshold] = useState("0.8");
  const [operator, setOperator] = useState("less_than");
  const [email, setEmail] = useState("");
  const projects = useQuery({
    queryKey: ["metric-projects", scope, projectPage, projectSearch],
    queryFn: ({ signal }) =>
      readObserveProjectPage({
        signal,
        params: {
          page_number: projectPage,
          page_size: 50,
          name: projectSearch,
        },
      }),
  });
  const definitions = useQuery({
    queryKey: ["derived-metrics", scope, before],
    queryFn: async ({ signal }) =>
      (
        await axios.get(endpoints.derivedMetrics.list, {
          signal,
          params: { limit: 30, ...(before ? { before } : {}) },
        })
      ).data,
  });
  const runs = useQuery({
    queryKey: ["metric-runs", scope, metric?.id, runBefore],
    enabled: Boolean(metric),
    queryFn: async ({ signal }) =>
      (
        await axios.get(endpoints.derivedMetrics.runs(metric.id), {
          signal,
          params: { limit: 30, ...(runBefore ? { before: runBefore } : {}) },
        })
      ).data,
  });
  const run = useQuery({
    queryKey: ["metric-run", scope, runId],
    enabled: Boolean(runId),
    queryFn: async ({ signal }) =>
      (await axios.get(endpoints.derivedMetrics.run(runId), { signal })).data,
    refetchInterval: (query) =>
      ["queued", "running"].includes(query.state.data?.status) &&
      Date.now() < pollUntil
        ? 2000
        : false,
  });
  const results = useQuery({
    queryKey: [
      "metric-results",
      scope,
      runId,
      run.data?.result_id,
      rollup,
      after,
    ],
    enabled: Boolean(run.data?.result_id),
    queryFn: async ({ signal }) =>
      (
        await axios.get(endpoints.derivedMetrics.results(runId), {
          signal,
          params: { rollup, after, limit: 50 },
        })
      ).data,
  });
  useEffect(() => {
    if (run.data?.status === "complete" || run.data?.status === "failed")
      cache.invalidateQueries({ queryKey: ["metric-runs", scope] });
  }, [run.data?.status, cache, scope]);
  useEffect(() => {
    setAfter("");
  }, [run.data?.result_id]);
  const change = (key, value) => {
    setForm((current) => ({ ...current, [key]: value }));
    setDirty(true);
    setRunId("");
    setPreview(null);
    setNotice("");
  };
  const clearResults = () => {
    setRunId("");
    setPreview(null);
    setAfter("");
  };
  const requestBody = () => metricRequestBody(form, metric);
  const runBody = () => ({
    window_start: new Date(start).toISOString(),
    window_end: new Date(end).toISOString(),
    version: metric.current_version,
    idempotency_key: crypto.randomUUID(),
    filters: filterValue ? [{ field: filterField, value: filterValue }] : [],
    ...(sourceRun ? { source_run_id: sourceRun } : {}),
  });
  const perform = async (action) => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await action();
    } catch (failure) {
      setError(
        failure.response?.data
          ? JSON.stringify(failure.response.data)
          : failure.message,
      );
    } finally {
      setBusy(false);
    }
  };
  const select = (item) => {
    setMetric(item);
    setForm(formFromMetric(item));
    setDirty(false);
    setRunId("");
    setRunBefore("");
    setPreview(null);
    setNotice("");
  };
  const save = () =>
    perform(async () => {
      const body = requestBody();
      const response = metric
        ? await axios.put(endpoints.derivedMetrics.detail(metric.id), body)
        : await axios.post(endpoints.derivedMetrics.list, body);
      select(response.data);
      setNotice(`Saved version ${response.data.current_version}.`);
      await cache.invalidateQueries({ queryKey: ["derived-metrics", scope] });
    });
  const publishDashboard = () =>
    perform(async () => {
      const created = await axios.post(endpoints.dashboard.create, {
        name: dashboardName,
        description: `Frozen metric run ${runId}. A point represents the complete run window, not an average of daily ratios.`,
      });
      const dashboard = created.data.result;
      await axios.post(endpoints.dashboard.widgets(dashboard.id), {
        name: metric.name,
        description: `Version ${run.data.metric_version} · ${run.data.window_start} to ${run.data.window_end}`,
        query_config: {
          project_ids: [form.project_id],
          time_range: {
            custom_start: run.data.window_start,
            custom_end: run.data.window_end,
          },
          granularity: "day",
          metrics: [
            {
              id: runId,
              name: metric.name,
              display_name: metric.name,
              type: "derived_metric",
              source: "derived_metrics",
              derived_run_id: runId,
              aggregation: "avg",
            },
          ],
          filters: [],
          breakdowns: [],
        },
        chart_config: { chart_type: "metric" },
      });
      navigate(paths.dashboard.dashboards.detail(dashboard.id));
    });
  const createMonitor = () =>
    perform(async () => {
      await axios.post(endpoints.project.createMonitor, {
        name: monitorName,
        project: form.project_id,
        metric_type: "derived_metric",
        metric: metric.id,
        threshold_operator: operator,
        threshold_type: "static",
        critical_threshold_value: Number(threshold),
        alert_frequency: 60,
        ...(email ? { notification_emails: [email] } : {}),
        is_mute: false,
        filters: {},
      });
      setNotice(
        "Monitor created. New completed runs record alerts and use the existing email/Slack notification delivery.",
      );
    });
  const displayed = runId ? results.data : preview;
  return (
    <Box sx={{ p: { xs: 2, md: 3 }, maxWidth: 1440, mx: "auto" }}>
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="center"
        sx={{ mb: 3 }}
      >
        <Box>
          <Typography variant="h4">Derived metrics</Typography>
          <Typography color="text.secondary">
            Define once. Freeze the evidence. Recompute the same result.
          </Typography>
        </Box>
        <Button
          onClick={() => {
            setMetric(null);
            setForm(initialForm());
            setRunId("");
            setDirty(false);
            setPreview(null);
          }}
        >
          New metric
        </Button>
      </Stack>
      <Stack
        direction={{ xs: "column", lg: "row" }}
        gap={3}
        alignItems="flex-start"
      >
        <Stack
          spacing={1}
          sx={{ width: { xs: "100%", lg: 260 }, flexShrink: 0 }}
        >
          <Typography variant="subtitle2">Saved definitions</Typography>
          {definitions.isError && (
            <Alert severity="error">
              Definitions could not be loaded.{" "}
              <Button onClick={() => definitions.refetch()}>Retry</Button>
            </Alert>
          )}
          {definitions.data?.results.map((item) => (
            <Button
              key={item.id}
              variant={metric?.id === item.id ? "outlined" : "text"}
              onClick={() => select(item)}
              sx={{ justifyContent: "space-between", textAlign: "left" }}
            >
              {item.name}
              <Chip size="small" label={`v${item.current_version}`} />
            </Button>
          ))}
          {definitions.data?.results.length === 0 && (
            <Typography variant="body2" color="text.secondary">
              Create a formula using saved evaluations or numeric span fields.
            </Typography>
          )}
          <Stack direction="row">
            <Button disabled={!before} onClick={() => setBefore("")}>
              First page
            </Button>
            <Button
              disabled={!definitions.data?.next_cursor}
              onClick={() => setBefore(definitions.data.next_cursor)}
            >
              Next page
            </Button>
          </Stack>
        </Stack>
        <Stack spacing={2.5} sx={{ flex: 1, minWidth: 0 }}>
          {(error || projects.isError || run.isError || results.isError) && (
            <Alert severity="error">
              {error ||
                "This read failed. Reload or retry; unavailable data is not zero."}
            </Alert>
          )}
          {notice && <Alert severity="info">{notice}</Alert>}
          {(busy ||
            projects.isLoading ||
            definitions.isLoading ||
            run.isFetching) && (
            <LinearProgress aria-label="Loading metric data" />
          )}
          {isReadOnly && (
            <Alert severity="info">
              You can inspect saved definitions and results. Workspace member
              access is required to change or run metrics.
            </Alert>
          )}
          <Stack direction={{ xs: "column", md: "row" }} gap={2}>
            <TextField
              label="Metric name"
              value={form.name}
              disabled={isReadOnly}
              onChange={(e) => change("name", e.target.value)}
              fullWidth
            />
            <TextField
              select
              label="Project"
              value={form.project_id}
              disabled={Boolean(metric) || isReadOnly}
              onChange={(e) => change("project_id", e.target.value)}
              fullWidth
            >
              {metric &&
                !projects.data?.rows.some((p) => p.id === form.project_id) && (
                  <MenuItem value={form.project_id}>{form.project_id}</MenuItem>
                )}
              {projects.data?.rows.map((project) => (
                <MenuItem key={project.id} value={project.id}>
                  {project.name}
                </MenuItem>
              ))}
            </TextField>
          </Stack>
          {!metric && (
            <Stack direction="row" gap={1}>
              <TextField
                size="small"
                label="Search projects"
                value={projectSearch}
                onChange={(e) => {
                  setProjectSearch(e.target.value);
                  setProjectPage(0);
                }}
              />
              <Button
                disabled={projectPage === 0}
                onClick={() => setProjectPage((p) => p - 1)}
              >
                Previous projects
              </Button>
              <Button
                disabled={
                  !projects.data || projectPage + 1 >= projects.data.totalPages
                }
                onClick={() => setProjectPage((p) => p + 1)}
              >
                More projects
              </Button>
            </Stack>
          )}
          <TextField
            label="Description"
            value={form.description}
            disabled={isReadOnly}
            onChange={(e) => change("description", e.target.value)}
            fullWidth
          />
          <Stack direction={{ xs: "column", md: "row" }} gap={2}>
            <TextField
              select
              label="Selection unit"
              value={form.selection_unit}
              disabled={isReadOnly}
              onChange={(e) => change("selection_unit", e.target.value)}
              fullWidth
            >
              {["session", "user", "span"].map((value) => (
                <MenuItem key={value} value={value}>
                  {value}
                </MenuItem>
              ))}
            </TextField>
            <TextField
              select
              label="Weighting"
              value={form.weighting}
              disabled={isReadOnly}
              onChange={(e) => change("weighting", e.target.value)}
              fullWidth
            >
              <MenuItem value="none">
                Unweighted · explicit probability 1
              </MenuItem>
              <MenuItem value="inverse_probability">
                Inverse inclusion probability
              </MenuItem>
            </TextField>
            <TextField
              label="Cohort attribute key"
              value={form.cohort_key}
              disabled={isReadOnly}
              onChange={(e) => change("cohort_key", e.target.value)}
              fullWidth
            />
          </Stack>
          <MetricInputs
            inputs={form.inputs}
            disabled={isReadOnly}
            onChange={(inputs) => change("inputs", inputs)}
          />
          <TextField
            label="Formula"
            multiline
            minRows={2}
            value={form.formula}
            disabled={isReadOnly}
            onChange={(e) => change("formula", e.target.value)}
            inputProps={{
              style: { fontFamily: "monospace" },
              spellCheck: false,
            }}
            helperText="ratio(numerator, denominator), mean(value), sum(value), count_if(condition). Use named inputs, arithmetic, comparisons and and/or/not."
          />
          <Typography variant="body2" color="text.secondary">
            Each input is reduced independently before joining. Missing inputs
            are excluded, not zero. Invalid probabilities invalidate the rollup;
            zero denominators have no value. Weighted metrics require matching
            persisted sampling metadata.
          </Typography>
          <Stack direction="row" gap={1}>
            <Button
              disabled={busy || isReadOnly}
              onClick={() =>
                perform(async () => {
                  const response = await axios.post(
                    endpoints.derivedMetrics.validate,
                    requestBody(),
                  );
                  setNotice(response.data.semantics);
                })
              }
            >
              Validate formula
            </Button>
            <Button
              variant="contained"
              disabled={busy || isReadOnly}
              onClick={save}
            >
              {metric ? "Save new version" : "Save metric"}
            </Button>
            {metric && (
              <Chip
                label={`Version ${metric.current_version}${dirty ? " · unsaved changes" : ""}`}
              />
            )}
          </Stack>
          {metric && (
            <>
              <Divider />
              <Typography variant="h6">Freeze a closed window</Typography>
              <Stack direction={{ xs: "column", md: "row" }} gap={2}>
                <TextField
                  type="datetime-local"
                  label="Window start"
                  value={start}
                  onChange={(e) => {
                    setStart(e.target.value);
                    clearResults();
                  }}
                  InputLabelProps={{ shrink: true }}
                />
                <TextField
                  type="datetime-local"
                  label="Window end"
                  value={end}
                  onChange={(e) => {
                    setEnd(e.target.value);
                    clearResults();
                  }}
                  InputLabelProps={{ shrink: true }}
                />
                <TextField
                  label="Evaluation task ID (optional)"
                  value={sourceRun}
                  onChange={(e) => {
                    setSourceRun(e.target.value);
                    clearResults();
                  }}
                  fullWidth
                />
              </Stack>
              <Stack direction="row" gap={1}>
                <TextField
                  select
                  label="Run filter"
                  value={filterField}
                  onChange={(e) => {
                    setFilterField(e.target.value);
                    clearResults();
                  }}
                  sx={{ minWidth: 150 }}
                >
                  {["model", "provider", "status", "name", "service_name"].map(
                    (field) => (
                      <MenuItem key={field} value={field}>
                        {field}
                      </MenuItem>
                    ),
                  )}
                </TextField>
                <TextField
                  fullWidth
                  label="Exact filter value (optional)"
                  value={filterValue}
                  onChange={(e) => {
                    setFilterValue(e.target.value);
                    clearResults();
                  }}
                />
              </Stack>
              <Stack direction="row" gap={1}>
                <Button
                  disabled={busy || dirty || isReadOnly}
                  onClick={() =>
                    perform(async () => {
                      const response = await axios.post(
                        endpoints.derivedMetrics.preview(metric.id),
                        runBody(),
                      );
                      setPreview(response.data);
                      setRunId("");
                      setNotice(
                        "Preview only — inputs have not been frozen yet.",
                      );
                    })
                  }
                >
                  Preview window
                </Button>
                <Button
                  variant="contained"
                  disabled={busy || dirty || isReadOnly}
                  onClick={() =>
                    perform(async () => {
                      const response = await axios.post(
                        endpoints.derivedMetrics.runs(metric.id),
                        runBody(),
                      );
                      setRunId(response.data.id);
                      setAfter("");
                      setRollup("overall");
                      setPollUntil(Date.now() + 900000);
                      await runs.refetch();
                    })
                  }
                >
                  Run and persist
                </Button>
              </Stack>
              {runs.data && (
                <TextField
                  select
                  label="Past runs"
                  value={runId}
                  onChange={(e) => {
                    setRunId(e.target.value);
                    setAfter("");
                    setPollUntil(Date.now() + 900000);
                  }}
                >
                  <MenuItem value="">Select a saved run</MenuItem>
                  {runId &&
                    !runs.data.results.some((item) => item.id === runId) && (
                      <MenuItem value={runId}>Selected run {runId}</MenuItem>
                    )}
                  {runs.data.results.map((item) => (
                    <MenuItem key={item.id} value={item.id}>
                      v{item.metric_version} ·{" "}
                      {new Date(item.window_end).toLocaleString()} ·{" "}
                      {item.status}
                    </MenuItem>
                  ))}
                </TextField>
              )}
              {runs.data && (
                <Stack direction="row">
                  <Button
                    disabled={!runBefore}
                    onClick={() => setRunBefore("")}
                  >
                    First runs
                  </Button>
                  <Button
                    disabled={!runs.data.next_cursor}
                    onClick={() => setRunBefore(runs.data.next_cursor)}
                  >
                    More past runs
                  </Button>
                </Stack>
              )}
              {run.data && (
                <Stack gap={1}>
                  <Alert
                    severity={run.data.status === "failed" ? "error" : "info"}
                  >
                    Run {run.data.status} · v{run.data.metric_version}.{" "}
                    {run.data.error} Window{" "}
                    {new Date(run.data.window_start).toLocaleString()} to{" "}
                    {new Date(run.data.window_end).toLocaleString()}.{" "}
                    {run.data.snapshot_id
                      ? `Captured at ${new Date(run.data.as_of).toLocaleString()}.`
                      : "No snapshot captured yet."}{" "}
                    Replay uses immutable inputs; late arrivals require a new
                    run.
                  </Alert>
                  {run.data.result_id && run.data.status !== "complete" && (
                    <Typography variant="body2">
                      Showing the last committed result; the latest recompute
                      has not completed.
                    </Typography>
                  )}
                  <Button
                    sx={{ alignSelf: "flex-start" }}
                    disabled={
                      busy ||
                      isReadOnly ||
                      (["queued", "running"].includes(run.data.status) &&
                        new Date(run.data.lease_expires_at).getTime() >
                          Date.now())
                    }
                    onClick={() =>
                      perform(async () => {
                        await axios.post(
                          endpoints.derivedMetrics.recompute(runId),
                          {},
                        );
                        setPollUntil(Date.now() + 900000);
                        await run.refetch();
                      })
                    }
                  >
                    {run.data.snapshot_id
                      ? "Recompute frozen run"
                      : "Retry run"}
                  </Button>
                  <Button
                    sx={{ alignSelf: "flex-start" }}
                    onClick={() => run.refetch()}
                  >
                    Refresh run status
                  </Button>
                </Stack>
              )}
              {displayed && (
                <>
                  {runId && (
                    <TextField
                      select
                      label="Rollup"
                      value={rollup}
                      onChange={(e) => {
                        setRollup(e.target.value);
                        setAfter("");
                      }}
                    >
                      {["overall", "session", "user", "cohort", "day"].map(
                        (value) => (
                          <MenuItem key={value} value={value}>
                            {value}
                          </MenuItem>
                        ),
                      )}
                    </TextField>
                  )}
                  <MetricResults
                    results={displayed.results}
                    rollup={runId ? rollup : "overall"}
                  />
                  {runId && (
                    <Stack direction="row">
                      <Button disabled={!after} onClick={() => setAfter("")}>
                        First results
                      </Button>
                      <Button
                        disabled={!displayed.next_cursor}
                        onClick={() => setAfter(displayed.next_cursor)}
                      >
                        Next results
                      </Button>
                    </Stack>
                  )}
                </>
              )}
              <Divider />
              <Typography variant="h6">Use the saved result</Typography>
              <Stack direction={{ xs: "column", md: "row" }} gap={1}>
                <TextField
                  fullWidth
                  label="New dashboard name"
                  value={dashboardName}
                  onChange={(e) => setDashboardName(e.target.value)}
                />
                <Button
                  disabled={
                    busy || isReadOnly || !dashboardName || !run.data?.result_id
                  }
                  onClick={publishDashboard}
                >
                  Add run to dashboard
                </Button>
              </Stack>
              <Typography variant="body2" color="text.secondary">
                The dashboard pins this run and its exact window. It never
                averages daily ratios.
              </Typography>
              <Stack direction={{ xs: "column", md: "row" }} gap={1}>
                <TextField
                  label="Monitor name"
                  value={monitorName}
                  onChange={(e) => setMonitorName(e.target.value)}
                />
                <TextField
                  select
                  label="Alert when"
                  value={operator}
                  onChange={(e) => setOperator(e.target.value)}
                  sx={{ minWidth: 145 }}
                >
                  <MenuItem value="less_than">Below</MenuItem>
                  <MenuItem value="greater_than">Above</MenuItem>
                </TextField>
                <TextField
                  label="Threshold"
                  type="number"
                  value={threshold}
                  onChange={(e) => setThreshold(e.target.value)}
                />
                <TextField
                  label="Notification email (optional)"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                />
                <Button
                  disabled={
                    busy ||
                    isReadOnly ||
                    dirty ||
                    !monitorName ||
                    threshold.trim() === "" ||
                    !Number.isFinite(Number(threshold))
                  }
                  onClick={createMonitor}
                >
                  Create monitor
                </Button>
              </Stack>
              <Typography variant="body2" color="text.secondary">
                Monitors consume new completed runs for the current version,
                using their frozen filters. No value means no alert, not a
                healthy zero.
              </Typography>
            </>
          )}
        </Stack>
      </Stack>
    </Box>
  );
}

MetricEditor.propTypes = { scope: PropTypes.string.isRequired };
