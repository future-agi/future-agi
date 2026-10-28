import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  LinearProgress,
  Paper,
  Stack,
  Typography,
} from "@mui/material";
import { keyframes } from "@mui/material/styles";
import { useQueryClient } from "@tanstack/react-query";
import ConfirmDialog from "src/components/custom-dialog/confirm-dialog";
import Iconify from "src/components/iconify";
import { useDeploymentMode } from "src/hooks/useDeploymentMode";
import { UNITS } from "src/sections/projects/observePopulation";
import { useCanStartRuns } from "src/sections/projects/runPermission";
import { insightsKeys, stopRun } from "./insightsApi";
import { formatCount, formatSpan } from "./insightsFormat";
import { useCancellable, useElapsedSeconds } from "./insightsHooks";
import { STAGES, stageIndex } from "./insightsLenses";

// A run usually takes 2–10 minutes. One whose progress hasn't moved for
// this long says so instead.
const USUAL_S = 10 * 60;
const STALLED_S = 10 * 60;

const arrive = keyframes`
  from { opacity: 0; transform: translateY(-4px); }
  to { opacity: 1; transform: none; }
`;

function StageRail({ current }) {
  const visible = STAGES.filter((stage) => stage.key !== "done");
  return (
    <Stack
      direction="row"
      component="ol"
      aria-label="Report stages"
      sx={{ listStyle: "none", m: 0, p: 0, gap: 0.5 }}
    >
      {visible.map((stage, index) => {
        const done = index < current;
        const active = index === current;
        return (
          <Stack
            key={stage.key}
            component="li"
            aria-current={active ? "step" : undefined}
            spacing={0.75}
            sx={{ flex: 1, minWidth: 0 }}
          >
            <Box
              sx={{
                height: 4,
                borderRadius: 2,
                bgcolor: done || active ? "primary.main" : "action.hover",
                opacity: active ? 0.55 : 1,
              }}
            />
            <Stack direction="row" spacing={0.5} alignItems="center">
              {done && (
                <Iconify
                  icon="mdi:check"
                  width={14}
                  sx={{ color: "primary.main" }}
                />
              )}
              <Typography
                variant="caption"
                fontWeight={active ? 600 : 400}
                color={done || active ? "text.primary" : "text.secondary"}
                noWrap
              >
                {stage.label}
              </Typography>
            </Stack>
          </Stack>
        );
      })}
    </Stack>
  );
}

StageRail.propTypes = { current: PropTypes.number.isRequired };

// Stop, behind a confirmation: the run fails as "Stopped by <you>". The
// dialog closes once the stopped run has been read again, so the page goes
// straight to "Stopping…" (reads under way still being saved) or to its
// final counts. On Cloud the work already done is charged; how much isn't
// known yet (calls under way still add to it), so no figure.
function StopRun({ run }) {
  const queryClient = useQueryClient();
  const { isCloud } = useDeploymentMode();
  const canRun = useCanStartRuns();
  const [open, setOpen] = useState(false);
  const stopping = useCancellable(
    "The run could not be stopped.",
    "Couldn't stop it right now. Try again in a moment.",
  );
  const close = () => {
    stopping.reset();
    setOpen(false);
  };
  const stop = () =>
    stopping.run(async (signal) => {
      const stopped = await stopRun(run.id, signal);
      const key = insightsKeys.run(run.id);
      await queryClient.refetchQueries({
        queryKey: key,
        exact: true,
        type: "active",
      });
      queryClient.setQueryData(key, (current) =>
        current ? { ...current, ...stopped } : current,
      );
      setOpen(false);
      return stopped;
    });
  const busy = stopping.state.status === "busy";
  return (
    <>
      <Button
        size="small"
        variant="outlined"
        color="inherit"
        startIcon={<Iconify icon="mdi:stop" width={16} />}
        onClick={() => setOpen(true)}
        disabled={!canRun}
        sx={{ fontSize: 13 }}
      >
        Stop
      </Button>
      <ConfirmDialog
        open={open}
        onClose={close}
        title="Stop this run?"
        content={
          <Stack spacing={1}>
            <Typography variant="body2">
              It stops reading now and won&apos;t write a report.
              {isCloud && " You'll be charged for the work already done."} You
              can run it again later.
            </Typography>
            {stopping.state.status === "error" && (
              <Alert severity="error">{stopping.state.error}</Alert>
            )}
          </Stack>
        }
        action={
          <Button
            size="small"
            variant="contained"
            color="error"
            onClick={stop}
            disabled={busy}
          >
            Stop run
          </Button>
        }
      />
    </>
  );
}

StopRun.propTypes = { run: PropTypes.object.isRequired };

/**
 * How long a run takes, honestly: the usual range at first, then that it is
 * taking longer, and when nothing has moved for STALLED_S, that it is stuck
 * on the model and when it gives up. Runs from before progress was stamped
 * (no `updated_at`) never read as stuck.
 */
function durationNote(elapsed, quiet, stamped) {
  const away =
    "You can leave this page; the report keeps running and will be here when you come back.";
  if (stamped && quiet >= STALLED_S)
    return {
      stalled: true,
      text: `No progress in ${Math.floor(quiet / 60)} minutes: the analysis model may be slow or not answering. The report keeps trying until it has run for 2 hours, or you can stop it.`,
    };
  if (elapsed >= USUAL_S) return { text: `Taking longer than usual. ${away}` };
  return { text: `Usually 2–10 minutes. ${away}` };
}

export default function RunProgress({ run }) {
  const progress = run.progress || {};
  const stage = progress.stage || run.stage || "queued";
  const current = stageIndex(stage);
  const meta = STAGES[current];
  const plural = (UNITS[run.unit] || UNITS.traces).plural;
  const elapsed = useElapsedSeconds(run.started_at || run.created_at, true);
  const quiet = useElapsedSeconds(
    progress.updated_at || run.started_at || run.created_at,
    true,
  );
  const note = durationNote(elapsed, quiet, Boolean(progress.updated_at));
  const done = Number(progress.done) || 0;
  const total = Number(progress.total) || run.sample_size || 0;
  const counted = stage === "reading" || stage === "summarising";
  const recent = [...(progress.recent || [])].reverse();

  return (
    <Paper variant="outlined" sx={{ p: 3, borderRadius: 1.5 }}>
      <Stack spacing={2.5}>
        <Stack
          direction="row"
          justifyContent="space-between"
          alignItems="baseline"
        >
          <Box>
            <Typography variant="subtitle1" component="h2">
              {run.status === "pending" ? "Queued" : `${meta.label}…`}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              {counted && total
                ? `${meta.detail}: ${formatCount(done)} of ${formatCount(total)} ${plural}`
                : meta.detail}
            </Typography>
          </Box>
          <Stack direction="row" spacing={1.5} alignItems="center">
            <Typography variant="caption" color="text.secondary">
              Running for {formatSpan(elapsed)}
            </Typography>
            <StopRun run={run} />
          </Stack>
        </Stack>
        <StageRail current={current} />
        {counted && total > 0 && (
          <LinearProgress
            variant="determinate"
            value={Math.min(100, (done / total) * 100)}
            sx={{ height: 6, borderRadius: 3 }}
            aria-label={`${done} of ${total} ${plural}`}
          />
        )}
        {recent.length > 0 && (
          <Box>
            <Typography
              variant="overline"
              color="text.secondary"
              sx={{ display: "block", mb: 1 }}
            >
              Just read
            </Typography>
            <Stack
              spacing={1}
              aria-live="polite"
              sx={{ maxHeight: 260, overflow: "hidden" }}
            >
              {recent.slice(0, 6).map((text, index) => (
                <Typography
                  key={text}
                  variant="body2"
                  color={index === 0 ? "text.primary" : "text.secondary"}
                  sx={{
                    pl: 1.5,
                    borderLeft: "2px solid",
                    borderColor: index === 0 ? "primary.main" : "divider",
                    animation: `${arrive} 300ms ease-out`,
                    opacity: Math.max(0.45, 1 - index * 0.12),
                  }}
                >
                  {text}
                </Typography>
              ))}
            </Stack>
          </Box>
        )}
        <Typography
          variant="caption"
          color={note.stalled ? "warning.dark" : "text.secondary"}
        >
          {note.text}
        </Typography>
      </Stack>
    </Paper>
  );
}

RunProgress.propTypes = { run: PropTypes.object.isRequired };
