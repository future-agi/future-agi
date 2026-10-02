/* eslint-disable react/prop-types */
import React, { useState, useMemo } from "react";
import {
  Box,
  Card,
  CardContent,
  Chip,
  Collapse,
  IconButton,
  InputAdornment,
  MenuItem,
  Select,
  FormControl,
  InputLabel,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Tooltip,
  Typography,
  Skeleton,
} from "@mui/material";
import Iconify from "src/components/iconify";

function formatLatency(ms) {
  if (!ms || ms === 0) return "—";
  return `${Number(ms).toFixed(1)} ms`;
}

function formatErrorRate(rate) {
  if (!rate || rate === 0) return "0%";
  return `${(Number(rate) * 100).toFixed(1)}%`;
}

function formatLastCalled(ts) {
  if (!ts) return "—";
  const d = new Date(ts);
  if (isNaN(d.getTime())) return "—";
  const diff = Date.now() - d.getTime();
  if (diff < 60000) return "just now";
  if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
  return d.toLocaleDateString();
}

const ToolDetailRow = ({ tool }) => {
  const schema = tool.inputSchema;
  let schemaStr = "";
  if (schema) {
    try {
      schemaStr =
        typeof schema === "string"
          ? JSON.stringify(JSON.parse(schema), null, 2)
          : JSON.stringify(schema, null, 2);
    } catch {
      schemaStr = String(schema);
    }
  }

  return (
    <Box sx={{ p: 2, bgcolor: "action.hover" }}>
      <Stack spacing={2}>
        {tool.deprecated && (
          <Box
            sx={{
              p: 1.5,
              borderRadius: 1,
              bgcolor: "warning.lighter",
              border: "1px solid",
              borderColor: "warning.light",
            }}
          >
            <Typography variant="subtitle2" color="warning.dark" gutterBottom>
              Deprecated
            </Typography>
            {tool.deprecation_message && (
              <Typography variant="body2" color="warning.dark">
                {tool.deprecation_message}
              </Typography>
            )}
            {tool.replaced_by && (
              <Typography variant="body2" color="warning.dark" sx={{ mt: 0.5 }}>
                Replacement: <strong>{tool.replaced_by}</strong>
              </Typography>
            )}
          </Box>
        )}
        {tool.annotations && (
          <Box>
            <Typography variant="subtitle2" gutterBottom>
              Annotations
            </Typography>
            <Stack direction="row" spacing={1} flexWrap="wrap">
              {tool.annotations.readOnlyHint && (
                <Chip label="Read-only" size="small" color="info" />
              )}
              {tool.annotations.destructiveHint && (
                <Chip label="Destructive" size="small" color="error" />
              )}
              {tool.annotations.idempotentHint && (
                <Chip label="Idempotent" size="small" color="success" />
              )}
              {tool.annotations.openWorldHint && (
                <Chip label="Open World" size="small" />
              )}
            </Stack>
          </Box>
        )}
        {schemaStr && (
          <Box>
            <Typography variant="subtitle2" gutterBottom>
              Input Schema
            </Typography>
            <Box
              sx={{
                p: 1.5,
                borderRadius: 1,
                bgcolor: "background.neutral",
                fontFamily: "monospace",
                fontSize: 12,
                whiteSpace: "pre-wrap",
                wordBreak: "break-all",
                maxHeight: 300,
                overflow: "auto",
              }}
            >
              {schemaStr}
            </Box>
          </Box>
        )}
      </Stack>
    </Box>
  );
};

const MCPToolsTab = ({ mcpTools, isLoading }) => {
  const [search, setSearch] = useState("");
  const [serverFilter, setServerFilter] = useState("");
  const [expandedTool, setExpandedTool] = useState(null);

  const tools = useMemo(() => mcpTools || [], [mcpTools]);

  const servers = useMemo(() => {
    const set = new Set();
    tools.forEach((t) => set.add(t.server));
    return Array.from(set).sort();
  }, [tools]);

  const filtered = useMemo(() => {
    let result = tools;
    if (serverFilter) {
      result = result.filter((t) => t.server === serverFilter);
    }
    if (search) {
      const q = search.toLowerCase();
      result = result.filter(
        (t) =>
          (t.name || "").toLowerCase().includes(q) ||
          (t.description || "").toLowerCase().includes(q),
      );
    }
    return result;
  }, [tools, search, serverFilter]);

  if (isLoading) {
    return (
      <Card>
        {[...Array(5)].map((_, i) => (
          <Stack
            key={i}
            direction="row"
            spacing={2}
            sx={{
              px: 2,
              py: 1.5,
              borderBottom: "1px solid",
              borderColor: "divider",
            }}
          >
            <Skeleton width="25%" height={20} />
            <Skeleton width="10%" height={20} />
            <Skeleton width="30%" height={20} />
            <Skeleton width="10%" height={20} />
          </Stack>
        ))}
      </Card>
    );
  }

  return (
    <Box>
      <Stack direction="row" spacing={2} sx={{ mb: 2 }}>
        <TextField
          size="small"
          placeholder="Search tools..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          sx={{ minWidth: 280 }}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <Iconify icon="mdi:magnify" width={20} />
              </InputAdornment>
            ),
          }}
        />
        <FormControl size="small" sx={{ minWidth: 160 }}>
          <InputLabel>Server</InputLabel>
          <Select
            value={serverFilter}
            onChange={(e) => setServerFilter(e.target.value)}
            label="Server"
            displayEmpty
            renderValue={(value) => value || "All Servers"}
          >
            <MenuItem value="">All Servers</MenuItem>
            {servers.map((s) => (
              <MenuItem key={s} value={s}>
                {s}
              </MenuItem>
            ))}
          </Select>
        </FormControl>
      </Stack>

      {filtered.length === 0 ? (
        <Card>
          <CardContent>
            <Typography
              variant="body2"
              color="text.secondary"
              align="center"
              sx={{ py: 3 }}
            >
              {tools.length === 0
                ? "No MCP tools registered. Connect an MCP server to discover tools."
                : "No tools match the current filters."}
            </Typography>
          </CardContent>
        </Card>
      ) : (
        <Card>
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell padding="checkbox" />
                  <TableCell>Name</TableCell>
                  <TableCell>Server</TableCell>
                  <TableCell>Description</TableCell>
                  <TableCell align="right">Calls</TableCell>
                  <TableCell align="right">Errors</TableCell>
                  <TableCell align="right">Avg Latency</TableCell>
                  <TableCell align="right">Error Rate</TableCell>
                  <TableCell>Last Called</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {filtered.map((tool) => {
                  const isExpanded = expandedTool === tool.name;
                  const stats = tool.stats || {};
                  return (
                    <React.Fragment key={tool.name}>
                      <TableRow
                        hover
                        sx={{ cursor: "pointer" }}
                        onClick={() =>
                          setExpandedTool(isExpanded ? null : tool.name)
                        }
                      >
                        <TableCell padding="checkbox">
                          <IconButton size="small">
                            <Iconify
                              icon={
                                isExpanded
                                  ? "mdi:chevron-up"
                                  : "mdi:chevron-down"
                              }
                              width={18}
                            />
                          </IconButton>
                        </TableCell>
                        <TableCell>
                          <Stack
                            direction="row"
                            alignItems="center"
                            spacing={0.5}
                          >
                            <Typography
                              variant="body2"
                              fontWeight={600}
                              sx={{
                                fontFamily: "monospace",
                                fontSize: 13,
                                textDecoration: tool.deprecated
                                  ? "line-through"
                                  : "none",
                                opacity: tool.deprecated ? 0.7 : 1,
                              }}
                            >
                              {tool.name}
                            </Typography>
                            {tool.version && (
                              <Chip
                                label={`v${tool.version}`}
                                size="small"
                                variant="outlined"
                                sx={{ fontSize: 10, height: 18 }}
                              />
                            )}
                            {tool.deprecated && (
                              <Tooltip
                                title={
                                  tool.deprecation_message ||
                                  (tool.replaced_by
                                    ? `Use ${tool.replaced_by} instead`
                                    : "This tool is deprecated")
                                }
                              >
                                <Chip
                                  label="Deprecated"
                                  size="small"
                                  color="warning"
                                  sx={{ fontSize: 10, height: 18 }}
                                />
                              </Tooltip>
                            )}
                          </Stack>
                        </TableCell>
                        <TableCell>
                          <Chip label={tool.server} size="small" />
                        </TableCell>
                        <TableCell>
                          <Typography
                            variant="body2"
                            color="text.secondary"
                            sx={{
                              maxWidth: 260,
                              overflow: "hidden",
                              textOverflow: "ellipsis",
                              whiteSpace: "nowrap",
                            }}
                            title={tool.description}
                          >
                            {tool.description || "—"}
                          </Typography>
                        </TableCell>
                        <TableCell align="right">
                          {Number(stats.callCount ?? stats.call_count ?? 0)}
                        </TableCell>
                        <TableCell align="right">
                          {Number(stats.errorCount ?? stats.error_count ?? 0)}
                        </TableCell>
                        <TableCell align="right">
                          {formatLatency(stats.avg_latency_ms)}
                        </TableCell>
                        <TableCell align="right">
                          {formatErrorRate(stats.error_rate)}
                        </TableCell>
                        <TableCell>
                          {formatLastCalled(
                            stats.lastCalled ?? stats.last_called,
                          )}
                        </TableCell>
                      </TableRow>
                      <TableRow>
                        <TableCell
                          colSpan={9}
                          sx={{ p: 0, border: isExpanded ? undefined : "none" }}
                        >
                          <Collapse in={isExpanded}>
                            <ToolDetailRow tool={tool} />
                          </Collapse>
                        </TableCell>
                      </TableRow>
                    </React.Fragment>
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

export default MCPToolsTab;
