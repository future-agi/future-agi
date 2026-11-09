import React, { useState } from "react";
import PropTypes from "prop-types";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import {
  Alert,
  Box,
  Button,
  Card,
  Grid,
  IconButton,
  Menu,
  MenuItem,
  Tooltip,
  Typography,
  useTheme,
} from "@mui/material";
import { LoadingButton } from "@mui/lab";
import Iconify from "src/components/iconify";
import { LoadingScreen } from "src/components/loading-screen";
import { paths } from "src/routes/paths";
import { fDateTime, fToNow } from "src/utils/format-time";
import {
  useIntegrationConnection,
  useSyncNow,
  usePauseConnection,
  useResumeConnection,
} from "src/api/integrations";
import PlatformLogo from "./PlatformLogo";
import StatusBadge from "./StatusBadge";
import IntegrationSyncHistory from "./IntegrationSyncHistory";
import EditIntegrationDialog from "./EditIntegrationDialog";
import DeleteIntegrationDialog from "./DeleteIntegrationDialog";
import { backButtonSx, outlinedNeutralButtonSx } from "./styles";
import { useCanManageIntegrations } from "./access";
import IntegrationAdminOnly from "./IntegrationAdminOnly";
import { EXPORT_PLATFORMS, platformName } from "./constants";
import { connectionDestination } from "./utils";

function InfoRow({ label, value }) {
  return (
    <Grid item xs={12} sm={6}>
      <Typography sx={{ typography: "s2", color: "text.disabled" }}>
        {label}
      </Typography>
      <Typography sx={{ typography: "s1", color: "text.primary" }}>
        {value || "—"}
      </Typography>
    </Grid>
  );
}

InfoRow.propTypes = {
  label: PropTypes.string.isRequired,
  value: PropTypes.node,
};

function formatInterval(seconds) {
  if (!seconds) return "—";
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} min`;
  return `${Math.round(seconds / 3600)} hr`;
}

export default function IntegrationDetail() {
  const theme = useTheme();
  const { connectionId, workspaceId } = useParams();
  const canManage = useCanManageIntegrations();
  const navigate = useNavigate();
  const location = useLocation();
  const integrationsPath =
    workspaceId &&
    location.pathname.includes(
      `/settings/workspace/${workspaceId}/integrations`,
    )
      ? paths.dashboard.settings.workspaceIntegrations(workspaceId)
      : paths.dashboard.settings.integrations;
  const {
    data: connection,
    isLoading,
    isError,
    error,
  } = useIntegrationConnection(connectionId);
  const {
    mutate: syncNow,
    isPending: isSyncing,
    completionUnconfirmed,
    checkStatus,
  } = useSyncNow(connectionId);
  const { mutate: pause, isPending: isPausing } = usePauseConnection();
  const { mutate: resume, isPending: isResuming } = useResumeConnection();

  const [editOpen, setEditOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [menuAnchor, setMenuAnchor] = useState(null);

  if (isLoading) {
    return <LoadingScreen sx={{ height: "100%", minHeight: "60vh" }} />;
  }

  if (isError && !(connection && (isSyncing || completionUnconfirmed))) {
    return (
      <Box py={theme.spacing(4)}>
        <Alert severity="error">
          Failed to load integration: {error?.message || "Unknown error"}
        </Alert>
        <Button
          size="small"
          variant="outlined"
          startIcon={<Iconify icon="formkit:left" width={16} height={16} />}
          onClick={() => navigate(integrationsPath)}
          sx={{ mt: theme.spacing(1), ...backButtonSx }}
        >
          Back to Integrations
        </Button>
      </Box>
    );
  }

  if (!connection) {
    return (
      <Box py={theme.spacing(4)}>
        <Alert severity="warning">Integration connection not found</Alert>
        <Button
          size="small"
          variant="outlined"
          startIcon={<Iconify icon="formkit:left" width={16} height={16} />}
          onClick={() => navigate(integrationsPath)}
          sx={{ mt: theme.spacing(1), ...backButtonSx }}
        >
          Back to Integrations
        </Button>
      </Box>
    );
  }

  const connData = connection;
  const isExport = EXPORT_PLATFORMS.includes(connData.platform);
  // In Error, Sync Now is Retry; while a sync runs or the connection is
  // paused it waits, and says why.
  const inError = connData.status === "error";
  const syncRunning =
    connData.status === "syncing" || connData.status === "backfilling";
  const syncWaits = completionUnconfirmed
    ? ""
    : syncRunning
      ? "A sync is running."
      : connData.status === "paused"
        ? "Resume the connection to sync it."
        : "";

  return (
    <Box>
      {/* Back button */}
      <Button
        size="small"
        variant="outlined"
        startIcon={<Iconify icon="formkit:left" width={16} height={16} />}
        onClick={() => navigate(integrationsPath)}
        sx={{ mb: theme.spacing(2), ...backButtonSx }}
      >
        Back to Integrations
      </Button>

      {/* Overview Card */}
      <Card sx={{ mb: theme.spacing(3), p: theme.spacing(3) }}>
        <Box
          display="flex"
          justifyContent="space-between"
          alignItems="center"
          mb={theme.spacing(2)}
        >
          <Box display="flex" alignItems="center" gap={theme.spacing(2)}>
            <PlatformLogo platform={connData.platform} />
            <Box>
              <Typography
                sx={{
                  typography: "m2",
                  fontWeight: "fontWeightSemiBold",
                  color: "text.primary",
                }}
              >
                {connData.display_name ||
                  connData.external_project_name ||
                  "Unnamed"}
              </Typography>
              <Typography
                data-testid="integration-header-destination"
                sx={{ typography: "s1", color: "text.secondary" }}
              >
                {connectionDestination(connData) || "—"}
              </Typography>
            </Box>
          </Box>
          <Box display="flex" alignItems="center" gap={theme.spacing(1)}>
            <StatusBadge status={connData.status} isExport={isExport} />
            <IntegrationAdminOnly allowed={canManage}>
              <IconButton
                aria-label="Integration options menu"
                disabled={!canManage}
                onClick={(e) => setMenuAnchor(e.currentTarget)}
              >
                <Iconify icon="solar:menu-dots-bold" />
              </IconButton>
            </IntegrationAdminOnly>
            <Menu
              anchorEl={menuAnchor}
              open={Boolean(menuAnchor)}
              onClose={() => setMenuAnchor(null)}
            >
              <MenuItem
                onClick={() => {
                  setMenuAnchor(null);
                  setEditOpen(true);
                }}
              >
                Edit
              </MenuItem>
              <MenuItem
                onClick={() => {
                  setMenuAnchor(null);
                  setDeleteOpen(true);
                }}
                sx={{ color: "error.main" }}
              >
                Delete
              </MenuItem>
            </Menu>
          </Box>
        </Box>

        {connData.status === "error" && connData.status_message && (
          <Alert severity="error" sx={{ mb: theme.spacing(2) }}>
            {connData.status_message}
          </Alert>
        )}

        <Grid container spacing={2}>
          <InfoRow
            label="Platform"
            value={connData.platform ? platformName(connData.platform) : null}
          />
          {/* An export has no Langfuse project, FutureAGI project or key pair. */}
          {!isExport && (
            <>
              <InfoRow
                label="Langfuse Project"
                value={connData.external_project_name}
              />
              <InfoRow
                label="FutureAGI Project"
                value={connData.project_name || connData.project?.name}
              />
              <InfoRow label="Public Key" value={connData.public_key_display} />
              <InfoRow label="Secret Key" value={connData.secret_key_display} />
            </>
          )}
          <InfoRow
            label="Created"
            value={connData.created_at ? fDateTime(connData.created_at) : null}
          />
          <InfoRow
            label="Sync Interval"
            value={formatInterval(connData.sync_interval_seconds)}
          />
        </Grid>
      </Card>

      {/* Sync Status Card */}
      <Card sx={{ mb: theme.spacing(3), p: theme.spacing(3) }}>
        <Typography
          sx={{
            typography: "m3",
            fontWeight: "fontWeightSemiBold",
            color: "text.primary",
            mb: theme.spacing(2),
          }}
        >
          Sync Status
        </Typography>
        <Grid container spacing={2} mb={theme.spacing(2)}>
          <InfoRow
            label="Last Synced"
            value={
              connData.last_synced_at
                ? fToNow(connData.last_synced_at)
                : "Never"
            }
          />
          {isExport && (
            <InfoRow
              label="Request Log Deliveries (Including Repeats)"
              value={(connData.total_traces_synced || 0).toLocaleString()}
            />
          )}
          {!isExport && (
            <>
              <InfoRow
                label="Total Traces"
                value={connData.total_traces_synced?.toLocaleString() || "0"}
              />
              <InfoRow
                label="Total Spans"
                value={connData.total_spans_synced?.toLocaleString() || "0"}
              />
              <InfoRow
                label="Total Scores"
                value={connData.total_scores_synced?.toLocaleString() || "0"}
              />
            </>
          )}
        </Grid>

        {connData.backfill_completed === false && (
          <Alert severity="info" sx={{ mb: theme.spacing(2) }}>
            {isExport ? "First export in progress" : "Backfill in progress"}
            {connData.backfill_progress?.total_windows > 0
              ? `: ${Math.min(
                  100,
                  Math.max(
                    0,
                    Math.round(
                      (connData.backfill_progress.completed_windows /
                        connData.backfill_progress.total_windows) *
                        100,
                    ),
                  ),
                )}% complete`
              : "..."}
          </Alert>
        )}

        {completionUnconfirmed && (
          <Alert severity="warning" sx={{ mb: theme.spacing(2) }}>
            Sync completion could not be confirmed. Check sync status to refresh
            its history without starting another sync.
          </Alert>
        )}

        <Box display="flex" gap={theme.spacing(2)}>
          <Tooltip title={syncWaits}>
            <Box component="span" display="inline-flex">
              <LoadingButton
                variant="outlined"
                size="small"
                loading={isSyncing}
                onClick={() =>
                  completionUnconfirmed
                    ? checkStatus(connectionId)
                    : syncNow(connectionId)
                }
                disabled={Boolean(syncWaits)}
                startIcon={<Iconify icon="solar:refresh-bold" width={16} />}
                sx={outlinedNeutralButtonSx}
              >
                {completionUnconfirmed
                  ? "Check sync status"
                  : inError
                    ? "Retry"
                    : "Sync Now"}
              </LoadingButton>
            </Box>
          </Tooltip>
          {(connData.status === "active" || inError) && (
            <IntegrationAdminOnly allowed={canManage}>
              <LoadingButton
                variant="outlined"
                size="small"
                loading={isPausing}
                disabled={!canManage}
                onClick={() => pause(connectionId)}
                startIcon={<Iconify icon="mdi:pause" width={16} />}
                sx={{
                  ...outlinedNeutralButtonSx,
                  color: "text.primary",
                  "&:hover": {
                    borderColor: "text.disabled",
                    color: "text.primary",
                  },
                }}
              >
                Pause
              </LoadingButton>
            </IntegrationAdminOnly>
          )}
          {connData.status === "paused" && (
            <IntegrationAdminOnly allowed={canManage}>
              <LoadingButton
                variant="contained"
                color="primary"
                size="small"
                loading={isResuming}
                disabled={!canManage}
                onClick={() => resume(connectionId)}
                startIcon={<Iconify icon="solar:play-bold" width={16} />}
                sx={{ fontWeight: 500 }}
              >
                Resume
              </LoadingButton>
            </IntegrationAdminOnly>
          )}
        </Box>
      </Card>

      {/* Sync History */}
      <Card sx={{ p: theme.spacing(3) }}>
        <Typography
          sx={{
            typography: "m3",
            fontWeight: "fontWeightSemiBold",
            color: "text.primary",
            mb: theme.spacing(2),
          }}
        >
          Sync History
        </Typography>
        <IntegrationSyncHistory
          connectionId={connectionId}
          isExport={isExport}
        />
      </Card>

      {/* Dialogs */}
      <EditIntegrationDialog
        open={canManage && editOpen}
        onClose={() => setEditOpen(false)}
        connection={connData}
      />
      <DeleteIntegrationDialog
        open={canManage && deleteOpen}
        onClose={() => setDeleteOpen(false)}
        onDeleted={() => {
          setDeleteOpen(false);
          navigate(integrationsPath);
        }}
        connection={connData}
      />
    </Box>
  );
}
