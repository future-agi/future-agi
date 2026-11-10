import { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
import StatusBadge from "src/components/status-badge/StatusBadge";
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

const STATE_ROLE = {
  checking: "running",
  failed: "failed",
  ours: "unavailable",
  retry: "untested",
  unchecked: "untested",
};

function stateText(view) {
  const text = COPY.states[view.state];
  return typeof text === "function" ? text(view.stateReason) : text;
}

function FixLine({ line, onRecheck, onRetryAgent, busy }) {
  const [open, setOpen] = useState(false);
  const { fix } = line;
  return (
    <Box sx={lineSx}>
      <Stack direction="row" spacing={1} alignItems="center">
        <Box sx={{ flexShrink: 0 }}>
          <StatusBadge
            status={line.status}
            label={COPY.areas[line.area] || line.area}
            variant="dot"
          />
        </Box>
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
        {fix && (
          <Button
            size="small"
            variant="outlined"
            onClick={() => setOpen((shown) => !shown)}
            sx={{ py: 0, minWidth: 0, flexShrink: 0 }}
          >
            {open ? COPY.hideFix : COPY.fix}
          </Button>
        )}
      </Stack>
      {open && fix && (
        <Stack spacing={1} sx={{ pt: 1, pl: 2.5 }}>
          <Typography sx={{ typography: "s3", color: "text.secondary" }}>
            {fix.action}
            {fix.would ? `. ${COPY.after(fix.would)}` : ""}
          </Typography>
          {fix.recheck && (
            <Box>
              <Button
                size="small"
                variant="contained"
                disabled={busy}
                onClick={
                  fix.recheck.scope === "agent" ? onRetryAgent : onRecheck
                }
              >
                {fix.recheck.scope === "agent"
                  ? COPY.retryAgent
                  : COPY.recheckPair}
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
  const view = useMemo(
    () => (readiness.data ? readinessView(readiness.data) : null),
    [readiness.data],
  );

  useEffect(() => {
    onView?.(view);
  }, [view, onView]);

  if (readiness.isLoading) {
    return <ContentSkeleton variant="rows" rows={3} label={COPY.title} />;
  }
  if (!view) {
    return readiness.isError ? (
      <ErrorState title={COPY.loadError} onRetry={() => readiness.refetch()} />
    ) : null;
  }

  const checking = view.state === "checking";
  const busy = checking || recheck.isPending || retryAgent.isPending;
  const runRecheck = () => recheck.mutate(picks);
  const agentId = readiness.data?.pairing?.agent_version?.id;
  const runRetryAgent = () =>
    retryAgent.mutate(
      { versionId: agentId },
      { onSettled: () => readiness.refetch() },
    );
  const blockers = view.open.filter((line) => line.status === "blocked").length;
  const readyLabels = view.ready.map((area) => COPY.areas[area] || area);
  if (!view.grading.notGradable) readyLabels.push(COPY.areas.grading);
  const note = stateText(view);

  return (
    <Box
      data-testid="ready-to-run"
      sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1 }}
    >
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
          onClick={runRecheck}
          sx={{ py: 0, flexShrink: 0 }}
        >
          {checking ? COPY.checking : COPY.recheck}
        </Button>
      </Stack>
      {note && (
        <Stack direction="row" spacing={1} alignItems="center" sx={lineSx}>
          <StatusBadge status={STATE_ROLE[view.state]} label="" variant="dot" />
          <Typography sx={{ typography: "s3", color: "text.secondary" }}>
            {note}
          </Typography>
        </Stack>
      )}
      {view.open.map((line) => (
        <FixLine
          key={line.area}
          line={line}
          onRecheck={runRecheck}
          onRetryAgent={runRetryAgent}
          busy={busy}
        />
      ))}
      {view.limits.map((line) => (
        <Stack
          key={line.area}
          direction="row"
          spacing={1}
          alignItems="center"
          sx={lineSx}
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
        <Stack direction="row" spacing={1} alignItems="center" sx={lineSx}>
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
        <Stack direction="row" spacing={1} alignItems="center" sx={lineSx}>
          <StatusBadge status="supported" label="" variant="dot" />
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
