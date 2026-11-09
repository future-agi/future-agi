import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  ButtonBase,
  IconButton,
  Popover,
  Stack,
  Typography,
} from "@mui/material";
import { useNavigate } from "react-router";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { isSurfaceShown } from "src/launch/launchSwitch";
import RunTrayWatcher from "./RunTrayWatcher";
import { getRunStatus } from "src/sections/common/Runs/runStatus";
import {
  runCountsText,
  waitingPhraseOf,
} from "src/sections/common/Runs/runLedger";
import RunChip from "src/sections/common/Runs/RunChip";
import { notify } from "src/sections/common/ux/toast";
import {
  finishToastText,
  RUN_STATUS_UNAVAILABLE,
  removedRunsText,
  trayLabel,
  useRunTray,
} from "./runTrayStore";

const experimentHref = (e) =>
  `/dashboard/develop/experiment/${e.experimentId}/data${e.datasetId ? `?datasetId=${e.datasetId}` : ""}`;

const isWaitingRun = (run) => Boolean(getRunStatus(run).waiting);

/** The finish toast's variant by the run's real outcome (the capability contract). */
const FINISH_VARIANT = {
  Completed: "success",
  PartialCompleted: "warning",
  Failed: "error",
};

// dataset integration FE-0 / L5 G3: a waiting run in the tray offers `Open` only (no
// runner command, CI workflow or upload snippet; those sources are absent).
// IT-R3 G1 P1: open surfaces the floating pill must never cover. Modal
// drawers and dialogs portal to <body>; Popovers / Menus (incl. the tray's
// own) are not surfaces and do not hide it.
export const OPEN_SURFACE_SELECTOR =
  ".MuiDialog-root:not(.MuiModal-hidden), .MuiDrawer-modal:not(.MuiModal-hidden)";

/** True while any modal drawer or dialog is open (MutationObserver on the
 * body's children + a slow poll for keepMounted modals toggling a class). */
export function useOpenSurface() {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (typeof document === "undefined") return undefined;
    const check = () =>
      setOpen(Boolean(document.querySelector(OPEN_SURFACE_SELECTOR)));
    check();
    const mo =
      typeof MutationObserver === "undefined"
        ? null
        : new MutationObserver(check);
    mo?.observe(document.body, { childList: true });
    const t = setInterval(check, 800);
    return () => {
      mo?.disconnect();
      clearInterval(t);
    };
  }, []);
  return open;
}

/**
 * RunTrayDock — the tray docked as a small chip in an open drawer's header
 * (IT-R3 G1 P1): `1 run waiting · #4A`. Renders nothing when nothing is
 * tracked. Read-only status; the pill comes back when the drawer closes.
 */
export function RunTrayDock({ sx }) {
  const tracked = useRunTray((s) => s.runs);
  const live = useRunTray((s) => s.live);
  const checks = useRunTray((s) => s.checks);
  if (!tracked.length) return null;
  const label = trayLabel(tracked, live, isWaitingRun, {
    short: true,
    waitingPhrase: waitingPhraseOf,
    checks,
  });
  const anyWaiting = tracked.some(
    (e) =>
      !checks[e.experimentId]?.failed &&
      live[e.runId] &&
      isWaitingRun(live[e.runId]),
  );
  return (
    <CustomTooltip
      show
      title={`${trayLabel(tracked, live, isWaitingRun, { waitingPhrase: waitingPhraseOf, checks })}. Close this panel to open the tray.`}
      size="small"
      arrow
    >
      <Box
        data-testid="run-tray-dock"
        tabIndex={0}
        aria-label={label}
        sx={{
          display: "inline-flex",
          alignItems: "center",
          gap: 0.5,
          height: 24,
          px: 1,
          borderRadius: "12px",
          border: "1px solid",
          borderColor: "divider",
          typography: "s3",
          color: "text.secondary",
          whiteSpace: "nowrap",
          maxWidth: 220,
          overflow: "hidden",
          textOverflow: "ellipsis",
          ...sx,
        }}
      >
        <Iconify
          icon={anyWaiting ? "mdi:timer-sand" : "mdi:progress-clock"}
          width={14}
          sx={{
            color: anyWaiting ? "info.main" : "primary.main",
            flexShrink: 0,
          }}
        />
        {label}
      </Box>
    </CustomTooltip>
  );
}

RunTrayDock.propTypes = { sx: PropTypes.object };

/**
 * RunTray — the global tray of running experiments (R3 P3, CR#42 / CR#43).
 * Mounted once in the dashboard layout. A chip left of the Falcon bubble:
 * `1 run waiting for results · #6A`; click for each run's status and
 * `Open`. A finished run leaves with a toast (`#6A finished · Completed ·
 * Open`, plus its errored rows from the ledger).
 *
 * Props (frozen): none.
 */
export default function RunTray() {
  const tracked = useRunTray((s) => s.runs);
  const untrack = useRunTray((s) => s.untrack);
  const live = useRunTray((s) => s.live);
  const setLive = useRunTray((s) => s.setLive);
  const checks = useRunTray((s) => s.checks);
  const canOpen = isSurfaceShown("exp.page");
  const navigate = useNavigate();
  const [anchor, setAnchor] = useState(null);
  const surfaceOpen = useOpenSurface();

  const byExperiment = tracked.reduce((m, e) => {
    if (!e.experimentId) return m;
    (m[e.experimentId] ??= []).push(e.runId);
    return m;
  }, {});

  const onRuns = (runs) => {
    setLive(runs);
    const finished = runs.filter((r) => getRunStatus(r).terminal);
    if (!finished.length) return;
    untrack(finished.map((r) => r.run_id));
    finished.forEach((r) => {
      const entry = tracked.find((e) => e.runId === r.run_id);
      const st = getRunStatus(r);
      notify(finishToastText(r, st.label), {
        key: `run-tray-${r.run_id}`,
        // the capability contract: only a run that really completed toasts success.
        variant: FINISH_VARIANT[st.key] ?? "default",
        action:
          entry && canOpen
            ? () => (
                <Button
                  size="small"
                  color="inherit"
                  onClick={() => navigate(experimentHref(entry))}
                >
                  Open
                </Button>
              )
            : undefined,
      });
    });
  };

  // IT-R3 G1 P1: the experiment is gone (reload / deleted): drop its runs
  // and say so once, neutrally.
  const onMissing = (experimentId) => {
    const gone = useRunTray
      .getState()
      .runs.filter((e) => e.experimentId === experimentId);
    if (!gone.length) return;
    untrack(gone.map((e) => e.runId));
    notify(removedRunsText(gone.map((e) => e.label)), {
      key: `run-tray-removed-${experimentId}`,
      variant: "default",
    });
  };

  if (!tracked.length) return null;

  const runsLive = tracked.map((e) => ({
    entry: e,
    run: checks[e.experimentId]?.failed ? undefined : live[e.runId],
    check: checks[e.experimentId],
  }));
  const waiting = runsLive.filter(({ run }) => run && isWaitingRun(run));
  const label = trayLabel(tracked, live, isWaitingRun, {
    waitingPhrase: waitingPhraseOf,
    checks,
  });
  // IT-R3 G1 P1: never drawn over an open drawer or dialog (the drawer's
  // header carries <RunTrayDock /> instead); the tray's own popover keeps
  // its anchor.
  const hidePill = surfaceOpen && !anchor;

  return (
    <>
      {Object.entries(byExperiment).map(([expId, ids]) => (
        <RunTrayWatcher
          key={expId}
          experimentId={expId}
          runIds={ids}
          onRuns={onRuns}
          onMissing={onMissing}
        />
      ))}
      {hidePill ? null : (
        // L7c g5 (runtray:popover): the tooltip wrapper stays mounted while
        // the popover is open (an empty title shows nothing), so the pill
        // is never remounted and the popover keeps its anchor.
        <CustomTooltip
          show
          title={
            anchor
              ? ""
              : "Runs you started or are waiting on. Click for details."
          }
          size="small"
          arrow
        >
          <ButtonBase
            data-testid="run-tray"
            aria-haspopup="dialog"
            onClick={(e) => setAnchor(e.currentTarget)}
            sx={(t) => ({
              position: "fixed",
              right: 72,
              bottom: 16,
              // IT-R3 G1 P1: below every drawer (the New experiment drawer
              // sits at 1099) and every modal backdrop.
              zIndex: t.zIndex.appBar - 2,
              height: 36,
              px: 1.5,
              gap: 0.75,
              borderRadius: "18px",
              border: "1px solid",
              borderColor: "divider",
              bgcolor: "background.paper",
              boxShadow: t.customShadows?.z8 ?? t.shadows[4],
              typography: "s2",
              color: "text.primary",
              maxWidth: 360,
            })}
          >
            <Iconify
              icon={waiting.length ? "mdi:timer-sand" : "mdi:progress-clock"}
              width={16}
              sx={{ color: waiting.length ? "info.main" : "primary.main" }}
            />
            <Box
              component="span"
              sx={{
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
            >
              {label}
            </Box>
          </ButtonBase>
        </CustomTooltip>
      )}
      <Popover
        open={Boolean(anchor)}
        anchorEl={anchor}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: "top", horizontal: "right" }}
        transformOrigin={{ vertical: "bottom", horizontal: "right" }}
        PaperProps={{
          sx: { width: 460, maxWidth: "calc(100vw - 32px)", p: 1.5, mt: -1 },
        }}
      >
        <Stack gap={1}>
          <Typography typography="s1" fontWeight="fontWeightSemiBold">
            Runs in progress
          </Typography>
          {runsLive.map(({ entry, run, check }) => {
            const st = run ? getRunStatus(run) : null;
            return (
              <Stack
                key={entry.runId}
                direction="row"
                alignItems="center"
                gap={1}
              >
                {run ? (
                  <RunChip run={run} showName={false} size="small" />
                ) : (
                  <Typography typography="s2">{entry.label}</Typography>
                )}
                <Typography
                  typography="s2"
                  color={check?.failed ? "status.danger.fg" : "text.secondary"}
                  role={check?.failed ? "status" : undefined}
                  sx={{ flex: 1, minWidth: 0 }}
                >
                  {/* L7c g5: a waiting run's line is its ledger line, which
                      already says what it waits for (one noun). */}
                  {check?.failed
                    ? RUN_STATUS_UNAVAILABLE
                    : st
                      ? st.waiting
                        ? runCountsText(run) || st.label
                        : [st.label, runCountsText(run)]
                            .filter(Boolean)
                            .join(" · ")
                      : "Checking…"}
                </Typography>
                {check?.failed && (
                  <Button
                    size="small"
                    // Keep keyboard focus during polls; join any active read.
                    onClick={() => check.retry({ cancelRefetch: false })}
                  >
                    Retry
                  </Button>
                )}
                {canOpen && (
                  <Button
                    size="small"
                    onClick={() => {
                      setAnchor(null);
                      navigate(experimentHref(entry));
                    }}
                  >
                    Open
                  </Button>
                )}
                <CustomTooltip
                  show
                  title="Dismiss from the tray (the run keeps going)"
                  size="small"
                  arrow
                >
                  <IconButton
                    size="small"
                    data-testid="run-tray-dismiss"
                    aria-label={`Dismiss ${entry.label} from the tray`}
                    onClick={() => untrack(entry.runId)}
                  >
                    <Iconify icon="mdi:close" width={14} />
                  </IconButton>
                </CustomTooltip>
              </Stack>
            );
          })}
        </Stack>
      </Popover>
    </>
  );
}
