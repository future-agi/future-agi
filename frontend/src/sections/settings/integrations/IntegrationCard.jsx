import { useState } from "react";
import PropTypes from "prop-types";
import {
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
import { useDeleteConnection } from "src/api/integrations";
import PlatformLogo from "./PlatformLogo";
import StatusBadge from "./StatusBadge";
import RunStatusBadge from "src/components/status-badge/StatusBadge";
import { SKIP_SYNC_SETTINGS_PLATFORMS } from "./constants";

export default function IntegrationCard({ connection }) {
  const theme = useTheme();
  const navigate = useNavigate();
  const location = useLocation();
  const isActionOnly = SKIP_SYNC_SETTINGS_PLATFORMS.includes(
    connection.platform,
  );
  const workspaceIntegrationsMatch = location.pathname.match(
    /^\/dashboard\/settings\/workspace\/([^/]+)\/integrations\/?$/,
  );
  const displayName =
    connection.display_name ||
    connection.displayName ||
    connection.external_project_name ||
    connection.externalProjectName ||
    "Unnamed";
  const hostUrl = connection.host_url || connection.hostUrl;
  const createdAt = connection.created_at || connection.createdAt;
  const totalTracesSynced =
    connection.total_traces_synced ?? connection.totalTracesSynced;
  const lastSyncedAt = connection.last_synced_at || connection.lastSyncedAt;

  const [disconnectOpen, setDisconnectOpen] = useState(false);
  const { mutate: deleteConnection, isPending: deleting } =
    useDeleteConnection();

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
    deleteConnection(connection.id, {
      onSuccess: () => setDisconnectOpen(false),
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
              title={hostUrl}
              sx={{ typography: "s2", color: "text.tertiary" }}
            >
              {hostUrl || "—"}
            </Typography>
          </Box>
          {isActionOnly ? (
            <RunStatusBadge
              status="connected"
              icon={<Iconify icon="solar:check-circle-bold" width={14} />}
            />
          ) : (
            <StatusBadge status={connection.status} />
          )}
        </Box>

        <Box
          display="flex"
          justifyContent="space-between"
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
                {totalTracesSynced?.toLocaleString() || 0} traces
              </Typography>
              <Typography sx={{ typography: "s2", color: "text.tertiary" }}>
                {lastSyncedAt
                  ? `Synced ${fToNow(lastSyncedAt)}`
                  : "Never synced"}
              </Typography>
            </>
          )}
        </Box>
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
              Connected {createdAt ? fDateTime(createdAt) : "—"}. Disconnecting
              will remove the API key and disable issue creation from Error
              Feed.
            </Typography>
          </DialogContent>
          <DialogActions>
            <Button onClick={() => setDisconnectOpen(false)} size="small">
              Cancel
            </Button>
            <Button
              onClick={handleDisconnect}
              color="error"
              variant="contained"
              size="small"
              disabled={deleting}
            >
              {deleting ? "Disconnecting…" : "Disconnect"}
            </Button>
          </DialogActions>
        </Dialog>
      )}
    </>
  );
}

IntegrationCard.propTypes = {
  connection: PropTypes.object.isRequired,
};
