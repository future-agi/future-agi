import { formatExactMoney } from "src/sections/common/format/money";
/* eslint-disable react/prop-types */
// Settings > Batch jobs: submit a batch of chat completions through the
// gateway as the organization, follow each job and read its results. The
// Batch API card states what the gateway reports about its batch runner
// (GET /-/config batch block). Submitting and cancelling a batch spend the
// organization's credits on its providers, so only an Owner or Admin can;
// reading stays open.
import React, { useCallback, useEffect, useId, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
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
import { useOrganization } from "src/contexts/OrganizationContext";
import GatewayAdminOnly from "../../components/GatewayAdminOnly";
import ConfirmDialog from "../../components/ConfirmDialog";
import { batchChargeKnown, batchTotalKnown } from "./batchCharge";
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
import CopyButton from "../ui/CopyButton";
import { batchResultsExport } from "./batchResultsExport";

const NotReported = ({ what }) => (
  <Typography variant="body2" color="text.secondary">
    This gateway did not report its {what} settings.
  </Typography>
);

// What a batch job is, said where the tab opens.
const BATCH_INTRO =
  "A batch job runs a list of chat completion requests through the gateway in one submission, as your organization. Follow each job here and read or export its results.";
// When this deployment has batch submission off (the gateway read's
// delegated_batch_available false; submit-batch refuses with 409 then), in
// the release's own words. Submit stays on screen, disabled; an existing job
// can still be opened and cancelled.
const BATCH_SUBMIT_OFF = "Batch submission is not available in this release.";

const SAMPLE_REQUEST = {
  model: "gpt-4o-mini",
  messages: [{ role: "user", content: "Say hello in one sentence." }],
};

// The cards pad like SettingsPanel, the panel the other Gateway settings
// tabs use (16 px on a phone, 24 px from 600 px up), so switching tabs does
// not shift the content sideways. The last-child bottom padding the theme
// sets is matched too, or it would win over the sx padding.
const CARD_PADDING = {
  p: { xs: 2, sm: 3 },
  "&:last-child": { pb: { xs: 2, sm: 3 } },
};

// There is no list-jobs API. Jobs submitted here are kept for this browser
// tab, per organization and gateway (every org's gateway id is "default"),
// so switching settings tabs or reloading does not lose them. Without a known
// organization the list stays in this view only.
const trackedJobsKey = (orgId, gatewayId) =>
  orgId && gatewayId
    ? `gateway-batch-jobs:${JSON.stringify([orgId, gatewayId])}`
    : null;

const readTrackedJobs = (key) => {
  if (!key) return [];
  try {
    const stored = JSON.parse(window.sessionStorage.getItem(key) || "[]");
    return Array.isArray(stored)
      ? stored.filter(
          (job) => typeof job?.batch_id === "string" && job.batch_id !== "",
        )
      : [];
  } catch {
    return [];
  }
};

const writeTrackedJobs = (key, jobs) => {
  if (!key) return;
  try {
    window.sessionStorage.setItem(key, JSON.stringify(jobs));
  } catch {
    // Storage is unavailable or full: the list stays in this view.
  }
};

// Submission history and pending dialogs belong only to this org/gateway.
const BatchJobsTab = (props) => {
  const { currentOrganizationId } = useOrganization();
  const orgId = currentOrganizationId || null;
  return (
    <BatchJobsView
      key={JSON.stringify([orgId, props.gatewayId ?? null])}
      storageKey={trackedJobsKey(orgId, props.gatewayId)}
      {...props}
    />
  );
};

const BatchJobsView = ({
  config,
  configLoading,
  configError,
  onRetryConfig,
  gatewayId,
  storageKey,
  submissionAvailable = true,
}) => {
  const submitOffId = useId();
  const location = useLocation();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const selectedJob = searchParams.get("batch_id") || null;
  const [jobIdInput, setJobIdInput] = useState("");

  const openJob = (batchId) => {
    const next = new URLSearchParams(location.search);
    next.set("tab", "batch-jobs");
    next.set("batch_id", batchId);
    navigate(
      { pathname: location.pathname, search: `?${next}`, hash: location.hash },
      {
        state: {
          ...location.state,
          batchRecovery: { gatewayId, batchId },
        },
      },
    );
  };

  const closeJob = () => {
    // Locally opened details have a prior entry (including its selection).
    // A pasted link has no such entry; clear only batch_id in place instead.
    const opened = location.state?.batchRecovery;
    if (opened?.gatewayId === gatewayId && opened?.batchId === selectedJob) {
      navigate(-1);
      return;
    }
    const next = new URLSearchParams(location.search);
    next.delete("batch_id");
    const state = { ...location.state };
    delete state.batchRecovery;
    navigate(
      { pathname: location.pathname, search: `?${next}`, hash: location.hash },
      { replace: true, state },
    );
  };

  // The gateway's batch facts (GET /-/config batch block).
  const batchConfig = isBlock(config?.batch) ? config.batch : null;
  const [submitOpen, setSubmitOpen] = useState(false);
  const [requestsJson, setRequestsJson] = useState(
    JSON.stringify([SAMPLE_REQUEST], null, 2),
  );
  const [maxConcurrency, setMaxConcurrency] = useState(5);
  const [jsonError, setJsonError] = useState("");
  const [trackedJobs, setTrackedJobs] = useState(() =>
    readTrackedJobs(storageKey),
  );
  const [cancelTarget, setCancelTarget] = useState(null);
  // Keep the target copy visible until the closing dialog has exited.
  const [cancelDisplayTarget, setCancelDisplayTarget] = useState(null);

  const submitBatch = useSubmitBatch();
  const cancelBatch = useCancelBatch();
  // Submitting or cancelling a batch spends the org's credits on its
  // providers: Owner or Admin only (useGatewayAdmin). Reading stays open.
  const { canEdit } = useGatewayAdmin();

  useEffect(() => {
    writeTrackedJobs(storageKey, trackedJobs);
  }, [storageKey, trackedJobs]);

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
              ...prev.filter((job) => job.batch_id !== batchId),
            ]);
          }
          setSubmitOpen(false);
          setRequestsJson(JSON.stringify([SAMPLE_REQUEST], null, 2));
        },
      },
    );
  };

  const requestCancel = (batchId) => {
    if (!canEdit || !gatewayId || cancelBatch.isPending) return;
    setCancelDisplayTarget(batchId);
    setCancelTarget(batchId);
  };
  const handleCancel = (batchId) => {
    if (!canEdit || !gatewayId || !batchId || cancelBatch.isPending) return;
    cancelBatch.mutate({ gatewayId, batchId });
  };

  return (
    <Stack spacing={2}>
      {/* Batch API Config */}
      <Card>
        <CardContent sx={CARD_PADDING}>
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
                disabled={!canEdit || !submissionAvailable}
                aria-describedby={submissionAvailable ? undefined : submitOffId}
              >
                Submit Batch Job
              </Button>
            </GatewayAdminOnly>
          </Stack>
          <Typography variant="body2" color="text.secondary" mb={2}>
            {BATCH_INTRO}
          </Typography>
          {!submissionAvailable && (
            <Alert severity="info" sx={{ mb: 2 }}>
              <span id={submitOffId}>{BATCH_SUBMIT_OFF}</span>
            </Alert>
          )}
          {configLoading ? (
            <Typography role="status" variant="body2">Loading batch settings…</Typography>
          ) : configError ? (
            <Alert severity="warning" action={
              <Button color="inherit" size="small" onClick={() => onRetryConfig?.()}>
                Retry batch settings
              </Button>
            }>
              Batch settings are unavailable. You can still open a job by ID.
            </Alert>
          ) : batchConfig ? (
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

      <Card>
        <CardContent sx={CARD_PADDING}>
          <Stack spacing={2}>
            <Box>
              <Typography variant="h6">Open job by ID</Typography>
              <Typography variant="body2" color="text.secondary">
                Reopen a job you are authorized to read, without submitting
                it again.
              </Typography>
            </Box>
            <Stack
              component="form"
              direction={{ xs: "column", sm: "row" }}
              alignItems={{ sm: "flex-end" }}
              spacing={1}
              onSubmit={(event) => {
                event.preventDefault();
                if (gatewayId && jobIdInput.trim()) openJob(jobIdInput.trim());
              }}
            >
              <TextField
                label="Batch job ID"
                value={jobIdInput}
                onChange={(event) => setJobIdInput(event.target.value)}
                size="small"
                fullWidth
              />
              <Button
                type="submit"
                variant="outlined"
                disabled={!gatewayId || !jobIdInput.trim()}
                sx={{ flexShrink: 0 }}
              >
                Open job
              </Button>
            </Stack>
          </Stack>
        </CardContent>
      </Card>

      {/* Jobs submitted in this browser tab, not an all-jobs API. */}
      <Card>
        <CardContent sx={CARD_PADDING}>
          <Typography variant="h6" mb={1}>
            Jobs submitted in this view
          </Typography>
          <Typography variant="body2" color="text.secondary" mb={2}>
            This list keeps jobs submitted in this browser tab. Copy a job&apos;s
            ID or link to reopen it later.
          </Typography>
          {trackedJobs.length === 0 ? (
            <Stack alignItems="center" spacing={1} sx={{ py: 3 }}>
              <Iconify
                icon="mdi:tray-arrow-up"
                width={40}
                sx={{ color: "text.disabled" }}
              />
              <Typography variant="body2" color="text.secondary">
                {!submissionAvailable
                  ? `No batch jobs submitted in this view. ${BATCH_SUBMIT_OFF.slice(0, -1)}; you can still open a job by its ID.`
                  : canEdit
                    ? 'No batch jobs submitted in this view. Click "Submit Batch Job" to get started.'
                    : "No batch jobs submitted in this view. An Owner or Admin can submit one."}
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
                      onCancel={requestCancel}
                      canCancel={canEdit}
                      onViewDetails={openJob}
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
        open={Boolean(cancelTarget) && canEdit}
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
          key={JSON.stringify([gatewayId, selectedJob])}
          gatewayId={gatewayId}
          batchId={selectedJob}
          onClose={closeJob}
          onCancel={requestCancel}
          canCancel={canEdit}
          cancelling={cancelBatch.isPending}
        />
      )}
    </Stack>
  );
};

// ---------------------------------------------------------------------------
// Batch Job Row — reads once and on Check status; only an open detail polls
// status, so the row says when its status was last checked
// ---------------------------------------------------------------------------

const BatchJobRow = ({
  job,
  gatewayId,
  onCancel,
  canCancel,
  onViewDetails,
}) => {
  const { data, isLoading, error, dataUpdatedAt, errorUpdatedAt, refetch } =
    useBatchStatus(gatewayId, job.batch_id, {
      poll: false,
    });
  const checkedAt = error ? errorUpdatedAt : dataUpdatedAt;
  const status = error ? null : data;
  const batchStatus = error
    ? "Read failed"
    : status?.status || (isLoading ? "Loading" : "Not reported");
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
        <Stack direction="row" alignItems="center" spacing={0.5}>
          <Typography
            variant="body2"
            fontFamily={GATEWAY_MONO}
            fontSize="0.75rem"
            sx={{ overflowWrap: "anywhere" }}
          >
            {job.batch_id}
          </Typography>
          <CopyButton text={job.batch_id} label="Copy batch ID" />
        </Stack>
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
          {checkedAt > 0 && (
            <Typography variant="caption" color="text.secondary">
              {`Checked ${new Date(checkedAt).toLocaleTimeString()}`}
            </Typography>
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
          <Tooltip title="Check status">
            <IconButton
              size="small"
              onClick={() => refetch({ cancelRefetch: false })}
            >
              <Iconify icon="mdi:refresh" width={18} />
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

const batchReadError = (error) => {
  const status = error?.statusCode ?? error?.response?.status;
  if (status === 404) return "Batch job not found or not accessible.";
  if (status === 403) return "You do not have access to this batch job.";
  if (status === 401) return "Sign in again to read this batch job.";
  return "Couldn't load this batch job. Its completion could not be checked.";
};

const BatchDetailDialog = ({ gatewayId, batchId, onClose, onCancel, canCancel, cancelling }) => {
  const { data: batch, isLoading, error, isFetching, refetch } = useBatchStatus(
    gatewayId,
    batchId,
  );
  const location = useLocation();
  const [expandedIdx, setExpandedIdx] = useState(null);
  const [copyStatus, setCopyStatus] = useState("");
  const [exportStatus, setExportStatus] = useState("");

  const exportResults = async (format) => {
    if (error || isLoading || !gatewayId) return;
    const text = batchResultsExport(batch, batchId, format);
    if (!text) return;
    setExportStatus("");
    try {
      if (format === "copy") {
        await navigator.clipboard.writeText(text);
        setExportStatus("copied");
        return;
      }
      const url = URL.createObjectURL(new Blob([text], {
        type: format === "jsonl" ? "application/x-ndjson" : "application/json",
      }));
      const link = document.createElement("a");
      try {
        link.href = url;
        link.download = `batch-${batchId.replace(/[^a-zA-Z0-9_-]/g, "_")}-snapshot.${format}`;
        document.body.appendChild(link);
        link.click();
      } finally {
        link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      }
    } catch {
      setExportStatus(format === "copy" ? "copy-failed" : "download-failed");
    }
  };

  const copyLink = async () => {
    const link = new URL(
      location.pathname + location.search + location.hash,
      window.location.origin,
    );
    link.searchParams.set("tab", "batch-jobs");
    link.searchParams.set("batch_id", batchId);
    try {
      await navigator.clipboard.writeText(link.toString());
      setCopyStatus("copied");
    } catch {
      setCopyStatus("failed");
    }
  };

  const summary = batch?.summary;
  const results = Array.isArray(batch?.results) ? batch.results : [];
  const isTerminal = ["completed", "failed", "cancelled"].includes(batch?.status);
  const unknownCompletion = batch?.outcome === "unknown";
  const isRunning =
    !unknownCompletion && ["pending", "processing"].includes(batch?.status);
  const reportedError = typeof batch?.error === "string" ? batch.error : "";
  const canRetry =
    error ||
    !batch?.status ||
    unknownCompletion ||
    reportedError ||
    batch?.status === "failed";
  const hasProgress =
    batch?.total > 0 && summary?.completed != null && summary?.failed != null;

  return (
    <Dialog
      open
      onClose={onClose}
      maxWidth="lg"
      fullWidth
      aria-labelledby="batch-details-title"
    >
      <DialogTitle component="div">
        <Stack
          direction="row"
          justifyContent="space-between"
          alignItems="center"
        >
          <Stack direction="row" alignItems="center" spacing={1}>
            <Typography variant="h6" component="h2" id="batch-details-title">
              Batch Details
            </Typography>
            <Chip
              label={
                error
                  ? "Read failed"
                  : batch?.status || (isLoading ? "loading" : "Not reported")
              }
              size="small"
              color={
                error || batch?.status === "failed"
                  ? "error"
                  : batch?.status === "completed"
                    ? "success"
                    : batch?.status === "cancelled"
                      ? "warning"
                      : "default"
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
        <Typography
          variant="body2"
          fontFamily={GATEWAY_MONO}
          sx={{ mb: 2, overflowWrap: "anywhere" }}
        >
          {batchId}
        </Typography>
        {copyStatus === "copied" && (
          <Typography role="status" variant="body2" sx={{ mb: 2 }}>
            Job link copied
          </Typography>
        )}
        {copyStatus === "failed" && (
          <Alert severity="warning" sx={{ mb: 2 }}>
            Could not copy the job link. Copy the address from your browser instead.
          </Alert>
        )}
        {!gatewayId ? (
          <Alert severity="info">Select a gateway to read this batch job.</Alert>
        ) : error ? (
          <Alert severity="error">
            {batchReadError(error)} Retry reads status only; it does not submit
            or cancel work.
          </Alert>
        ) : isLoading ? (
          <Stack spacing={2} sx={{ py: 3 }} role="status">
            <Typography variant="body2">Loading batch job…</Typography>
            <Skeleton variant="rounded" height={60} />
            <Skeleton variant="rounded" height={200} />
          </Stack>
        ) : !batch ? (
          <Alert severity="warning">Batch status was not reported. Retry to read it again.</Alert>
        ) : (
          <Stack spacing={2}>
            {unknownCompletion ? (
              <Alert severity="warning">
                Completion unknown.{" "}
                {reportedError || "The gateway did not report whether this work completed."}{" "}
                Reopening or retrying this status read does not resubmit work.
              </Alert>
            ) : batch.status === "failed" ? (
              <Alert severity="error">Batch failed. {reportedError}</Alert>
            ) : reportedError ? (
              <Alert severity="warning">{reportedError}</Alert>
            ) : !batch.status ? (
              <Alert severity="warning">Batch status was not reported. Retry to read it again.</Alert>
            ) : null}
            {/* Show only reported counts; missing summaries are not zeroes. */}
            <Grid container spacing={2}>
              <Grid item xs={6} sm={2}>
                <StatBox label="Total" value={batch.total ?? "Not reported"} />
              </Grid>
              <Grid item xs={6} sm={2}>
                <StatBox label="Completed" value={summary?.completed ?? "Not reported"} />
              </Grid>
              <Grid item xs={6} sm={2}>
                <StatBox label="Failed" value={summary?.failed ?? "Not reported"} />
              </Grid>
              <Grid item xs={6} sm={2}>
                <StatBox label="Cancelled" value={summary?.cancelled ?? "Not reported"} />
              </Grid>
              <Grid item xs={6} sm={2}>
                <StatBox
                  label="Total Cost"
                  value={
                    batchTotalKnown(batch)
                      ? formatExactMoney(summary.total_cost, {
                          minimumFractionDigits: 4,
                        })
                      : "Cost not available"
                  }
                />
              </Grid>
              <Grid item xs={6} sm={2}>
                <StatBox
                  label="Tokens"
                  value={
                    summary?.total_input_tokens != null && summary?.total_output_tokens != null
                      ? summary.total_input_tokens + summary.total_output_tokens
                      : "Not reported"
                  }
                />
              </Grid>
            </Grid>

            {isRunning && (
              <LinearProgress
                sx={{ borderRadius: 2 }}
                variant={hasProgress ? "determinate" : "indeterminate"}
                value={hasProgress ? ((summary.completed + summary.failed) / batch.total) * 100 : 0}
              />
            )}

            {results.length > 0 && (
              <Stack spacing={1}>
                <Typography variant="body2" color="text.secondary">
                  {isTerminal && !unknownCompletion ? "Terminal job snapshot" : isRunning ? "Partial running snapshot" : "Partial snapshot"}: exports include only the {results.length} loaded results, not a guarantee of complete output.
                  {" "}JSONL starts with snapshot metadata, then one result per line.
                </Typography>
                <Stack direction="row" gap={1} flexWrap="wrap">
                  <Button onClick={() => exportResults("json")}>Download JSON</Button>
                  <Button onClick={() => exportResults("jsonl")}>Download JSONL</Button>
                  <Button onClick={() => exportResults("copy")}>Copy results</Button>
                </Stack>
                {exportStatus === "copied" && <Typography role="status" variant="body2">Results copied</Typography>}
                {exportStatus === "copy-failed" && <Alert severity="warning">Could not copy results. Try downloading instead.</Alert>}
                {exportStatus === "download-failed" && <Alert severity="warning">Could not download results. Try copying instead.</Alert>}
              </Stack>
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
                            {batchChargeKnown(r.cost, r.cost_known)
                              ? formatExactMoney(r.cost, {
                                  minimumFractionDigits: 4,
                                })
                              : "Not available"}
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
        <Button onClick={copyLink}>Copy job link</Button>
        {!error && !isLoading && isRunning && (
          <GatewayAdminOnly allowed={canCancel}>
            <Button color="error" disabled={!canCancel || !gatewayId || cancelling} onClick={() => onCancel(batchId)}>
              Cancel job
            </Button>
          </GatewayAdminOnly>
        )}
        {canRetry && (
          <Button onClick={() => refetch()} disabled={!gatewayId || isFetching}>
            Retry
          </Button>
        )}
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
      {value}
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
