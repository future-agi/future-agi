import { useEffect, useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
import StatusBadge from "src/components/status-badge/StatusBadge";
import ContentSkeleton from "src/components/content-skeleton";
import ErrorState from "src/components/error-state";
import { useRecheck } from "src/api/simulate-environments/readiness";
import {
  isVersioningDisabled,
  useRetryAgentVersion,
} from "src/api/simulate-environments/versions";
import { refusalText } from "../../evals/refusalText";
import ReadinessLine from "./ReadinessLine";
import Spinner from "./Spinner";
import { lineSx } from "./readinessStyles";
import { READY_TO_RUN_COPY as COPY } from "./readiness.constants";

const boxSx = { border: "1px solid", borderColor: "divider", borderRadius: 1 };

// The check state's dot: info while it waits or runs, danger when it holds the run, warning when it couldn't settle.
const STATE_ROLE = {
  checking: "info",
  unchecked: "info",
  failed: "danger",
  refused: "danger",
  ours: "warning",
  retry: "warning",
  unavailable: "warning",
  alreadyChecked: "info",
};

function stateText(state, reason) {
  const text = COPY.states[state];
  return typeof text === "function" ? text(reason) : text;
}

const areaLabel = (area) => COPY.areas[area] || area;

/**
 * Whether the picked agent, environment and scenario versions can run together: what isn't ready and why, its
 * fix, and a Re-check of the pair. The dialog owns the report; this says when a re-check is being sent.
 */
export default function ReadyToRunSection({
  envId,
  picks,
  keys = null,
  trials = 1,
  readiness,
  view,
  onSendingChange,
  onOpenAgentVersions,
}) {
  const recheck = useRecheck(envId);
  const retryAgent = useRetryAgentVersion(envId);
  // Which line's button started the check, so that button shows it running.
  const [pressed, setPressed] = useState(null);
  // One line open at a time keeps the section short; the dialog body scrolls.
  const [openArea, setOpenArea] = useState(null);
  const sending = recheck.isPending || retryAgent.isPending;
  useEffect(() => {
    onSendingChange?.(sending);
  }, [sending, onSendingChange]);
  useEffect(() => () => onSendingChange?.(false), [onSendingChange]);
  // Other picks start with nothing open, nothing running and no answer from the last picks' re-check.
  const picksKey = JSON.stringify(picks || {});
  const { reset: resetRecheck } = recheck;
  const { reset: resetRetry } = retryAgent;
  useEffect(() => {
    setPressed(null);
    setOpenArea(null);
    resetRecheck();
    resetRetry();
  }, [picksKey, resetRecheck, resetRetry]);

  if (readiness.isLoading) {
    return (
      <Box sx={boxSx}>
        <ContentSkeleton variant="panel" rows={3} label={COPY.title} />
      </Box>
    );
  }
  if (!view) {
    // No saved version yet: nothing to check, and the run still goes.
    if (isVersioningDisabled(readiness.error)) {
      return (
        <Typography
          data-testid="ready-to-run-no-versions"
          sx={{ typography: "s3", color: "text.secondary" }}
        >
          {COPY.noVersions}
        </Typography>
      );
    }
    return readiness.isError ? (
      <ErrorState title={COPY.loadError} onRetry={() => readiness.refetch()} />
    ) : null;
  }

  // A re-check reads as running from the click, not from the next report.
  const checking = view.state === "checking" || sending;
  const dimSx = { ...lineSx, opacity: checking ? 0.5 : 1 };
  const agentId = readiness.data?.pairing?.agent_version?.id;
  const runRecheck = (from = null) => {
    setPressed(from);
    retryAgent.reset();
    recheck.mutate(picks);
  };
  const runRetryAgent = (from = null) => {
    setPressed(from);
    recheck.reset();
    retryAgent.mutate({ versionId: agentId });
  };
  const blockers = view.open.filter((line) => line.status === "blocked").length;
  const readyLabels = view.ready.map(areaLabel);
  // Grading is ready only once some scenario is known to be gradable.
  if (view.grading.gradable > 0 && !view.grading.notGradable) {
    readyLabels.push(COPY.areas.grading);
  }
  // A re-check or retry the server turned down says so, in its words.
  const refusal = recheck.isError ? recheck.error : retryAgent.error;
  const answer = recheck.data?.validation;
  const cantCheck = answer?.state === "unavailable";
  // A pair already certified starts no check: ready, with no job.
  const alreadyChecked = answer?.state === "ready" && !answer.job_id;
  let noteState = view.state;
  if (checking) noteState = "checking";
  else if (refusal) noteState = "refused";
  else if (cantCheck) noteState = "unavailable";
  else if (alreadyChecked) noteState = "alreadyChecked";
  const note = stateText(
    noteState,
    refusal ? refusalText(refusal, "") : view.stateReason,
  );

  return (
    <Box data-testid="ready-to-run" sx={boxSx}>
      <Stack
        direction="row"
        spacing={1}
        alignItems="center"
        sx={{ px: 1.5, py: 1 }}
      >
        <Typography noWrap sx={{ typography: "s2", flex: 1, minWidth: 0 }}>
          <Box component="span" sx={{ fontWeight: "fontWeightSemiBold" }}>
            {COPY.title}
          </Box>
          <Box component="span" sx={{ color: "text.secondary" }}>
            {` · ${COPY.blockers(blockers)}`}
          </Box>
        </Typography>
        <Button
          size="small"
          variant="outlined"
          disabled={checking}
          startIcon={checking ? <Spinner /> : null}
          onClick={() => runRecheck()}
          sx={{ py: 0, flexShrink: 0 }}
        >
          {checking ? COPY.checking : COPY.recheck}
        </Button>
      </Stack>
      {note && (
        <Stack direction="row" spacing={0.75} alignItems="center" sx={lineSx}>
          <StatusBadge
            status={noteState}
            role={STATE_ROLE[noteState]}
            label=""
            variant="dot"
            sx={{ gap: 0 }}
          />
          <Typography
            aria-live="polite"
            sx={{ typography: "s3", color: "text.secondary" }}
          >
            {note}
          </Typography>
        </Stack>
      )}
      {view.open.map((line) => (
        <ReadinessLine
          key={line.area}
          line={line}
          envId={envId}
          picks={picks}
          keys={keys}
          trials={trials}
          report={view.computedAt}
          open={openArea === line.area}
          onToggle={() =>
            setOpenArea((area) => (area === line.area ? null : line.area))
          }
          checking={checking}
          inFlight={checking && pressed === line.area}
          onRecheck={() => runRecheck(line.area)}
          onRetryAgent={() => runRetryAgent(line.area)}
          onOpenAgentVersions={onOpenAgentVersions}
        />
      ))}
      {view.limits.map((line) => (
        <Stack
          key={line.area}
          direction="row"
          spacing={1}
          alignItems="center"
          sx={dimSx}
        >
          <StatusBadge
            status={line.status}
            label={areaLabel(line.area)}
            variant="dot"
          />
          <Typography
            noWrap
            sx={{ typography: "s3", color: "text.tertiary", minWidth: 0 }}
          >
            {line.reason}
          </Typography>
        </Stack>
      ))}
      {view.grading.notGradable > 0 && (
        <Stack direction="row" spacing={1} alignItems="center" sx={dimSx}>
          <StatusBadge
            status="partial"
            label={COPY.areas.grading}
            variant="dot"
          />
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            {COPY.notGradable(view.grading.notGradable)}
          </Typography>
        </Stack>
      )}
      {readyLabels.length > 0 && (
        <Stack
          direction="row"
          spacing={0.75}
          alignItems="flex-start"
          sx={dimSx}
        >
          {/* As tall as one text line, so the dot sits on the first. */}
          <StatusBadge
            status="supported"
            label=""
            variant="dot"
            sx={{ gap: 0, height: 16 }}
          />
          <Typography
            sx={{ typography: "s3", color: "text.tertiary", minWidth: 0 }}
          >
            {COPY.moreReady(
              readyLabels,
              view.open.length === 0 && view.limits.length === 0,
            )}
          </Typography>
        </Stack>
      )}
    </Box>
  );
}

ReadyToRunSection.propTypes = {
  envId: PropTypes.string,
  picks: PropTypes.object,
  keys: PropTypes.arrayOf(PropTypes.string),
  // Repeats per scenario; more than one needs World resets.
  trials: PropTypes.number,
  // The readiness query for these picks and scenarios.
  readiness: PropTypes.shape({
    data: PropTypes.object,
    error: PropTypes.any,
    isLoading: PropTypes.bool,
    isError: PropTypes.bool,
    refetch: PropTypes.func,
  }).isRequired,
  // readinessView of the report, or null before it lands.
  view: PropTypes.object,
  // Told while a re-check or agent retry is being sent, so Run waits for it.
  onSendingChange: PropTypes.func,
  onOpenAgentVersions: PropTypes.func,
};
