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
import GatewayAdminOnly from "../components/GatewayAdminOnly";
import { useGatewayAdmin } from "../hooks/useGatewayAdmin";

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

// Every value on these cards is what the gateway reports in GET /-/config,
// passed through by the config action. A value
// the gateway does not report reads "Not reported", never a default.
const NOT_REPORTED = "Not reported";

const isBlock = (value) =>
  value != null && typeof value === "object" && !Array.isArray(value);

function formatMs(ms) {
  if (typeof ms !== "number" || !Number.isFinite(ms)) return NOT_REPORTED;
  if (Math.abs(ms) < 1000) return `${ms} ms`;
  const seconds = ms / 1000;
  return `${Number.isInteger(seconds) ? seconds : seconds.toFixed(1)} s`;
}

function formatBytes(bytes) {
  if (typeof bytes !== "number" || !Number.isFinite(bytes)) return NOT_REPORTED;
  const MiB = 1024 * 1024;
  if (bytes >= MiB && bytes % MiB === 0) return `${bytes / MiB} MiB`;
  if (bytes >= 1024 && bytes % 1024 === 0) return `${bytes / 1024} KiB`;
  return `${bytes} bytes`;
}

const onOff = (value, on, off) =>
  typeof value === "boolean" ? (value ? on : off) : NOT_REPORTED;

function lastHealthCheckText(config, gateway) {
  const fromConfig = config?.last_health_check;
  const at = fromConfig ?? gateway?.lastHealthCheck ?? null;
  if (!at) return "No check recorded";
  const outcome = fromConfig
    ? config?.last_health_check_status
    : gateway?.last_health_check_status ?? gateway?.lastHealthCheckStatus;
  const when = new Date(at).toLocaleString();
  return outcome ? `${when} (${outcome})` : when;
}

function enforcedSectionsText(enforced) {
  if (!Array.isArray(enforced)) return "Not reported (enforcement unknown)";
  return enforced.length > 0 ? enforced.join(", ") : "None on this gateway";
}

// "section: reason" for each org section the gateway says it does not
// enforce (org_sections_not_enforced), or null when it names none.
function notEnforcedSectionsText(reasons) {
  if (!reasons || typeof reasons !== "object" || Array.isArray(reasons)) {
    return null;
  }
  const entries = Object.entries(reasons).filter(
    ([, reason]) => typeof reason === "string" && reason,
  );
  if (entries.length === 0) return null;
  return entries.map(([section, reason]) => `${section}: ${reason}`).join("; ");
}

const NotReported = ({ what }) => (
  <Typography variant="body2" color="text.secondary">
    This gateway did not report its {what} settings.
  </Typography>
);

const SettingsTab = ({ config, gateway }) => {
  const server = isBlock(config?.server) ? config.server : null;
  const logging = isBlock(config?.logging) ? config.logging : null;
  const costTracking = config?.cost_tracking ?? config?.costTracking ?? {};

  return (
    <Stack spacing={2}>
      {/* Server config */}
      <Card>
        <CardContent>
          <Typography variant="h6" mb={2}>
            Server Configuration
          </Typography>
          {server ? (
            <Stack spacing={1}>
              <InfoLine
                label="Host"
                value={
                  typeof server.host === "string"
                    ? server.host || "All interfaces"
                    : NOT_REPORTED
                }
              />
              <InfoLine label="Port" value={server.port ?? NOT_REPORTED} />
              <InfoLine
                label="Read Timeout"
                value={formatMs(server.read_timeout_ms)}
              />
              <InfoLine
                label="Write Timeout"
                value={formatMs(server.write_timeout_ms)}
              />
              <InfoLine
                label="Idle Timeout"
                value={formatMs(server.idle_timeout_ms)}
              />
              <InfoLine
                label="Max Request Time"
                value={formatMs(server.max_request_timeout_ms)}
              />
            </Stack>
          ) : (
            <NotReported what="server" />
          )}
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
              value={lastHealthCheckText(config, gateway)}
            />
            <InfoLine
              label="Org Sections Enforced"
              value={enforcedSectionsText(config?.org_sections_enforced)}
            />
            {notEnforcedSectionsText(config?.org_sections_not_enforced) && (
              <InfoLine
                label="Org Sections Not Enforced"
                value={notEnforcedSectionsText(
                  config?.org_sections_not_enforced,
                )}
              />
            )}
          </Stack>
        </CardContent>
      </Card>

      {/* Logging */}
      <Card>
        <CardContent>
          <Typography variant="h6" mb={2}>
            Logging
          </Typography>
          {logging ? (
            <Stack spacing={1}>
              <InfoLine label="Level" value={logging.level || NOT_REPORTED} />
              <InfoLine
                label="Request Logging"
                value={onOff(logging.request_logging, "Enabled", "Disabled")}
              />
              <InfoLine
                label="Include Bodies"
                value={onOff(logging.include_bodies, "Yes", "No")}
              />
              <InfoLine
                label="Content Mode"
                value={logging.content_mode || NOT_REPORTED}
              />
            </Stack>
          ) : (
            <NotReported what="logging" />
          )}
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
  // The gateway's batch facts (GET /-/config).
  const batchConfig = isBlock(config?.batch) ? config.batch : null;
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
  // Submitting or cancelling a batch spends the org's credits on its
  // providers: Owner or Admin only (useGatewayAdmin). Reading stays open.
  const { canEdit } = useGatewayAdmin();

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
            <GatewayAdminOnly allowed={canEdit}>
              <Button
                variant="contained"
                size="small"
                startIcon={<Iconify icon="mdi:plus" width={18} />}
                onClick={() => setSubmitOpen(true)}
                disabled={!canEdit}
              >
                Submit Batch Job
              </Button>
            </GatewayAdminOnly>
          </Stack>
          {batchConfig ? (
            <Stack spacing={1}>
              <InfoLine
                label="Storage"
                value={
                  batchConfig.storage === "memory"
                    ? "In this gateway node's memory: a restart loses its batches"
                    : batchConfig.storage || NOT_REPORTED
                }
              />
              <InfoLine
                label="Concurrency"
                value={
                  typeof batchConfig.default_max_concurrency === "number"
                    ? `Set per submission (${batchConfig.default_max_concurrency} when a submission sets none)`
                    : "Set per submission"
                }
              />
              <InfoLine
                label="Largest Submission"
                value={formatBytes(batchConfig.max_body_bytes)}
              />
            </Stack>
          ) : (
            <NotReported what="batch" />
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
                {canEdit
                  ? 'No batch jobs submitted yet. Click "Submit Batch Job" to get started.'
                  : "No batch jobs submitted yet. An Owner or Admin can submit one."}
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
                      canCancel={canEdit}
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

const BatchJobRow = ({
  job,
  gatewayId,
  onCancel,
  canCancel,
  onViewDetails,
}) => {
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
          {(batchStatus === "pending" || batchStatus === "processing") &&
            (canCancel ? (
              <Tooltip title="Cancel">
                <IconButton
                  size="small"
                  color="error"
                  onClick={() => onCancel(job.batch_id)}
                >
                  <Iconify icon="mdi:close-circle-outline" width={18} />
                </IconButton>
              </Tooltip>
            ) : (
              <GatewayAdminOnly allowed={false}>
                <IconButton
                  size="small"
                  color="error"
                  aria-label="Cancel"
                  disabled
                >
                  <Iconify icon="mdi:close-circle-outline" width={18} />
                </IconButton>
              </GatewayAdminOnly>
            ))}
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

  const {
    data: config,
    isLoading: configLoading,
    refetch: refetchConfig,
  } = useGatewayConfig(gatewayId);
  const reloadMutation = useReloadConfig();

  // A check records its time and outcome either way, so both the gateway
  // list and the config are re-read after it.
  const refreshAfterHealthCheck = () => {
    queryClient.invalidateQueries({ queryKey: ["agentcc-gateways"] });
    refetchConfig?.();
  };
  const healthCheckMutation = useMutation({
    mutationFn: async (id) => {
      const { data } = await axiosInstance.post(
        endpoints.gateway.healthCheck(id),
        {},
      );
      return data.result;
    },
    onSuccess: () => {
      refreshAfterHealthCheck();
      enqueueSnackbar("Health check complete", { variant: "success" });
    },
    onError: () => {
      refreshAfterHealthCheck();
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
