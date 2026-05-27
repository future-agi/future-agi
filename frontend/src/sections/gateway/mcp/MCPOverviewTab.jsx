/* eslint-disable react/prop-types */
import React from "react";
import {
  Box,
  Card,
  CardContent,
  Chip,
  Grid,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import {
  getMCPServerId,
  getMCPServerStatusColor,
  getMCPServerStatusLabel,
  getMCPServerToolCount,
} from "./utils";

const StatCard = ({ label, value, chip }) => (
  <Card>
    <CardContent>
      <Typography variant="body2" color="text.secondary" gutterBottom>
        {label}
      </Typography>
      {chip ? chip : <Typography variant="h4">{value ?? "—"}</Typography>}
    </CardContent>
  </Card>
);

const MCPOverviewTab = ({ mcpStatus }) => {
  const status = mcpStatus || {};
  const servers = status.servers || [];

  return (
    <Box>
      <Grid container spacing={3} sx={{ mb: 3 }}>
        <Grid item xs={12} sm={6} md={3}>
          <StatCard
            label="MCP Status"
            chip={
              <Chip
                label={status.enabled ? "Enabled" : "Disabled"}
                color={status.enabled ? "success" : "default"}
                size="small"
                sx={{ mt: 0.5 }}
              />
            }
          />
        </Grid>
        <Grid item xs={12} sm={6} md={3}>
          <StatCard
            label="Active Sessions"
            value={Number(status.sessions || 0)}
          />
        </Grid>
        <Grid item xs={12} sm={6} md={3}>
          <StatCard label="Total Tools" value={Number(status.tools || 0)} />
        </Grid>
        <Grid item xs={12} sm={6} md={3}>
          <StatCard label="Connected Servers" value={servers.length} />
        </Grid>
        <Grid item xs={12} sm={6} md={3}>
          <StatCard label="Resources" value={Number(status.resources || 0)} />
        </Grid>
        <Grid item xs={12} sm={6} md={3}>
          <StatCard label="Prompts" value={Number(status.prompts || 0)} />
        </Grid>
      </Grid>

      <Typography variant="h6" sx={{ mb: 2 }}>
        Server Health
      </Typography>

      {servers.length === 0 ? (
        <Card>
          <CardContent>
            <Typography
              variant="body2"
              color="text.secondary"
              align="center"
              sx={{ py: 3 }}
            >
              No MCP servers connected. Add a server from the Servers tab.
            </Typography>
          </CardContent>
        </Card>
      ) : (
        <Card>
          <TableContainer>
            <Table>
              <TableHead>
                <TableRow>
                  <TableCell>Server ID</TableCell>
                  <TableCell>Status</TableCell>
                  <TableCell align="right">Tool Count</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {servers.map((server) => {
                  const serverId = getMCPServerId(server);
                  return (
                    <TableRow key={serverId}>
                      <TableCell>
                        <Typography variant="body2" fontWeight={600}>
                          {serverId || "—"}
                        </Typography>
                      </TableCell>
                      <TableCell>
                        <Chip
                          label={getMCPServerStatusLabel(server)}
                          color={getMCPServerStatusColor(server)}
                          size="small"
                        />
                      </TableCell>
                      <TableCell align="right">
                        {getMCPServerToolCount(server)}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </TableContainer>
        </Card>
      )}
    </Box>
  );
};

export default MCPOverviewTab;
