import PropTypes from "prop-types";
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  Box,
  Breadcrumbs,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Link,
  ListItemIcon,
  Menu,
  MenuItem,
  Stack,
  Tab,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from "@mui/material";
import { visuallyHidden } from "@mui/utils";
import Iconify from "src/components/iconify";
import PageHeader from "src/components/page-header";
import StatusBadge from "src/components/status-badge";
import { useOrganization } from "src/contexts/OrganizationContext";
import { useWorkspace } from "src/contexts/WorkspaceContext";
import { useParams, useRouter } from "src/routes/hooks";
import { paths } from "src/routes/paths";
import { ROLES } from "src/utils/rolePermissionMapping";
import { SegmentedTabs } from "src/components/tabs/tabs";
import { metricKeys, useMetricDetail, useRefreshMetric } from "./api";
import { localTime, localTimeZone, metricWindow } from "./metricTime";
import MetricStatusLine, { MetricReadNotice } from "./MetricStatusLine";
import { metricReadMessage } from "./metricPageValues";
import MetricOverview from "./MetricOverview";
import MetricDrivers from "./MetricDrivers";
import MetricHistory from "./MetricHistory";
import MetricDefinition from "./MetricDefinition";
import SaveSnapshot from "./SaveSnapshot";
import { definitionSummary } from "./metricPresentation";
import {
  metricProjectLabel,
  useMetricProjectNames,
} from "./useMetricProjectNames";

const tabs = ["Overview", "Drivers", "History", "Definition"];
const WINDOWS = [
  [1, "24h", "Last 24 hours"],
  [7, "7 days", "Last 7 days"],
  [30, "30 days", "Last 30 days"],
];
// Reads whose query key carries the window (as in api.js).
const WINDOW_READS = new Set(["kpi", "trend", "drivers", "unitValues"]);

export default function MetricDetailPage() {
  const { metricId } = useParams();
  const {
    currentOrganizationId,
    currentOrganizationRole,
    isReady: organizationReady,
  } = useOrganization();
  const {
    currentWorkspaceId,
    currentWorkspaceRole,
    isReady: workspaceReady,
  } = useWorkspace();
  // Like the list page, the header stays in place while the workspace loads.
  if (!organizationReady || !workspaceReady)
    return (
      <Box sx={{ p: 2 }}>
        <PageHeader title="Metric" />
        <Typography role="status" variant="body2" color="text.secondary">
          Loading workspace…
        </Typography>
      </Box>
    );
  if (!currentOrganizationId || !currentWorkspaceId)
    return (
      <Alert severity="info">Select a workspace to view this metric.</Alert>
    );
  return (
    <WorkspaceMetricDetail
      key={`${currentOrganizationId}:${currentWorkspaceId}:${metricId}`}
      metricId={metricId}
      scope={{
        organizationId: currentOrganizationId,
        workspaceId: currentWorkspaceId,
      }}
      canWrite={
        [ROLES.OWNER, ROLES.ADMIN].includes(currentOrganizationRole) ||
        [ROLES.WORKSPACE_ADMIN, ROLES.WORKSPACE_MEMBER].includes(
          currentWorkspaceRole,
        )
      }
    />
  );
}

function WorkspaceMetricDetail({ scope, metricId, canWrite }) {
  const router = useRouter();
  const client = useQueryClient();
  // windowReads: false skips the reads keyed by the window. A preset Refresh
  // moves to a new window, so those reads start fresh under their new keys;
  // invalidating the old keys would only refetch a window being left.
  const reload = ({ windowReads = true } = {}) => {
    void client.invalidateQueries({
      queryKey: metricKeys.workspace(scope),
      predicate: ({ queryKey }) =>
        queryKey[5] === metricId &&
        (windowReads || !WINDOW_READS.has(queryKey[4])),
    });
  };
  const detail = useMetricDetail({ scope, metricId });
  const metric = detail.data;
  const refresh = useRefreshMetric({
    scope: { ...scope, projectId: metric?.project_id },
    metricId,
  });
  const [tab, setTab] = useState("Overview");
  const [window, setWindow] = useState(() => metricWindow(7));
  const [range, setRange] = useState(7);
  const [custom, setCustom] = useState(false);
  const [menu, setMenu] = useState(null);
  const [readRevision, setReadRevision] = useState(0);
  // Keep only the identity across masked reads, never an unverified definition
  // or value. A background refetch must not reset the current tab's controls.
  const [definitionId, setDefinitionId] = useState(null);
  if (metric && definitionId !== (metric.definition?.id ?? null)) {
    setDefinitionId(metric.definition?.id ?? null);
  }
  const userMetric =
    metric?.definition?.level === "user" ||
    (!metric?.definition && metric?.draft_definition?.level === "user");
  const scopeWithProject = { ...scope, projectId: metric?.project_id };
  const readKey = `${definitionId}:${window.window_start}:${window.window_end}:${readRevision}`;
  const summary = definitionSummary(
    metric?.definition || metric?.draft_definition,
  );
  // The same project read the Metrics list uses; no extra endpoint.
  const projectNames = useMetricProjectNames(
    scope,
    metric?.project_id ? [metric.project_id] : [],
  );
  const statusBadge = !metric ? null : metric.definition ? (
    <StatusBadge
      status="active"
      label={`Active · v${metric.definition.version}`}
      size="sm"
    />
  ) : (
    <StatusBadge
      status="draft"
      label={
        metric.draft_definition?.version != null
          ? `Draft v${metric.draft_definition.version}`
          : "Draft"
      }
      variant="dot"
    />
  );
  const projectLabel =
    metric?.project_id && metricProjectLabel(projectNames, metric.project_id);
  // Project first, then status, as the Tasks header shows them.
  const badge = metric ? (
    <>
      {projectLabel && (
        <Chip
          size="small"
          variant="outlined"
          title={projectLabel}
          icon={<Iconify icon="solar:folder-linear" width={14} />}
          label={
            <>
              <Box component="span" sx={visuallyHidden}>
                Project:{" "}
              </Box>
              {projectLabel}
            </>
          }
          sx={{
            maxWidth: 280,
            borderColor: "divider",
            "& .MuiChip-icon": { ml: 0.5 },
          }}
        />
      )}
      {statusBadge}
    </>
  ) : null;
  const windowLabel =
    range === "custom"
      ? "Custom window"
      : WINDOWS.find(([days]) => days === range)?.[2];
  // Like the list page's Refresh: a preset re-anchors to a fresh window ending
  // now. A custom window is explicit, so it is read again unchanged.
  const reanchor = () => {
    if (range !== "custom") setWindow(metricWindow(range));
  };
  const refreshValues = () => {
    setReadRevision((value) => value + 1);
    if (range === "custom") reload();
    else {
      reanchor();
      reload({ windowReads: false });
    }
  };
  return (
    <Stack sx={{ p: 2, minHeight: "100%", bgcolor: "background.paper" }}>
      <Breadcrumbs
        aria-label="Breadcrumb"
        sx={{ typography: "s1", "& .MuiBreadcrumbs-separator": { mx: 0.75 } }}
      >
        <Link
          color="text.secondary"
          href={paths.dashboard.metrics.root}
          underline="hover"
        >
          Metrics
        </Link>
        <Typography variant="body2" color="text.primary" noWrap>
          {metric?.name || "Metric"}
        </Typography>
      </Breadcrumbs>
      <PageHeader
        title={metric?.name || "Metric"}
        badge={badge}
        description={metric?.description || summary || undefined}
        sx={{ mt: 1, mb: 0 }}
        actions={
          <>
            <SaveSnapshot
              metric={metric}
              scope={scopeWithProject}
              window={window}
              canWrite={canWrite}
              onSaved={() => {
                setTab("History");
                setReadRevision((value) => value + 1);
                reload();
              }}
            />
            <Tooltip
              title={
                canWrite
                  ? ""
                  : "A workspace member role is required to edit metrics."
              }
            >
              <span>
                <Button
                  variant="outlined"
                  color="inherit"
                  size="small"
                  startIcon={<Iconify icon="solar:pen-linear" width={16} />}
                  disabled={!canWrite || !metric}
                  onClick={() =>
                    router.push(paths.dashboard.metrics.edit(metricId))
                  }
                >
                  Edit
                </Button>
              </span>
            </Tooltip>
            <Tooltip
              title={
                userMetric
                  ? "User-level metrics are not available yet."
                  : canWrite
                    ? "Queue a recalculation. Existing values change only after it commits."
                    : "A workspace member role is required to run metrics."
              }
            >
              <span>
                <Button
                  variant="outlined"
                  color="inherit"
                  size="small"
                  startIcon={<Iconify icon="solar:play-linear" width={16} />}
                  disabled={
                    !canWrite || !metric || userMetric || refresh.isPending
                  }
                  onClick={() => refresh.mutate({ confirm: true })}
                >
                  {refresh.isPending ? "Queuing…" : "Run now"}
                </Button>
              </span>
            </Tooltip>
            <Button
              variant="outlined"
              color="inherit"
              size="small"
              aria-label="More"
              aria-haspopup="menu"
              aria-expanded={Boolean(menu)}
              aria-controls={menu ? "metric-more-menu" : undefined}
              disabled={!metric}
              onClick={(event) => setMenu(event.currentTarget)}
              sx={{ minWidth: 0, px: 0.75 }}
            >
              <Iconify icon="solar:menu-dots-bold" width={16} />
            </Button>
            <Menu
              id="metric-more-menu"
              anchorEl={menu}
              open={Boolean(menu)}
              onClose={() => setMenu(null)}
            >
              <Typography
                variant="caption"
                component="li"
                sx={{
                  display: "block",
                  px: 1.5,
                  py: 1,
                  color: "text.secondary",
                }}
              >
                These actions are not available yet.
              </Typography>
              {[
                ["Add to dashboard", "solar:widget-add-linear"],
                ["Create alert", "solar:bell-linear"],
                ["Duplicate", "solar:copy-linear"],
              ].map(([action, icon]) => (
                <MenuItem key={action} disabled>
                  <ListItemIcon>
                    <Iconify icon={icon} width={16} />
                  </ListItemIcon>
                  {action}
                </MenuItem>
              ))}
            </Menu>
          </>
        }
      />
      <Stack spacing={1.5} sx={{ mt: 1.5 }}>
        {refresh.isError && (
          <Alert severity="warning">
            {metricReadMessage(refresh.error, "Refresh request")}
          </Alert>
        )}
        {refresh.isSuccess && (
          <Alert
            severity="info"
            action={
              <Button
                color="inherit"
                size="small"
                onClick={() => {
                  setReadRevision((value) => value + 1);
                  reload();
                }}
              >
                Check for updates
              </Button>
            }
          >
            {refresh.data?.status === "already_queued"
              ? "A recalculation is already queued."
              : "Recalculation queued."}{" "}
            Values change only after a successful commit.
          </Alert>
        )}
        <MetricReadNotice query={detail} label="Metric" />
        {metric && userMetric && (
          <Alert severity="info">
            User-level metrics are not available yet.
          </Alert>
        )}
        {metric?.definition &&
          metric.draft_definition &&
          metric.draft_definition.id !== metric.definition.id && (
            <Stack
              spacing={1}
              sx={{
                p: 1.5,
                border: 1,
                borderColor: "divider",
                borderRadius: 1,
              }}
            >
              <Typography variant="body2" color="text.secondary">
                Draft v{metric.draft_definition.version} is not active. Values
                below still use v{metric.definition.version}.
              </Typography>
              <MetricStatusLine
                definition={metric.draft_definition}
                showFreshness={false}
              />
            </Stack>
          )}
      </Stack>
      {metric && (
        <Stack
          direction="row"
          alignItems="center"
          justifyContent="space-between"
          spacing={2}
          sx={{ mt: 2 }}
        >
          <SegmentedTabs
            value={tab}
            onChange={(_, value) => setTab(value)}
            aria-label="Metric views"
          >
            {tabs.map((label) => (
              <Tab
                key={label}
                value={label}
                label={label}
                id={`metric-tab-${label}`}
                aria-controls={`metric-panel-${label}`}
              />
            ))}
          </SegmentedTabs>
          {["Overview", "Drivers"].includes(tab) && metric.definition && (
            <Stack
              direction="row"
              spacing={1}
              alignItems="center"
              aria-label="Metric date range"
            >
              <ToggleButtonGroup
                exclusive
                size="small"
                value={range}
                aria-label="Window"
                onChange={(_, value) => {
                  // An exclusive group reports a click on the active preset as
                  // null; it still means "this range, ending now".
                  if (value == null) reanchor();
                  else if (value !== "custom") {
                    setRange(value);
                    setWindow(metricWindow(value));
                  }
                }}
              >
                {WINDOWS.map(([days, label]) => (
                  <ToggleButton key={days} value={days}>
                    {label}
                  </ToggleButton>
                ))}
                <ToggleButton
                  value="custom"
                  onClick={(event) => {
                    event.preventDefault();
                    setCustom(true);
                  }}
                >
                  Custom
                </ToggleButton>
              </ToggleButtonGroup>
              <Tooltip
                title={
                  range === "custom"
                    ? "Read values again for the custom window"
                    : "Read values again for a fresh window ending now"
                }
              >
                {/* Labelled like the list page's Refresh, which it matches. */}
                <Button
                  variant="outlined"
                  color="inherit"
                  size="small"
                  aria-label="Refresh values"
                  startIcon={<Iconify icon="solar:refresh-linear" width={16} />}
                  onClick={refreshValues}
                >
                  Refresh
                </Button>
              </Tooltip>
            </Stack>
          )}
        </Stack>
      )}
      {/* Focusable because the Definition panel has no control of its own;
          the ring matches the list and value cells so the stop is visible. */}
      <Box
        role="tabpanel"
        id={`metric-panel-${tab}`}
        aria-labelledby={`metric-tab-${tab}`}
        tabIndex={0}
        hidden={!metric}
        sx={{
          mt: 2.5,
          minWidth: 0,
          borderRadius: 0.5,
          "&:focus-visible": {
            outline: "2px solid",
            outlineColor: "text.primary",
            outlineOffset: 4,
          },
        }}
      >
        {tab === "Overview" && (
          <MetricOverview
            key={readKey}
            metric={metric}
            scope={scopeWithProject}
            window={window}
            windowLabel={windowLabel}
          />
        )}
        {tab === "Drivers" && (
          <MetricDrivers
            key={readKey}
            metric={metric}
            scope={scopeWithProject}
            window={window}
          />
        )}
        {tab === "History" && (
          <MetricHistory
            canWrite={canWrite}
            key={readKey}
            metric={metric}
            scope={scopeWithProject}
          />
        )}
        {tab === "Definition" && metric && (
          <MetricDefinition
            metric={metric}
            onEditDraft={
              canWrite
                ? () => router.push(paths.dashboard.metrics.edit(metricId))
                : undefined
            }
          />
        )}
      </Box>
      {custom && (
        <CustomWindow
          window={window}
          onClose={() => setCustom(false)}
          onApply={(value) => {
            setRange("custom");
            setWindow(value);
            setCustom(false);
          }}
        />
      )}
    </Stack>
  );
}
WorkspaceMetricDetail.propTypes = {
  scope: PropTypes.object.isRequired,
  metricId: PropTypes.string.isRequired,
  canWrite: PropTypes.bool.isRequired,
};

function CustomWindow({ window, onClose, onApply }) {
  const [start, setStart] = useState(localTime(window.window_start));
  const [end, setEnd] = useState(localTime(window.window_end));
  const [error, setError] = useState("");
  const apply = () => {
    const from = Date.parse(start);
    const to = Date.parse(end);
    if (!Number.isFinite(from) || !Number.isFinite(to) || from >= to)
      return setError("Choose an end after the start.");
    if (to > Date.now()) return setError("Choose a window ending in the past.");
    if (to - from > 90 * 86400000)
      return setError("Choose a window of 90 days or less.");
    if (Math.floor(to / 900000) <= Math.floor(from / 900000))
      return setError("Include at least one complete 15-minute bucket.");
    onApply({
      window_start: new Date(from).toISOString(),
      window_end: new Date(to).toISOString(),
    });
  };
  return (
    <Dialog
      open
      onClose={onClose}
      fullWidth
      maxWidth="sm"
      aria-labelledby="metric-window-title"
    >
      <DialogTitle id="metric-window-title">Custom window</DialogTitle>
      <DialogContent>
        <Stack spacing={4} sx={{ pt: 2 }}>
          <Typography variant="body2" color="text.secondary">
            Enter times in {localTimeZone()}. Results use complete 15-minute UTC
            buckets and show the effective window.
          </Typography>
          <TextField
            label="Window start"
            type="datetime-local"
            value={start}
            onChange={(event) => setStart(event.target.value)}
            InputLabelProps={{ shrink: true }}
          />
          <TextField
            label="Window end"
            type="datetime-local"
            value={end}
            onChange={(event) => setEnd(event.target.value)}
            InputLabelProps={{ shrink: true }}
          />
          {error && <Alert severity="error">{error}</Alert>}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button color="inherit" onClick={onClose}>
          Cancel
        </Button>
        <Button color="inherit" variant="contained" onClick={apply}>
          Apply window
        </Button>
      </DialogActions>
    </Dialog>
  );
}
CustomWindow.propTypes = {
  window: PropTypes.object.isRequired,
  onClose: PropTypes.func.isRequired,
  onApply: PropTypes.func.isRequired,
};
