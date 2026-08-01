import { useState } from "react";
import PropTypes from "prop-types";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import {
  Alert,
  Box,
  Button,
  Card,
  CircularProgress,
  Grid,
  IconButton,
  Menu,
  MenuItem,
  Typography,
  useTheme,
} from "@mui/material";
import { LoadingButton } from "@mui/lab";
import Iconify from "src/components/iconify";
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
  const { mutate: syncNow, isPending: isSyncing } = useSyncNow();
  const { mutate: pause, isPending: isPausing } = usePauseConnection();
  const { mutate: resume, isPending: isResuming } = useResumeConnection();

  const [editOpen, setEditOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [menuAnchor, setMenuAnchor] = useState(null);

  if (isLoading) {
    return (
      <Box display="flex" justifyContent="center" py={8}>
        <CircularProgress />
      </Box>
    );
  }

  if (isError) {
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
              <Typography sx={{ typography: "s1", color: "text.secondary" }}>
                {connData.host_url || "—"}
              </Typography>
            </Box>
          </Box>
          <Box display="flex" alignItems="center" gap={theme.spacing(1)}>
            <StatusBadge status={connData.status} />
            <IconButton
              aria-label="Integration options menu"
              onClick={(e) => setMenuAnchor(e.currentTarget)}
            >
              <Iconify icon="solar:menu-dots-bold" />
            </IconButton>
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
          <InfoRow label="Platform" value={connData.platform || "Unknown"} />
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
        </Grid>

        {connData.backfill_completed === false && (
          <Alert severity="info" sx={{ mb: theme.spacing(2) }}>
            Backfill in progress
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

        <Box display="flex" gap={theme.spacing(2)}>
          <LoadingButton
            variant="outlined"
            size="small"
            loading={isSyncing}
            onClick={() => syncNow(connectionId)}
            disabled={
              connData.status === "syncing" || connData.status === "backfilling"
            }
            startIcon={<Iconify icon="solar:refresh-bold" width={16} />}
            sx={outlinedNeutralButtonSx}
          >
            Sync Now
          </LoadingButton>
          {connData.status === "active" && (
            <LoadingButton
              variant="outlined"
              size="small"
              loading={isPausing}
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
          )}
          {connData.status === "paused" && (
            <LoadingButton
              variant="contained"
              color="primary"
              size="small"
              loading={isResuming}
              onClick={() => resume(connectionId)}
              startIcon={<Iconify icon="solar:play-bold" width={16} />}
              sx={{ fontWeight: 500 }}
            >
              Resume
            </LoadingButton>
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
        <IntegrationSyncHistory connectionId={connectionId} />
      </Card>

      {/* Dialogs */}
      <EditIntegrationDialog
        open={editOpen}
        onClose={() => setEditOpen(false)}
        connection={connData}
      />
      <DeleteIntegrationDialog
        open={deleteOpen}
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
