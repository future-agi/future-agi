// Section 2 of `Trace + upload` (lane Ra MUST 4, persona fix P3): what
// happens next. Waiting line with a timer, a checklist that ticks from the
// activity (`First trace with this run's id` · `First result uploaded` ·
// `Rows 0 / 30`), a 60 s hint, and the arrival line `Your run
// is in · T1 passed` with `Open results` · `Open the trace for T1`.
// `Simulate your terminal` (or the CI job) stands in for the external event
// (S2); it sends the draft's config, tries and row scope (C15), so the rows
// that arrive are the ones this run would produce.
import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  CircularProgress,
  Link,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { fmtCount, fmtMs } from "src/sections/common/format/fmt";
import TraceDetailDrawerV2 from "src/components/traceDetail/TraceDetailDrawerV2";
import { useExperimentForm } from "src/sections/develop-detail/Experiment/ExperimentFormContext";
import { VOCAB } from "src/sections/common/vocab";
import { useRunActivity } from "src/api/develop/r4";
import {
  STAND_IN_EVENTS,
  liveCountsOf,
  standInTip,
  terminalStripLine,
} from "src/sections/common/Connect/connectCopy";
import { useWizardLayer } from "./pickerUi";

export const HINT_AFTER_MS = 60_000;

/**
 * The stand-in of each place a run starts from (S2). `event` names the
 * machine the button stands in for, in the one tag wording (standInTip).
 */
export const SIMULATE_OF = Object.freeze({
  terminal: {
    label: "Simulate your terminal",
    event: STAND_IN_EVENTS.terminal,
    running: "Running on your machine",
  },
  ci_job: {
    label: "Simulate the CI job",
    event: STAND_IN_EVENTS.ci_job,
    running: "Running in CI",
    waiting: "Waiting for the CI job",
  },
  upload: {
    label: "Simulate the upload",
    event: STAND_IN_EVENTS.upload,
    running: "Uploading",
  },
  sandbox: {
    label: "Simulate the sandbox starting",
    event: STAND_IN_EVENTS.sandbox,
    running: "Running in the sandbox",
    waiting: "Waiting for the sandbox",
  },
});

/** Rows × tries of a scope: `1 task × 3 tries` → 3 (pure). */
export function attemptsOf(rows, tries) {
  const r = Number(rows);
  const t = Math.max(1, Number(tries) || 1);
  return Number.isFinite(r) && r > 0 ? r * t : null;
}

/**
 * The C15 draft simulate body (pure). options-truth L2 G3 (L3): the
 * config carries every limit the drawer set (tries, at a time, cost cap,
 * time limit), so the stream stops and times out where the run will.
 */
export function simulateBody({
  simulateRows,
  datasetId,
  entryId,
  tries,
  config,
  rowIds,
  splitIds,
  as = "terminal",
}) {
  const body = {
    rows: simulateRows,
    as,
    dataset_id: datasetId,
    entry_id: entryId,
    tries: Math.max(1, Number(tries) || 1),
  };
  // `tries` rides next to the config (the stream plays body.tries).
  if (config) body.config = config;
  if (rowIds?.length) body.row_ids = rowIds;
  else if (splitIds?.length) body.split_ids = splitIds;
  return body;
}

/** `0:12` / `1:04` (m:ss) for the waiting timer (pure). */
export function clockOf(ms) {
  const s = Math.max(0, Math.floor((Number(ms) || 0) / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** `1 m 04 s` (the first-result line). */
export function spanOf(ms) {
  const s = Math.max(0, Math.round((Number(ms) || 0) / 1000));
  const m = Math.floor(s / 60);
  return m ? `${m} m ${String(s % 60).padStart(2, "0")} s` : fmtMs(s * 1000);
}

const ITEMS = [
  { id: "trace", label: "First trace with this run's id" },
  { id: "result", label: "First result uploaded" },
  { id: "rows", label: null },
];
// A sandbox run starts itself: nothing is uploaded from the user's shell.
const SANDBOX_ITEMS = [
  { id: "trace", label: "Sandbox started" },
  { id: "result", label: "First result scored" },
  { id: "rows", label: null },
];

/**
 * The `Simulated` tag next to a stand-in button (S2), with the one wording
 * `Stands in for <the machine> sending results.` (L7c g1, the capability contract c: no
 * playback variant on progress lines).
 */
export function SimulatedTag({ event }) {
  return (
    <CustomTooltip show size="small" arrow title={standInTip(event)}>
      <Typography
        typography="s3"
        color="text.secondary"
        data-testid="simulated-tag"
        sx={{
          px: 0.75,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 0.5,
          flexShrink: 0,
        }}
      >
        {VOCAB.SIMULATED}
      </Typography>
    </CustomTooltip>
  );
}
SimulatedTag.propTypes = { event: PropTypes.string };

function Check({ done, children }) {
  return (
    <Stack direction="row" alignItems="center" gap={0.75}>
      <Iconify
        icon={done ? "mdi:check-circle" : "mdi:circle-outline"}
        width={16}
        sx={{ color: done ? "success.main" : "text.disabled", flexShrink: 0 }}
      />
      <Typography
        typography="s2"
        color={done ? "text.primary" : "text.secondary"}
      >
        {children}
      </Typography>
    </Stack>
  );
}
Check.propTypes = { done: PropTypes.bool, children: PropTypes.node };

export default function TodayStatus({
  runId,
  rowCount,
  rowNoun = "row",
  openedAtMs,
  compact = false,
  arrival,
  onArrival,
  onOpenResults,
  projectHref,
  nowMs,
  simulateRows = 1,
  openResultsReason = "Results open once you add this run and run the experiment",
  openResultsLabel = "Open results",
  tries = 1,
  simulateRef,
  as = "terminal",
  follow = false,
  waitingText,
}) {
  const stand = SIMULATE_OF[as] ?? SIMULATE_OF.terminal;
  // IT-R4 G1 P2: pass / fail only when the Evaluations step holds an eval.
  const { evalCount } = useExperimentForm();
  const scored = evalCount == null || evalCount > 0;
  // S3: `1 task` simulates one row (`1 of 1`), `All 24 tasks` every row.
  // options-truth L1 G2: the denominator is rows in scope × tries.
  const totalRows = attemptsOf(simulateRows === 1 ? 1 : rowCount, tries);
  const [now, setNow] = useState(() => Date.now());
  const [ticks, setTicks] = useState(arrival ? 3 : 0);
  const [trace, setTrace] = useState(false);
  // IT-R2 G1 P1: the trace drawer is the top layer; Escape closes it only
  // (captured before the wizard and the page shortcuts see it). A menu or
  // dialog opened inside the trace keeps its own Escape.
  const traceOpen = Boolean(trace && arrival?.trace_id);
  useWizardLayer(traceOpen);
  useEffect(() => {
    if (!traceOpen) return undefined;
    const onKey = (e) => {
      if (e.key !== "Escape" || e.defaultPrevented) return;
      const t = e.target;
      if (
        t?.closest?.(
          '[role="menu"],[role="listbox"],[role="dialog"],.MuiPopover-root',
        )
      )
        return;
      e.preventDefault();
      e.stopPropagation();
      setTrace(false);
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [traceOpen]);
  // IT-R2 G1 P2: the clock runs while rows arrive and stops when all are in.
  const stopped = Boolean(arrival?.finished);
  useEffect(() => {
    if (stopped) return undefined;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [stopped]);
  const elapsed = Math.max(0, (nowMs ?? now) - (openedAtMs ?? now));
  const notStarted = openedAtMs == null;
  // IT-R1 G1 P5: a created run streams its rows from the run itself (the
  // same status the experiment header and strip read), with live counts.
  // A stream that started in the draft keeps going after Add / Run (the
  // created run continues the same clock).
  // L2 G3 (L4): a run that starts itself (the sandbox) is followed from
  // the start; no stand-in event is needed.
  const [streaming, setStreaming] = useState(
    () =>
      (follow && !arrival?.finished) ||
      (simulateRows === "all" && Boolean(arrival) && !arrival?.finished),
  );
  // L2 G3 (one count): the compact footer reads the same run's activity
  // while its rows stream, so it never repeats a stale `T1 passed`.
  const footerRunId =
    compact && arrival?.run_id && !arrival?.finished ? arrival.run_id : null;
  const live = useRunActivity(runId ?? footerRunId, {
    enabled: Boolean(footerRunId) || (Boolean(runId) && streaming),
    poll: footerRunId ? 1000 : 300,
  });
  const act = live.data?.terminal ? live.data : null;
  useEffect(() => {
    if (!streaming || !act) return;
    if (follow && act.status && act.status !== "Queued" && ticks < 1)
      setTicks(1);
    if (act.done > 0 && ticks < 2) setTicks(2);
    // A followed run: its first scored row is the arrival (trace link).
    if (follow && !arrival && act.first_result && act.done > 0)
      onArrival?.({
        ...act.first_result,
        uploaded: act.done,
        at: Date.now(),
      });
    // L2 G3 (S5): the run ended (every try in, or stopped at its cap):
    // the footer reads its tries, never `N of 1`.
    const ended =
      act.state === "done" || (act.state === "stopped" && act.stop_reason);
    if (ended) {
      setStreaming(false);
      setTicks(3);
      onArrival?.({
        ...(arrival ?? act.first_result ?? {}),
        uploaded: act.tries_done ?? act.done,
        total: act.tries_total ?? act.total,
        rows_done: act.rows_done,
        rows_total: act.rows_total,
        trials: act.trials,
        stop_line: act.stop_reason
          ? liveCountsOf(act, { rowNoun, rowCount, tries }).footer
          : null,
        passed_count: act.tries_passed ?? act.passed,
        failed_count: act.tries_failed ?? act.failed,
        finished: true,
        at: arrival?.at ?? Date.now(),
        finished_at: act.finished_at ? Date.parse(act.finished_at) : Date.now(),
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [act?.done, act?.state, act?.status, streaming]);

  // Compatibility for hosts that still pass a footer ref: no synthetic action.
  if (simulateRef) simulateRef.current = null;
  const first = arrival;
  const passed = first?.status === "passed";
  const rowsDone = act ? act.done : first ? first.uploaded ?? 1 : 0;
  const countsRaw =
    act && act.done > 0
      ? `${fmtCount(act.passed)} passed · ${fmtCount(act.failed)} failed`
      : first?.finished
        ? `${fmtCount(first.passed_count ?? 0)} passed · ${fmtCount(first.failed_count ?? 0)} failed`
        : null;
  const counts = countsRaw && !scored ? null : countsRaw;
  const unscoredLine = (n) =>
    `${fmtCount(n)} ${n === 1 ? "result" : "results"} uploaded · add evals (step 3 of the wizard) to score them`;
  const finished = Boolean(first?.finished) || act?.state === "done";

  if (compact)
    return (
      <Stack
        direction="row"
        alignItems="center"
        gap={0.75}
        sx={{ minWidth: 0 }}
        data-testid="today-status-compact"
      >
        {first ? (
          <Iconify
            icon={passed ? "mdi:check-circle" : "mdi:close-circle"}
            width={16}
            sx={{ color: passed ? "success.main" : "error.main" }}
          />
        ) : (
          <CircularProgress
            size={12}
            thickness={5}
            sx={{ color: "text.disabled" }}
          />
        )}
        <Typography
          typography="s2"
          color="text.secondary"
          title={undefined}
          sx={{
            minWidth: 0,
            // IT-R4 G1 P2: at most two lines, never `T1 p…`.
            display: "-webkit-box",
            WebkitLineClamp: 2,
            WebkitBoxOrient: "vertical",
            overflow: "hidden",
          }}
        >
          {first?.finished
            ? scored
              ? first.stop_line ??
                `Results in · ${fmtCount(first.uploaded ?? 0)} of ${fmtCount(first.total ?? totalRows ?? first.uploaded ?? 0)}${(first.trials ?? tries) > 1 ? " tries" : ""} · ${fmtCount(first.passed_count ?? 0)} passed · ${fmtCount(first.failed_count ?? 0)} failed`
              : unscoredLine(first.uploaded ?? 0)
            : first && act?.rows_done > 0
              ? `${stand.running} · ${scored ? liveCountsOf(act, { rowNoun, rowCount, tries }).checklist : `${fmtCount(act.rows_done)} of ${fmtCount(act.rows_total)}`}`
              : first
                ? `${stand.running} · ${first.row_label} ${scored ? (passed ? "passed" : "failed") : "in"}, more rows arriving`
                : notStarted
                  ? "Not started · copy the run command to start"
                  : `${stand.waiting ?? "Waiting for your first upload"}… ${clockOf(elapsed)}`}
        </Typography>
      </Stack>
    );

  // `0:08` since the stream started; frozen when every row is in.
  const clockEnd = first?.finished_at ?? (finished ? first?.at : null);
  const runClock =
    openedAtMs != null
      ? clockOf((clockEnd ?? nowMs ?? now) - openedAtMs)
      : null;
  return (
    <Stack spacing={1} data-testid="today-status">
      {first ? (
        <Stack spacing={0.5} data-testid="first-result">
          <Stack direction="row" alignItems="center" gap={0.75} flexWrap="wrap">
            <Iconify
              icon={
                finished
                  ? "mdi:check-circle"
                  : streaming || (act && act.done > 0)
                    ? "mdi:progress-clock"
                    : passed
                      ? "mdi:check-circle"
                      : "mdi:close-circle"
              }
              width={18}
              sx={{
                color:
                  finished || (!streaming && passed)
                    ? "success.main"
                    : streaming
                      ? "primary.main"
                      : "error.main",
              }}
            />
            <Typography
              typography="s1"
              fontWeight="fontWeightSemiBold"
              data-testid="run-line"
            >
              {finished
                ? counts
                  ? first?.stop_line ??
                    (first?.total != null
                      ? `Results in · ${fmtCount(first.uploaded ?? rowsDone)} of ${fmtCount(first.total)}${(first.trials ?? tries) > 1 ? " tries" : ""} · ${counts}`
                      : `Results in · ${fmtCount(rowsDone)} of ${fmtCount(totalRows ?? rowsDone)} · ${counts}`)
                  : unscoredLine(rowsDone)
                : act && act.done > 0
                  ? scored
                    ? terminalStripLine(act)
                        .replace("Running on your machine", stand.running)
                        .replace(
                          "ran on your machine",
                          stand.running.replace(/^Running/, "ran"),
                        )
                    : `${stand.running} · ${fmtCount(rowsDone)} of ${fmtCount(totalRows ?? rowsDone)}`
                  : streaming
                    ? `${stand.running} · ${fmtCount(rowsDone)} of ${fmtCount(totalRows ?? rowsDone)}`
                    : `Your run is in · ${first.row_label}${scored ? ` ${passed ? "passed" : "failed"}` : ""}`}
            </Typography>
            {runClock && (streaming || finished) ? (
              <Typography
                typography="s2"
                color="text.secondary"
                data-testid="run-clock"
                sx={{ fontVariantNumeric: "tabular-nums" }}
              >
                {finished ? `· took ${runClock}` : `· ${runClock}`}
              </Typography>
            ) : null}
            <Typography typography="s2" color="text.disabled">
              ·
            </Typography>
            <Link
              component="button"
              type="button"
              typography="s2"
              disabled={!first.trace_id}
              onClick={() => first.trace_id && setTrace(true)}
              data-testid="open-first-trace"
            >
              {`Open the trace for ${first.row_label}`}
            </Link>
            {projectHref ? (
              <>
                <Typography typography="s2" color="text.disabled">
                  ·
                </Typography>
                <Link
                  href={projectHref}
                  target="_blank"
                  rel="noopener"
                  typography="s2"
                  data-testid="open-run-traces"
                >
                  Open traces for this run →
                </Link>
              </>
            ) : null}
          </Stack>
          {onOpenResults &&
          (finished || (simulateRows !== "all" && !streaming)) ? (
            // IT-R2 G1 P2: the next step, never a disabled dead end.
            <Box>
              <Button
                size="small"
                variant={finished ? "contained" : "outlined"}
                color="primary"
                onClick={onOpenResults}
                data-testid="open-results"
                endIcon={<Iconify icon="mdi:arrow-right" width={16} />}
              >
                {openResultsLabel}
              </Button>
            </Box>
          ) : !onOpenResults && openResultsReason && !streaming ? (
            <Typography
              component="span"
              typography="s2"
              color="text.secondary"
              data-testid="open-results-reason"
            >
              {openResultsReason}
            </Typography>
          ) : null}
        </Stack>
      ) : (
        <Stack direction="row" alignItems="center" gap={0.75}>
          <CircularProgress
            size={12}
            thickness={5}
            sx={{ color: "text.disabled" }}
          />
          <Typography typography="s2" data-testid="waiting-line">
            {notStarted
              ? "Not started · the clock starts when you copy the run command"
              : follow
                ? act && act.status && act.status !== "Queued"
                  ? `${stand.running} · ${terminalStripLine(act)
                      .split(" · ")
                      .slice(1)
                      .join(" · ")}`
                  : waitingText ?? `${stand.running}…`
                : `${stand.waiting ?? "Waiting for your first upload"}… ${clockOf(elapsed)}`}
          </Typography>
        </Stack>
      )}
      {/* Finished: the headline says it all (no checklist under it). */}
      <Stack
        spacing={0.5}
        sx={{ pl: 0.25, display: finished ? "none" : undefined }}
      >
        {(as === "sandbox" ? SANDBOX_ITEMS : ITEMS).map((it, i) => (
          <Check
            key={it.id}
            done={
              it.id === "rows"
                ? finished || (totalRows != null && rowsDone >= totalRows)
                : ticks > i
            }
          >
            {it.id === "rows"
              ? act?.rows_total != null && scored
                ? liveCountsOf(act, { rowNoun, rowCount, tries }).checklist
                : (Number(tries) || 1) > 1
                  ? `Tries ${fmtCount(Math.min(rowsDone, totalRows ?? rowsDone))} of ${fmtCount(totalRows ?? 0)}${counts ? ` · ${counts}` : ""}`
                  : `${rowNoun === "task" ? "Tasks" : "Rows"} ${fmtCount(Math.min(rowsDone, totalRows ?? rowsDone))} of ${fmtCount(totalRows ?? 0)}${counts ? ` · ${counts}` : ""}`
              : it.label}
          </Check>
        ))}
      </Stack>
      {!first && !notStarted && !follow && elapsed >= HINT_AFTER_MS ? (
        <Stack
          direction="row"
          alignItems="flex-start"
          gap={0.75}
          data-testid="nothing-yet"
        >
          <Iconify
            icon="mdi:help-circle-outline"
            width={16}
            sx={{ color: "text.secondary", mt: "2px" }}
          />
          <Typography typography="s2" color="text.secondary">
            Nothing yet? Run it with --limit 1 first, check FI_API_KEY in that
            shell, and open the trace project to see whether spans arrive.{" "}
            {projectHref ? (
              <Link
                href={projectHref}
                target="_blank"
                rel="noopener"
                typography="s2"
              >
                Open trace project
              </Link>
            ) : null}
          </Typography>
        </Stack>
      ) : null}
      {trace && first?.trace_id ? (
        <TraceDetailDrawerV2
          traceId={first.trace_id}
          open={trace}
          onClose={() => setTrace(false)}
        />
      ) : null}
    </Stack>
  );
}

TodayStatus.propTypes = {
  datasetId: PropTypes.string,
  entryId: PropTypes.string,
  runId: PropTypes.string,
  rowCount: PropTypes.number,
  rowNoun: PropTypes.string,
  openedAtMs: PropTypes.number,
  tryCommand: PropTypes.string,
  compact: PropTypes.bool,
  arrival: PropTypes.object,
  onArrival: PropTypes.func,
  onOpenResults: PropTypes.func,
  projectHref: PropTypes.string,
  nowMs: PropTypes.number,
  simulateRows: PropTypes.oneOf([1, "all"]),
  /** Additive (IT-R1 G1): Simulate was clicked (the clock starts). */
  onSimulate: PropTypes.func,
  /** Additive (IT-R1 G1 P5): why `Open results` is not a link yet. */
  openResultsReason: PropTypes.string,
  /** Additive (IT-R2 G1 P2): `Add to experiment and open results`. */
  openResultsLabel: PropTypes.string,
  /** Additive (IT-R2 G1 P2): tries per row of the simulated stream. */
  tries: PropTypes.number,
  /** Additive (IT-R5 G1 P3): start the stand-in once mounted. */
  autoStart: PropTypes.bool,
  /** Additive (IT-R5 G1 P3): called when autoStart is consumed. */
  onAutoStart: PropTypes.func,
  /** Additive (IT-R2 G1 P9): receives `simulate` for a footer button. */
  simulateRef: PropTypes.object,
  /** Additive (IT-R3 G1 P3): false when the panel's footer has the one
   * Simulate button. */
  inlineSimulate: PropTypes.bool,
  /** Additive (ot-L1 G2, C15): the draft's run config, sent with Simulate. */
  config: PropTypes.object,
  rowIds: PropTypes.array,
  splitIds: PropTypes.array,
  /** Which external event Simulate stands in for (SIMULATE_OF). */
  as: PropTypes.oneOf(["terminal", "ci_job", "upload", "sandbox"]),
  /** Additive (ot-L2 G3): follow a run that starts itself (the sandbox). */
  follow: PropTypes.bool,
  /** Additive (ot-L2 G3): the line before the first row (`Sandbox starting · …`). */
  waitingText: PropTypes.string,
};
