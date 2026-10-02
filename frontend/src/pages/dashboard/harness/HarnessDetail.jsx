import Markdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Divider,
  IconButton,
  InputBase,
  LinearProgress,
  Paper,
  Stack,
  Tab,
  Tabs,
  Typography,
} from "@mui/material";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { formatDistanceToNow } from "date-fns";
import { Helmet } from "react-helmet-async";
import { useNavigate, useParams } from "react-router-dom";

import Iconify from "src/components/iconify";
import { CreditExhaustionBanner } from "src/components/CreditExhaustionBanner";
import StatusChip from "src/components/custom-status-chip/CustomStatusChip";
import ScenarioOutcome from "./ScenarioOutcome";
import CustomTooltip from "src/components/tooltip";

import StageOutput from "./StageOutput";
import ConfirmDialog from "src/components/custom-dialog/confirm-dialog";
import EnvironmentSwitcher from "src/components/harness/EnvironmentSwitcher";
import { compactActivityEvents } from "./activityEvents";
import {
  adjustHarnessJob,
  cancelHarnessJob,
  extendHarnessJob,
  getHarnessJob,
  listHarnessJobs,
  sendHarnessConversationMessage,
} from "src/api/harness/harness";
import { paths } from "src/routes/paths";
import { useCreditExhaustion } from "src/hooks/use-credit-exhaustion";

import {
  adjustmentStatus,
  completedStageCount,
  degradeReasonCopy,
  errorMessage,
  eventTime,
  runElapsed,
  shortDuration,
  shortRunId,
  STAGE_STATE,
  stageElapsed,
  tabState,
  TAB_STATE,
  stageState,
  eventMessage,
  displayText,
  jobProgress,
  readable,
  scenarioOutcome,
  scenarioTally,
  stages,
  stageStatus,
  terminalStages,
  environmentName,
} from "./harnessShared";

// A tab says where the run is: spinning while its stage is being worked, ticked once it has
// something to show, bare until then.
const tabIcon = (state) => {
  if (state === TAB_STATE.WORKING) return <CircularProgress size={13} />;
  if (state === TAB_STATE.DONE)
    return (
      <Iconify
        // Outline, not filled: a solid glyph carries as much ink as the label beside it,
        // which is too much for a secondary marker.
        icon="solar:check-circle-linear"
        color="accent.pass"
        width={14}
      />
    );
  return undefined;
};

const DETAIL_TABS = [
  { value: "contract", label: "Contract" },
  { value: "environment", label: "Environment" },
  { value: "scenarios", label: "Scenarios" },
  { value: "runs", label: "Runs" },
];

// Parallelism surface (C4 §6). Three distinct, info-styled notices — never
// presented as a scenario/agent failure:
//  1. runtime degrade — effective W (from the attempt projection, NOT the event
//     window) + per-reason copy, shown when the guest reduced parallelism;
//  2. platform clamp — metadata.parallelism_clamped, shown when admission held
//     the run at 1 because W>1 is not enabled for this deployment;
//  3. literal-endpoint warning — metadata.parallelism_warnings from the
//     submit-time environment_values scan.
function ParallelismNotice({ parallelism, clamped, warnings }) {
  const requested = parallelism?.requested;
  const effective = parallelism?.effective;
  const reasons = parallelism?.degrade_reasons || [];
  const degraded = reasons.length > 0;
  const clampedRequested = clamped?.requested;
  const warningList = Array.isArray(warnings) ? warnings : [];
  if (!degraded && !clampedRequested && warningList.length === 0) return null;
  return (
    <Stack spacing={1} sx={{ mt: 1.5 }}>
      {degraded && (
        <Alert severity="info" variant="outlined">
          <Typography variant="subtitle2">
            Parallelism: {effective} (requested {requested})
          </Typography>
          {reasons.map((reason) => (
            <Typography key={reason} variant="body2" sx={{ mt: 0.25 }}>
              {degradeReasonCopy(reason, effective)}
            </Typography>
          ))}
        </Alert>
      )}
      {!degraded && clampedRequested ? (
        <Alert severity="info" variant="outlined">
          Requested {clampedRequested}, admitted {clamped?.admitted ?? 1} —
          deployment, scenario-count, or sandbox capacity limits apply.
        </Alert>
      ) : null}
      {warningList.map((warning, index) => (
        <Alert key={index} severity="warning" variant="outlined">
          An environment value points at a fixed local address
          {warning?.aliases?.length ? ` (${warning.aliases.join(", ")})` : ""},
          which can prevent scenarios from running in parallel.
        </Alert>
      ))}
    </Stack>
  );
}

ParallelismNotice.propTypes = {
  parallelism: PropTypes.shape({
    requested: PropTypes.number,
    effective: PropTypes.number,
    degrade_reasons: PropTypes.arrayOf(PropTypes.string),
  }),
  clamped: PropTypes.shape({
    requested: PropTypes.number,
    admitted: PropTypes.number,
  }),
  warnings: PropTypes.arrayOf(
    PropTypes.shape({
      aliases: PropTypes.arrayOf(PropTypes.string),
    }),
  ),
};

const parseActivityPayload = (text) => {
  if (!text) return null;
  try {
    const value = JSON.parse(text);
    return value && typeof value === "object" && !Array.isArray(value)
      ? value
      : null;
  } catch {
    return null;
  }
};

const activitySummary = (entry) => {
  const payload = parseActivityPayload(entry.text);
  if (entry.kind === "tool") {
    const label = payload?.label || readable(entry.tool || "tool");
    const target = payload?.target || payload?.path;
    return target ? `ALK used ${label} · ${target}` : `ALK used ${label}`;
  }
  if (payload?.stage) {
    return `Stage · ${readable(payload.stage)}`;
  }
  return (
    displayText(entry.text)
      .split("\n")
      .map((line) => line.trim())
      .find(Boolean)
      ?.slice(0, 180) || "Authoring update"
  );
};
const activityResultSummary = (entry) => {
  const payload = parseActivityPayload(entry.text);
  const value = payload?.text || payload?.summary || entry.text;
  return displayText(value)
    .split("\n")
    .map((line) => line.trim())
    .find(Boolean)
    ?.slice(0, 180);
};
const consumptionValue = (value) =>
  typeof value === "number" && Number.isFinite(value)
    ? value.toLocaleString(undefined, { maximumFractionDigits: 6 })
    : "Unavailable";
export default function HarnessDetail() {
  const { jobId } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const {
    exhaustionError,
    handleError: handleCreditError,
    handleUpgradeClick,
    handleDismiss: dismissCreditBanner,
    clearError: clearCreditError,
  } = useCreditExhaustion({ feature: "hosted_harness" });
  const [clock, setClock] = useState(Date.now());
  const [cancelError, setCancelError] = useState("");
  const [confirmingCancel, setConfirmingCancel] = useState(false);
  const [conversationError, setConversationError] = useState("");
  const [adjustError, setAdjustError] = useState("");
  const [extendError, setExtendError] = useState("");
  const [stagesOpen, setStagesOpen] = useState(false);
  const [copiedId, setCopiedId] = useState(false);
  const [detailTab, setDetailTab] = useState("contract");
  const [message, setMessage] = useState("");
  const [adjustment, setAdjustment] = useState("");
  const [addCount, setAddCount] = useState(3);
  const feedRef = useRef(null);
  const conversationRef = useRef(null);
  const conversationAtEnd = useRef(true);
  const hadRemoteUsageLimit = useRef(false);
  // Whether the reader is sitting at the end of the feed. New activity follows the end
  // only while they are; someone who scrolled up to read history is left where they are.
  const pinnedToEnd = useRef(true);
  // Arriving at the tab is a jump; new activity is a slide. Told apart so a first paint
  // does not animate through the whole history.
  const arriving = useRef(true);
  const lastScrollTop = useRef(0);
  const following = useRef(true);

  const {
    data: current,
    isPending,
    error,
  } = useQuery({
    queryKey: ["harness-job", jobId],
    queryFn: () => getHarnessJob(jobId),
    enabled: Boolean(jobId),
    refetchInterval: (query) => {
      const value = query.state.data;
      const conversation = value?.conversation;
      const pending = conversation?.messages?.some((item) =>
        ["queued", "delivered", "streaming"].includes(item.state),
      );
      const active = ["starting", "hydrating", "responding"].includes(
        conversation?.state,
      );
      if (pending || active) return 1000;
      return terminalStages.has(value?.status?.stage) ? false : 2000;
    },
    meta: { errorHandled: true },
  });
  const usageLimit = current?.usage_limit;
  useEffect(() => {
    if (usageLimit) {
      if (!hadRemoteUsageLimit.current) {
        hadRemoteUsageLimit.current = true;
        handleCreditError(usageLimit);
      }
    } else if (hadRemoteUsageLimit.current) {
      hadRemoteUsageLimit.current = false;
      clearCreditError();
    }
  }, [usageLimit, handleCreditError, clearCreditError]);

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
  const chatAvailable = Boolean(current?.conversation?.runtime?.available);

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

  const { mutate: sendMessage, isPending: sendingMessage } = useMutation({
    mutationFn: async ({ content, replyTo = null, kind = "user_message" }) => {
      const randomUUID = window.crypto?.randomUUID;
      const requestId =
        typeof randomUUID === "function"
          ? randomUUID.call(window.crypto)
          : `message-${Date.now().toString(36)}`;
      const conversation = await sendHarnessConversationMessage(jobId, {
        content,
        client_request_id: requestId,
        kind,
        ...(replyTo ? { reply_to: replyTo } : {}),
      });
      return { conversation };
    },
    onMutate: () => setConversationError(""),
    onSuccess: (value, variables) => {
      queryClient.setQueryData(["harness-job", jobId], (existing) => ({
        ...existing,
        conversation: value.conversation,
      }));
      if (!variables.replyTo) setMessage("");
      pinnedToEnd.current = true;
      conversationAtEnd.current = true;
      setDetailTab("runs");
    },
    onError: (requestError) => {
      setConversationError(errorMessage(requestError));
    },
  });

  const { mutate: adjust, isPending: adjusting } = useMutation({
    mutationFn: () => {
      const instruction = adjustment.trim();
      // client_request_id is optional; window.crypto.randomUUID is undefined on an
      // insecure origin, so the key is omitted rather than sent as undefined.
      const requestId = window.crypto?.randomUUID?.();
      return adjustHarnessJob(jobId, {
        instruction,
        ...(requestId ? { client_request_id: requestId } : {}),
      });
    },
    onMutate: () => setAdjustError(""),
    onSuccess: (value) => {
      queryClient.setQueryData(["harness-job", jobId], value);
      setAdjustment("");
      // The change lands in the timeline, so go to where it lands — including when the
      // reader is already on the tab but scrolled back through history.
      pinnedToEnd.current = true;
      setDetailTab("runs");
    },
    onError: (requestError) => {
      // eslint-disable-next-line no-console
      console.error("Adjust run failed", {
        jobId,
        statusCode: requestError?.statusCode,
        detail: requestError?.detail,
        message: requestError?.message,
      });
      if (!handleCreditError(requestError)) {
        setAdjustError(errorMessage(requestError));
      }
    },
  });

  const { mutate: extend, isPending: extending } = useMutation({
    mutationFn: () => {
      // The finished-run "Add scenarios" action: add `addCount` scenarios to the saved
      // world, steered by the optional guidance typed in the box. Rerun is a separate action.
      const guidance = adjustment.trim();
      const requestId = window.crypto?.randomUUID?.();
      return extendHarnessJob(jobId, {
        count: addCount,
        ...(guidance ? { guidance } : {}),
        ...(requestId ? { client_request_id: requestId } : {}),
      });
    },
    onMutate: () => setExtendError(""),
    onSuccess: (value) => {
      // The follow-up relaunches the environment (extended or replayed): the job returns
      // to queued and this page's poll resumes.
      queryClient.setQueryData(["harness-job", jobId], value);
      queryClient.invalidateQueries({ queryKey: ["harness-jobs"] });
      setAdjustment("");
      pinnedToEnd.current = true;
      setDetailTab("runs");
    },
    onError: (requestError) => {
      // eslint-disable-next-line no-console
      console.error("Extend environment failed", {
        jobId,
        statusCode: requestError?.statusCode,
        detail: requestError?.detail,
        message: requestError?.message,
      });
      if (!handleCreditError(requestError)) {
        setExtendError(errorMessage(requestError));
      }
    },
  });

  const status = current?.status;
  const cancellationRequested = Boolean(status?.cancel_requested_at);
  const progress = jobProgress(status);
  const isTerminal = terminalStages.has(status?.stage);
  const conversationComposerAvailable = chatAvailable || !isTerminal;
  const blockingReplyTo = chatAvailable
    ? current?.conversation?.blocking_input?.message_id || null
    : null;

  // Only the finished prefix folds away, so the stage a run failed or stopped on is never
  // hidden by collapsing — it is the first row still on screen.
  const doneCount = completedStageCount(status, current?.events);
  const collapsible = doneCount > 0;
  const visibleStages =
    !collapsible || stagesOpen ? stages : stages.slice(doneCount);
  const elapsedLabel = shortDuration(
    runElapsed(current?.events, clock, isTerminal),
  );
  const stageLabel = shortDuration(
    stageElapsed(status, current?.events, clock),
  );

  // ALK reports one artifact per stage group. "Runs" is the catch-all so a new kind never
  // disappears: anything that is not a named tab lands there alongside the activity feed.
  const stageOutputs = current?.stage_outputs || [];
  const selectedOutputs = stageOutputs.filter((output) =>
    detailTab === "runs"
      ? !["contract", "environment", "scenarios"].includes(output.kind)
      : output.kind === detailTab,
  );
  const outputCounts = stageOutputs.reduce((counts, output) => {
    const key = ["contract", "environment", "scenarios"].includes(output.kind)
      ? output.kind
      : "runs";
    return { ...counts, [key]: (counts[key] || 0) + 1 };
  }, {});

  const tabStates = DETAIL_TABS.reduce(
    (states, tab) => ({
      ...states,
      [tab.value]: tabState(
        tab.value,
        status,
        current?.events,
        Boolean(outputCounts[tab.value]),
      ),
    }),
    {},
  );
  const workingTab = DETAIL_TABS.find(
    (tab) => tabStates[tab.value] === TAB_STATE.WORKING,
  )?.value;

  // The page follows the run from stage to stage on its own, so watching a job does not mean
  // clicking tabs to find where the work moved. Choosing a tab that is not the live one is a
  // decision to stay put; coming back to the live one resumes the follow.
  useEffect(() => {
    if (!following.current || !workingTab) return;
    setDetailTab(workingTab);
  }, [workingTab]);

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

  // One timeline, not two lists. A correction you send is part of the run's story and belongs
  // beside the stages it lands between, so events and adjustments are merged and sorted by
  // the moment each happened.
  const timeline = useMemo(() => {
    const seen = new Set();
    const events = compactActivityEvents(current?.events || [])
      .filter((event) => {
        const key = event.event_id || JSON.stringify(event);
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      })
      .map((event) => ({
        kind: "event",
        id: event.event_id,
        at: event.emitted_at || event.wall_time,
        event,
      }));
    const authoringActivity =
      stageOutputs.find((output) => output.kind === "activity")?.events || [];
    const authoringEvents = authoringActivity.map((event) => ({
      kind: "event",
      id: event.event_id,
      at: event.emitted_at,
      event: {
        event_id: event.event_id,
        emitted_at: event.emitted_at,
        type: event.event_type,
        event_type: event.event_type,
        payload: event.payload || {},
        stage: event.payload?.stage,
      },
    }));
    const changes = (current?.adjustments || []).map((item) => ({
      kind: "adjustment",
      id: item.adjustment_id,
      at: item.created_at,
      item,
    }));
    return [...events, ...authoringEvents, ...changes].sort(
      (a, b) => new Date(a.at || 0) - new Date(b.at || 0),
    );
  }, [current?.events, current?.adjustments, stageOutputs]);

  const conversationFeed = useMemo(() => {
    const entries = (current?.conversation?.messages || []).map((item) => ({
      kind: "message",
      id: item.message_id,
      at: item.created_at,
      item,
    }));
    const tools = new Map();
    (current?.conversation?.events || []).forEach((event) => {
      if (event.kind === "tool_started") {
        const key = event.function_call_id || event.event_id;
        const entry = {
          kind: "tool",
          id: key,
          at: event.emitted_at,
          stage: displayText(event.stage),
          tool: displayText(event.payload?.tool),
          label: displayText(event.payload?.label),
          state: "running",
          text: "",
        };
        tools.set(key, entry);
        entries.push(entry);
      } else if (event.kind === "tool_result") {
        const key = event.function_call_id || event.event_id;
        const existing = tools.get(key);
        if (existing) {
          existing.state = event.payload?.is_error ? "failed" : "completed";
          existing.text = displayText(event.payload?.text);
        } else {
          entries.push({
            kind: "tool",
            id: key,
            at: event.emitted_at,
            stage: displayText(event.stage),
            tool: displayText(event.payload?.tool),
            state: event.payload?.is_error ? "failed" : "completed",
            text: displayText(event.payload?.text),
          });
        }
      } else if (event.kind === "authoring_activity") {
        const activity = event.payload?.event || {};
        const activityText = displayText(activity.text);
        if (!activityText && !activity.tool) return;
        const text = activityText || readable(displayText(activity.tool));
        entries.push({
          kind: "activity",
          id: event.event_id,
          at: event.emitted_at,
          text,
        });
      } else if (event.kind === "stage_changed") {
        entries.push({
          kind: "activity",
          id: event.event_id,
          at: event.emitted_at,
          text: `ALK moved to ${readable(displayText(event.payload?.to || event.stage))}`,
        });
      }
    });
    const sorted = entries.sort(
      (a, b) => new Date(a.at || 0) - new Date(b.at || 0),
    );
    return sorted.reduce((grouped, entry) => {
      const previous = grouped.at(-1);
      if (entry.kind === "activity" && previous?.kind === "activity") {
        previous.text = `${previous.text}\n${entry.text}`;
        previous.count += 1;
      } else {
        grouped.push({
          ...entry,
          count: entry.kind === "activity" ? 1 : undefined,
        });
      }
      return grouped;
    }, []);
  }, [current?.conversation?.events, current?.conversation?.messages]);
  const activityEntries = useMemo(
    () => conversationFeed.filter((entry) => entry.kind !== "message"),
    [conversationFeed],
  );

  // Arriving at the feed always lands at the newest entry, whatever the reader was doing
  // on a previous visit.
  useLayoutEffect(() => {
    if (detailTab !== "runs") return;
    pinnedToEnd.current = true;
    arriving.current = true;
  }, [detailTab]);

  useLayoutEffect(() => {
    const feed = feedRef.current;
    if (!feed || detailTab !== "runs" || !pinnedToEnd.current) return;
    const reducedMotion = window.matchMedia?.(
      "(prefers-reduced-motion: reduce)",
    )?.matches;
    // A new entry slides into view so it reads as an arrival. Landing on the tab jumps,
    // because animating through two thousand pixels of history looks like a fault.
    // jsdom has no scrollTo, so the fallback keeps the component renderable under test.
    if (typeof feed.scrollTo === "function")
      feed.scrollTo({
        top: feed.scrollHeight,
        behavior: arriving.current || reducedMotion ? "auto" : "smooth",
      });
    else feed.scrollTop = feed.scrollHeight;
    arriving.current = false;
  }, [
    conversationFeed.length,
    detailTab,
    selectedOutputs.length,
    timeline.length,
  ]);
  useLayoutEffect(() => {
    const conversation = conversationRef.current;
    if (!conversation || !conversationAtEnd.current) return;
    conversation.scrollTop = conversation.scrollHeight;
  }, [current?.conversation?.messages]);

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
          <title>Environment | Future AGI</title>
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
          {environmentName(current.job, "Environment")} | Future AGI
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
                currentName={environmentName(current.job, "")}
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
                  disabled={canceling || cancellationRequested}
                  onClick={() => setConfirmingCancel(true)}
                  startIcon={
                    canceling || cancellationRequested ? (
                      <CircularProgress size={14} color="inherit" />
                    ) : (
                      <Iconify icon="solar:close-circle-linear" width={18} />
                    )
                  }
                >
                  {canceling || cancellationRequested
                    ? "Canceling…"
                    : "Cancel run"}
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
          {cancellationRequested && !isTerminal && (
            <Alert severity="info" variant="outlined" sx={{ mt: 1.5 }}>
              Cancellation requested. The sandbox is stopping and cleaning up.
            </Alert>
          )}
          <ParallelismNotice
            parallelism={current.parallelism}
            clamped={current.job?.metadata?.parallelism_clamped}
            warnings={current.job?.metadata?.parallelism_warnings}
          />
          {current.parallelism && (
            <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
              World slots: {current.parallelism.effective} effective /{" "}
              {current.parallelism.admitted ?? current.parallelism.effective}{" "}
              admitted / {current.parallelism.requested} requested
              {Number.isInteger(status?.active_scenarios) && (
                <>
                  {" "}
                  · {status.active_scenarios} active · {status.queued_scenarios}{" "}
                  queued
                </>
              )}
            </Typography>
          )}
          <CreditExhaustionBanner
            error={exhaustionError}
            onUpgrade={handleUpgradeClick}
            onDismiss={dismissCreditBanner}
            sx={{ mt: 1.5 }}
          />
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
            <Typography variant="h6">{environmentName(current.job)}</Typography>
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
                    icon={
                      copiedId ? "solar:check-read-linear" : "solar:copy-linear"
                    }
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
                {scenarioTally(status, current.job)}
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
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ display: "block", mt: 0.25 }}
            >
              {isTerminal ? `${updatedLabel} · ` : ""}attempt{" "}
              {status?.attempt || 1}
            </Typography>
            <Box sx={{ mt: 1.5 }}>
              <Typography variant="caption" fontWeight={600}>
                Consumption
              </Typography>
              {current.consumption == null ? (
                <Typography
                  variant="caption"
                  color="text.secondary"
                  sx={{ display: "block", mt: 0.25 }}
                >
                  Usage unavailable
                </Typography>
              ) : (
                <Stack spacing={0.25} sx={{ mt: 0.5 }}>
                  {[
                    ["Text simulation tokens", "text_sim_tokens"],
                    ["Voice simulation minutes", "voice_sim_minutes"],
                    ["AI credits", "ai_credits"],
                    ["Sandbox seconds", "sandbox_seconds"],
                  ].map(([label, key]) => (
                    <Stack
                      key={key}
                      direction="row"
                      justifyContent="space-between"
                      spacing={1}
                    >
                      <Typography variant="caption" color="text.secondary">
                        {label}
                      </Typography>
                      <Typography variant="caption">
                        {consumptionValue(current.consumption[key])}
                      </Typography>
                    </Stack>
                  ))}
                </Stack>
              )}
            </Box>
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
                    icon={
                      stagesOpen
                        ? "solar:alt-arrow-up-linear"
                        : "solar:alt-arrow-down-linear"
                    }
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
                <Iconify
                  icon="solar:check-circle-bold"
                  color="accent.pass"
                  width={18}
                />
                <Typography variant="body2" color="text.secondary">
                  {doneCount} {doneCount === 1 ? "stage" : "stages"} complete
                </Typography>
              </Button>
            )}

            <Stack spacing={1.1} sx={{ pl: collapsible ? "11px" : 0 }}>
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
                      sx={{
                        opacity: muted ? 0.6 : 1,
                        mt: "1px",
                        flexShrink: 0,
                      }}
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
                // No bottom padding: the tab indicator has to land ON the divider, not float
                // above it. MUI draws the indicator at the bottom edge of the Tabs.
                pb: 0,
                flexShrink: 0,
                borderBottom: 1,
                borderColor: "divider",
              }}
            >
              <Tabs
                value={detailTab}
                onChange={(_, value) => {
                  following.current = value === workingTab;
                  setDetailTab(value);
                }}
                variant="scrollable"
                scrollButtons={false}
                sx={(theme) => ({
                  minHeight: 38,
                  // The theme gives every tab a 40px right margin at sm+
                  // (theme/overrides/components/tabs.js), which spreads four short labels
                  // across the whole column. Override that rather than adding a gap on top.
                  // Unselected labels sit on text.subtitle rather than text.secondary. In
                  // light mode that is black[600] against black[800] — noticeably softer —
                  // while in dark mode both resolve to the same value, so nothing moves.
                  "& .MuiTab-root:not(.Mui-selected)": {
                    color: "text.subtitle",
                  },
                  // The pass green is #16A34A in light and #4ADE80 in dark, so the same
                  // opacity reads heavy on white and washed out on black. Hold the ticks
                  // back in dark, and let them keep their weight in light.
                  "& .MuiTab-iconWrapper": {
                    opacity: theme.palette.mode === "light" ? 0.9 : 0.5,
                  },
                  "& .Mui-selected .MuiTab-iconWrapper": { opacity: 1 },
                  // The tick is a marker and can sit back; a spinner is the live signal on
                  // the page and has to hold its weight on every tab, selected or not.
                  "& .MuiTab-iconWrapper:has(.MuiCircularProgress-root)": {
                    opacity: 1,
                  },
                  "& .MuiTab-root": {
                    minHeight: 38,
                    minWidth: "auto",
                    // Tabs sit flush against each other and space themselves with padding,
                    // so the indicator spans a whole tab and the row reads as one rail. The
                    // theme's 40px right margin would break it back into separate boxes.
                    // The tick sits to the left of the label, so equal padding leaves the
                    // label sitting right of centre. A little extra on the right settles it.
                    pl: 1.5,
                    pr: 2.25,
                    "&:not(:last-of-type)": { mr: 0 },
                  },
                })}
              >
                {DETAIL_TABS.map((tab) => (
                  <Tab
                    key={tab.value}
                    value={tab.value}
                    label={tab.label}
                    icon={tabIcon(tabStates[tab.value])}
                    // Leading, and tight against the label: the tick marks the tab, so it
                    // reads as part of the name rather than a badge trailing after it.
                    iconPosition="start"
                    sx={{
                      textTransform: "none",
                      // MUI gives the icon wrapper its own 8px margin, which stacks on top
                      // of the gap. Zero it so the spacing is this one value.
                      gap: 0.5,
                      "& .MuiTab-iconWrapper": { m: 0 },
                    }}
                  />
                ))}
              </Tabs>
            </Box>
            <Box
              ref={feedRef}
              onScroll={() => {
                const feed = feedRef.current;
                if (!feed) return;
                const atEnd =
                  feed.scrollHeight - feed.scrollTop - feed.clientHeight < 80;
                const scrolledUp = feed.scrollTop < lastScrollTop.current;
                lastScrollTop.current = feed.scrollTop;
                // Only scrolling *up* breaks the follow. A smooth scroll fires this
                // handler at every intermediate position on its way down, and treating
                // those as "not at the end" would cancel the follow mid-animation.
                if (atEnd) pinnedToEnd.current = true;
                else if (scrolledUp) pinnedToEnd.current = false;
              }}
              sx={{ flex: 1, px: 2, py: 1.5, overflow: "auto", minHeight: 0 }}
            >
              {detailTab !== "runs" ? (
                <Stack spacing={1.5}>
                  {selectedOutputs.length ? (
                    selectedOutputs.map((output) => (
                      <StageOutput
                        key={output.id}
                        output={output}
                        jobId={jobId}
                        onChanged={() =>
                          queryClient.invalidateQueries({
                            queryKey: ["harness-jobs"],
                          })
                        }
                      />
                    ))
                  ) : (
                    <Typography variant="body2" color="text.secondary">
                      {isTerminal
                        ? "The runner produced nothing for this stage."
                        : "This appears once the runner reaches this stage."}
                    </Typography>
                  )}
                </Stack>
              ) : (
                <Stack spacing={1.5}>
                  {selectedOutputs.map((output) => (
                    <StageOutput
                      key={output.id}
                      output={output}
                      jobId={jobId}
                      onChanged={() =>
                        queryClient.invalidateQueries({
                          queryKey: ["harness-jobs"],
                        })
                      }
                    />
                  ))}
                  {current.credentials && (
                    <Paper
                      variant="outlined"
                      sx={{ p: 1.5, bgcolor: "background.default" }}
                    >
                      <Typography variant="subtitle2">
                        Runtime preflight
                      </Typography>
                      <Typography variant="body2" color="text.secondary">
                        {current.credentials.scanned_files} source files
                        inspected ·{" "}
                        {(current.credentials.detected_connectors || []).join(
                          ", ",
                        ) || "generic connector"}{" "}
                        · {current.credentials.requirements?.length || 0}{" "}
                        configuration requirements
                      </Typography>
                    </Paper>
                  )}
                  {timeline.map((entry) =>
                    entry.kind === "adjustment" ? (
                      // A correction you asked for, in the run's own voice: indented and
                      // accented so it is legible as yours without leaving the timeline.
                      <Paper
                        key={entry.id}
                        variant="outlined"
                        sx={{
                          p: 1.5,
                          ml: 3,
                          bgcolor: "background.default",
                          borderColor: "accent.info",
                        }}
                      >
                        <Stack direction="row" justifyContent="space-between">
                          <Typography variant="caption" color="accent.info">
                            ALK requested a change
                          </Typography>
                          <Typography
                            variant="caption"
                            color="text.secondary"
                            title={entry.at || ""}
                          >
                            {eventTime(entry.at)}
                          </Typography>
                        </Stack>
                        <Typography variant="body2">
                          {entry.item.instruction}
                        </Typography>
                        <Stack
                          direction="row"
                          spacing={0.75}
                          alignItems="center"
                        >
                          {/* A change that has not landed by the time the run stops never
                            will. ALK leaves it at "pending" forever, so a spinner and a
                            "will land at" would promise work that cannot happen. */}
                          {entry.item.status !== "applied" && !isTerminal && (
                            <CircularProgress size={10} />
                          )}
                          <Typography
                            variant="caption"
                            color={
                              entry.item.status !== "applied" && isTerminal
                                ? "text.disabled"
                                : "text.secondary"
                            }
                          >
                            {adjustmentStatus(entry.item, status?.stage)}
                          </Typography>
                        </Stack>
                      </Paper>
                    ) : (
                      <Paper
                        key={entry.id}
                        variant="outlined"
                        sx={{ p: 1.5, bgcolor: "background.default" }}
                      >
                        <Stack direction="row" justifyContent="space-between">
                          <Typography variant="caption" color="accent.brand">
                            {/* Hosted events name their stage at the top level, the
                              sandbox nests it in the payload. Without both, a hosted
                              card falls back to its type and repeats the body. */}
                            {readable(
                              entry.event.payload?.stage ||
                                entry.event.stage ||
                                entry.event.type,
                            )}
                          </Typography>
                          <Typography
                            variant="caption"
                            color="text.secondary"
                            title={entry.at || ""}
                          >
                            {eventTime(entry.at)}
                          </Typography>
                        </Stack>
                        <Typography
                          variant="body2"
                          sx={{
                            whiteSpace: "pre-wrap",
                            overflowWrap: "anywhere",
                            wordBreak: "break-word",
                            maxHeight: 180,
                            overflow: "auto",
                          }}
                        >
                          {eventMessage(entry.event)}
                        </Typography>
                        <ScenarioOutcome
                          outcome={scenarioOutcome(
                            entry.event.payload?.scenario_key,
                            entry.event.payload?.scenario_attempt,
                            current,
                          )}
                        />
                      </Paper>
                    ),
                  )}
                  {status?.failure && (
                    <Alert severity="error" variant="outlined">
                      <Typography variant="subtitle2">
                        {readable(status.failure.domain)} ·{" "}
                        {status.failure.code}
                      </Typography>
                      {status.failure.message}
                      {status.failure.action && (
                        <Typography variant="body2" sx={{ mt: 0.75 }}>
                          Next step: {status.failure.action}
                        </Typography>
                      )}
                      {(status.failure.details?.packaging_type ||
                        status.failure.details?.failed_adapter) && (
                        <Typography
                          variant="caption"
                          component="div"
                          sx={{ mt: 0.75 }}
                        >
                          {status.failure.details.packaging_type
                            ? `Packaging: ${readable(status.failure.details.packaging_type)}`
                            : ""}
                          {status.failure.details.packaging_type &&
                          status.failure.details.failed_adapter
                            ? " · "
                            : ""}
                          {status.failure.details.failed_adapter
                            ? `Adapter: ${readable(status.failure.details.failed_adapter)}`
                            : ""}
                        </Typography>
                      )}
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
                  {isTerminal && !timeline.length && (
                    <Typography variant="body2" color="text.secondary">
                      This run recorded no activity.
                    </Typography>
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
              )}
              {activityEntries.length > 0 && (
                <Box
                  component="details"
                  sx={{
                    mb: 2,
                    px: 1.5,
                    py: 1,
                    border: 1,
                    borderColor: "divider",
                    borderRadius: 1,
                  }}
                >
                  <Typography
                    component="summary"
                    variant="caption"
                    sx={{ cursor: "pointer" }}
                  >
                    Run activity · {activityEntries.length} updates
                  </Typography>
                  <Stack
                    spacing={0.5}
                    sx={{ mt: 1, maxHeight: 260, overflow: "auto" }}
                  >
                    {activityEntries.map((entry) => (
                      <Typography
                        key={entry.id}
                        variant="caption"
                        color="text.secondary"
                        sx={{
                          whiteSpace: "pre-wrap",
                          overflowWrap: "anywhere",
                        }}
                      >
                        <Box component="span">{activitySummary(entry)}</Box>
                        {entry.kind === "tool" &&
                          activityResultSummary(entry) && (
                            <>
                              <Box component="span" color="text.disabled">
                                {" — "}
                              </Box>
                              <Box component="span">
                                {activityResultSummary(entry)}
                              </Box>
                            </>
                          )}
                      </Typography>
                    ))}
                  </Stack>
                </Box>
              )}
            </Box>
            <Box
              ref={conversationRef}
              aria-label="Conversation"
              onScroll={(event) => {
                const panel = event.currentTarget;
                conversationAtEnd.current =
                  panel.scrollHeight - panel.scrollTop - panel.clientHeight <
                  80;
              }}
              sx={{
                flexShrink: 0,
                maxHeight: "40vh",
                minHeight: 0,
                overflow: "auto",
                overflowWrap: "anywhere",
                px: 2,
                pt: 1.5,
                borderTop: 1,
                borderColor: "divider",
              }}
            >
              {conversationFeed.some((entry) => entry.kind === "message") && (
                <Typography variant="subtitle2" sx={{ mb: 1 }}>
                  Conversation
                </Typography>
              )}
              {Boolean(conversationFeed.length) && (
                <Stack spacing={1.25} sx={{ mb: 2 }}>
                  {conversationFeed
                    .filter((entry) => entry.kind === "message")
                    .map((entry) => {
                      const item = entry.item;
                      return (
                        <Paper
                          key={item.message_id}
                          variant="outlined"
                          sx={{
                            p: 1.5,
                            ml: item.role === "user" ? 6 : 0,
                            mr: item.role === "assistant" ? 6 : 0,
                            bgcolor: "background.default",
                            borderColor:
                              item.role === "user" ? "accent.info" : "divider",
                          }}
                        >
                          <Stack direction="row" justifyContent="space-between">
                            <Typography
                              variant="caption"
                              color={
                                item.role === "user"
                                  ? "accent.info"
                                  : "accent.brand"
                              }
                            >
                              {item.role === "user" ? "You" : "ALK"}
                            </Typography>
                            <Typography
                              variant="caption"
                              color="text.secondary"
                            >
                              {readable(displayText(item.stage))}
                            </Typography>
                          </Stack>
                          <Box
                            sx={{
                              "& p": { m: 0, mb: 0.75 },
                              "& p:last-child": { mb: 0 },
                              "& ul, & ol": { mb: 0.75, mt: 0.5, pl: 2.5 },
                              "& li": { mb: 0.25 },
                              "& pre": {
                                bgcolor: "action.hover",
                                borderRadius: 0.75,
                                m: 0.5,
                                overflowX: "auto",
                                p: 1,
                              },
                              "& code": {
                                bgcolor: "action.hover",
                                borderRadius: 0.5,
                                px: 0.35,
                              },
                              "& pre code": { bgcolor: "transparent", p: 0 },
                            }}
                          >
                            <Markdown
                              remarkPlugins={[remarkGfm]}
                              rehypePlugins={[rehypeSanitize]}
                            >
                              {displayText(item.content)}
                            </Markdown>
                          </Box>
                          {["queued", "delivered", "streaming"].includes(
                            item.state,
                          ) && (
                            <Stack
                              direction="row"
                              spacing={0.75}
                              alignItems="center"
                            >
                              <CircularProgress size={10} />
                              <Typography
                                variant="caption"
                                color="text.secondary"
                              >
                                {readable(item.state)}
                              </Typography>
                            </Stack>
                          )}
                          {item.kind === "question" &&
                            Boolean(item.payload?.options?.length) && (
                              <Stack direction="row" spacing={1} sx={{ mt: 1 }}>
                                {item.payload.options.map((option) => (
                                  <Button
                                    key={option}
                                    size="small"
                                    variant="outlined"
                                    disabled={sendingMessage}
                                    onClick={() =>
                                      sendMessage({
                                        content: option,
                                        replyTo: item.message_id,
                                        kind: "user_response",
                                      })
                                    }
                                  >
                                    {option}
                                  </Button>
                                ))}
                              </Stack>
                            )}
                        </Paper>
                      );
                    })}
                </Stack>
              )}
            </Box>

            {current && (
              <Box
                sx={{
                  flexShrink: 0,
                  px: 2,
                  py: 1.5,
                  borderTop: 1,
                  borderColor: "divider",
                }}
              >
                <Box
                  sx={{
                    borderRadius: 1,
                    border: 1,
                    borderColor: "divider",
                    bgcolor: "background.paper",
                    "&:focus-within": { borderColor: "text.disabled" },
                  }}
                >
                  <InputBase
                    fullWidth
                    multiline
                    maxRows={8}
                    placeholder={
                      conversationComposerAvailable
                        ? blockingReplyTo
                          ? "Answer ALK’s question…"
                          : "Ask ALK about this environment or tell it what to change…"
                        : isTerminal
                          ? "Describe the scenarios to add — e.g. 'calm first-time riders booking an airport pickup' (optional)"
                          : "Tell the run what to change…"
                    }
                    value={conversationComposerAvailable ? message : adjustment}
                    onChange={(event) =>
                      conversationComposerAvailable
                        ? setMessage(event.target.value)
                        : setAdjustment(event.target.value)
                    }
                    onKeyDown={(event) => {
                      if (event.key !== "Enter" || event.shiftKey) return;
                      event.preventDefault();
                      if (conversationComposerAvailable) {
                        const content = message.trim();
                        if (!content || sendingMessage) return;
                        sendMessage({
                          content,
                          replyTo: blockingReplyTo,
                          kind: blockingReplyTo
                            ? "user_response"
                            : "user_message",
                        });
                      } else if (isTerminal) {
                        if (extending) return;
                        extend();
                      } else {
                        if (adjusting || !adjustment.trim()) return;
                        adjust();
                      }
                    }}
                    sx={{ px: 1.5, pt: 1.25, typography: "body2" }}
                  />
                  <Stack
                    direction="row"
                    alignItems="center"
                    justifyContent="space-between"
                    sx={{ px: 1.5, pb: 1, pt: 0.5 }}
                  >
                    <Typography variant="caption" color="text.disabled">
                      {conversationComposerAvailable
                        ? "ALK answers directly and requests safe adjustments when needed"
                        : isTerminal
                          ? "Adds scenarios to the saved world"
                          : "Applied at the next stage boundary"}
                    </Typography>
                    {conversationComposerAvailable ? (
                      <IconButton
                        size="small"
                        onClick={() => {
                          const content = message.trim();
                          if (content)
                            sendMessage({
                              content,
                              replyTo: blockingReplyTo,
                              kind: blockingReplyTo
                                ? "user_response"
                                : "user_message",
                            });
                        }}
                        disabled={sendingMessage || !message.trim()}
                        aria-label="Send"
                        sx={{
                          bgcolor: "accent.brand",
                          color: "common.white",
                          "&:hover": { bgcolor: "accent.brand", opacity: 0.88 },
                          "&.Mui-disabled": {
                            bgcolor: "action.disabledBackground",
                            color: "text.disabled",
                          },
                        }}
                      >
                        {sendingMessage ? (
                          <CircularProgress size={14} color="inherit" />
                        ) : (
                          <Iconify icon="solar:plain-linear" width={15} />
                        )}
                      </IconButton>
                    ) : isTerminal ? (
                      <Stack direction="row" alignItems="center" spacing={1}>
                        <Stack
                          direction="row"
                          alignItems="center"
                          sx={{
                            border: 1,
                            borderColor: "divider",
                            borderRadius: 1,
                          }}
                        >
                          <IconButton
                            size="small"
                            aria-label="Fewer scenarios"
                            disabled={extending || addCount <= 1}
                            onClick={() =>
                              setAddCount((n) => Math.max(1, n - 1))
                            }
                          >
                            <Iconify
                              icon="solar:minus-square-linear"
                              width={15}
                            />
                          </IconButton>
                          <Typography
                            variant="body2"
                            sx={{ minWidth: 18, textAlign: "center" }}
                          >
                            {addCount}
                          </Typography>
                          <IconButton
                            size="small"
                            aria-label="More scenarios"
                            disabled={extending || addCount >= 20}
                            onClick={() =>
                              setAddCount((n) => Math.min(20, n + 1))
                            }
                          >
                            <Iconify
                              icon="solar:add-square-linear"
                              width={15}
                            />
                          </IconButton>
                        </Stack>
                        <Button
                          size="small"
                          variant="contained"
                          onClick={() => extend()}
                          disabled={extending}
                          startIcon={
                            extending ? (
                              <CircularProgress size={14} color="inherit" />
                            ) : (
                              <Iconify
                                icon="solar:add-circle-linear"
                                width={15}
                              />
                            )
                          }
                          sx={{
                            bgcolor: "accent.brand",
                            color: "common.white",
                            "&:hover": {
                              bgcolor: "accent.brand",
                              opacity: 0.88,
                            },
                          }}
                        >
                          Add scenarios
                        </Button>
                      </Stack>
                    ) : (
                      <IconButton
                        size="small"
                        onClick={() => adjust()}
                        disabled={adjusting || !adjustment.trim()}
                        aria-label="Send"
                        sx={{
                          bgcolor: "accent.brand",
                          color: "common.white",
                          "&:hover": { bgcolor: "accent.brand", opacity: 0.88 },
                          "&.Mui-disabled": {
                            bgcolor: "action.disabledBackground",
                            color: "text.disabled",
                          },
                        }}
                      >
                        {adjusting ? (
                          <CircularProgress size={14} color="inherit" />
                        ) : (
                          <Iconify icon="solar:plain-linear" width={15} />
                        )}
                      </IconButton>
                    )}
                  </Stack>
                </Box>
                {conversationError && (
                  <Alert
                    severity="error"
                    variant="outlined"
                    onClose={() => setConversationError("")}
                    sx={{ mt: 1 }}
                  >
                    {conversationError}
                  </Alert>
                )}
                {(adjustError || extendError) && (
                  <Alert
                    severity="error"
                    variant="outlined"
                    onClose={() => {
                      setAdjustError("");
                      setExtendError("");
                    }}
                    sx={{ mt: 1 }}
                  >
                    {adjustError || extendError}
                  </Alert>
                )}
              </Box>
            )}
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
