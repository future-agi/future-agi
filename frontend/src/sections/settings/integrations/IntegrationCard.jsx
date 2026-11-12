import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Typography,
  useTheme,
} from "@mui/material";
import Section from "src/components/section";
import { useLocation, useNavigate } from "react-router-dom";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { fToNow } from "src/utils/format-time";
import { fDateTime } from "src/utils/format-time";
import {
  useDeleteConnection,
  useStartSlackInstall,
} from "src/api/integrations";
import PlatformLogo from "./PlatformLogo";
import StatusBadge from "./StatusBadge";
import RunStatusBadge from "src/components/status-badge/StatusBadge";
import { EXPORT_PLATFORMS, SKIP_SYNC_SETTINGS_PLATFORMS } from "./constants";
import { connectionDestination, getErrorMessage } from "./utils";
import { useCanManageIntegrations } from "./access";
import IntegrationAdminOnly from "./IntegrationAdminOnly";

export default function IntegrationCard({ connection }) {
  const theme = useTheme();
  const navigate = useNavigate();
  const location = useLocation();
  const canManage = useCanManageIntegrations();
  const isActionOnly = SKIP_SYNC_SETTINGS_PLATFORMS.includes(
    connection.platform,
  );
  const isExport = EXPORT_PLATFORMS.includes(connection.platform);
  const workspaceIntegrationsMatch = location.pathname.match(
    /^\/dashboard\/settings\/workspace\/([^/]+)\/integrations\/?$/,
  );
  const displayName =
    connection.display_name ||
    connection.displayName ||
    connection.external_project_name ||
    connection.externalProjectName ||
    "Unnamed";
  const destination = connectionDestination(connection);
  const createdAt = connection.created_at || connection.createdAt;
  const totalTracesSynced =
    connection.total_traces_synced ?? connection.totalTracesSynced;
  const lastSyncedAt = connection.last_synced_at || connection.lastSyncedAt;
  // A Langfuse import in Error shows its reason on its card too, not only on
  // its connection page.
  const stoppedReason =
    connection.platform === "langfuse" && connection.status === "error"
      ? connection.status_message || connection.statusMessage || ""
      : "";

  const [disconnectOpen, setDisconnectOpen] = useState(false);
  const [connectionError, setConnectionError] = useState("");
  const { mutate: deleteConnection, isPending: deleting } =
    useDeleteConnection();
  const startSlackInstall = useStartSlackInstall();

  const handleClick = () => {
    if (isActionOnly) {
      setDisconnectOpen(true);
    } else {
      navigate(
        workspaceIntegrationsMatch
          ? paths.dashboard.settings.workspaceIntegrationDetail(
              workspaceIntegrationsMatch[1],
              connection.id,
            )
          : paths.dashboard.settings.integrationDetail(connection.id),
      );
    }
  };

  const handleDisconnect = () => {
    if (!canManage) return;
    deleteConnection(connection.id, {
      onSuccess: () => setDisconnectOpen(false),
    });
  };

  const handleReconnectSlack = () => {
    if (!canManage) return;
    setConnectionError("");
    startSlackInstall.mutate(connection.id, {
      onSuccess: (response) => {
        const authorizationUrl = response.data?.result?.authorization_url;
        if (authorizationUrl) window.location.assign(authorizationUrl);
        else
          setConnectionError(
            "Could not start Slack authorization. Please try again.",
          );
      },
      onError: (error) =>
        setConnectionError(
          getErrorMessage(error, "Could not reconnect Slack."),
        ),
    });
  };

  return (
    <>
      <Section variant="panel" onClick={handleClick}>
        <Box
          display="flex"
          alignItems="center"
          gap={theme.spacing(1.5)}
          mb={theme.spacing(1)}
        >
          <PlatformLogo platform={connection.platform} size={28} />
          <Box flex={1} minWidth={0}>
            <Typography
              noWrap
              title={displayName}
              sx={{
                typography: "s2_1",
                fontWeight: "fontWeightSemiBold",
                color: "text.primary",
              }}
            >
              {displayName}
            </Typography>
            <Typography
              noWrap
              title={destination}
              data-testid="integration-card-destination"
              sx={{ typography: "s2", color: "text.tertiary" }}
            >
              {destination || "—"}
            </Typography>
          </Box>
          {isActionOnly ? (
            <RunStatusBadge
              status="connected"
              icon={<Iconify icon="solar:check-circle-bold" width={14} />}
            />
          ) : (
            <StatusBadge status={connection.status} isExport={isExport} />
          )}
        </Box>

        <Box
          display="flex"
          flexWrap="wrap"
          justifyContent="space-between"
          columnGap={theme.spacing(1)}
          mt={theme.spacing(0.5)}
        >
          {isActionOnly ? (
            <>
              <Typography sx={{ typography: "s2", color: "text.tertiary" }}>
                Action integration
              </Typography>
              <Typography sx={{ typography: "s2", color: "text.tertiary" }}>
                {createdAt ? `Connected ${fToNow(createdAt)}` : ""}
              </Typography>
            </>
          ) : (
            <>
              <Typography sx={{ typography: "s2", color: "text.tertiary" }}>
                {isExport
                  ? `${(totalTracesSynced || 0).toLocaleString()} request log deliveries (including repeats)`
                  : `${(totalTracesSynced || 0).toLocaleString()} traces`}
              </Typography>
              <Typography sx={{ typography: "s2", color: "text.tertiary" }}>
                {lastSyncedAt
                  ? `Synced ${fToNow(lastSyncedAt)}`
                  : "Never synced"}
              </Typography>
            </>
          )}
        </Box>
        {stoppedReason && (
          <Typography
            data-testid="integration-card-stopped-reason"
            sx={{
              typography: "s2",
              color: "error.main",
              mt: theme.spacing(1),
              overflowWrap: "anywhere",
            }}
          >
            {stoppedReason}
          </Typography>
        )}
      </Section>

      {isActionOnly && (
        <Dialog
          open={disconnectOpen}
          onClose={() => setDisconnectOpen(false)}
          maxWidth={false}
          PaperProps={{ sx: { maxWidth: 560 } }}
          fullWidth
        >
          <DialogTitle>Manage {displayName}</DialogTitle>
          <DialogContent>
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              {connection.platform === "slack"
                ? `Connected ${createdAt ? fDateTime(createdAt) : "—"}. Reconnect to renew Slack access, or disconnect to stop Error Feed notifications using this workspace.`
                : `Connected ${createdAt ? fDateTime(createdAt) : "—"}. Disconnecting will remove the API key and disable issue creation from Error Feed.`}
            </Typography>
            {connectionError && (
              <Alert severity="error" sx={{ mt: 2 }}>
                {connectionError}
              </Alert>
            )}
          </DialogContent>
          <DialogActions>
            <Button onClick={() => setDisconnectOpen(false)} size="small">
              Cancel
            </Button>
            {connection.platform === "slack" && (
              <IntegrationAdminOnly allowed={canManage}>
                <Button
                  onClick={handleReconnectSlack}
                  size="small"
                  disabled={!canManage || startSlackInstall.isPending}
                >
                  {startSlackInstall.isPending
                    ? "Connecting…"
                    : "Reconnect Slack"}
                </Button>
              </IntegrationAdminOnly>
            )}
            <IntegrationAdminOnly allowed={canManage}>
              <Button
                onClick={handleDisconnect}
                color="error"
                variant="contained"
                size="small"
                disabled={!canManage || deleting}
              >
                {deleting ? "Disconnecting…" : "Disconnect"}
              </Button>
            </IntegrationAdminOnly>
          </DialogActions>
        </Dialog>
      )}
    </>
  );
}

IntegrationCard.propTypes = {
  connection: PropTypes.object.isRequired,
};
