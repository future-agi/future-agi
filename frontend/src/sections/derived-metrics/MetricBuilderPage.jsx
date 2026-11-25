import PropTypes from "prop-types";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  IconButton,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import { useSnackbar } from "notistack";
import { ClosableDialog } from "src/components/custom-dialog";
import Iconify from "src/components/iconify";
import StatusBadge from "src/components/status-badge";
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
  sourceMatches,
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
import { inputLabel } from "./metricPresentation";
import useMetricNavigationGuard from "./use_metric_navigation_guard";
import usePreview from "./usePreview";

const ROOT = "/dashboard/metrics";
// With no search typed, the source list reads further catalog pages on its
// own until it holds this many span fields (custom attributes often sit past
// the first page), up to AUTO_CATALOG_PAGES pages.
const AUTO_SOURCE_TARGET = 20;
const AUTO_CATALOG_PAGES = 5;
// Text fields, selects and searchable pickers share one 32px height here so
// a row of mixed controls reads as one line.
const FIELD_SX = {
  "& .MuiOutlinedInput-root:not(.MuiInputBase-multiline)": { height: 32 },
  "& .MuiAutocomplete-root .MuiOutlinedInput-root": { py: 0 },
};
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

/** The editor's title bar. The loading and error states render the same bar,
 * back button included, so nothing shifts when the form mounts and there is
 * always an in-page way back. */
function BuilderTitleBar({ title, onBack, children }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={1}
      sx={{
        px: 2,
        minHeight: 52,
        flexShrink: 0,
        borderBottom: 1,
        borderColor: "divider",
      }}
    >
      <Tooltip title="Back to metrics">
        <IconButton size="small" aria-label="Back to metrics" onClick={onBack}>
          <Iconify icon="solar:arrow-left-linear" width={18} />
        </IconButton>
      </Tooltip>
      <Typography
        component="h1"
        variant="m1"
        sx={{ fontWeight: 600, color: "text.primary" }}
      >
        {title}
      </Typography>
      {children}
    </Stack>
  );
}
BuilderTitleBar.propTypes = {
  title: PropTypes.string.isRequired,
  onBack: PropTypes.func.isRequired,
  children: PropTypes.node,
};

function BuilderLoader({ scope, metricId, projectId, canWrite }) {
  const navigate = useNavigate();
  const query = useMetricDetail({ scope, metricId });
  // Pin the edit baseline. Background refetch must not unmount a dirty editor
  // or replace its recipe; expected_version protects the subsequent write.
  const [metric, setMetric] = useState(null);
  useEffect(() => {
    if (!metric && query.data) setMetric(query.data);
  }, [metric, query.data]);
  // Before the form mounts: the same title bar, whose back button goes where
  // the loaded editor's does (the metric's page).
  const pending = (body) => (
    <Stack sx={{ height: "100%", bgcolor: "background.paper" }}>
      <BuilderTitleBar
        title="Edit metric"
        onBack={() => navigate(`${ROOT}/${metricId}`)}
      />
      <Box sx={{ p: 3 }}>{body}</Box>
    </Stack>
  );
  if (metricId && !metric)
    return pending(
      ["loading", "idle", "ready"].includes(query.readState) ? (
        <CircularProgress size={20} aria-label="Loading metric" />
      ) : (
        <Alert
          severity="warning"
          action={<Button onClick={() => query.refetch()}>Retry</Button>}
        >
          Metric definition unavailable.
        </Alert>
      ),
    );
  if (metricId && !(metric?.draft_definition || metric?.definition))
    return pending(
      <Alert severity="warning">
        The saved definition is unavailable. No replacement has been created.
      </Alert>,
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
  const { enqueueSnackbar } = useSnackbar();
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
  const spanSourceCount = options.filter(
    (option) => option.descriptor?.source === "span",
  ).length;
  const nextCatalogCursor = catalog.data?.result?.next_cursor;
  // Only once the current page is merged, so the page count and the span
  // fields it counts include that page.
  const currentPageMerged =
    Boolean(catalog.data) && catalogPages.at(-1) === catalog.data;
  useEffect(() => {
    if (
      !sourceSearch &&
      nextCatalogCursor &&
      catalog.readState === "ready" &&
      currentPageMerged &&
      catalogPages.length < AUTO_CATALOG_PAGES &&
      spanSourceCount < AUTO_SOURCE_TARGET
    )
      setCursor(nextCatalogCursor);
  }, [
    sourceSearch,
    nextCatalogCursor,
    catalog.readState,
    currentPageMerged,
    catalogPages.length,
    spanSourceCount,
  ]);
  const sourceLabels = Object.fromEntries(
    (form.inputs || []).flatMap((input) => {
      const option = options.find((item) => sourceMatches(input, item));
      return option ? [[input.alias, option.label]] : [];
    }),
  );
  // Messages name a source the way the person picked it, never by its alias.
  const labelFor = (alias) =>
    inputLabel(alias, { inputs: form.inputs, sourceLabels });
  const problems = builderProblems(form, capabilities.data, { labelFor });
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
      // The metric page shows the calculation's progress; this says the save
      // itself worked.
      enqueueSnackbar(
        metric
          ? "Metric saved. Recalculation queued."
          : "Metric saved. First calculation queued.",
        { variant: "success" },
      );
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
  const hint = saveError
    ? null
    : problems[0] ||
      (metric
        ? `Saving creates version ${metric.current_version + 1} and queues recalculation of history. The active version stays available until the new one is ready.`
        : null);
  return (
    <Stack sx={{ height: "100%", minHeight: 0, bgcolor: "background.paper" }}>
      <BuilderTitleBar
        title={metric ? "Edit metric" : "New metric"}
        onBack={cancel}
      >
        {metric && (
          <Typography variant="body2" color="text.secondary" noWrap>
            {metric.name}
          </Typography>
        )}
        {dirty && (
          <StatusBadge
            status="draft"
            label="Unsaved changes"
            variant="dot"
            sx={{ ml: 0.5 }}
          />
        )}
      </BuilderTitleBar>
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: "minmax(480px, 1fr) minmax(400px, 520px)",
          flex: 1,
          minHeight: 0,
        }}
      >
        <Box
          sx={{
            minWidth: 0,
            minHeight: 0,
            overflow: "auto",
            borderRight: 1,
            borderColor: "divider",
          }}
        >
          <Stack gap={3} sx={{ px: 3, py: 2.5, maxWidth: 760, ...FIELD_SX }}>
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
                  The old definition is preserved read-only. Rebuild explicitly
                  as a new user-level version; it will not be converted
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
                    typography: "s1",
                  }}
                >
                  {JSON.stringify(previewDefinition(baseline), null, 2)}
                </Box>
              </Alert>
            )}
            <Stack component="section" aria-label="Details" gap={2}>
              <Typography component="h2" variant="subtitle1" fontWeight={600}>
                Details
              </Typography>
              <Box
                sx={{
                  display: "grid",
                  gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)",
                  gap: 2,
                }}
              >
                <TextField
                  label="Metric name"
                  size="small"
                  placeholder="e.g. Resolution share"
                  value={form.name}
                  disabled={disabled}
                  inputProps={{ maxLength: 160 }}
                  onChange={(event) =>
                    setForm({ ...form, name: event.target.value })
                  }
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
              </Box>
              {projects.isError && (
                <Alert
                  severity="warning"
                  action={
                    <Button onClick={() => projects.refetch()}>Retry</Button>
                  }
                >
                  Projects unavailable.
                </Alert>
              )}
              <TextField
                label="Description (optional)"
                size="small"
                placeholder="What this metric tells your team"
                value={form.description}
                disabled={disabled}
                inputProps={{ maxLength: 2000 }}
                onChange={(event) =>
                  setForm({ ...form, description: event.target.value })
                }
              />
            </Stack>
            <Divider />
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
              sourceLabels={sourceLabels}
              projectName={projectName}
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
            {(catalog.data?.result?.next_cursor ||
              evaluators.data?.truncated) && (
              <Stack direction="row" alignItems="center" gap={1.5}>
                {catalog.data?.result?.next_cursor && (
                  <Button
                    color="inherit"
                    variant="outlined"
                    size="small"
                    disabled={catalog.readState === "loading"}
                    onClick={() => setCursor(catalog.data.result.next_cursor)}
                  >
                    Load more fields
                  </Button>
                )}
                {evaluators.data?.truncated && (
                  <Typography variant="caption" color="text.secondary">
                    More saved evaluations are available. Search to narrow the
                    list.
                  </Typography>
                )}
              </Stack>
            )}
          </Stack>
        </Box>
        <Box sx={{ minWidth: 0, minHeight: 0, overflow: "auto" }}>
          <MetricPreviewPanel
            preview={preview}
            form={form}
            sourceLabels={sourceLabels}
            mode={metric ? "edit" : "new"}
            idleMessage={
              legacy && !rebuilding
                ? "Rebuild this metric to see a preview."
                : capabilities.data
                  ? undefined
                  : capabilities.readState === "loading"
                    ? "The preview starts once sources load."
                    : "Preview is unavailable until sources load."
            }
          />
        </Box>
      </Box>
      <Stack
        direction="row"
        alignItems="center"
        gap={1}
        sx={{
          px: 3,
          py: 1.25,
          flexShrink: 0,
          borderTop: 1,
          borderColor: "divider",
        }}
      >
        <Box sx={{ flex: 1, minWidth: 0 }}>
          {saveError && (
            <Alert severity="error" sx={{ py: 0 }}>
              {saveError}
            </Alert>
          )}
          {hint && (
            <Typography
              variant="body2"
              color="text.secondary"
              role={problems.length ? "status" : undefined}
            >
              {hint}
            </Typography>
          )}
        </Box>
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
      <ClosableDialog
        open={Boolean(pendingNavigation)}
        onClose={() => setPendingNavigation(null)}
        maxWidth="xs"
        fullWidth
        aria-labelledby="discard-metric-title"
      >
        <DialogTitle id="discard-metric-title">
          Discard unsaved changes?
        </DialogTitle>
        <DialogContent>
          <Typography variant="body2" color="text.secondary">
            {metric
              ? "Your changes are lost. The saved metric does not change."
              : "This metric has not been saved. Your changes are lost."}
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button
            color="inherit"
            variant="outlined"
            size="small"
            onClick={() => setPendingNavigation(null)}
          >
            Keep editing
          </Button>
          <Button
            color="inherit"
            variant="contained"
            size="small"
            onClick={() => {
              clearMetricDraft(recoveryKey);
              pendingNavigation();
              setPendingNavigation(null);
            }}
          >
            Discard changes
          </Button>
        </DialogActions>
      </ClosableDialog>
    </Stack>
  );
}
BuilderForm.propTypes = {
  scope: PropTypes.object.isRequired,
  metric: PropTypes.object,
  projectId: PropTypes.string,
  canWrite: PropTypes.bool,
};
