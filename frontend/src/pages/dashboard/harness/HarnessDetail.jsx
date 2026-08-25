import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Divider,
  IconButton,
  LinearProgress,
  Paper,
  Stack,
  Typography,
} from "@mui/material";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { formatDistanceToNow } from "date-fns";
import { Helmet } from "react-helmet-async";
import { useNavigate, useParams } from "react-router-dom";

import Iconify from "src/components/iconify";
import StatusChip from "src/components/custom-status-chip/CustomStatusChip";
import CustomTooltip from "src/components/tooltip";
import ConfirmDialog from "src/components/custom-dialog/confirm-dialog";
import EnvironmentSwitcher from "src/components/harness/EnvironmentSwitcher";
import {
  cancelHarnessJob,
  getHarnessJob,
  listHarnessJobs,
} from "src/api/harness/harness";
import { paths } from "src/routes/paths";

import {
  completedStageCount,
  errorMessage,
  eventTime,
  runElapsed,
  shortDuration,
  shortRunId,
  STAGE_STATE,
  stageElapsed,
  stageState,
  eventMessage,
  jobProgress,
  readable,
  stages,
  stageStatus,
  terminalStages,
} from "./harnessShared";

export default function HarnessDetail() {
  const { jobId } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [clock, setClock] = useState(Date.now());
  const [cancelError, setCancelError] = useState("");
  const [confirmingCancel, setConfirmingCancel] = useState(false);
  const [stagesOpen, setStagesOpen] = useState(false);
  const [copiedId, setCopiedId] = useState(false);

  const {
    data: current,
    isPending,
    error,
  } = useQuery({
    queryKey: ["harness-job", jobId],
    queryFn: () => getHarnessJob(jobId),
    enabled: Boolean(jobId),
    // Poll only while the run can still change; a terminal job would otherwise be refetched
    // forever for a payload that never moves again.
    refetchInterval: (query) =>
      terminalStages.has(query.state.data?.status?.stage) ? false : 2000,
    meta: { errorHandled: true },
  });

  // Shares the list page's key, so arriving from the list reuses what is already cached
  // rather than issuing a second request for the same array.
  const { data: listData } = useQuery({
    queryKey: ["harness-jobs"],
    queryFn: listHarnessJobs,
    meta: { errorHandled: true },
  });
  const jobs = useMemo(
    () => (Array.isArray(listData) ? listData : []),
    [listData],
  );

  const { mutate: cancel, isPending: canceling } = useMutation({
    mutationFn: () => {
      // A missing id would otherwise throw inside axios and never reach the network,
      // which looks identical to the button doing nothing at all.
      if (!jobId) throw new Error("No job id in the route; cannot cancel.");
      return cancelHarnessJob(jobId);
    },
    onMutate: () => {
      setCancelError("");
      setConfirmingCancel(false);
    },
    onSuccess: (value) => {
      queryClient.setQueryData(["harness-job", jobId], value);
      queryClient.invalidateQueries({ queryKey: ["harness-jobs"] });
    },
    onError: (requestError) => {
      // Surfaced in the page rather than swallowed: a cancel that fails quietly is
      // indistinguishable from a dead button.
      // eslint-disable-next-line no-console
      console.error("Cancel run failed", {
        jobId,
        statusCode: requestError?.statusCode,
        code: requestError?.code,
        detail: requestError?.detail,
        message: requestError?.message,
      });
      setCancelError(errorMessage(requestError));
    },
  });

  const status = current?.status;
  const progress = jobProgress(status);
  const isTerminal = terminalStages.has(status?.stage);

  // Only the finished prefix folds away, so the stage a run failed or stopped on is never
  // hidden by collapsing — it is the first row still on screen.
  const doneCount = completedStageCount(status, current?.events);
  const collapsible = doneCount > 0;
  const visibleStages =
    !collapsible || stagesOpen
      ? stages
      : stages.slice(doneCount);
  const elapsedLabel = shortDuration(
    runElapsed(current?.events, clock, isTerminal),
  );
  const stageLabel = shortDuration(stageElapsed(status, current?.events, clock));

  // The live seconds counter is a heartbeat: it says the run is still being watched. Once
  // the run is terminal there is nothing left to watch, so the tick stops and the label
  // settles into a plain relative time rather than counting up forever.
  useEffect(() => {
    if (isTerminal) return undefined;
    const timer = window.setInterval(() => setClock(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [isTerminal]);

  const updatedAt = status?.updated_at ? new Date(status.updated_at) : null;
  const secondsSinceUpdate = updatedAt
    ? Math.max(0, Math.floor((clock - updatedAt.getTime()) / 1000))
    : null;
  const updatedLabel = !updatedAt
    ? "Not updated yet"
    : isTerminal
      ? `Updated ${formatDistanceToNow(updatedAt, { addSuffix: true })}`
      : `Updated ${secondsSinceUpdate ?? 0}s ago`;

  const messages = useMemo(() => {
    const seen = new Set();
    return (current?.events || []).filter((event) => {
      const key = event.event_id || JSON.stringify(event);
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }, [current?.events]);

  const copyRunId = async () => {
    const runId = current?.job?.run_id;
    if (!runId) return;
    try {
      await navigator.clipboard.writeText(runId);
      setCopiedId(true);
      window.setTimeout(() => setCopiedId(false), 1500);
    } catch {
      // Clipboard access can be refused; the full id is still on the tooltip.
      setCopiedId(false);
    }
  };

  const goToCreate = () => navigate(paths.dashboard.simulate.harness.new);

  // The runner records the simulation it reported into once it has a destination, so a
  // finished run can link straight to its results. Runs that never reached the platform
  // carry no ids, and an unfinished one has nothing to show yet.
  const simulation = current?.platform;
  const canViewSimulation =
    status?.stage === "completed" &&
    Boolean(simulation?.run_test_id) &&
    Boolean(simulation?.test_execution_id);

  if (error) {
    return (
      <>
        <Helmet>
          <title>RL Environment | Future AGI</title>
        </Helmet>
        <Box sx={{ p: 2 }}>
          <Alert
            severity="error"
            variant="outlined"
            action={
              <Button
                color="inherit"
                size="small"
                onClick={() => navigate(paths.dashboard.simulate.harness.root)}
              >
                Back to environments
              </Button>
            }
          >
            {errorMessage(error)}
          </Alert>
        </Box>
      </>
    );
  }

  if (isPending || !current) {
    return (
      <Box
        sx={{
          height: "100vh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <CircularProgress />
      </Box>
    );
  }

  return (
    <>
      <Helmet>
        <title>
          {current.job?.metadata?.agent_name || "RL Environment"} | Future AGI
        </title>
      </Helmet>

      <Box
        sx={{
          height: "100vh",
          display: "flex",
          flexDirection: "column",
          minHeight: 0,
          overflow: "hidden",
          bgcolor: "background.default",
        }}
      >
        <Box
          sx={{
            px: 2,
            py: 1.5,
            flexShrink: 0,
            borderBottom: 1,
            borderColor: "divider",
          }}
        >
          <Stack
            direction="row"
            alignItems="center"
            justifyContent="space-between"
            spacing={2}
          >
            <Stack direction="row" alignItems="center" spacing={1.5}>
              <Button
                size="small"
                color="inherit"
                onClick={() => navigate(paths.dashboard.simulate.harness.root)}
                startIcon={<Iconify icon="eva:arrow-back-fill" width={18} />}
                sx={{ color: "text.secondary", px: 1, ml: -1, minWidth: 0 }}
              >
                All environments
              </Button>
              <EnvironmentSwitcher
                jobs={jobs}
                currentJobId={jobId}
                currentName={current.job?.metadata?.agent_name}
                onSelect={(nextId) =>
                  navigate(paths.dashboard.simulate.harness.detail(nextId))
                }
                onCreate={goToCreate}
              />
            </Stack>

            <Stack direction="row" alignItems="center" spacing={1.5}>
              <StatusChip
                label={readable(status?.stage)}
                status={stageStatus(status?.stage)}
                showIcon={false}
              />
              {canViewSimulation && (
                <Button
                  variant="contained"
                  size="small"
                  onClick={() =>
                    navigate(
                      paths.dashboard.simulate.testCallDetails(
                        simulation.run_test_id,
                        simulation.test_execution_id,
                      ),
                    )
                  }
                  endIcon={<Iconify icon="eva:arrow-forward-fill" width={18} />}
                >
                  View simulation
                </Button>
              )}
              {!terminalStages.has(status?.stage) && (
                <Button
                  color="inherit"
                  variant="outlined"
                  size="small"
                  disabled={canceling}
                  onClick={() => setConfirmingCancel(true)}
                  startIcon={
                    canceling ? (
                      <CircularProgress size={14} color="inherit" />
                    ) : (
                      <Iconify icon="solar:close-circle-linear" width={18} />
                    )
                  }
                >
                  {canceling ? "Canceling…" : "Cancel run"}
                </Button>
              )}
            </Stack>
          </Stack>
          {cancelError && (
            <Alert
              severity="error"
              variant="outlined"
              onClose={() => setCancelError("")}
              sx={{ mt: 1.5 }}
            >
              Could not cancel this run. {cancelError}
            </Alert>
          )}
        </Box>

        <Box
          sx={{
            flex: 1,
            minHeight: 0,
            display: "grid",
            gridTemplateColumns: {
              xs: "minmax(0, 1fr)",
              // A fr-based left column grew to a third of a wide viewport around ~200px of
              // content, which is what read as skewed. Fixed width; the feed takes the slack.
              md: "264px minmax(0, 1fr)",
            },
          }}
        >
          <Box
            sx={{
              p: 2,
              overflow: "auto",
              borderRight: 1,
              borderColor: "divider",
            }}
          >
            <Typography variant="h6">
              {current.job?.metadata?.agent_name}
            </Typography>
            <Stack direction="row" alignItems="center" spacing={0.5}>
              <Typography variant="caption" color="text.secondary" noWrap>
                {shortRunId(current.job?.run_id)}
              </Typography>
              <CustomTooltip
                show
                arrow
                size="small"
                title={copiedId ? "Copied" : current.job?.run_id || ""}
              >
                <IconButton
                  size="small"
                  aria-label="Copy run id"
                  onClick={copyRunId}
                  sx={{ p: 0.25 }}
                >
                  <Iconify
                    icon={copiedId ? "solar:check-read-linear" : "solar:copy-linear"}
                    width={14}
                    color={copiedId ? "accent.pass" : "text.secondary"}
                  />
                </IconButton>
              </CustomTooltip>
            </Stack>
            <LinearProgress
              variant="determinate"
              value={progress}
              color={status?.stage === "failed" ? "error" : "primary"}
              sx={{ mt: 1.5 }}
            />
            <Stack direction="row" justifyContent="space-between" mt={0.5}>
              <Typography variant="caption" color="text.secondary">
                {status?.completed_scenarios || 0} /{" "}
                {status?.total_scenarios || current.job?.scenario_count || 0}{" "}
                scenarios
              </Typography>
              {elapsedLabel && (
                <Typography variant="caption" color="text.secondary">
                  {elapsedLabel}
                </Typography>
              )}
            </Stack>
            {/* While a run is live the elapsed time above and "updated Ns ago" tick from the
                same moment and read as the same number. Only once it is terminal do they mean
                different things: how long it took, and when it finished. */}
            <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.25 }}>
              {isTerminal ? `${updatedLabel} · ` : ""}attempt{" "}
              {status?.attempt || 1}
            </Typography>
            <Divider sx={{ my: 2 }} />

            {/* Finished stages fold away: on a completed run the full list is fifteen ticks
                carrying no information. A run that ended badly always stays expanded. */}
            {collapsible && (
              <Button
                fullWidth
                color="inherit"
                onClick={() => setStagesOpen((open) => !open)}
                aria-expanded={stagesOpen}
                endIcon={
                  <Iconify
                    icon={stagesOpen ? "solar:alt-arrow-up-linear" : "solar:alt-arrow-down-linear"}
                    width={18}
                  />
                }
                sx={{
                  justifyContent: "flex-start",
                  gap: 1,
                  px: 1.25,
                  py: 0.875,
                  mb: 1.25,
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: "6px",
                  textTransform: "none",
                  "& .MuiButton-endIcon": { ml: "auto" },
                }}
              >
                <Iconify icon="solar:check-circle-bold" color="accent.pass" width={18} />
                <Typography variant="body2" color="text.secondary">
                  {doneCount} {doneCount === 1 ? "stage" : "stages"} complete
                </Typography>
              </Button>
            )}

            <Stack
              spacing={1.1}
              sx={{ pl: collapsible ? "11px" : 0 }}
            >
              {visibleStages.map((stage) => {
                const index = stages.indexOf(stage);
                const state = stageState(status, index, current?.events);
                const muted = state === STAGE_STATE.PENDING;
                const isCurrent = state === STAGE_STATE.ACTIVE;
                // accent.* are the mode-aware status tokens; success.main/error.main are
                // tuned for light only and fail contrast on the dark surface.
                const glyph = {
                  [STAGE_STATE.DONE]: {
                    icon: "solar:check-circle-bold",
                    color: "accent.pass",
                  },
                  [STAGE_STATE.ACTIVE]: {
                    icon: "solar:record-circle-bold",
                    color: "accent.info",
                  },
                  [STAGE_STATE.FAILED]: {
                    icon: "solar:close-circle-bold",
                    color: "accent.fail",
                  },
                  [STAGE_STATE.STOPPED]: {
                    icon: "solar:pause-circle-bold",
                    color: "text.secondary",
                  },
                  [STAGE_STATE.PENDING]: {
                    icon: "solar:record-circle-linear",
                    color: "text.secondary",
                  },
                }[state];
                return (
                  <Stack
                    key={stage}
                    direction="row"
                    spacing={1.2}
                    alignItems="flex-start"
                  >
                    <Iconify
                      icon={glyph.icon}
                      color={glyph.color}
                      width={18}
                      sx={{ opacity: muted ? 0.6 : 1, mt: "1px", flexShrink: 0 }}
                    />
                    <Box sx={{ minWidth: 0 }}>
                      <Typography
                        variant="body2"
                        color={
                          state === STAGE_STATE.FAILED
                            ? "accent.fail"
                            : muted
                              ? "text.secondary"
                              : "text.primary"
                        }
                        sx={{
                          opacity: muted ? 0.6 : 1,
                          fontWeight: isCurrent ? 600 : 400,
                        }}
                      >
                        {readable(stage)}
                      </Typography>
                      {/* Only six of the fifteen stages emit an event, so this is absent more
                          often than not. Omitted rather than zeroed: 0s would read as instant. */}
                      {isCurrent && stageLabel && (
                        <Typography variant="caption" color="text.secondary">
                          {stageLabel}
                        </Typography>
                      )}
                    </Box>
                  </Stack>
                );
              })}
            </Stack>
          </Box>

          <Box
            sx={{
              display: "flex",
              flexDirection: "column",
              minHeight: 0,
              overflow: "hidden",
            }}
          >
            <Box
              sx={{
                px: 2,
                pt: 2,
                pb: 1,
                flexShrink: 0,
                borderBottom: 1,
                borderColor: "divider",
              }}
            >
              <Typography variant="overline" color="text.secondary">
                Live harness activity
              </Typography>
            </Box>
            <Box sx={{ px: 2, py: 1.5, overflow: "auto", minHeight: 0 }}>
              <Stack spacing={1.5}>
                {current.credentials && (
                  <Paper variant="outlined" sx={{ p: 1.5 }}>
                    <Typography variant="subtitle2">
                      Runtime preflight
                    </Typography>
                    <Typography variant="body2" color="text.secondary">
                      {current.credentials.scanned_files} source files inspected
                      ·{" "}
                      {(current.credentials.detected_connectors || []).join(
                        ", ",
                      ) || "generic connector"}{" "}
                      · {current.credentials.requirements?.length || 0}{" "}
                      configuration requirements
                    </Typography>
                  </Paper>
                )}
                {messages.map((event) => (
                  <Paper
                    key={event.event_id}
                    variant="outlined"
                    sx={{ p: 1.5 }}
                  >
                    <Stack direction="row" justifyContent="space-between">
                      <Typography variant="caption" color="accent.brand">
                        {readable(event.payload?.stage || event.type)}
                      </Typography>
                      <Typography
                        variant="caption"
                        color="text.secondary"
                        title={event.wall_time || ""}
                      >
                        {eventTime(event.wall_time)}
                      </Typography>
                    </Stack>
                    <Typography variant="body2">
                      {eventMessage(event)}
                    </Typography>
                  </Paper>
                ))}
                {status?.failure && (
                  <Alert severity="error" variant="outlined">
                    <Typography variant="subtitle2">
                      {readable(status.failure.domain)} · {status.failure.code}
                    </Typography>
                    {status.failure.message}
                  </Alert>
                )}
                {status?.detail && (
                  <Alert
                    severity={status.stage === "failed" ? "error" : "info"}
                    variant="outlined"
                  >
                    {status.detail}
                  </Alert>
                )}
                {!terminalStages.has(status?.stage) && (
                  <Stack direction="row" spacing={1} alignItems="center">
                    <CircularProgress size={16} />
                    <Typography variant="body2" color="text.secondary">
                      ALK is working autonomously…
                    </Typography>
                  </Stack>
                )}
              </Stack>
            </Box>
          </Box>
        </Box>
      </Box>

      <ConfirmDialog
        open={confirmingCancel}
        onClose={() => setConfirmingCancel(false)}
        maxWidth="sm"
        title="Cancel this run?"
        content="This run stops where it is and cannot be resumed. Results already reported are kept, and a few more may arrive for up to two minutes."
        action={
          // size and padding match the dialog's own Cancel button so the pair reads as one control group.
          <Button
            size="small"
            variant="contained"
            color="error"
            disabled={canceling}
            onClick={() => cancel()}
            sx={{ paddingX: "24px" }}
          >
            Cancel run
          </Button>
        }
      />
    </>
  );
}
