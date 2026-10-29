import PropTypes from "prop-types";
import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  Box,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  LinearProgress,
  MenuItem,
  Stack,
  Typography,
  Tab,
  Breadcrumbs,
} from "@mui/material";
import { useLocation, useNavigate } from "react-router-dom";
import axios, { endpoints } from "src/utils/axios";
import { useWorkspace } from "src/contexts/WorkspaceContext";
import { useOrganization } from "src/contexts/OrganizationContext";
import { readObserveProjectPage } from "src/api/project/observe-project-list";
import { paths } from "src/routes/paths";
import { SegmentedTabs } from "src/components/tabs/tabs";
import MetricList from "./MetricList";
import MetricRunHistory from "./MetricRunHistory";
import { definitionSummary } from "./metric_calculation";
import EvalsSectionTabs from "src/sections/evals/components/evals_section_tabs";
import useCanEditDashboard from "src/sections/dashboards/hooks/useCanEditDashboard";
import MetricResults from "./MetricResults";
import useMetricNavigationGuard from "./use_metric_navigation_guard";
import MetricContributors from "./MetricContributors";
import useMetricEvaluatorNames from "./use_metric_evaluator_names";
import {
  initialForm,
  formFromMetric,
  metricRequestBody,
  weightedInputProblems,
} from "./metricForm";
import TextField from "./MetricField";
import SelectedRunSummary from "./SelectedRunSummary";
import MetricDefinitionEditor from "./metric_definition_editor";
import MetricRunDialog from "./metric_run_dialog";
import MetricResultActions from "./metric_result_actions";
import useCommittedMetricResults from "./useCommittedMetricResults";
import { monitorFailureMessage } from "./monitorFeedback";
import { localTime, formatMetricTime, formatMetricWindow } from "./metricTime";
import { metricFailureText } from "./metric_feedback";
import {
  clearMetricDraft,
  readMetricDraft,
  writeMetricDraft,
  updateMetricDraft,
} from "./metric_draft";

const uuid = (value) =>
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(
    value || "",
  );

export default function DerivedMetricsView() {
  const { currentWorkspaceId, isReady } = useWorkspace();
  const { currentOrganizationId } = useOrganization();
  const navigate = useNavigate();
  const location = useLocation();
  const scope =
    currentOrganizationId && currentWorkspaceId
      ? `${currentOrganizationId}:${currentWorkspaceId}`
      : "";
  const previousScope = useRef(scope);
  const changedScope = Boolean(
    previousScope.current && scope && previousScope.current !== scope,
  );
  useEffect(() => {
    if (!scope) return;
    if (previousScope.current && previousScope.current !== scope) {
      const params = new URLSearchParams(location.search);
      params.delete("metric");
      params.delete("run");
      navigate(
        {
          pathname: paths.dashboard.evalMetrics,
          search: params.toString() ? `?${params}` : "",
          hash: location.hash,
        },
        { replace: true },
      );
    }
    previousScope.current = scope;
  }, [scope, navigate, location.pathname, location.search, location.hash]);
  if (changedScope || !isReady || !currentWorkspaceId || !currentOrganizationId)
    return (
      <Box sx={{ p: 3 }}>
        <LinearProgress aria-label="Loading workspace" />
      </Box>
    );
  return (
    <MetricEditor
      key={scope}
      scope={scope}
      organizationId={currentOrganizationId}
    />
  );
}

function MetricEditor({ scope, organizationId }) {
  const navigate = useNavigate();
  const location = useLocation();
  const cache = useQueryClient();
  const { isReadOnly } = useCanEditDashboard();
  const query = new URLSearchParams(location.search);
  const routeMetric = location.pathname.match(
    /\/evaluations\/metrics\/([^/]+)\/?$/,
  )?.[1];
  const requestedMetric = routeMetric || query.get("metric");
  const requestedRun = query.get("run");
  const metricId = uuid(requestedMetric) ? requestedMetric.toLowerCase() : "";
  const runId =
    metricId && uuid(requestedRun) ? requestedRun.toLowerCase() : "";
  const invalidSelection =
    (requestedMetric !== null && !metricId) ||
    (requestedRun !== null && requestedRun !== "" && (!runId || !metricId));
  const [form, setForm] = useState(initialForm);
  const [editorDraft, setEditorDraft] = useState(null);
  const [mode, setMode] = useState("inspect");
  const [detailTab, setDetailTab] = useState(
    location.hash === "#saved-run-results" ? "contributors" : "result",
  );
  const [editorRevision, setEditorRevision] = useState(0);
  const [dirty, setDirty] = useState(false);
  const [recovery, setRecovery] = useState(() => readMetricDraft(scope));
  const recoveryDismissed = useRef(false);
  const newMetricButton = useRef(null);
  const [draftStorageFailed, setDraftStorageFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [saveBlocked, setSaveBlocked] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [monitorFeedback, setMonitorFeedback] = useState(null);
  const [monitorLocked, setMonitorLocked] = useState(false);
  const [dashboardBlocked, setDashboardBlocked] = useState(false);
  const createdDashboard = useRef(null);
  const draftLocation = useRef(location);
  const draftMetric = useRef(null);
  const draftToken = useRef(crypto.randomUUID());
  const alive = useRef(false);
  const [projectSearch, setProjectSearch] = useState("");
  const [projectPage, setProjectPage] = useState(0);
  const [listPagination, setListPagination] = useState({
    page: 0,
    pageSize: 25,
    cursors: [""],
  });
  const before = listPagination.cursors[listPagination.page];
  const [runBefore, setRunBefore] = useState("");
  const [pollUntil, setPollUntil] = useState(() => Date.now() + 900000);
  const [preview, setPreview] = useState(null);
  const [rollup, setRollup] = useState("overall");
  const [after, setAfter] = useState("");
  const [dialog, setDialog] = useState("");
  const [pendingExit, setPendingExit] = useState(null);
  const allowNavigation = useMetricNavigationGuard(dirty, (go) =>
    setPendingExit(() => () => {
      // A replayed POP can change the route before the dirty-state update is
      // committed. Explicitly leave authoring rather than relying on a route
      // effect that may still see the previous dirty render.
      setMode("inspect");
      go();
    }),
  );
  const pendingAuthoring = useRef(null);
  const [evidenceOpen, setEvidenceOpen] = useState(
    location.hash === "#saved-run-results",
  );
  const evidenceRef = useRef(null);
  const runAttempt = useRef(null);
  const [runForm, setRunForm] = useState(() => ({
    start: localTime(new Date(Date.now() - 86400000)),
    end: localTime(new Date()),
    sourceRun: "",
    filterField: "model",
    filterValue: "",
  }));
  const [actionForm, setActionForm] = useState({
    dashboardName: "",
    monitorName: "",
    threshold: "0.8",
    operator: "less_than",
    alertWhen: "point",
    email: "",
  });
  const projects = useQuery({
    queryKey: ["metric-projects", scope, projectPage, projectSearch],
    enabled: true,
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
    queryKey: ["derived-metrics", scope, before, listPagination.pageSize],
    queryFn: async ({ signal }) =>
      (
        await axios.get(endpoints.derivedMetrics.list, {
          signal,
          params: {
            limit: listPagination.pageSize,
            ...(before ? { before } : {}),
          },
        })
      ).data,
  });
  const listedMetric = definitions.data?.results.find(
    (item) => item.id === metricId,
  );
  const detail = useQuery({
    queryKey: ["metric-definition", scope, metricId],
    enabled:
      Boolean(metricId) &&
      (definitions.isSuccess || definitions.isError) &&
      !listedMetric,
    retry: false,
    queryFn: async ({ signal }) =>
      (await axios.get(endpoints.derivedMetrics.detail(metricId), { signal }))
        .data,
  });
  const candidate = detail.data || listedMetric;
  const metric =
    !invalidSelection && candidate?.id === metricId ? candidate : null;
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
    queryKey: ["metric-run", scope, metric?.id, runId],
    enabled: Boolean(metric && runId),
    retry: false,
    queryFn: async ({ signal }) =>
      (await axios.get(endpoints.derivedMetrics.run(runId), { signal })).data,
    refetchInterval: (q) =>
      ["queued", "running"].includes(q.state.data?.status) &&
      Date.now() < pollUntil
        ? 2000
        : false,
  });
  const selectedRun =
    metric && run.data?.id === runId && run.data?.metric_id === metric.id
      ? run.data
      : null;
  const previewKey = JSON.stringify([
    metric?.id,
    metric?.current_version,
    runForm,
  ]);
  const previewIdentity = useRef({ key: previewKey, generation: 0 });
  if (previewIdentity.current.key !== previewKey)
    previewIdentity.current = {
      key: previewKey,
      generation: previewIdentity.current.generation + 1,
    };
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      previewIdentity.current.generation += 1;
    };
  }, []);
  useEffect(() => {
    if (dirty && mode !== "inspect")
      setDraftStorageFailed(
        !writeMetricDraft(scope, {
          form,
          mode,
          metric: draftMetric.current,
          location: draftLocation.current,
          token: draftToken.current,
          saveBlocked,
          conflict,
          editorDraft,
        }),
      );
  }, [scope, dirty, mode, form, saveBlocked, conflict, editorDraft]);
  const results = useCommittedMetricResults({
    scope,
    metricId: metric?.id,
    runId,
    run: run.data,
    refetchRun: run.refetch,
    rollup,
    after,
  });
  const updateSelection = (id, selectedRunId, replace = false) => {
    const params = new URLSearchParams(location.search);
    params.delete("metric");
    if (selectedRunId === null || !id) params.delete("run");
    else params.set("run", selectedRunId);
    navigate(
      {
        pathname: id
          ? `${paths.dashboard.evalMetrics}/${id}`
          : paths.dashboard.evalMetrics,
        search: params.toString() ? `?${params}` : "",
        hash: location.hash,
      },
      { replace },
    );
  };
  useEffect(() => {
    if (
      mode === "inspect" &&
      metric &&
      requestedRun === null &&
      runs.data?.results[0] &&
      !runBefore
    )
      updateSelection(metric.id, runs.data.results[0].id, true);
    // Selection is URL-owned. Do not substitute a different run for an invalid/deleted one.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [metric?.id, requestedRun, runs.data, runBefore, mode]);
  useEffect(() => {
    if (dirty) return;
    setMode("inspect");
    setDetailTab(
      location.hash === "#saved-run-results" ? "contributors" : "result",
    );
    setDirty(false);
    setDialog("");
    setRunBefore("");
    setError("");
    setNotice("");
    setSaveBlocked(false);
    setConflict(false);
    setMonitorFeedback(null);
    setMonitorLocked(false);
    setDashboardBlocked(false);
    createdDashboard.current = null;
    setActionForm({
      dashboardName: "",
      monitorName: "",
      threshold: "0.8",
      operator: "less_than",
      alertWhen: "point",
      email: "",
    });
    // Only a selection change resets the editor; typing must not reset it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [metricId]);
  useEffect(() => {
    setAfter("");
    setRollup("overall");
    setPreview(null);
    previewIdentity.current.generation += 1;
    setPollUntil(Date.now() + 900000);
  }, [runId]);
  useEffect(() => {
    setPreview(null);
    previewIdentity.current.generation += 1;
  }, [metricId, metric?.current_version]);
  useEffect(() => {
    setAfter("");
  }, [run.data?.result_id]);
  useEffect(() => {
    if (run.data?.status === "complete" || run.data?.status === "failed") {
      cache.invalidateQueries({ queryKey: ["metric-runs", scope] });
      cache.invalidateQueries({ queryKey: ["metric-list-latest", scope] });
    }
  }, [run.data?.status, cache, scope]);
  useEffect(() => {
    if (location.hash === "#saved-run-results" && results.matched?.page) {
      setEvidenceOpen(true);
      evidenceRef.current?.focus();
    }
  }, [location.hash, results.matched?.page]);
  useEffect(() => {
    const warn = (event) => {
      if (dirty) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);
  const guardExit = (go) => {
    if (busyRef.current) return;
    if (dirty) setPendingExit(() => go);
    else go();
  };
  const change = (key, value) => {
    if (isReadOnly || busyRef.current) return;
    setForm((current) => ({ ...current, [key]: value }));
    setDirty(true);
    setNotice("");
  };
  const perform = async (action) => {
    if (isReadOnly || busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await action();
    } catch (failure) {
      setError(metricFailureText(failure, dialog === "run"));
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  };
  const select = (item) =>
    guardExit(() => {
      setError("");
      setNotice("");
      setMode("inspect");
      setDirty(false);
      setDialog("");
      updateSelection(item.id, null);
    });
  const resolveDeferredDraft = (go) => {
    const deferred = mode === "inspect" && !dirty && readMetricDraft(scope);
    if (deferred && !isReadOnly) {
      pendingAuthoring.current = go;
      setRecovery(deferred);
    } else go();
  };
  const beginEdit = () =>
    resolveDeferredDraft(() => {
      if (!metric) return;
      draftToken.current = crypto.randomUUID();
      draftMetric.current = metric;
      draftLocation.current = location;
      setForm(formFromMetric(metric));
      setEditorDraft(null);
      setEditorRevision((value) => value + 1);
      setMode("edit");
      setDirty(false);
      setError("");
      setNotice("");
    });
  const newMetric = () =>
    guardExit(() =>
      resolveDeferredDraft(() => {
        draftToken.current = crypto.randomUUID();
        draftMetric.current = null;
        draftLocation.current = location;
        setForm(initialForm());
        setEditorDraft(null);
        setEditorRevision((value) => value + 1);
        setMode("new");
        setDirty(false);
        setError("");
        setNotice("");
        setSaveBlocked(false);
        setConflict(false);
        setDialog("");
      }),
    );
  const save = () =>
    perform(async () => {
      if (saveBlocked || weightedInputProblems(form).length) return;
      const editing = mode === "edit" ? draftMetric.current : null;
      const token = draftToken.current;
      if (editing && editing.id !== metricId) return;
      // Validate first; a failed preflight has not attempted a definition write.
      await axios.post(
        endpoints.derivedMetrics.validate,
        metricRequestBody(form, editing),
      );
      if (!alive.current || draftToken.current !== token) return;
      // Persist the uncertainty lock BEFORE sending a write. A route unmount
      // must not turn an in-flight/lost save into an unlocked recovered draft.
      writeMetricDraft(scope, {
        token,
        form,
        mode,
        metric: editing,
        location: draftLocation.current,
        saveBlocked: true,
        conflict: false,
        editorDraft,
      });
      let response;
      try {
        response = editing
          ? await axios.put(
              endpoints.derivedMetrics.detail(editing.id),
              metricRequestBody(form, editing),
            )
          : await axios.post(
              endpoints.derivedMetrics.list,
              metricRequestBody(form, null),
            );
      } catch (failure) {
        if (failure.statusCode === 409) {
          updateMetricDraft(scope, token, {
            conflict: true,
            saveBlocked: true,
          });
          setConflict(true);
          setSaveBlocked(true);
        } else if (![400, 402, 403, 404, 422].includes(failure.statusCode)) {
          setSaveBlocked(true);
          throw new Error(
            "Could not confirm the save. Check saved metrics before saving again; the definition may already exist.",
          );
        } else {
          updateMetricDraft(scope, token, { saveBlocked: false });
        }
        throw failure;
      }
      if (
        !uuid(response.data?.id) ||
        !response.data?.definition ||
        !Number.isInteger(response.data?.current_version)
      ) {
        setSaveBlocked(true);
        throw new Error(
          "Could not confirm the save. Check saved metrics before saving again; the definition may already exist.",
        );
      }
      cache.setQueryData(
        ["metric-definition", scope, response.data.id],
        response.data,
      );
      clearMetricDraft(scope, token);
      if (!alive.current || draftToken.current !== token) {
        await cache.invalidateQueries({ queryKey: ["derived-metrics", scope] });
        return;
      }
      setForm(formFromMetric(response.data));
      setDirty(false);
      setMode("inspect");
      allowNavigation(() =>
        updateSelection(
          response.data.id,
          editing && requestedRun !== null ? runId : null,
        ),
      );
      setNotice(
        `Saved version ${response.data.current_version}. Saving does not run the metric.`,
      );
      await cache.invalidateQueries({ queryKey: ["derived-metrics", scope] });
    });
  const runBody = () => {
    const body = {
      window_start: new Date(runForm.start).toISOString(),
      window_end: new Date(runForm.end).toISOString(),
      version: metric.current_version,
      filters: runForm.filterValue
        ? [{ field: runForm.filterField, value: runForm.filterValue }]
        : [],
      ...(runForm.sourceRun ? { source_run_id: runForm.sourceRun } : {}),
    };
    const signature = JSON.stringify([metric.id, body]);
    if (runAttempt.current?.signature !== signature)
      runAttempt.current = { signature, key: crypto.randomUUID() };
    return { ...body, idempotency_key: runAttempt.current.key };
  };
  const persistRun = () =>
    perform(async () => {
      if (!metric || dirty) return;
      const response = await axios.post(
        endpoints.derivedMetrics.runs(metric.id),
        runBody(),
      );
      if (!uuid(response.data?.id))
        throw new Error(
          "Could not confirm the run. Retry the unchanged window to reconcile the same request.",
        );
      updateSelection(metric.id, response.data.id);
      setDialog("");
      setAfter("");
      setRollup("overall");
      setPollUntil(Date.now() + 900000);
      runAttempt.current = null;
      await cache.invalidateQueries({
        queryKey: ["metric-list-latest", scope, metric.id],
      });
      await runs.refetch();
    });
  const previewRun = () =>
    perform(async () => {
      if (!metric || dirty) return;
      const identity = {
        ...previewIdentity.current,
        generation: previewIdentity.current.generation + 1,
      };
      previewIdentity.current = identity;
      setPreview(null);
      const isCurrent = () =>
        previewIdentity.current.key === identity.key &&
        previewIdentity.current.generation === identity.generation;
      try {
        const response = await axios.post(
          endpoints.derivedMetrics.preview(metric.id),
          runBody(),
        );
        if (isCurrent()) setPreview({ key: identity.key, data: response.data });
      } catch (failure) {
        if (isCurrent()) throw failure;
      }
    });
  const publishDashboard = () =>
    perform(async () => {
      if (!results.matched || results.pending || dashboardBlocked) return;
      const savedRun = results.matched.run;
      let dashboard = createdDashboard.current;
      if (!dashboard) {
        try {
          const created = await axios.post(endpoints.dashboard.create, {
            name: actionForm.dashboardName,
            description: `${metric.name} · ${formatMetricWindow(savedRun)}. A point represents the complete run window, not an average of daily ratios.`,
          });
          dashboard = created.data.result;
          createdDashboard.current = dashboard;
        } catch (failure) {
          if (![400, 402, 403, 422].includes(failure.statusCode)) {
            setDashboardBlocked(true);
            throw new Error(
              "Could not confirm dashboard creation. Check Dashboards before creating another; do not retry blindly.",
            );
          }
          throw failure;
        }
      }
      try {
        await axios.post(endpoints.dashboard.widgets(dashboard.id), {
          name: metric.name,
          description: `Version ${savedRun.metric_version} · ${formatMetricWindow(savedRun)}${savedRun.definition ? ` · ${savedRun.definition.selection_unit} · ${savedRun.definition.weighting === "none" ? "Unweighted" : "Inverse-probability weighted"}` : ""}`,
          query_config: {
            project_ids: [metric.project_id],
            time_range: {
              custom_start: savedRun.window_start,
              custom_end: savedRun.window_end,
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
      } catch {
        setDashboardBlocked(true);
        throw new Error(
          "The dashboard was created, but the widget could not be confirmed. Check Dashboards before creating another.",
        );
      }
      navigate(paths.dashboard.dashboards.detail(dashboard.id));
    });
  const createMonitor = async () => {
    if (
      isReadOnly ||
      busyRef.current ||
      dirty ||
      !organizationId ||
      !metric ||
      monitorLocked
    )
      return;
    busyRef.current = true;
    setBusy(true);
    setMonitorFeedback(null);
    try {
      const response = await axios.post(endpoints.project.createMonitor, {
        organization: organizationId,
        name: actionForm.monitorName,
        project: metric.project_id,
        metric_type: "derived_metric",
        metric: metric.id,
        threshold_operator: actionForm.operator,
        threshold_type: "static",
        ...(actionForm.alertWhen === "confident"
          ? { alert_when: "confident" }
          : {}),
        critical_threshold_value: Number(actionForm.threshold),
        alert_frequency: 60,
        ...(actionForm.email
          ? { notification_emails: [actionForm.email] }
          : {}),
        is_mute: false,
        filters: {},
      });
      if (
        response.data?.status !== true ||
        typeof response.data?.result !== "string"
      )
        throw new Error("Unconfirmed monitor response");
      setMonitorFeedback({
        severity: "success",
        message:
          "Monitor created. It will check new completed runs. Creation does not confirm an alert or notification delivery.",
      });
      setMonitorLocked(true);
    } catch (failure) {
      const message = monitorFailureMessage(failure);
      setMonitorFeedback({ severity: "error", message });
      if (message.startsWith("Could not confirm")) setMonitorLocked(true);
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  };
  const displayed = results.matched?.page;
  const displayedRun = results.matched?.run || selectedRun;
  const currentEvaluatorNames = useMetricEvaluatorNames(
    metric?.project_id,
    metric?.definition?.inputs,
    scope,
  );
  const savedEvaluatorNames = useMetricEvaluatorNames(
    metric?.project_id,
    displayedRun?.definition?.inputs,
    scope,
  );
  const resultReadNotice =
    results.pending &&
    run.data &&
    (selectedRun?.result_id !== null || results.matched)
      ? `Run ${selectedRun?.status || "identity unavailable"}. ${results.matched ? "Keeping the previously verified result while the latest committed result is reconciled." : "Committed result identity is not verified yet; unavailable data is not zero."} ${["queued", "running"].includes(selectedRun?.status) ? "Refresh run status" : "Retry saved result"} to retry.`
      : "";
  const openDialog = (name) => {
    setError("");
    setDialog(name);
  };
  const dismissRecovery = () => {
    pendingAuthoring.current = null;
    recoveryDismissed.current = true;
    setRecovery(null);
  };
  return (
    <Box
      sx={{
        p: 2,
        display: "flex",
        flexDirection: "column",
        gap: 2,
        height: "100%",
        width: "100%",
        minWidth: 0,
        minHeight: 0,
        flexShrink: 0,
        boxSizing: "border-box",
        bgcolor: "background.paper",
        color: "text.primary",
      }}
    >
      {mode === "inspect" && !requestedMetric ? (
        <>
          <Stack
            direction="row"
            justifyContent="space-between"
            alignItems="center"
            gap={2}
          >
            <Box>
              <Typography variant="h6" sx={{ fontSize: 18 }}>
                Evaluations
              </Typography>
              <Typography variant="body2" color="text.secondary">
                Create and manage your evaluations
              </Typography>
            </Box>
            <Button
              ref={newMetricButton}
              variant="contained"
              color="primary"
              disabled={busy || isReadOnly}
              onClick={newMetric}
            >
              New metric
            </Button>
          </Stack>
          <EvalsSectionTabs onNavigate={guardExit} />
        </>
      ) : (
        <Stack
          direction={{ xs: "column", sm: "row" }}
          justifyContent="space-between"
          gap={2}
          sx={{
            borderBottom: 1,
            borderColor: "divider",
            pb: 1.5,
            flexShrink: 0,
            alignItems: { sm: "center" },
          }}
        >
          <Breadcrumbs
            aria-label="Metric breadcrumbs"
            sx={{ minWidth: 0, "& .MuiBreadcrumbs-ol": { flexWrap: "wrap" } }}
          >
            <Button
              size="small"
              onClick={() =>
                guardExit(() => {
                  setMode("inspect");
                  setDirty(false);
                  updateSelection(null, null);
                })
              }
            >
              Metrics
            </Button>
            <Typography
              component="h1"
              sx={{
                fontSize: 14,
                fontWeight: 600,
                whiteSpace: "normal",
                overflowWrap: "anywhere",
              }}
            >
              {mode === "new"
                ? "New metric"
                : mode === "edit"
                  ? `Edit ${metric?.name || "metric"}`
                  : metric?.name || "Metric"}
            </Typography>
          </Breadcrumbs>
          {mode === "inspect" && metric && (
            <Stack direction="row" gap={1} flexShrink={0} flexWrap="wrap">
              <Chip size="small" label={`v${metric.current_version}`} />
              <Button
                size="small"
                variant="outlined"
                disabled={
                  busy || isReadOnly || !results.matched || results.pending
                }
                onClick={() => openDialog("dashboard")}
              >
                Create dashboard
              </Button>
              <Button
                size="small"
                variant="outlined"
                disabled={busy || isReadOnly}
                onClick={() => openDialog("monitor")}
              >
                Create monitor
              </Button>
              <Button
                ref={newMetricButton}
                variant="outlined"
                size="small"
                disabled={busy}
                onClick={beginEdit}
              >
                {isReadOnly ? "View definition" : "Edit definition"}
              </Button>
              <Button
                variant="contained"
                size="small"
                disabled={busy || isReadOnly}
                onClick={() => openDialog("run")}
              >
                Run metric
              </Button>
            </Stack>
          )}
        </Stack>
      )}
      <Stack spacing={2} sx={{ flex: 1, minHeight: 0, minWidth: 0 }}>
        {isReadOnly && (
          <Alert severity="info">
            You can inspect saved definitions and results. Workspace member
            access is required to change or run metrics.
          </Alert>
        )}
        {invalidSelection && (
          <Alert severity="error">
            Invalid metric or run link. Select a saved metric to continue.
          </Alert>
        )}
        {metricId &&
          (detail.isError || (detail.data && detail.data.id !== metricId)) && (
            <Alert severity="error">
              This metric is unavailable in this workspace.{" "}
              <Button onClick={() => detail.refetch()}>Retry metric</Button>
            </Alert>
          )}
        {metricId && !metric && !invalidSelection && detail.isFetching && (
          <LinearProgress aria-label="Loading selected metric" />
        )}
        {error && !dialog && <Alert severity="error">{error}</Alert>}
        {notice && <Alert severity="info">{notice}</Alert>}
        {(busy || run.isFetching) && (
          <LinearProgress aria-label="Loading metric data" />
        )}
        {mode !== "inspect" ? (
          <>
            {dirty && draftStorageFailed && (
              <Alert severity="warning">
                This browser could not save a recovery copy. Keep this page open
                until you save or discard your changes.
              </Alert>
            )}
            {conflict && (
              <Button
                disabled={busy}
                onClick={() =>
                  guardExit(async () => {
                    const refreshed = await detail.refetch();
                    if (refreshed.data?.id === metricId) {
                      draftMetric.current = refreshed.data;
                      setForm(formFromMetric(refreshed.data));
                      setEditorDraft(null);
                      setEditorRevision((value) => value + 1);
                      setDirty(false);
                      setConflict(false);
                      setSaveBlocked(false);
                      setError("");
                    }
                  })
                }
              >
                Reload saved definition
              </Button>
            )}
            <MetricDefinitionEditor
              key={`${mode}:${metric?.id || "new"}:${editorRevision}`}
              form={form}
              metric={mode === "edit" ? draftMetric.current : null}
              initialDraft={editorDraft}
              onDraftChange={(value) => {
                if (!isReadOnly && !busyRef.current) {
                  setEditorDraft(value);
                  setDirty(true);
                }
              }}
              projects={projects}
              projectSearch={projectSearch}
              projectPage={projectPage}
              onProjectSearch={(value) => {
                setProjectSearch(value);
                setProjectPage(0);
              }}
              onProjectPage={setProjectPage}
              onChange={change}
              onReplace={(next) => {
                if (!isReadOnly && !busyRef.current) {
                  setForm(next);
                  setDirty(true);
                  setNotice("");
                }
              }}
              busy={busy}
              readOnly={isReadOnly}
              dirty={dirty}
              onValidate={() =>
                perform(async () =>
                  setNotice(
                    (
                      await axios.post(
                        endpoints.derivedMetrics.validate,
                        metricRequestBody(
                          form,
                          mode === "edit" ? draftMetric.current : null,
                        ),
                      )
                    ).data.semantics,
                  ),
                )
              }
              onSave={save}
              onCancel={() =>
                guardExit(() => {
                  setMode("inspect");
                  setDirty(false);
                  setError("");
                })
              }
              saveBlocked={
                saveBlocked || weightedInputProblems(form).length > 0
              }
            />
          </>
        ) : metric ? (
          <>
            {metric.description && (
              <Typography
                variant="body2"
                color="text.secondary"
                sx={{ overflowWrap: "anywhere" }}
              >
                {metric.description}
              </Typography>
            )}
            <SegmentedTabs
              aria-label="Metric sections"
              value={detailTab}
              onChange={(_, value) => {
                setDetailTab(value);
                if (value === "result") {
                  setRollup("overall");
                  setAfter("");
                }
              }}
            >
              <Tab label="Result" value="result" />
              <Tab label="Past runs" value="runs" />
              <Tab label="Contributors" value="contributors" />
              <Tab label="Definition" value="definition" />
            </SegmentedTabs>
            <Box
              sx={{
                flex: 1,
                minHeight: 0,
                overflowY: "auto",
                minWidth: 0,
                pb: 2,
              }}
            >
              <Box hidden={detailTab !== "result"}>
                {detailTab === "result" && !runId && runs.isLoading && (
                  <LinearProgress aria-label="Loading past runs" />
                )}
                {detailTab === "result" && !runId && runs.isError && (
                  <Alert severity="error">
                    Past runs could not be loaded.{" "}
                    <Button onClick={() => runs.refetch()}>
                      Retry past runs
                    </Button>
                  </Alert>
                )}
                {!runId && runs.isSuccess && runs.data.results.length > 0 && (
                  <Typography variant="body2">
                    Select a saved run to inspect its result.
                  </Typography>
                )}
                {!runId && runs.isSuccess && runs.data.results.length === 0 && (
                  <Box>
                    <Typography variant="h6">No saved results</Typography>
                    <Typography variant="body2" color="text.secondary">
                      This definition is saved. Run it on a closed time window
                      to see a result.
                    </Typography>
                  </Box>
                )}
                {run.isError && (
                  <Alert severity="error">
                    The selected run is unavailable in this workspace. No other
                    run has been substituted.{" "}
                    <Button onClick={() => run.refetch()}>
                      Retry selected run
                    </Button>
                  </Alert>
                )}
                {run.data && !selectedRun && (
                  <Alert severity="error">
                    Selected run identity could not be verified. Unavailable
                    data is not zero.
                  </Alert>
                )}
                {results.isError && (
                  <Alert severity="error">
                    This result read failed. Use the retry action below;
                    unavailable data is not zero.
                  </Alert>
                )}
              </Box>
              {displayedRun &&
                ["result", "contributors"].includes(detailTab) && (
                  <SelectedRunSummary
                    run={displayedRun}
                    evaluatorNames={savedEvaluatorNames}
                    readNotice={resultReadNotice}
                    result={
                      rollup === "overall" && !results.matched?.after
                        ? displayed?.results[0]
                        : null
                    }
                    actions={
                      selectedRun &&
                      (["queued", "running"].includes(selectedRun.status) ||
                        results.pending ||
                        results.isError) ? (
                        <Stack
                          direction="row"
                          gap={1}
                          flexWrap="wrap"
                          component="nav"
                          aria-label="Result actions"
                        >
                          <Button
                            onClick={async () => {
                              const refreshed = await run.refetch();
                              if (
                                results.pending &&
                                refreshed.data?.result_id === run.data.result_id
                              )
                                await results.refetch();
                            }}
                          >
                            {["queued", "running"].includes(selectedRun.status)
                              ? "Refresh run status"
                              : "Retry saved result"}
                          </Button>
                        </Stack>
                      ) : null
                    }
                    detailsOpen={evidenceOpen}
                    onDetailsToggle={setEvidenceOpen}
                    detailsRef={evidenceRef}
                  />
                )}
              <Box hidden={detailTab !== "contributors"}>
                {detailTab === "contributors" && selectedRun && (
                  <Box
                    component="section"
                    aria-label="Contributors and breakdown"
                  >
                    <Stack gap={2}>
                      <Typography variant="body2">
                        Run {selectedRun.status} · v{selectedRun.metric_version}
                        . {selectedRun.error} Window{" "}
                        {formatMetricWindow(selectedRun)}.{" "}
                        {selectedRun.snapshot_id
                          ? `Captured at ${formatMetricTime(selectedRun.as_of)}.`
                          : "No snapshot captured yet."}{" "}
                        Replay uses immutable inputs; late arrivals require a
                        new run.
                      </Typography>
                      {selectedRun.result_id &&
                        selectedRun.status !== "complete" && (
                          <Typography variant="body2">
                            {resultReadNotice ||
                              "Showing the last committed result; the latest recompute has not completed."}
                          </Typography>
                        )}
                      <Button
                        sx={{ alignSelf: "flex-start" }}
                        disabled={
                          busy ||
                          isReadOnly ||
                          (["queued", "running"].includes(selectedRun.status) &&
                            new Date(selectedRun.lease_expires_at).getTime() >
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
                        {selectedRun.snapshot_id
                          ? "Recompute frozen run"
                          : "Retry run"}
                      </Button>
                      {displayed && (
                        <>
                          <TextField
                            select
                            label="Group results by"
                            value={rollup}
                            onChange={(e) => {
                              setRollup(e.target.value);
                              setAfter("");
                            }}
                          >
                            {[
                              "overall",
                              "session",
                              "user",
                              "cohort",
                              "day",
                            ].map((value) => (
                              <MenuItem key={value} value={value}>
                                {value}
                              </MenuItem>
                            ))}
                          </TextField>
                          <MetricResults
                            results={displayed.results}
                            rollup={rollup}
                          />
                          {results.matched && displayed.results.length > 0 && (
                            <MetricContributors
                              key={`${runId}:${displayed.result_id}:${rollup}:${results.matched.after}`}
                              expanded={detailTab === "contributors"}
                              scope={scope}
                              runId={runId}
                              resultId={displayed.result_id}
                              projectId={metric.project_id}
                              rollup={rollup}
                              groups={displayed.results}
                            />
                          )}
                          <Stack direction="row">
                            <Button
                              disabled={!after}
                              onClick={() => setAfter("")}
                            >
                              First results
                            </Button>
                            <Button
                              disabled={
                                results.pending || !displayed.next_cursor
                              }
                              onClick={() => setAfter(displayed.next_cursor)}
                            >
                              Next results
                            </Button>
                          </Stack>
                        </>
                      )}
                    </Stack>
                  </Box>
                )}
                {!selectedRun && (
                  <Typography variant="body2">
                    Select a saved run to inspect its contributors.
                  </Typography>
                )}
              </Box>
              <Box hidden={detailTab !== "runs"}>
                {detailTab === "runs" && runs.isError && (
                  <Alert severity="error">
                    Past runs could not be loaded.{" "}
                    <Button onClick={() => runs.refetch()}>
                      Retry past runs
                    </Button>
                  </Alert>
                )}
                {detailTab === "runs" && runs.isLoading && (
                  <LinearProgress aria-label="Loading past runs" />
                )}
                {detailTab === "runs" && runs.data && (
                  <Stack gap={1}>
                    <MetricRunHistory
                      runs={runs.data.results}
                      selectedRun={selectedRun}
                      metricId={metric.id}
                      scope={scope}
                      onSelect={(id) => {
                        setRollup("overall");
                        setAfter("");
                        updateSelection(metric.id, id);
                        setDetailTab("result");
                      }}
                      onClear={
                        runId
                          ? () => {
                              setRollup("overall");
                              setAfter("");
                              updateSelection(metric.id, "");
                              setDetailTab("result");
                            }
                          : undefined
                      }
                    />
                    {(runBefore || runs.data.next_cursor) && (
                      <Stack
                        direction="row"
                        gap={1}
                        aria-label="Past runs pages"
                      >
                        <Button
                          size="small"
                          variant="outlined"
                          disabled={!runBefore}
                          onClick={() => setRunBefore("")}
                        >
                          Newest runs
                        </Button>
                        <Button
                          size="small"
                          variant="outlined"
                          disabled={!runs.data.next_cursor}
                          onClick={() => setRunBefore(runs.data.next_cursor)}
                        >
                          Older runs
                        </Button>
                      </Stack>
                    )}
                  </Stack>
                )}
              </Box>
              <Box hidden={detailTab !== "definition"}>
                <Stack
                  spacing={1.5}
                  sx={{ minWidth: 0, overflowWrap: "anywhere" }}
                >
                  <Typography variant="subtitle2">
                    Current definition · version {metric.current_version}
                  </Typography>
                  <Typography variant="body2">
                    {definitionSummary(
                      formFromMetric(metric),
                      currentEvaluatorNames,
                    )}
                  </Typography>
                  <Typography variant="body2">
                    Saving a new version does not change previous runs. Each
                    result uses its own frozen definition.
                  </Typography>
                  <Typography
                    component="pre"
                    variant="body2"
                    sx={{
                      whiteSpace: "pre-wrap",
                      overflowWrap: "anywhere",
                      m: 0,
                    }}
                  >
                    {JSON.stringify(metric.definition, null, 2)}
                  </Typography>
                </Stack>
              </Box>
            </Box>
          </>
        ) : (
          !invalidSelection &&
          !metricId && (
            <MetricList
              definitions={definitions}
              scope={scope}
              onSelect={select}
              pagination={listPagination}
              onPageChange={(page, nextCursor) =>
                setListPagination((current) => {
                  if (page > current.page && !nextCursor) return current;
                  const cursors = current.cursors.slice(0, page + 1);
                  if (page > current.page) cursors[page] = nextCursor;
                  return { ...current, page, cursors };
                })
              }
              onPageSizeChange={(pageSize) =>
                setListPagination({ page: 0, pageSize, cursors: [""] })
              }
              disabled={busy}
            />
          )
        )}
      </Stack>
      <MetricRunDialog
        open={dialog === "run"}
        onClose={() => {
          previewIdentity.current.generation += 1;
          setDialog("");
        }}
        metricName={metric?.name}
        definitionVersion={metric?.current_version}
        inspectedVersion={selectedRun?.metric_version}
        {...runForm}
        onChange={(key, value) => {
          previewIdentity.current.generation += 1;
          setRunForm((current) => ({ ...current, [key]: value }));
          setPreview(null);
        }}
        onPreview={previewRun}
        onRun={persistRun}
        busy={busy}
        readOnly={isReadOnly}
        dirty={dirty}
        preview={preview?.key === previewKey ? preview.data : null}
        error={error}
      />
      <MetricResultActions
        action={["dashboard", "monitor"].includes(dialog) ? dialog : ""}
        onClose={() => setDialog("")}
        busy={busy}
        readOnly={isReadOnly}
        dirty={dirty}
        matched={Boolean(results.matched)}
        pending={results.pending}
        {...actionForm}
        onChange={(key, value) =>
          setActionForm((current) => ({ ...current, [key]: value }))
        }
        onDashboard={publishDashboard}
        onMonitor={createMonitor}
        feedback={monitorFeedback}
        monitorLocked={monitorLocked}
        dashboardBlocked={dashboardBlocked}
        error={error}
      />
      <Dialog
        open={Boolean(recovery) && !isReadOnly}
        onClose={dismissRecovery}
        TransitionProps={{
          onExited: () => {
            if (recoveryDismissed.current) {
              recoveryDismissed.current = false;
              newMetricButton.current?.focus();
            }
          },
        }}
        aria-labelledby="metric-restore-title"
      >
        <DialogTitle id="metric-restore-title">
          Restore unsaved metric?
        </DialogTitle>
        <DialogContent>
          {recovery?.form.name || "Untitled metric"} has unsaved changes from
          this workspace, kept only in this browser. Restoring does not save a
          version or run the metric.
        </DialogContent>
        <DialogActions>
          <Button onClick={dismissRecovery}>Not now</Button>
          <Button
            onClick={() => {
              clearMetricDraft(scope);
              setRecovery(null);
              const go = pendingAuthoring.current;
              pendingAuthoring.current = null;
              go?.();
            }}
          >
            Discard draft
          </Button>
          <Button
            variant="contained"
            onClick={() => {
              // Restored editing owns a new recovery copy. A late response for
              // the old token cannot clear it; its uncertain-write lock survives.
              pendingAuthoring.current = null;
              draftToken.current = crypto.randomUUID();
              draftMetric.current = recovery.metric;
              draftLocation.current = recovery.location;
              setForm(recovery.form);
              setEditorDraft(recovery.editorDraft || null);
              setError(
                recovery.saveBlocked
                  ? recovery.conflict
                    ? "This definition changed. Reload the saved definition before editing again; your draft has not been overwritten."
                    : "Could not confirm the previous save. Check saved metrics before saving again; the definition may already exist."
                  : "",
              );
              setMode(recovery.mode);
              setDirty(true);
              setSaveBlocked(Boolean(recovery.saveBlocked));
              setConflict(Boolean(recovery.conflict));
              setEditorRevision((value) => value + 1);
              navigate(recovery.location, { replace: true });
              setRecovery(null);
            }}
          >
            Restore draft
          </Button>
        </DialogActions>
      </Dialog>
      <Dialog
        open={Boolean(pendingExit)}
        onClose={() => setPendingExit(null)}
        aria-labelledby="metric-discard-title"
      >
        <DialogTitle id="metric-discard-title">
          Discard unsaved changes?
        </DialogTitle>
        <DialogContent>
          Your saved definition and results have not changed.
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setPendingExit(null)}>Keep editing</Button>
          <Button
            variant="contained"
            onClick={() => {
              const go = pendingExit;
              clearMetricDraft(scope);
              draftToken.current = crypto.randomUUID();
              setPendingExit(null);
              setDirty(false);
              allowNavigation(go);
            }}
          >
            Discard changes
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
MetricEditor.propTypes = {
  scope: PropTypes.string.isRequired,
  organizationId: PropTypes.string.isRequired,
};
