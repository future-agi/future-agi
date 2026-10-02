/* eslint-disable react/prop-types */
import React, { useState, useMemo, useCallback } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  Box,
  Typography,
  Stack,
  Tab,
  Tabs,
  Card,
  CardContent,
  Grid,
  Chip,
  Button,
  Skeleton,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  InputAdornment,
  Slider,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  IconButton,
  Collapse,
  LinearProgress,
  Alert,
  Tooltip,
  CircularProgress,
} from "@mui/material";
import Iconify from "src/components/iconify";
import SectionHeader from "../components/SectionHeader";
import { GATEWAY_ICONS } from "../constants/gatewayIcons";
import { enqueueSnackbar } from "notistack";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import axiosInstance, { endpoints } from "src/utils/axios";
import {
  useGatewayConfig,
  useReloadConfig,
} from "../providers/hooks/useGatewayConfig";
import { useGatewayContext } from "../context/useGatewayContext";

import OrgConfigSection from "./OrgConfigSection";
import EmailAlertsCard from "./EmailAlertsCard";
import {
  useSubmitBatch,
  useBatchStatus,
  useCancelBatch,
} from "./hooks/useBatchJobs";

// ---------------------------------------------------------------------------
// Gateway Settings Tab
// ---------------------------------------------------------------------------

const SettingsTab = ({ config, gateway }) => {
  const serverConfig = config?.server || {};
  const logging = config?.logging || {};
  const costTracking = config?.cost_tracking ?? config?.costTracking ?? {};

  return (
    <Stack spacing={2}>
      {/* Server config */}
      <Card>
        <CardContent>
          <Typography variant="h6" mb={2}>
            Server Configuration
          </Typography>
          <Stack spacing={1}>
            <InfoLine label="Host" value={serverConfig.host || "\u2014"} />
            <InfoLine label="Port" value={serverConfig.port || "\u2014"} />
            <InfoLine
              label="Read Timeout"
              value={
                serverConfig.read_timeout ??
                serverConfig.readTimeout ??
                "\u2014"
              }
            />
            <InfoLine
              label="Write Timeout"
              value={
                serverConfig.write_timeout ??
                serverConfig.writeTimeout ??
                "\u2014"
              }
            />
            <InfoLine
              label="Idle Timeout"
              value={
                serverConfig.idle_timeout ??
                serverConfig.idleTimeout ??
                "\u2014"
              }
            />
            <InfoLine
              label="Shutdown Timeout"
              value={
                serverConfig.shutdown_timeout ??
                serverConfig.shutdownTimeout ??
                "\u2014"
              }
            />
          </Stack>
        </CardContent>
      </Card>

      {/* Gateway info */}
      <Card>
        <CardContent>
          <Typography variant="h6" mb={2}>
            Gateway Info
          </Typography>
          <Stack spacing={1}>
            <InfoLine label="Name" value={gateway?.name || "\u2014"} />
            <InfoLine label="Base URL" value={gateway?.baseUrl || "\u2014"} />
            <InfoLine label="Status" value={gateway?.status || "\u2014"} />
            <InfoLine
              label="Providers"
              value={gateway?.providerCount ?? "\u2014"}
            />
            <InfoLine label="Models" value={gateway?.modelCount ?? "\u2014"} />
            <InfoLine
              label="Last Health Check"
              value={
                gateway?.lastHealthCheck
                  ? new Date(gateway.lastHealthCheck).toLocaleString()
                  : "Never"
              }
            />
          </Stack>
        </CardContent>
      </Card>

      {/* Logging */}
      <Card>
        <CardContent>
          <Typography variant="h6" mb={2}>
            Logging
          </Typography>
          <Stack spacing={1}>
            <InfoLine label="Level" value={logging.level || "\u2014"} />
            <InfoLine label="Format" value={logging.format || "\u2014"} />
            <InfoLine
              label="Request Logging"
              value={logging.request_logging?.enabled ? "Enabled" : "Disabled"}
            />
            <InfoLine
              label="Include Bodies"
              value={logging.request_logging?.include_bodies ? "Yes" : "No"}
            />
          </Stack>
        </CardContent>
      </Card>

      {/* Cost Tracking */}
      <Card>
        <CardContent>
          <Typography variant="h6" mb={2}>
            Cost Tracking
          </Typography>
          <InfoLine
            label="Enabled"
            value={costTracking.enabled ? "Yes" : "No"}
          />
        </CardContent>
      </Card>

      {/* Email Alerts */}
      <EmailAlertsCard />
    </Stack>
  );
};

// ---------------------------------------------------------------------------
// Full Config Tab
// ---------------------------------------------------------------------------

const FullConfigTab = ({ config }) => {
  const [search, setSearch] = useState("");
  const jsonStr = useMemo(
    () => JSON.stringify(config || {}, null, 2),
    [config],
  );

  const highlighted = useMemo(() => {
    if (!search.trim()) return null;
    const term = search.trim();
    const parts = jsonStr.split(
      new RegExp(`(${term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})`, "gi"),
    );
    return parts.map((part, i) =>
      part.toLowerCase() === term.toLowerCase() ? (
        <Box
          key={i}
          component="span"
          sx={{
            bgcolor: "warning.light",
            color: "warning.contrastText",
            borderRadius: 0.5,
            px: 0.25,
          }}
        >
          {part}
        </Box>
      ) : (
        part
      ),
    );
  }, [jsonStr, search]);

  const matchCount = useMemo(() => {
    if (!search.trim()) return 0;
    const term = search.trim().replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    return (jsonStr.match(new RegExp(term, "gi")) || []).length;
  }, [jsonStr, search]);

  return (
    <Card>
      <CardContent>
        <Stack
          direction="row"
          justifyContent="space-between"
          alignItems="center"
          mb={2}
        >
          <Typography variant="h6">Full Gateway Configuration</Typography>
          <TextField
            size="small"
            placeholder="Search config..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            sx={{ width: 260 }}
            InputProps={{
              startAdornment: (
                <InputAdornment position="start">
                  <Iconify icon="mdi:magnify" width={18} />
                </InputAdornment>
              ),
              endAdornment: search.trim() ? (
                <InputAdornment position="end">
                  <Typography variant="caption" color="text.secondary">
                    {matchCount} match{matchCount !== 1 ? "es" : ""}
                  </Typography>
                </InputAdornment>
              ) : null,
            }}
          />
        </Stack>
        <Box
          sx={{
            bgcolor: "action.hover",
            borderRadius: 1,
            p: 2,
            fontFamily: "monospace",
            fontSize: "0.75rem",
            whiteSpace: "pre-wrap",
            overflowX: "auto",
            maxHeight: 600,
          }}
        >
          {highlighted || jsonStr}
        </Box>
      </CardContent>
    </Card>
  );
};

// ---------------------------------------------------------------------------
// Batch Jobs Tab
// ---------------------------------------------------------------------------

const SAMPLE_REQUEST = {
  model: "gpt-4o-mini",
  messages: [{ role: "user", content: "Say hello in one sentence." }],
};

const BatchJobsTab = ({ config, gatewayId }) => {
  const batchConfig = config?.batch || config?.batch_api || {};
  const [submitOpen, setSubmitOpen] = useState(false);
  const [requestsJson, setRequestsJson] = useState(
    JSON.stringify([SAMPLE_REQUEST], null, 2),
  );
  const [maxConcurrency, setMaxConcurrency] = useState(5);
  const [jsonError, setJsonError] = useState("");
  const [trackedJobs, setTrackedJobs] = useState([]);
  const [selectedJob, setSelectedJob] = useState(null);

  const submitBatch = useSubmitBatch();
  const cancelBatch = useCancelBatch();

  const handleJsonChange = useCallback((val) => {
    setRequestsJson(val);
    try {
      const parsed = JSON.parse(val);
      if (!Array.isArray(parsed)) {
        setJsonError("Must be a JSON array of request objects");
      } else if (parsed.length === 0) {
        setJsonError("At least one request is required");
      } else {
        setJsonError("");
      }
    } catch {
      setJsonError("Invalid JSON");
    }
  }, []);

  const handleSubmit = () => {
    let requests;
    try {
      requests = JSON.parse(requestsJson);
    } catch {
      return;
    }
    submitBatch.mutate(
      { gatewayId, requests, maxConcurrency },
      {
        onSuccess: (result) => {
          const batchId = result?.batch_id;
          if (batchId) {
            setTrackedJobs((prev) => [
              {
                batch_id: batchId,
                submittedAt: new Date().toISOString(),
                total: requests.length,
              },
              ...prev,
            ]);
          }
          setSubmitOpen(false);
          setRequestsJson(JSON.stringify([SAMPLE_REQUEST], null, 2));
        },
      },
    );
  };

  const handleCancel = (batchId) => {
    cancelBatch.mutate({ gatewayId, batchId });
  };

  return (
    <Stack spacing={2}>
      {/* Batch API Config */}
      <Card>
        <CardContent>
          <Stack
            direction="row"
            justifyContent="space-between"
            alignItems="center"
            mb={2}
          >
            <Typography variant="h6">Batch API</Typography>
            <Button
              variant="contained"
              size="small"
              startIcon={<Iconify icon="mdi:plus" width={18} />}
              onClick={() => setSubmitOpen(true)}
            >
              Submit Batch Job
            </Button>
          </Stack>
          {Object.keys(batchConfig).length > 0 ? (
            <Stack spacing={1}>
              <InfoLine
                label="Enabled"
                value={batchConfig.enabled ? "Yes" : "No"}
              />
              <InfoLine
                label="Max Concurrent"
                value={
                  batchConfig.max_concurrent ??
                  batchConfig.maxConcurrent ??
                  "\u2014"
                }
              />
              <InfoLine
                label="Storage"
                value={
                  batchConfig.storage_path ??
                  batchConfig.storagePath ??
                  "\u2014"
                }
              />
            </Stack>
          ) : (
            <Typography variant="body2" color="text.secondary">
              Batch API configuration not found in gateway config. Jobs can
              still be submitted directly.
            </Typography>
          )}
        </CardContent>
      </Card>

      {/* Tracked Jobs */}
      <Card>
        <CardContent>
          <Typography variant="h6" mb={2}>
            Batch Jobs
          </Typography>
          {trackedJobs.length === 0 ? (
            <Stack alignItems="center" spacing={1} sx={{ py: 3 }}>
              <Iconify
                icon="mdi:tray-arrow-up"
                width={40}
                sx={{ color: "text.disabled" }}
              />
              <Typography variant="body2" color="text.secondary">
                No batch jobs submitted yet. Click &quot;Submit Batch Job&quot;
                to get started.
              </Typography>
            </Stack>
          ) : (
            <TableContainer>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>Batch ID</TableCell>
                    <TableCell>Requests</TableCell>
                    <TableCell>Status</TableCell>
                    <TableCell>Submitted</TableCell>
                    <TableCell align="right">Actions</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {trackedJobs.map((job) => (
                    <BatchJobRow
                      key={job.batch_id}
                      job={job}
                      gatewayId={gatewayId}
                      onCancel={handleCancel}
                      onViewDetails={setSelectedJob}
                    />
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          )}
        </CardContent>
      </Card>

      {/* Submit Dialog */}
      <Dialog
        open={submitOpen}
        onClose={() => setSubmitOpen(false)}
        maxWidth="md"
        fullWidth
      >
        <DialogTitle>Submit Batch Job</DialogTitle>
        <DialogContent dividers>
          <Stack spacing={2.5} sx={{ pt: 1 }}>
            <Box>
              <Typography variant="subtitle2" mb={1}>
                Requests (JSON array of chat completion request objects)
              </Typography>
              <TextField
                multiline
                minRows={8}
                maxRows={20}
                fullWidth
                value={requestsJson}
                onChange={(e) => handleJsonChange(e.target.value)}
                error={Boolean(jsonError)}
                helperText={
                  jsonError ||
                  "Each object should have model and messages fields"
                }
                sx={{
                  "& .MuiInputBase-input": {
                    fontFamily: "monospace",
                    fontSize: "0.8rem",
                  },
                }}
              />
            </Box>
            <Box>
              <Typography variant="subtitle2" mb={1}>
                Max Concurrency: {maxConcurrency}
              </Typography>
              <Slider
                value={maxConcurrency}
                onChange={(_, v) => setMaxConcurrency(v)}
                min={1}
                max={50}
                marks={[
                  { value: 1, label: "1" },
                  { value: 10, label: "10" },
                  { value: 25, label: "25" },
                  { value: 50, label: "50" },
                ]}
                valueLabelDisplay="auto"
                sx={{ maxWidth: 400 }}
              />
            </Box>
            <Alert severity="info" variant="outlined">
              Each request is sent through the gateway pipeline (routing,
              guardrails, caching) independently. Results include per-request
              cost and token usage.
            </Alert>
          </Stack>
        </DialogContent>
        <DialogActions sx={{ px: 3, py: 2 }}>
          <Button onClick={() => setSubmitOpen(false)}>Cancel</Button>
          <Button
            variant="contained"
            onClick={handleSubmit}
            disabled={submitBatch.isPending || Boolean(jsonError)}
            startIcon={
              submitBatch.isPending ? <CircularProgress size={16} /> : null
            }
          >
            {submitBatch.isPending ? "Submitting..." : "Submit"}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Batch Detail Dialog */}
      {selectedJob && (
        <BatchDetailDialog
          gatewayId={gatewayId}
          batchId={selectedJob}
          onClose={() => setSelectedJob(null)}
        />
      )}
    </Stack>
  );
};

// ---------------------------------------------------------------------------
// Batch Job Row — auto-polls status
// ---------------------------------------------------------------------------

const BatchJobRow = ({ job, gatewayId, onCancel, onViewDetails }) => {
  const { data: status } = useBatchStatus(gatewayId, job.batch_id);

  const batchStatus = status?.status || "pending";
  const completed =
    (status?.summary?.completed ?? 0) + (status?.summary?.failed ?? 0);
  const total = status?.total ?? job.total;
  const progress = total > 0 ? (completed / total) * 100 : 0;

  const statusColor = {
    pending: "default",
    processing: "info",
    completed: "success",
    cancelled: "warning",
    failed: "error",
  };

  return (
    <TableRow>
      <TableCell>
        <Typography variant="body2" fontFamily="monospace" fontSize="0.75rem">
          {job.batch_id.substring(0, 12)}...
        </Typography>
      </TableCell>
      <TableCell>{total}</TableCell>
      <TableCell>
        <Stack spacing={0.5}>
          <Chip
            label={batchStatus}
            size="small"
            color={statusColor[batchStatus] || "default"}
            variant="outlined"
          />
          {batchStatus === "processing" && (
            <LinearProgress
              variant="determinate"
              value={progress}
              sx={{ height: 4, borderRadius: 2, width: 80 }}
            />
          )}
        </Stack>
      </TableCell>
      <TableCell>
        <Typography variant="caption">
          {new Date(job.submittedAt).toLocaleTimeString()}
        </Typography>
      </TableCell>
      <TableCell align="right">
        <Stack direction="row" spacing={0.5} justifyContent="flex-end">
          <Tooltip title="View Details">
            <IconButton
              size="small"
              onClick={() => onViewDetails(job.batch_id)}
            >
              <Iconify icon="mdi:eye-outline" width={18} />
            </IconButton>
          </Tooltip>
          {(batchStatus === "pending" || batchStatus === "processing") && (
            <Tooltip title="Cancel">
              <IconButton
                size="small"
                color="error"
                onClick={() => onCancel(job.batch_id)}
              >
                <Iconify icon="mdi:close-circle-outline" width={18} />
              </IconButton>
            </Tooltip>
          )}
        </Stack>
      </TableCell>
    </TableRow>
  );
};

// ---------------------------------------------------------------------------
// Batch Detail Dialog — shows results once complete
// ---------------------------------------------------------------------------

const BatchDetailDialog = ({ gatewayId, batchId, onClose }) => {
  const { data: batch, isLoading } = useBatchStatus(gatewayId, batchId);
  const [expandedIdx, setExpandedIdx] = useState(null);

  const summary = batch?.summary;
  const results = batch?.results || [];
  const isTerminal =
    batch?.status === "completed" || batch?.status === "cancelled";

  return (
    <Dialog open onClose={onClose} maxWidth="lg" fullWidth>
      <DialogTitle>
        <Stack
          direction="row"
          justifyContent="space-between"
          alignItems="center"
        >
          <Stack direction="row" alignItems="center" spacing={1}>
            <Typography variant="h6">Batch Details</Typography>
            <Chip
              label={batch?.status || "loading"}
              size="small"
              color={
                batch?.status === "completed"
                  ? "success"
                  : batch?.status === "cancelled"
                    ? "warning"
                    : "info"
              }
              variant="outlined"
            />
          </Stack>
          <IconButton onClick={onClose} size="small">
            <Iconify icon="mdi:close" width={20} />
          </IconButton>
        </Stack>
      </DialogTitle>
      <DialogContent dividers>
        {isLoading ? (
          <Stack spacing={2} sx={{ py: 3 }}>
            <Skeleton variant="rounded" height={60} />
            <Skeleton variant="rounded" height={200} />
          </Stack>
        ) : (
          <Stack spacing={2}>
            {/* Summary stats */}
            <Grid container spacing={2}>
              <Grid item xs={6} sm={2}>
                <StatBox label="Total" value={batch?.total ?? 0} />
              </Grid>
              <Grid item xs={6} sm={2}>
                <StatBox label="Completed" value={summary?.completed ?? 0} />
              </Grid>
              <Grid item xs={6} sm={2}>
                <StatBox label="Failed" value={summary?.failed ?? 0} />
              </Grid>
              <Grid item xs={6} sm={2}>
                <StatBox label="Cancelled" value={summary?.cancelled ?? 0} />
              </Grid>
              <Grid item xs={6} sm={2}>
                <StatBox
                  label="Total Cost"
                  value={
                    summary?.total_cost != null
                      ? `$${Number(summary.total_cost).toFixed(4)}`
                      : "\u2014"
                  }
                />
              </Grid>
              <Grid item xs={6} sm={2}>
                <StatBox
                  label="Tokens"
                  value={
                    (summary?.total_input_tokens ?? 0) +
                    (summary?.total_output_tokens ?? 0)
                  }
                />
              </Grid>
            </Grid>

            {!isTerminal && (
              <LinearProgress
                sx={{ borderRadius: 2 }}
                variant={batch?.total > 0 ? "determinate" : "indeterminate"}
                value={
                  batch?.total > 0
                    ? (((summary?.completed ?? 0) + (summary?.failed ?? 0)) /
                        batch.total) *
                      100
                    : 0
                }
              />
            )}

            {/* Results table */}
            {results.length > 0 && (
              <TableContainer>
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell width={40}>#</TableCell>
                      <TableCell>Status</TableCell>
                      <TableCell>Cost</TableCell>
                      <TableCell>Input Tokens</TableCell>
                      <TableCell>Output Tokens</TableCell>
                      <TableCell>Response Preview</TableCell>
                      <TableCell width={40} />
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {results.map((r) => (
                      <React.Fragment key={r.index}>
                        <TableRow
                          hover
                          sx={{ cursor: "pointer" }}
                          onClick={() =>
                            setExpandedIdx(
                              expandedIdx === r.index ? null : r.index,
                            )
                          }
                        >
                          <TableCell>{r.index}</TableCell>
                          <TableCell>
                            <Chip
                              label={r.status}
                              size="small"
                              color={
                                r.status === "success"
                                  ? "success"
                                  : r.status === "error"
                                    ? "error"
                                    : "default"
                              }
                              variant="outlined"
                            />
                          </TableCell>
                          <TableCell>
                            {r.cost != null
                              ? `$${Number(r.cost).toFixed(4)}`
                              : "\u2014"}
                          </TableCell>
                          <TableCell>{r.input_tokens ?? "\u2014"}</TableCell>
                          <TableCell>{r.output_tokens ?? "\u2014"}</TableCell>
                          <TableCell>
                            <Typography
                              variant="caption"
                              noWrap
                              sx={{ maxWidth: 300, display: "block" }}
                            >
                              {r.error ||
                                r.response?.choices?.[0]?.message?.content?.substring(
                                  0,
                                  100,
                                ) ||
                                "\u2014"}
                            </Typography>
                          </TableCell>
                          <TableCell>
                            <Iconify
                              icon={
                                expandedIdx === r.index
                                  ? "mdi:chevron-up"
                                  : "mdi:chevron-down"
                              }
                              width={18}
                            />
                          </TableCell>
                        </TableRow>
                        <TableRow>
                          <TableCell colSpan={7} sx={{ py: 0, border: 0 }}>
                            <Collapse in={expandedIdx === r.index}>
                              <Box
                                sx={{
                                  bgcolor: "action.hover",
                                  borderRadius: 1,
                                  p: 2,
                                  my: 1,
                                  fontFamily: "monospace",
                                  fontSize: "0.7rem",
                                  whiteSpace: "pre-wrap",
                                  maxHeight: 300,
                                  overflow: "auto",
                                }}
                              >
                                {JSON.stringify(
                                  r.error
                                    ? { error: r.error }
                                    : r.response || {},
                                  null,
                                  2,
                                )}
                              </Box>
                            </Collapse>
                          </TableCell>
                        </TableRow>
                      </React.Fragment>
                    ))}
                  </TableBody>
                </Table>
              </TableContainer>
            )}

            {isTerminal && results.length === 0 && (
              <Typography
                variant="body2"
                color="text.secondary"
                textAlign="center"
                py={2}
              >
                No results available.
              </Typography>
            )}
          </Stack>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Close</Button>
      </DialogActions>
    </Dialog>
  );
};

const StatBox = ({ label, value }) => (
  <Box textAlign="center">
    <Typography variant="caption" color="text.secondary">
      {label}
    </Typography>
    <Typography variant="h6" fontWeight={600}>
      {String(value)}
    </Typography>
  </Box>
);

const TAB_SLUGS = ["settings", "org-config", "batch-jobs", "full-config"];

function tabSlugToIndex(slug) {
  const idx = TAB_SLUGS.indexOf(slug);
  return idx >= 0 ? idx : 0;
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

const GatewaySettingsSection = () => {
  const { tab: tabSlug } = useParams();
  const navigate = useNavigate();
  const tab = tabSlugToIndex(tabSlug);

  const handleTabChange = useCallback(
    (_, newIndex) => {
      if (newIndex === 0) {
        navigate("/dashboard/gateway/settings", { replace: true });
      } else {
        navigate(`/dashboard/gateway/settings/${TAB_SLUGS[newIndex]}`, {
          replace: true,
        });
      }
    },
    [navigate],
  );

  const queryClient = useQueryClient();
  const { gateway, gatewayId, isLoading: gwLoading } = useGatewayContext();

  const { data: config, isLoading: configLoading } =
    useGatewayConfig(gatewayId);
  const reloadMutation = useReloadConfig();

  const healthCheckMutation = useMutation({
    mutationFn: async (id) => {
      const { data } = await axiosInstance.post(
        endpoints.gateway.healthCheck(id),
        {},
      );
      return data.result;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agentcc-gateways"] });
      enqueueSnackbar("Health check complete", { variant: "success" });
    },
    onError: () => {
      enqueueSnackbar("Health check failed", { variant: "error" });
    },
  });

  const handleReloadConfig = useCallback(() => {
    reloadMutation.mutate(gatewayId, {
      onSuccess: () => {
        enqueueSnackbar("Configuration reloaded", { variant: "success" });
      },
      onError: () => {
        enqueueSnackbar("Failed to reload config", { variant: "error" });
      },
    });
  }, [gatewayId, reloadMutation]);

  if (gwLoading || configLoading) {
    return (
      <Box p={3}>
        <Stack direction="row" justifyContent="space-between" mb={3}>
          <Skeleton width={220} height={40} />
          <Stack direction="row" spacing={1}>
            <Skeleton width={100} height={36} variant="rounded" />
            <Skeleton width={120} height={36} variant="rounded" />
            <Skeleton width={100} height={36} variant="rounded" />
          </Stack>
        </Stack>
        <Skeleton width="40%" height={40} sx={{ mb: 3 }} />
        <Stack spacing={2}>
          <Skeleton variant="rounded" width="100%" height={180} />
          <Skeleton variant="rounded" width="100%" height={180} />
          <Skeleton variant="rounded" width="100%" height={100} />
        </Stack>
      </Box>
    );
  }

  return (
    <Box p={3}>
      <SectionHeader
        icon={GATEWAY_ICONS.settings}
        title="Settings"
        subtitle="Gateway configuration, health checks, and administration"
        actions={[
          {
            label: healthCheckMutation.isPending
              ? "Checking..."
              : "Health Check",
            variant: "outlined",
            size: "small",
            onClick: () => healthCheckMutation.mutate(gatewayId),
            disabled: healthCheckMutation.isPending,
          },
          {
            label: reloadMutation.isPending ? "Reloading..." : "Reload Config",
            variant: "outlined",
            size: "small",
            icon: "mdi:refresh",
            onClick: handleReloadConfig,
            disabled: reloadMutation.isPending,
          },
        ]}
      />

      <Tabs
        value={tab}
        onChange={handleTabChange}
        sx={{ mb: 3, borderBottom: 1, borderColor: "divider" }}
      >
        <Tab label="Settings" />
        <Tab label="Org Config" />
        <Tab label="Batch Jobs" />
        <Tab label="Full Config (Read-Only)" />
      </Tabs>

      {tab === 0 && <SettingsTab config={config} gateway={gateway} />}
      {tab === 1 && <OrgConfigSection />}
      {tab === 2 && <BatchJobsTab config={config} gatewayId={gatewayId} />}
      {tab === 3 && <FullConfigTab config={config} />}
    </Box>
  );
};

const InfoLine = ({ label, value }) => (
  <Stack direction="row" spacing={2}>
    <Typography
      variant="body2"
      color="text.secondary"
      sx={{ minWidth: 150, flexShrink: 0 }}
    >
      {label}
    </Typography>
    <Typography variant="body2" sx={{ wordBreak: "break-all" }}>
      {String(value)}
    </Typography>
  </Stack>
);

export default GatewaySettingsSection;
