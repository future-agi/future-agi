import { formatExactMoney } from "src/sections/common/format/money";
/* eslint-disable react/prop-types */
// Settings > Batch jobs: submit a batch of chat completions through the
// gateway as the organization, follow each job and read its results. The
// Batch API card states what the gateway reports about its batch runner
// (GET /-/config batch block). Submitting and cancelling a batch spend the
// organization's credits on its providers, so only an Owner or Admin can;
// reading stays open.
import React, { useCallback, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Collapse,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Grid,
  IconButton,
  LinearProgress,
  Skeleton,
  Slider,
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
} from "@mui/material";
import Iconify from "src/components/iconify";
import GatewayAdminOnly from "../../components/GatewayAdminOnly";
import ConfirmDialog from "../../components/ConfirmDialog";
import { useGatewayAdmin } from "../../hooks/useGatewayAdmin";
import {
  useSubmitBatch,
  useBatchStatus,
  useCancelBatch,
} from "../hooks/useBatchJobs";
import {
  NOT_REPORTED,
  batchPipelineNote,
  formatBytes,
  isBlock,
} from "../gatewayReport";
import { GATEWAY_MONO } from "../../utils/fonts";

const NotReported = ({ what }) => (
  <Typography variant="body2" color="text.secondary">
    This gateway did not report its {what} settings.
  </Typography>
);

const SAMPLE_REQUEST = {
  model: "gpt-4o-mini",
  messages: [{ role: "user", content: "Say hello in one sentence." }],
};

const BatchJobsTab = ({ config, gatewayId }) => {
  // The gateway's batch facts (GET /-/config batch block).
  const batchConfig = isBlock(config?.batch) ? config.batch : null;
  const [submitOpen, setSubmitOpen] = useState(false);
  const [requestsJson, setRequestsJson] = useState(
    JSON.stringify([SAMPLE_REQUEST], null, 2),
  );
  const [maxConcurrency, setMaxConcurrency] = useState(5);
  const [jsonError, setJsonError] = useState("");
  const [trackedJobs, setTrackedJobs] = useState([]);
  const [selectedJob, setSelectedJob] = useState(null);
  const [cancelTarget, setCancelTarget] = useState(null);
  // Keep the target copy visible until the closing dialog has exited.
  const [cancelDisplayTarget, setCancelDisplayTarget] = useState(null);

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
                color="primary"
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
                  ? 'No batch jobs submitted. Click "Submit Batch Job" to get started.'
                  : "No batch jobs submitted. An Owner or Admin can submit one."}
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
                      onCancel={(batchId) => {
                        setCancelDisplayTarget(batchId);
                        setCancelTarget(batchId);
                      }}
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
                    fontFamily: GATEWAY_MONO,
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
              {batchPipelineNote(config)}
            </Alert>
          </Stack>
        </DialogContent>
        <DialogActions sx={{ px: 3, py: 2 }}>
          <Button onClick={() => setSubmitOpen(false)}>Cancel</Button>
          <Button
            variant="contained"
            color="primary"
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

      <ConfirmDialog
        open={Boolean(cancelTarget)}
        onClose={() => setCancelTarget(null)}
        onConfirm={() => {
          handleCancel(cancelTarget);
          setCancelTarget(null);
        }}
        onExited={() => setCancelDisplayTarget(null)}
        title={`Cancel batch job ${cancelDisplayTarget}?`}
        message={`Cancelling batch job ${cancelDisplayTarget} cancels queued requests and asks requests in progress to stop. Requests already sent may still finish and incur charges. Cancellation cannot be undone. Cancelling does not delete completed results.`}
        confirmLabel="Cancel job"
        cancelLabel="Keep running"
        confirmColor="error"
        isLoading={cancelBatch.isPending}
      />

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
        <Typography
          variant="body2"
          fontFamily={GATEWAY_MONO}
          fontSize="0.75rem"
        >
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
          <IconButton
            onClick={onClose}
            size="small"
            aria-label="Close batch details"
          >
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
                      ? formatExactMoney(summary.total_cost, {
                          minimumFractionDigits: 4,
                        })
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
                      <TableCell>Response</TableCell>
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
                              ? formatExactMoney(r.cost, {
                                  minimumFractionDigits: 4,
                                })
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
                                  fontFamily: GATEWAY_MONO,
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

export default BatchJobsTab;
