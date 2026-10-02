/* eslint-disable react/prop-types */
import React, { useState, useMemo } from "react";
import {
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  Grid,
  Stack,
  Typography,
} from "@mui/material";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import { useRemoveMCPServer } from "./hooks/useMCPConfig";
import {
  getMCPServerId,
  getMCPServerStatusColor,
  getMCPServerStatusLabel,
  getMCPServerToolCount,
} from "./utils";

const MCPServersTab = ({ config, mcpStatus, gatewayId, onEditServer }) => {
  const [confirmDelete, setConfirmDelete] = useState(null);
  const removeMutation = useRemoveMCPServer();

  const mcpConfig = config?.mcp || {};
  const servers = mcpConfig.servers || {};
  const serverIds = Object.keys(servers);

  const healthMap = useMemo(() => {
    const map = {};
    const statusServers = mcpStatus?.servers || [];
    statusServers.forEach((s) => {
      const id = getMCPServerId(s);
      if (id) map[id] = s;
    });
    return map;
  }, [mcpStatus]);

  const handleDelete = (serverId) => {
    removeMutation.mutate(
      { gatewayId, serverId },
      {
        onSuccess: () => {
          enqueueSnackbar(`Server "${serverId}" removed`, {
            variant: "success",
          });
          setConfirmDelete(null);
        },
        onError: () => {
          enqueueSnackbar("Failed to remove server", { variant: "error" });
        },
      },
    );
  };

  if (serverIds.length === 0) {
    return (
      <Card>
        <CardContent>
          <Typography
            variant="body2"
            color="text.secondary"
            align="center"
            sx={{ py: 3 }}
          >
            No MCP servers configured. Click &quot;Add Server&quot; to connect
            an upstream MCP tool server.
          </Typography>
        </CardContent>
      </Card>
    );
  }

  return (
    <Box>
      <Grid container spacing={3}>
        {serverIds.map((serverId) => {
          const serverCfg = servers[serverId] || {};
          const health = healthMap[serverId];
          const transport = serverCfg.transport || "http";
          const authType = serverCfg.auth?.type || "none";

          return (
            <Grid item xs={12} sm={6} md={4} key={serverId}>
              <Card sx={{ height: "100%" }}>
                <CardContent>
                  <Stack spacing={1.5}>
                    <Stack
                      direction="row"
                      justifyContent="space-between"
                      alignItems="center"
                    >
                      <Typography variant="subtitle1" fontWeight={700}>
                        {serverId}
                      </Typography>
                      {health ? (
                        <Chip
                          label={getMCPServerStatusLabel(health)}
                          color={getMCPServerStatusColor(health)}
                          size="small"
                        />
                      ) : (
                        <Chip label="Unknown" size="small" />
                      )}
                    </Stack>

                    <Box>
                      <Typography
                        variant="caption"
                        color="text.secondary"
                        display="block"
                      >
                        URL
                      </Typography>
                      <Typography
                        variant="body2"
                        sx={{
                          fontFamily: "monospace",
                          fontSize: 12,
                          wordBreak: "break-all",
                        }}
                      >
                        {serverCfg.url || serverCfg.command || "—"}
                      </Typography>
                    </Box>

                    <Stack direction="row" spacing={1}>
                      <Chip
                        label={transport.toUpperCase()}
                        size="small"
                        variant="outlined"
                      />
                      <Chip
                        label={`Auth: ${authType}`}
                        size="small"
                        variant="outlined"
                      />
                      {health && (
                        <Chip
                          label={`${getMCPServerToolCount(health)} tools`}
                          size="small"
                          variant="outlined"
                          color="primary"
                        />
                      )}
                    </Stack>

                    {serverCfg.tools_cache_ttl && (
                      <Typography variant="caption" color="text.secondary">
                        Cache TTL: {serverCfg.tools_cache_ttl}
                      </Typography>
                    )}

                    <Stack direction="row" spacing={1} sx={{ mt: 1 }}>
                      <Button
                        size="small"
                        variant="outlined"
                        startIcon={<Iconify icon="mdi:pencil" width={16} />}
                        onClick={() => onEditServer(serverId, serverCfg)}
                      >
                        Edit
                      </Button>
                      <Button
                        size="small"
                        variant="outlined"
                        color="error"
                        startIcon={<Iconify icon="mdi:delete" width={16} />}
                        onClick={() => setConfirmDelete(serverId)}
                        disabled={removeMutation.isPending}
                      >
                        Delete
                      </Button>
                    </Stack>
                  </Stack>
                </CardContent>
              </Card>
            </Grid>
          );
        })}
      </Grid>

      <Dialog
        open={Boolean(confirmDelete)}
        onClose={() => setConfirmDelete(null)}
      >
        <DialogTitle>Remove MCP Server</DialogTitle>
        <DialogContent>
          <DialogContentText>
            Are you sure you want to remove the server &quot;{confirmDelete}
            &quot;? All tools from this server will be unregistered.
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setConfirmDelete(null)}>Cancel</Button>
          <Button
            color="error"
            variant="contained"
            onClick={() => handleDelete(confirmDelete)}
            disabled={removeMutation.isPending}
          >
            {removeMutation.isPending ? "Removing..." : "Remove"}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default MCPServersTab;
