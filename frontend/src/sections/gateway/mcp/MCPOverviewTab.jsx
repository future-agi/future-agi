/* eslint-disable react/prop-types */
import StatusBadge from "src/components/status-badge/StatusBadge";
import React from "react";
import { Box, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Typography } from "@mui/material";
import Section from "src/components/section";
import MetricStrip from "src/components/metric-strip";
import {
  getMCPServerId,
  getMCPServerStatusColor,
  getMCPServerStatusLabel,
  getMCPServerToolCount,
} from "./utils";

const CHIP_ROLE = {
  success: "success",
  warning: "warning",
  error: "danger",
  info: "info",
  primary: "discovery",
  secondary: "discovery",
  default: "neutral",
};

const MCPOverviewTab = ({ mcpStatus }) => {
  const status = mcpStatus || {};
  const servers = status.servers || [];

  return (
    <Box>
      <MetricStrip
        sx={{ mb: 3 }}
        items={[
          {
            label: "MCP status",
            value: status.enabled ? "Enabled" : "Disabled",
          },
          { label: "Active sessions", value: Number(status.sessions || 0) },
          { label: "Total tools", value: Number(status.tools || 0) },
          { label: "Connected servers", value: servers.length },
          { label: "Resources", value: Number(status.resources || 0) },
          { label: "Prompts", value: Number(status.prompts || 0) },
        ]}
      />

      <Typography variant="h6" sx={{ mb: 2 }}>
        Server Health
      </Typography>

      {servers.length === 0 ? (
        <Section variant="panel">
          <Typography
            variant="body2"
            color="text.secondary"
            align="center"
            sx={{ py: 3 }}
          >
            No MCP servers connected. Add a server from the Servers tab.
          </Typography>
        </Section>
      ) : (
        <Section variant="panel" sx={{ p: 0, overflow: "hidden" }}>
          <TableContainer>
            <Table>
              <TableHead>
                <TableRow>
                  <TableCell>Server ID</TableCell>
                  <TableCell>Status</TableCell>
                  <TableCell
                    sx={{ fontVariantNumeric: "tabular-nums" }}
                    align="right"
                  >
                    Tool Count
                  </TableCell>
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
                        <StatusBadge
                          status={getMCPServerStatusLabel(server)}
                          label={String(getMCPServerStatusLabel(server))}
                          role={CHIP_ROLE[getMCPServerStatusColor(server)]}
                        />
                      </TableCell>
                      <TableCell
                        sx={{ fontVariantNumeric: "tabular-nums" }}
                        align="right"
                      >
                        {getMCPServerToolCount(server)}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </TableContainer>
        </Section>
      )}
    </Box>
  );
};

export default MCPOverviewTab;
