import PropTypes from "prop-types";
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  Box,
  Breadcrumbs,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Link,
  Menu,
  MenuItem,
  Stack,
  Tab,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
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

const tabs = ["Overview", "Drivers", "History", "Definition"];

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
  if (!organizationReady || !workspaceReady)
    return (
      <Typography role="status" sx={{ p: 4 }}>
        Loading workspace…
      </Typography>
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
  const reload = () => {
    void client.invalidateQueries({
      queryKey: metricKeys.workspace(scope),
      predicate: ({ queryKey }) => queryKey[5] === metricId,
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
  const userMetric =
    metric?.definition?.level === "user" ||
    (!metric?.definition && metric?.draft_definition?.level === "user");
  const scopeWithProject = { ...scope, projectId: metric?.project_id };
  const readKey = `${metric?.definition?.id}:${window.window_start}:${window.window_end}:${readRevision}`;
  return (
    <Stack
      spacing={2}
      sx={{ p: 2, minHeight: "100%", bgcolor: "background.paper" }}
    >
      <Breadcrumbs aria-label="Breadcrumb">
        <Link
          color="inherit"
          href={paths.dashboard.metrics.root}
          underline="hover"
        >
          Metrics
        </Link>
        <Typography variant="body2" color="text.primary">
          {metric?.name || "Metric"}
        </Typography>
      </Breadcrumbs>
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="space-between"
        spacing={4}
      >
        <Box sx={{ minWidth: 0 }}>
          <Typography
            component="h1"
            variant="h6"
            fontWeight={600}
            sx={{ overflowWrap: "anywhere" }}
          >
            {metric?.name || "Metric"}
          </Typography>
          {metric?.description && (
            <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
              {metric.description}
            </Typography>
          )}
        </Box>
        <Stack direction="row" spacing={2} sx={{ flexShrink: 0 }}>
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
            color="inherit"
            variant="outlined"
            aria-haspopup="menu"
            aria-expanded={Boolean(menu)}
            aria-controls={menu ? "metric-more-menu" : undefined}
            disabled={!metric}
            onClick={(event) => setMenu(event.currentTarget)}
          >
            More
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
              sx={{ px: 3, py: 2, color: "text.secondary" }}
            >
              These actions are not available yet.
            </Typography>
            {["Add to dashboard", "Create alert", "Duplicate"].map((action) => (
              <MenuItem key={action} disabled>
                {action}
              </MenuItem>
            ))}
          </Menu>
        </Stack>
      </Stack>
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
      {metric && (
        <>
          {userMetric && (
            <Alert severity="info">
              User-level metrics are not available yet.
            </Alert>
          )}
          {metric.definition &&
            metric.draft_definition &&
            metric.draft_definition.id !== metric.definition.id && (
              <Stack
                spacing={1.5}
                sx={{ p: 2, bgcolor: "background.neutral", borderRadius: 1 }}
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
          {["Overview", "Drivers"].includes(tab) && (
            <Stack
              direction="row"
              spacing={1.5}
              alignItems="center"
              aria-label="Metric date range"
            >
              <Typography variant="body2" color="text.secondary" sx={{ mr: 2 }}>
                Window
              </Typography>
              {[
                [1, "24h"],
                [7, "7 days"],
                [30, "30 days"],
              ].map(([days, label]) => (
                <Button
                  key={days}
                  color="inherit"
                  variant={range === days ? "outlined" : "text"}
                  aria-pressed={range === days}
                  size="small"
                  onClick={() => {
                    setRange(days);
                    setWindow(metricWindow(days));
                  }}
                >
                  {label}
                </Button>
              ))}
              <Button
                color="inherit"
                size="small"
                variant={range === "custom" ? "outlined" : "text"}
                aria-pressed={range === "custom"}
                onClick={() => setCustom(true)}
              >
                Custom
              </Button>
              <Button
                color="inherit"
                size="small"
                onClick={() => {
                  setReadRevision((value) => value + 1);
                  reload();
                }}
              >
                Refresh values
              </Button>
            </Stack>
          )}
          <Box
            role="tabpanel"
            id={`metric-panel-${tab}`}
            aria-labelledby={`metric-tab-${tab}`}
            tabIndex={0}
            sx={{ pt: 3, minWidth: 0 }}
          >
            {tab === "Overview" && (
              <MetricOverview
                key={readKey}
                metric={metric}
                scope={scopeWithProject}
                window={window}
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
                key={readKey}
                metric={metric}
                scope={scopeWithProject}
              />
            )}
            {tab === "Definition" && <MetricDefinition metric={metric} />}
          </Box>
        </>
      )}
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
