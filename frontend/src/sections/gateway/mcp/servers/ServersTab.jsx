import React, { useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, Skeleton, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { useGatewayConfig } from "../../providers/hooks/useGatewayConfig";
import { useMCPStatus, useMCPTools } from "../hooks/useMCPConfig";
import { hasStoredCredential, stripSecrets } from "../utils";
import AddMCPServerDialog from "./AddMCPServerDialog";
import ServerCards from "./ServerCards";
import ToolsTable from "./ToolsTable";
import GatewayAdminOnly from "../../components/GatewayAdminOnly";
import { useGatewayAdmin } from "../../hooks/useGatewayAdmin";

// The org's MCP servers, their tools, and the add and edit dialog. The dialog
// gets a server's config with every secret removed, plus whether one is set.
const ServersTab = ({ gatewayId }) => {
  const { data: config, isLoading: configLoading } =
    useGatewayConfig(gatewayId);
  const { data: mcpStatus } = useMCPStatus(gatewayId);
  const {
    data: mcpTools,
    isLoading: toolsLoading,
    isError: toolsError,
  } = useMCPTools(gatewayId);

  const [dialogOpen, setDialogOpen] = useState(false);
  const [editServer, setEditServer] = useState(null);
  // Adding, editing and removing a server write the org's gateway config:
  // Owner or Admin only (useGatewayAdmin). The list and tools stay readable.
  const { canEdit } = useGatewayAdmin();

  const openAdd = () => {
    setEditServer(null);
    setDialogOpen(true);
  };

  const openEdit = (serverId, serverConfig) => {
    setEditServer({
      serverId,
      config: stripSecrets(serverConfig),
      hasCredential: hasStoredCredential(serverConfig),
    });
    setDialogOpen(true);
  };

  const closeDialog = () => {
    setDialogOpen(false);
    setEditServer(null);
  };

  return (
    <Stack spacing={3}>
      <Stack direction="row" justifyContent="flex-end">
        <GatewayAdminOnly allowed={canEdit}>
          <Button
            variant="contained"
            size="small"
            startIcon={<Iconify icon="mdi:plus" width={18} />}
            onClick={openAdd}
            disabled={!canEdit}
          >
            Add Server
          </Button>
        </GatewayAdminOnly>
      </Stack>

      {configLoading ? (
        <Skeleton variant="rounded" height={160} />
      ) : (
        <ServerCards
          config={config}
          mcpStatus={mcpStatus}
          gatewayId={gatewayId}
          onEditServer={openEdit}
        />
      )}

      <Box>
        <Typography variant="h6" sx={{ mb: 2 }}>
          Tools
        </Typography>
        <ToolsTable
          mcpTools={mcpTools}
          isLoading={toolsLoading}
          isError={toolsError}
        />
      </Box>

      <AddMCPServerDialog
        open={dialogOpen}
        onClose={closeDialog}
        gatewayId={gatewayId}
        editServer={editServer}
      />
    </Stack>
  );
};

ServersTab.propTypes = {
  gatewayId: PropTypes.string,
};

export default ServersTab;
