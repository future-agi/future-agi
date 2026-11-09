import PropTypes from "prop-types";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { useAuthContext } from "src/auth/hooks";
import { useOrganization } from "src/contexts/OrganizationContext";
import { useWorkspace } from "src/contexts/WorkspaceContext";
import axios, { endpoints } from "src/utils/axios";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { getSafeActionErrorMessage } from "src/utils/errorUtils";
import {
  useCreateMetric,
  useMetricCapabilities,
  useMetricCatalog,
  useMetricDetail,
  useMetricEvaluators,
  useUpdateMetric,
  useValidateMetric,
} from "./api";
import {
  builderFromMetric,
  builderProblems,
  builderWrite,
  needsRebuild,
  newBuilder,
  previewDefinition,
  rebuildAsUser,
  supportedSourceOptions,
} from "./builderSpec";
import {
  clearMetricDraft,
  readMetricDraft,
  writeMetricDraft,
} from "./metric_draft";
import MetricProjectSelect from "./MetricProjectSelect";
import SentenceBuilder from "./SentenceBuilder";
import MetricPreviewPanel from "./MetricPreviewPanel";
import useMetricNavigationGuard from "./use_metric_navigation_guard";
import usePreview from "./usePreview";

const ROOT = "/dashboard/metrics";
export default function MetricBuilderPage() {
  const { metricId } = useParams();
  const [params] = useSearchParams();
  const { currentOrganizationId } = useOrganization();
  const { currentWorkspaceId, currentWorkspaceRole } = useWorkspace();
  const { role } = useAuthContext();
  const scope = {
    organizationId: currentOrganizationId,
    workspaceId: currentWorkspaceId,
  };
  const canWrite = Boolean(
    RolePermission.OBSERVABILITY[PERMISSIONS.ADD_TASKS_ALERTS][
      currentWorkspaceRole || role
    ],
  );
  return (
    <BuilderLoader
      key={`${currentOrganizationId}:${currentWorkspaceId}:${metricId || "new"}`}
      scope={scope}
      metricId={metricId}
      projectId={params.get("project") || ""}
      canWrite={canWrite}
    />
  );
}

function BuilderLoader({ scope, metricId, projectId, canWrite }) {
  const query = useMetricDetail({ scope, metricId });
  // Pin the edit baseline. Background refetch must not unmount a dirty editor
  // or replace its recipe; expected_version protects the subsequent write.
  const [metric, setMetric] = useState(null);
  useEffect(() => {
    if (!metric && query.data) setMetric(query.data);
  }, [metric, query.data]);
  if (metricId && !metric)
    return (
      <Box sx={{ p: 3 }}>
        {["loading", "idle", "ready"].includes(query.readState) ? (
          <CircularProgress size={20} aria-label="Loading metric" />
        ) : (
          <Alert
            severity="warning"
            action={<Button onClick={() => query.refetch()}>Retry</Button>}
          >
            Metric definition unavailable.
          </Alert>
        )}
      </Box>
    );
  if (metricId && !(metric?.draft_definition || metric?.definition))
    return (
      <Alert severity="warning">
        The saved definition is unavailable. No replacement has been created.
      </Alert>
    );
  return (
    <BuilderForm
      scope={scope}
      metric={metric}
      projectId={projectId}
      canWrite={canWrite}
    />
  );
}
BuilderLoader.propTypes = {
  scope: PropTypes.object.isRequired,
  metricId: PropTypes.string,
  projectId: PropTypes.string,
  canWrite: PropTypes.bool,
};

function BuilderForm({ scope: workspaceScope, metric, projectId, canWrite }) {
  const navigate = useNavigate();
  const recoveryKey = `builder-v2:${workspaceScope.organizationId}:${workspaceScope.workspaceId}:${metric?.id || "new"}`;
  const [baseline] = useState(() =>
    metric ? builderFromMetric(metric) : newBuilder(projectId),
  );
  const legacy = needsRebuild(metric);
  const [form, setForm] = useState(() => {
    const recovered = readMetricDraft(recoveryKey);
    return !legacy &&
      recovered?.form &&
      recovered.version === metric?.current_version
      ? recovered.form
      : baseline;
  });
  const [rebuilding, setRebuilding] = useState(false);
  const [pendingNavigation, setPendingNavigation] = useState(null);
  const [saveError, setSaveError] = useState("");
  const [sourceSearch, setSourceSearch] = useState("");
  const [cursor, setCursor] = useState(null);
  const [catalogPages, setCatalogPages] = useState([]);
  const [projectSearch, setProjectSearch] = useState("");
  const [saving, setSaving] = useState(false);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const scope = useMemo(
    () => ({ ...workspaceScope, projectId: form.project_id }),
    [workspaceScope, form.project_id],
  );
  const dirty = JSON.stringify(form) !== JSON.stringify(baseline);
  const allowNavigation = useMetricNavigationGuard(dirty, (go) =>
    setPendingNavigation(() => go),
  );
  const capabilities = useMetricCapabilities({ scope: workspaceScope });
  const create = useCreateMetric({ scope });
  const update = useUpdateMetric({ scope, metricId: metric?.id });
  const validate = useValidateMetric({ scope });
  const readOnly = !canWrite || (legacy && !rebuilding);
  const disabled = readOnly || saving;
  const projects = useQuery({
    queryKey: [
      "metric-builder-projects",
      workspaceScope.organizationId,
      workspaceScope.workspaceId,
    ],
    enabled: Boolean(
      workspaceScope.organizationId && workspaceScope.workspaceId,
    ),
    retry: false,
    queryFn: async ({ signal }) => {
      const response = await axios.get(endpoints.project.listProjects(), {
        signal,
        params: { project_type: "observe" },
        headers: {
          "X-Organization-Id": workspaceScope.organizationId,
          "X-Workspace-Id": workspaceScope.workspaceId,
        },
      });
      signal.throwIfAborted();
      return { rows: response.data.result.projects, totalPages: 1 };
    },
  });
  const projectOptions = {
    ...projects,
    data: projects.data
      ? {
          ...projects.data,
          rows: projects.data.rows.filter((project) =>
            project.name.toLowerCase().includes(projectSearch.toLowerCase()),
          ),
        }
      : undefined,
  };
  const catalog = useMetricCatalog({
    scope,
    enabled: Boolean(form.project_id),
    params: {
      project_ids: form.project_id,
      source: "spans",
      cursor_mode: true,
      // The property catalog rejects cursor pages above its server maximum
      // (PROPERTY_CATALOG_MAX_PAGE_SIZE, 50 by default) with HTTP 400.
      page_size: 50,
      search: sourceSearch,
      ...(cursor ? { cursor } : {}),
    },
  });
  const evaluators = useMetricEvaluators({
    scope,
    params: { search: sourceSearch, limit: 100 },
  });
  useEffect(() => {
    if (catalog.data)
      setCatalogPages((pages) =>
        cursor ? [...pages, catalog.data] : [catalog.data],
      );
  }, [catalog.data, cursor]);
  const searchSources = (search) => {
    setSourceSearch(search);
    setCursor(null);
    setCatalogPages([]);
  };
  const catalogData = {
    result: {
      metrics: Array.from(
        new Map(
          catalogPages
            .flatMap((page) => page.result?.metrics || [])
            .map((item) => [item.property_id, item]),
        ).values(),
      ),
    },
  };
  const projectName = projects.data?.rows.find(
    (project) => project.id === form.project_id,
  )?.name;
  const options = supportedSourceOptions(
    catalogData,
    evaluators.data,
    capabilities.data,
    projectName,
  );
  const problems = builderProblems(form, capabilities.data);
  const previewProblems = builderProblems(form, capabilities.data, {
    metadata: false,
  });
  const [range] = useState(() => {
    const end = new Date();
    return {
      window_start: new Date(end.getTime() - 7 * 86400000).toISOString(),
      window_end: end.toISOString(),
    };
  });
  const preview = usePreview({
    scope,
    definition: previewDefinition(form),
    window: range,
    enabled: !previewProblems.length && !(legacy && !rebuilding),
  });
  useEffect(() => {
    if (dirty)
      writeMetricDraft(recoveryKey, {
        form,
        mode: metric ? "edit" : "new",
        version: metric?.current_version,
        location: {
          pathname: metric ? `${ROOT}/${metric.id}/edit` : `${ROOT}/new`,
        },
      });
    else clearMetricDraft(recoveryKey);
    const beforeUnload = (event) => {
      if (dirty) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", beforeUnload);
    return () => window.removeEventListener("beforeunload", beforeUnload);
  }, [dirty, form, recoveryKey, metric]);
  const cancel = () => navigate(metric ? `${ROOT}/${metric.id}` : ROOT);
  const save = async () => {
    if (
      disabled ||
      problems.length ||
      (legacy && (!rebuilding || form.level !== "user"))
    )
      return;
    setSaving(true);
    setSaveError("");
    try {
      const body = builderWrite(form, metric);
      await validate.mutateAsync(body);
      if (!mounted.current) return;
      const saved = await (metric ? update : create).mutateAsync(body);
      if (!mounted.current) return;
      clearMetricDraft(recoveryKey);
      allowNavigation(() => navigate(saved?.id ? `${ROOT}/${saved.id}` : ROOT));
    } catch (error) {
      if (mounted.current)
        setSaveError(
          getSafeActionErrorMessage(
            error,
            "Metric could not be saved. Your changes are still here. Reload if another version was saved.",
          ),
        );
    } finally {
      if (mounted.current) setSaving(false);
    }
  };
  return (
    <Stack sx={{ height: "100%", minHeight: 0, bgcolor: "background.paper" }}>
      <Stack
        direction="row"
        alignItems="center"
        gap={2}
        sx={{ px: 3, py: 2, borderBottom: 1, borderColor: "divider" }}
      >
        <Button color="inherit" onClick={cancel}>
          Back to metrics
        </Button>
        <Typography component="h1" variant="h6" fontWeight={600}>
          {metric ? "Edit metric" : "New metric"}
        </Typography>
        {dirty && (
          <Typography variant="caption" color="text.secondary">
            Unsaved changes
          </Typography>
        )}
      </Stack>
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: "minmax(440px, 1fr) minmax(360px, 0.85fr)",
          flex: 1,
          minHeight: 0,
          overflow: "auto",
        }}
      >
        <Stack
          gap={3}
          sx={{ p: 3, borderRight: 1, borderColor: "divider", minWidth: 0 }}
        >
          {!canWrite && (
            <Alert severity="info">
              You can view this definition. A workspace member must save
              changes.
            </Alert>
          )}
          {legacy && (
            <Alert severity="warning">
              <Typography variant="subtitle2">
                This legacy user metric needs a rebuild
              </Typography>
              <Typography variant="body2">
                The old definition is preserved read-only. Rebuild explicitly as
                a new user-level version; it will not be converted
                automatically.
              </Typography>
              {!rebuilding && canWrite && (
                <Button
                  color="inherit"
                  onClick={() => {
                    setForm(rebuildAsUser(baseline));
                    setRebuilding(true);
                  }}
                >
                  Rebuild as user metric
                </Button>
              )}
              <Box
                component="pre"
                sx={{
                  whiteSpace: "pre-wrap",
                  overflowWrap: "anywhere",
                  fontSize: 12,
                }}
              >
                {JSON.stringify(previewDefinition(baseline), null, 2)}
              </Box>
            </Alert>
          )}
          <TextField
            label="Metric name"
            size="small"
            value={form.name}
            disabled={disabled}
            inputProps={{ maxLength: 160 }}
            onChange={(event) => setForm({ ...form, name: event.target.value })}
          />
          <MetricProjectSelect
            value={form.project_id}
            projects={projectOptions}
            search={projectSearch}
            page={0}
            onSearch={setProjectSearch}
            readOnly={Boolean(metric) || readOnly}
            disabled={saving}
            onChange={(id) => {
              setForm({
                ...newBuilder(id),
                name: form.name,
                description: form.description,
              });
              searchSources("");
            }}
          />
          {projects.isError && (
            <Alert
              severity="warning"
              action={<Button onClick={() => projects.refetch()}>Retry</Button>}
            >
              Projects unavailable.
            </Alert>
          )}
          <TextField
            label="Description (optional)"
            size="small"
            value={form.description}
            disabled={disabled}
            inputProps={{ maxLength: 2000 }}
            onChange={(event) =>
              setForm({ ...form, description: event.target.value })
            }
          />
          {!capabilities.data && (
            <Alert
              severity="info"
              action={
                <Button onClick={() => capabilities.refetch()}>Retry</Button>
              }
            >
              {capabilities.readState === "loading"
                ? "Loading source capabilities…"
                : "Source capabilities unavailable. Saving and preview are disabled."}
            </Alert>
          )}
          <SentenceBuilder
            key={`${form.project_id}:${rebuilding}`}
            form={form}
            capabilities={capabilities.data}
            options={options}
            loading={
              catalog.readState === "loading" ||
              evaluators.readState === "loading"
            }
            disabled={disabled || !capabilities.data}
            onChange={setForm}
            onSearch={searchSources}
            rebuilding={rebuilding}
          />
          {form.project_id &&
            [catalog, evaluators].some(
              (query) =>
                !["idle", "ready", "loading"].includes(query.readState),
            ) && (
              <Alert
                severity="warning"
                action={
                  <Button
                    onClick={() => {
                      catalog.refetch();
                      evaluators.refetch();
                    }}
                  >
                    Retry sources
                  </Button>
                }
              >
                Some sources are unavailable. Saved bindings are preserved.
              </Alert>
            )}
          {catalog.data?.result?.next_cursor && (
            <Button
              color="inherit"
              disabled={catalog.readState === "loading"}
              onClick={() => setCursor(catalog.data.result.next_cursor)}
            >
              Load more fields
            </Button>
          )}
          {evaluators.data?.truncated && (
            <Typography variant="caption" color="text.secondary">
              More saved evaluations are available. Search to narrow the list.
            </Typography>
          )}
          {metric && (
            <Typography variant="body2" color="text.secondary">
              Saving creates version {metric.current_version + 1} and queues
              recalculation of history. The active version stays available until
              the new one is ready.
            </Typography>
          )}
          {saveError && <Alert severity="error">{saveError}</Alert>}
          {problems.length > 0 && (
            <Typography variant="caption" color="text.secondary" role="status">
              {problems[0]}
            </Typography>
          )}
        </Stack>
        <Box sx={{ minWidth: 0 }}>
          <MetricPreviewPanel preview={preview} form={form} />
        </Box>
      </Box>
      <Stack
        direction="row"
        gap={1}
        justifyContent="flex-end"
        sx={{ px: 3, py: 1.5, borderTop: 1, borderColor: "divider" }}
      >
        <Button
          color="inherit"
          variant="outlined"
          disabled={saving}
          onClick={cancel}
        >
          Cancel
        </Button>
        <Button
          color="inherit"
          variant="contained"
          disabled={disabled || problems.length > 0}
          onClick={save}
        >
          {saving ? "Saving…" : "Save metric"}
        </Button>
      </Stack>
      <Dialog
        open={Boolean(pendingNavigation)}
        onClose={() => setPendingNavigation(null)}
        aria-labelledby="discard-metric-title"
      >
        <DialogTitle id="discard-metric-title">
          Discard unsaved changes?
        </DialogTitle>
        <DialogContent>Your saved metric will not change.</DialogContent>
        <DialogActions>
          <Button color="inherit" onClick={() => setPendingNavigation(null)}>
            Keep editing
          </Button>
          <Button
            color="inherit"
            variant="contained"
            onClick={() => {
              clearMetricDraft(recoveryKey);
              pendingNavigation();
              setPendingNavigation(null);
            }}
          >
            Discard changes
          </Button>
        </DialogActions>
      </Dialog>
    </Stack>
  );
}
BuilderForm.propTypes = {
  scope: PropTypes.object.isRequired,
  metric: PropTypes.object,
  projectId: PropTypes.string,
  canWrite: PropTypes.bool,
};
