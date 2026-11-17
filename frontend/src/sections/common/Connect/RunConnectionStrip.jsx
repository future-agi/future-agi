// `Running on alice@mbp-14 · 4 at a time · 41/90 · 2 failed` + Stop / Resume
// (R4-SPEC §4; implementation; frozen `{run}` + additive optional props). Null
// unless the run runs on a connected machine (the seeded R4 world; FE-5
// deletes it). dataset integration FE-0: no Watch mode or simulated edit. `▸ Details`
// expands it in place (slot dots, a mini log with each try's sha;
// remembered). Waiting for a local confirm: the reason, Cancel and `Simulate the owner
// confirming`. Offline: its own line and next step. Stop is a named confirm
// with Undo. `placement="results"` (C13) sits in the results RunBar.
import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  ButtonBase,
  Collapse,
  Link,
  Stack,
  Typography,
} from "@mui/material";
import { useNavigate } from "react-router-dom";
import Iconify from "src/components/iconify";
import { useRunActivity, useRunControl } from "src/api/develop/r4";
import ConfirmDialog from "src/sections/common/ux/ConfirmDialog";
import KeyHintTooltip from "src/sections/common/ux/KeyHintTooltip";
import { notify } from "src/sections/common/ux/toast";
import useShortcuts, {
  useCurrentSurface,
} from "src/sections/common/ux/useShortcuts";
import useLayoutPrefs from "src/sections/common/ux/oneview/layoutPrefs";
import { fmtMs } from "src/sections/common/format/fmt";
import { isPrototypeMode } from "src/utils/prototype";
import { useNow, useReadOnly, useRefreshMachines } from "./useConnect";
import {
  localConfirmLine,
  offlineLine,
  ownerConfirmEvent,
  reloadLine,
  rerunToast,
  resumeLabel,
  stoppedToast,
  stopConfirmOf,
  stripLine,
  tryLineText,
} from "./connectCopy";
import { elapsedMs } from "./connectModel";
import { Dot, ReasonTip, SimulatedTag } from "./connectParts";

/** The file a watched worker reloads on (`agent.py` from `agent.py:graph`). */
export function watchedFileOf(a) {
  const file = String(a?.agent?.entry ?? "")
    .split(":")[0]
    .trim();
  return /^[\w./-]+\.(py|ts|js|mjs)$/.test(file) ? file : "your code";
}

const W_SURFACES = ["results", "leaderboard", "matrix", "drawer"];

/** Is this run on a connected machine? */
export const isLocalRun = (run) => run?.config?.execution?.mode === "local";

function SlotDots({ slots, rows }) {
  return (
    <Stack direction="row" gap={1} flexWrap="wrap" alignItems="center">
      {Array.from({ length: slots ?? 0 }).map((_, i) => {
        const r = rows?.[i];
        return (
          <Stack
            key={`slot-${i + 1}`}
            direction="row"
            gap={0.5}
            alignItems="center"
          >
            <Dot tone={r ? "success" : "grey"} pulse={Boolean(r)} size={8} />
            <Typography
              typography="s3"
              color={r ? "text.primary" : "text.secondary"}
            >
              {r ? `${r.row_label} · try ${r.trial}` : "free"}
            </Typography>
          </Stack>
        );
      })}
    </Stack>
  );
}
SlotDots.propTypes = { slots: PropTypes.number, rows: PropTypes.array };

export default function RunConnectionStrip({
  run,
  keys = true,
  onOpenRun,
  showLabel = false,
  placement = "page",
  sx,
}) {
  const local = isLocalRun(run);
  const runId = run?.id ?? run?.run_id;
  const navigate = useNavigate();
  const readOnly = useReadOnly();
  const proto = isPrototypeMode();
  const inResults = placement === "results";
  const [poll, setPoll] = useState(1000);
  const q = useRunActivity(runId, { enabled: local && Boolean(runId), poll });
  const a = q.data;
  const control = useRunControl();
  const [confirm, setConfirm] = useState(false);
  const [prefs, patchPrefs] = useLayoutPrefs("runStrip");
  const open = Boolean(prefs.sections?.details);
  const live = ["running", "stopped", "waiting_local_confirm"].includes(
    a?.state,
  );
  const now = useNow(1000, Boolean(a?.reload) && live);
  const current = useCurrentSurface();
  const surface = W_SURFACES.includes(current) ? current : "results";

  React.useEffect(() => {
    const next =
      a?.state === "running" || a?.state === "waiting_local_confirm"
        ? 1000
        : 5000;
    if (a && next !== poll) setPoll(next);
  }, [a, poll]);

  const refreshMachines = useRefreshMachines();
  // Runners' `Running now` reads the same activity: refresh it after a control.
  const send = (action, extra = {}) =>
    control.mutateAsync({ runId, action, ...extra }).then((res) => {
      refreshMachines();
      return res;
    });
  const reloadPending =
    Boolean(a?.reload) &&
    a?.reload?.sha !== a?.sha &&
    a?.state !== "waiting_local_confirm";

  const rerun = () =>
    send("rerun_changed").then((res) =>
      notify(rerunToast(res), {
        key: `rerun-${runId}`,
        variant: "success",
        action: () => (
          <Button
            size="small"
            color="inherit"
            onClick={() =>
              onOpenRun
                ? onOpenRun(res)
                : navigate(
                    `/dashboard/develop/experiment/${res.experiment_id}/data`,
                  )
            }
          >
            Open
          </Button>
        ),
      }),
    );

  useShortcuts(
    surface,
    {
      rerunChanged: {
        run: rerun,
        when: () => reloadPending && !readOnly,
        reason: "Needs a code reload on a live local run",
      },
    },
    { enabled: local && keys && reloadPending, register: false },
  );

  if (!local) return null;
  if (!a)
    return (
      <Box
        sx={{
          height: 32,
          display: "flex",
          alignItems: "center",
          gap: 1,
          ...sx,
        }}
      >
        <Dot tone="grey" pulse size={8} />
        <Typography typography="s2" color="text.secondary">
          Reading {run?.config?.execution?.label ?? "the machine"}…
        </Typography>
      </Box>
    );

  const stop = stopConfirmOf(a);
  const stopNow = () => {
    setConfirm(false);
    send("stop").then((res) =>
      notify(stoppedToast(a, res), {
        key: `stop-${runId}`,
        undo: { onUndo: () => send("resume") },
      }),
    );
  };

  let line = stripLine(a);
  // IT-R1 G1 P5: a run from the user's terminal reads the run's status; we
  // cannot stop the user's terminal, so it has no Stop / Watch / Details.
  const terminal = Boolean(a.terminal);
  let tone =
    a.state === "running" || a.state === "scoring"
      ? "success"
      : a.state === "done"
        ? "success"
        : "grey";
  if (a.state === "waiting_local_confirm") {
    line = localConfirmLine(a);
    tone = "warning";
  } else if (a.state === "offline") {
    line = offlineLine(
      a.machine,
      elapsedMs(a.machine?.last_seen_at, Date.now()),
    );
    tone = "grey";
  }

  const writeReason = readOnly ? "Viewers can't control runs" : null;

  return (
    <Box
      data-run-strip={runId}
      data-strip-state={a.state}
      data-placement={placement}
      sx={{
        border: inResults ? "none" : "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        bgcolor: inResults ? "transparent" : "background.paper",
        px: inResults ? 0 : 1.25,
        py: inResults ? 0 : 0.5,
        ...sx,
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        gap={1}
        sx={{ minHeight: 30 }}
        flexWrap="wrap"
      >
        <Dot
          tone={tone}
          pulse={a.state === "running" || a.state === "scoring"}
          size={8}
        />
        <Typography
          typography="s2"
          color="text.primary"
          sx={{ fontVariantNumeric: "tabular-nums", minWidth: 0 }}
        >
          {showLabel && a.run_label ? (
            <Box component="span" sx={{ fontWeight: "fontWeightSemiBold" }}>
              {a.run_label} ·{" "}
            </Box>
          ) : null}
          {line}
        </Typography>
        {/* L7c g1 (the capability contract c): no Simulated playback tag on progress. */}
        <Box sx={{ flex: 1 }} />
        {a.state === "running" && !terminal ? (
          <ReasonTip reason={writeReason}>
            <Button
              size="small"
              color="inherit"
              variant="outlined"
              disabled={Boolean(writeReason)}
              onClick={() => setConfirm(true)}
              startIcon={<Iconify icon="mdi:stop" width={16} />}
            >
              Stop
            </Button>
          </ReasonTip>
        ) : null}
        {a.state === "stopped" ? (
          <ReasonTip reason={writeReason}>
            <Button
              size="small"
              color="inherit"
              variant="outlined"
              disabled={Boolean(writeReason)}
              onClick={() => send("resume")}
              startIcon={<Iconify icon="mdi:play" width={16} />}
            >
              {resumeLabel(a)}
            </Button>
          </ReasonTip>
        ) : null}
        {a.state === "waiting_local_confirm" ? (
          <>
            <Button size="small" color="inherit" onClick={() => send("cancel")}>
              Cancel
            </Button>
            {proto ? (
              <Stack direction="row" alignItems="center" gap={0.5}>
                <Link
                  component="button"
                  type="button"
                  typography="s2"
                  data-testid="simulate-owner-confirm"
                  onClick={() => send("simulate_local_confirm")}
                >
                  Simulate the owner confirming
                </Link>
                <SimulatedTag
                  event={ownerConfirmEvent(
                    a.pending_confirm?.owner ?? a.machine?.owner?.name,
                    a.machine?.name,
                  )}
                />
              </Stack>
            ) : null}
          </>
        ) : null}
        {/* dataset integration FE-0: no Watch mode (a live connection that reloads on
            save does not exist). */}
        {terminal ? null : (
          <ButtonBase
            aria-expanded={open}
            onClick={() => patchPrefs({ sections: { details: !open } })}
            sx={{
              typography: "s2",
              color: "text.secondary",
              px: 0.5,
              borderRadius: 0.5,
              gap: 0.25,
            }}
          >
            <Iconify
              icon={open ? "mdi:chevron-down" : "mdi:chevron-right"}
              width={16}
            />
            Details
          </ButtonBase>
        )}
      </Stack>
      {reloadPending ? (
        <Stack
          direction="row"
          alignItems="center"
          gap={1}
          sx={{ py: 0.5 }}
          data-reload-line
        >
          <Iconify
            icon="mdi:file-document-edit-outline"
            width={16}
            sx={{ color: "info.main" }}
          />
          <Typography typography="s2" color="text.primary" sx={{ minWidth: 0 }}>
            {reloadLine(a, a.reload, now).split(" · ").slice(0, 2).join(" · ")}{" "}
            ·{" "}
          </Typography>
          {!terminal ? (
            <KeyHintTooltip
              title="Start a new run with the new code"
              action="rerunChanged"
            >
              <span>
                <Link
                  component="button"
                  type="button"
                  typography="s2"
                  disabled={Boolean(writeReason)}
                  onClick={rerun}
                >
                  {reloadLine(a, a.reload, now)
                    .split(" · ")
                    .slice(2)
                    .join(" · ")}{" "}
                  (W)
                </Link>
              </span>
            </KeyHintTooltip>
          ) : (
            <Typography typography="s2" color="text.secondary">
              {reloadLine(a, a.reload, now).split(" · ").slice(2).join(" · ")}
            </Typography>
          )}
        </Stack>
      ) : null}
      <Collapse in={open && !terminal} unmountOnExit>
        <Stack gap={1} sx={{ py: 1 }}>
          {a.state === "running" ? (
            <SlotDots slots={a.slots} rows={a.slot_rows} />
          ) : null}
          <Stack
            component="ol"
            gap={0.25}
            sx={{
              m: 0,
              p: 0,
              listStyle: "none",
              maxHeight: 180,
              overflow: "auto",
            }}
          >
            {(a.lines ?? []).map((l) => (
              <Typography
                key={`${l.row_id}-${l.trial}-${l.at}`}
                component="li"
                typography="s3"
                color={
                  l.passed === false
                    ? "error.main"
                    : l.stopped
                      ? "text.secondary"
                      : "text.primary"
                }
                sx={{ fontVariantNumeric: "tabular-nums" }}
              >
                {tryLineText(l, { fmtMs })}
              </Typography>
            ))}
            {!a.lines?.length ? (
              <Typography component="li" typography="s3" color="text.secondary">
                No try has finished yet.
              </Typography>
            ) : null}
          </Stack>
          <Typography typography="s3" color="text.secondary">
            Every try runs on the code the run started with ({a.sha}); stopped
            tries never count toward the pass rate.
          </Typography>
        </Stack>
      </Collapse>
      <ConfirmDialog
        open={confirm}
        title={stop.title}
        body={stop.body}
        confirmLabel="Stop run"
        cancelLabel="Keep running"
        onConfirm={stopNow}
        onClose={() => setConfirm(false)}
      />
    </Box>
  );
}

RunConnectionStrip.propTypes = {
  run: PropTypes.object,
  /** Additive (implementation): bind `W` while a reload is pending (default true). */
  keys: PropTypes.bool,
  /** Additive (implementation): lead with the run label (lists of strips). */
  showLabel: PropTypes.bool,
  /** C13: `results` = inline in the results RunBar (no border); default `page`. */
  placement: PropTypes.oneOf(["page", "results"]),
  /** Additive (implementation): open the rerun the toast names (default: its experiment). */
  onOpenRun: PropTypes.func,
  sx: PropTypes.object,
};
