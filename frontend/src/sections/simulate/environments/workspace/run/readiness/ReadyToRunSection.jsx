import { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  CircularProgress,
  Stack,
  Typography,
} from "@mui/material";
import StatusBadge from "src/components/status-badge/StatusBadge";
import CustomTooltip from "src/components/tooltip";
import ContentSkeleton from "src/components/content-skeleton";
import ErrorState from "src/components/error-state";
import {
  useReadiness,
  useRecheck,
} from "src/api/simulate-environments/readiness";
import { useRetryAgentVersion } from "src/api/simulate-environments/versions";
import { readinessView } from "./readinessView";
import { READY_TO_RUN_COPY as COPY } from "./readiness.constants";

const lineSx = {
  borderTop: "1px solid",
  borderColor: "divider",
  px: 1.5,
  py: 1,
};

const boxSx = { border: "1px solid", borderColor: "divider", borderRadius: 1 };

// Until the report lands nothing is known to be blocked or fine, so Run waits.
// A report that fails to load leaves Run as it was: unknown is not blocked.
const LOADING_VIEW = { canRun: false, loading: true, summary: null, open: [] };

const STATE_ROLE = {
  checking: "running",
  failed: "failed",
  ours: "unavailable",
  retry: "untested",
  unchecked: "untested",
};

function stateText(state, reason) {
  const text = COPY.states[state];
  return typeof text === "function" ? text(reason) : text;
}

const spinner = <CircularProgress size={12} color="inherit" />;

function FixLine({ line, onRecheck, onRetryAgent, busy, inFlight, checking }) {
  const [open, setOpen] = useState(false);
  const { fix } = line;
  // Checking the pair again is this line's whole fix: its button says
  // Re-check, not a Fix that would only say "check again".
  const recheckOnly = fix?.type === "recheck" && fix.recheck?.scope === "pair";
  return (
    // While a check runs these are the last results: dimmed, no Fix.
    <Box sx={{ ...lineSx, opacity: checking && !inFlight ? 0.5 : 1 }}>
      <Stack direction="row" spacing={1} alignItems="center">
        <Box sx={{ flexShrink: 0 }}>
          <StatusBadge
            status={line.status}
            label={COPY.areas[line.area] || line.area}
            variant="dot"
          />
        </Box>
        {/* One line here; the full reason and what it means on hover. */}
        <CustomTooltip
          show={Boolean(line.reason)}
          title={
            <Box sx={{ whiteSpace: "pre-line" }}>
              {[line.reason, line.consequence].filter(Boolean).join("\n\n")}
            </Box>
          }
          size="small"
          arrow
        >
          <Typography
            noWrap
            sx={{
              typography: "s3",
              color: "text.secondary",
              flex: 1,
              minWidth: 0,
            }}
          >
            {line.reason}
            {line.affected ? ` · ${COPY.affected(line.affected)}` : ""}
          </Typography>
        </CustomTooltip>
        {fix && !checking && (
          <Button
            size="small"
            variant="outlined"
            onClick={() => setOpen((shown) => !shown)}
            sx={{ py: 0, minWidth: 0, flexShrink: 0 }}
          >
            {open && COPY.hideFix}
            {!open && (recheckOnly ? COPY.recheck : COPY.fix)}
          </Button>
        )}
      </Stack>
      {open && fix && (!checking || inFlight) && (
        <Stack spacing={1} sx={{ pt: 1, pl: 2.5 }}>
          {/* A re-check line opens on its full reason and what it means;
              a fix line on what to change. */}
          <Typography sx={{ typography: "s3", color: "text.primary" }}>
            {recheckOnly ? line.reason : fix.action}
          </Typography>
          {recheckOnly && line.consequence && (
            <Typography sx={{ typography: "s3", color: "text.secondary" }}>
              {line.consequence}
            </Typography>
          )}
          {!recheckOnly && fix.would && (
            <Typography sx={{ typography: "s3", color: "text.secondary" }}>
              {COPY.after(fix.would)}
            </Typography>
          )}
          {fix.recheck && (
            <Box>
              <Button
                size="small"
                variant="contained"
                disabled={busy}
                startIcon={inFlight ? spinner : null}
                onClick={
                  fix.recheck.scope === "agent" ? onRetryAgent : onRecheck
                }
              >
                {inFlight && COPY.checking}
                {!inFlight &&
                  (fix.recheck.scope === "agent"
                    ? COPY.retryAgent
                    : COPY.recheckPair)}
              </Button>
            </Box>
          )}
        </Stack>
      )}
    </Box>
  );
}

FixLine.propTypes = {
  line: PropTypes.object.isRequired,
  onRecheck: PropTypes.func.isRequired,
  onRetryAgent: PropTypes.func.isRequired,
  busy: PropTypes.bool,
  checking: PropTypes.bool,
  // This line's own re-check is the one running.
  inFlight: PropTypes.bool,
};

/**
 * Whether the picked agent, environment and scenario versions can run
 * together: what is blocked and why, what to fix, and a Re-check of the pair.
 * The popup reads the counts and whether Run is allowed through `onView`.
 */
export default function ReadyToRunSection({
  envId,
  picks,
  keys = null,
  trials = 1,
  onView,
}) {
  const readiness = useReadiness(envId, picks, { keys, trials });
  const recheck = useRecheck(envId);
  const retryAgent = useRetryAgentVersion(envId);
  // Which line's button started the check, so that button shows it running.
  const [pressed, setPressed] = useState(null);
  const view = useMemo(
    () => (readiness.data ? readinessView(readiness.data) : null),
    [readiness.data],
  );

  // From the click until the server says it is checking, Run waits too.
  const sending = recheck.isPending || retryAgent.isPending;
  useEffect(() => {
    if (readiness.isLoading) onView?.(LOADING_VIEW);
    else onView?.(view && sending ? { ...view, canRun: false } : view);
  }, [view, onView, readiness.isLoading, sending]);

  if (readiness.isLoading) {
    return (
      <Box sx={boxSx}>
        <ContentSkeleton variant="panel" rows={3} label={COPY.title} />
      </Box>
    );
  }
  if (!view) {
    return readiness.isError ? (
      <ErrorState title={COPY.loadError} onRetry={() => readiness.refetch()} />
    ) : null;
  }

  // A re-check reads as running from the click, not from the next report.
  const checking = view.state === "checking" || sending;
  const dimSx = { ...lineSx, opacity: checking ? 0.5 : 1 };
  const busy = checking;
  const runRecheck = (from = null) => {
    setPressed(from);
    recheck.mutate(picks);
  };
  const agentId = readiness.data?.pairing?.agent_version?.id;
  const runRetryAgent = (from = null) => {
    setPressed(from);
    retryAgent.mutate(
      { versionId: agentId },
      { onSettled: () => readiness.refetch() },
    );
  };
  const blockers = view.open.filter((line) => line.status === "blocked").length;
  const readyLabels = view.ready.map((area) => COPY.areas[area] || area);
  if (!view.grading.notGradable) readyLabels.push(COPY.areas.grading);
  const noteState = checking ? "checking" : view.state;
  const note = stateText(noteState, view.stateReason);

  return (
    <Box data-testid="ready-to-run" sx={boxSx}>
      <Stack
        direction="row"
        spacing={1}
        alignItems="center"
        sx={{ px: 1.5, py: 1 }}
      >
        <Typography sx={{ typography: "s2", fontWeight: "fontWeightSemiBold" }}>
          {COPY.title}
        </Typography>
        <Typography
          noWrap
          sx={{
            typography: "s3",
            color: "text.secondary",
            flex: 1,
            minWidth: 0,
          }}
        >
          {COPY.summary(view.summary, blockers)}
        </Typography>
        <Button
          size="small"
          variant="outlined"
          disabled={busy}
          startIcon={checking ? spinner : null}
          onClick={() => runRecheck()}
          sx={{ py: 0, flexShrink: 0 }}
        >
          {checking ? COPY.checking : COPY.recheck}
        </Button>
      </Stack>
      {note && (
        // Dot and text spaced as a badge's own dot and label, so this text
        // starts where the area names below start.
        <Stack direction="row" spacing={0.75} alignItems="center" sx={lineSx}>
          <StatusBadge
            status={STATE_ROLE[noteState]}
            label=""
            variant="dot"
            sx={{ gap: 0 }}
          />
          <Typography sx={{ typography: "s3", color: "text.secondary" }}>
            {note}
          </Typography>
        </Stack>
      )}
      {view.open.map((line) => (
        <FixLine
          key={line.area}
          line={line}
          onRecheck={() => runRecheck(line.area)}
          onRetryAgent={() => runRetryAgent(line.area)}
          inFlight={checking && pressed === line.area}
          checking={checking}
          busy={busy}
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
            label={COPY.areas[line.area] || line.area}
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
        <Stack direction="row" spacing={0.75} alignItems="center" sx={dimSx}>
          <StatusBadge
            status="supported"
            label=""
            variant="dot"
            sx={{ gap: 0 }}
          />
          <Typography
            noWrap
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
  trials: PropTypes.number,
  onView: PropTypes.func,
};
