import React, { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import { Link as RouterLink } from "react-router-dom";
import { Box, Button, Stack, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { useExperimentDetail } from "src/api/develop/runs";
import { useRunAnalysis } from "src/api/develop/analysis";
import { useRunConfigs } from "src/api/develop/r2";
import RunChip from "src/sections/common/Runs/RunChip";
import {
  getRunStatus,
  pollIntervalFor,
} from "src/sections/common/Runs/runStatus";
import { trackRuns } from "./runTrayStore";
import {
  isExternalWaiting,
  waitNounOf,
  waitNounOfAll,
  waitingOf,
} from "./waitingModel";

function WaitingRunLine({ run, config }) {
  const withConfig = { ...run, config: run.config ?? config };
  const wait = waitingOf(withConfig);

  return (
    <Box data-testid="waiting-run">
      <Stack
        direction="row"
        alignItems="center"
        gap={0.75}
        flexWrap="wrap"
        sx={{ minHeight: 30 }}
      >
        {/* The config names the source, so the chip shows its mark. */}
        <RunChip
          run={withConfig}
          showName={false}
          showStatus={false}
          size="small"
        />
        <Typography
          typography="s2"
          color="text.primary"
          sx={{ fontWeight: "fontWeightMedium" }}
        >
          {run.name}
        </Typography>
        {wait ? (
          <Typography
            typography="s2"
            color="text.secondary"
            data-testid="waiting-title"
          >
            {wait.title}
          </Typography>
        ) : null}
      </Stack>
    </Box>
  );
}

WaitingRunLine.propTypes = {
  run: PropTypes.object.isRequired,
  config: PropTypes.object,
  experimentId: PropTypes.string.isRequired,
  datasetId: PropTypes.string,
};

/**
 * IT-R5 G1 P1: `1 of 24 received · waiting for the other 23 from your
 * terminal` once a row is in; `#2A LangGraph waits for your terminal` before.
 */
export function waitingTitle(pending) {
  if (pending.length === 1) {
    const r = pending[0];
    const done = r.progress?.done ?? 0;
    const total = r.progress?.total ?? 0;
    const noun = waitNounOf(r);
    if (done > 0 && total > done)
      return `${done} of ${total} received · waiting for the other ${total - done} from ${noun}`;
    return `${r.label ?? ""} ${r.name} waits for ${noun}`.trim();
  }
  const noun = waitNounOfAll(pending);
  const done = pending.reduce((a, r) => a + (r.progress?.done ?? 0), 0);
  const total = pending.reduce((a, r) => a + (r.progress?.total ?? 0), 0);
  if (done > 0)
    return `${done} of ${total} received · ${pending.length} runs wait for ${noun}`;
  return `${pending.length} runs wait for ${noun}`;
}

/**
 * IT-R5 G1 P8: once every row of a run that waited here is in, the banner
 * says so with the next step: `24 results are in · 9 got worse than #1B
 * [Compare to baseline]` (S2: runs, baseline, cand). Dismissible.
 */
function ResultsLandedBanner({ run, datasetId, onDismiss }) {
  const { data: first } = useRunAnalysis(datasetId, { runIds: [run.run_id] });
  const baseId =
    first?.dataset_baseline?.run_id ??
    first?.dataset_baseline?.baseline_run_id ??
    null;
  const pair = baseId && baseId !== run.run_id;
  const { data: vs } = useRunAnalysis(datasetId, {
    runIds: pair ? [baseId, run.run_id] : [],
    baselineId: pair ? baseId : undefined,
    enabled: Boolean(pair),
  });
  // Tasks that got worse (the Matrix rule, R3 P5), from the same analysis.
  const worse =
    (vs?.runs ?? []).find((r) => r.run_id === run.run_id)?.tasks_vs_baseline
      ?.worse ?? null;
  const baseLabel =
    first?.dataset_baseline?.label ?? vs?.baseline_label ?? "the baseline";
  const total = run.progress?.total ?? 0;
  const href = pair
    ? `/dashboard/develop/${datasetId}/compare/data?runs=${baseId},${run.run_id}&baseline=${baseId}&cand=${run.run_id}`
    : null;
  const noun = total === 1 ? "result is" : "results are";
  return (
    <Box
      role="status"
      aria-live="polite"
      data-testid="external-run-landed-banner"
      sx={(theme) => ({
        mx: 2,
        mt: 1,
        mb: 0.5,
        px: 1.5,
        py: 0.75,
        borderRadius: 1,
        border: "1px solid",
        borderColor: alpha(theme.palette.success.main, 0.35),
        bgcolor: alpha(theme.palette.success.main, 0.06),
      })}
    >
      <Stack direction="row" alignItems="center" gap={1} flexWrap="wrap">
        <Iconify
          icon="mdi:check-circle-outline"
          width={18}
          sx={{ color: "success.main" }}
        />
        <Typography typography="s1" fontWeight="fontWeightMedium">
          {`${total} ${noun} in`}
          {pair && worse != null
            ? ` · ${worse} got worse than ${baseLabel}`
            : ""}
        </Typography>
        {href ? (
          // options-truth L7b G4 (after:results-landed-banner): an in-app
          // link, so the simulated results stay in the world (a full page
          // load would reset it).
          <Button
            size="small"
            variant="outlined"
            color="primary"
            component={RouterLink}
            to={href}
            data-testid="landed-compare"
          >
            Compare to baseline
          </Button>
        ) : null}
        <Box sx={{ flex: 1 }} />
        <CustomTooltip show title="Dismiss" size="small" arrow>
          <Button
            size="small"
            color="inherit"
            onClick={onDismiss}
            aria-label="Dismiss"
            sx={{ minWidth: 28, px: 0.5, color: "text.secondary" }}
          >
            <Iconify icon="mdi:close" width={16} />
          </Button>
        </CustomTooltip>
      </Stack>
    </Box>
  );
}

ResultsLandedBanner.propTypes = {
  run: PropTypes.object.isRequired,
  datasetId: PropTypes.string.isRequired,
  onDismiss: PropTypes.func,
};

/**
 * ExternalRunWaitingBanner — "waiting for your results" banner above the
 * experiment results grid (R3 P3, CR#39 / CR#1). One line per From code run
 * still waiting: a live strip (waiting → connected · n of N received →
 * stalled after 2 min of silence), the run id to copy, `How to upload` and
 * the stand-in by the run's mode (`Simulate your terminal`, …). Nothing when no run waits.
 * Registers the waiting runs in the run tray (CR#42).
 *
 * Props (frozen):
 * @param {string} props.experimentId
 * @param {string[]} props.runs  the experiment's run ids, in order (read the
 *   run objects with useExperimentDetail(experimentId); same cache, no
 *   extra request)
 */
export default function ExternalRunWaitingBanner({ experimentId, runs = [] }) {
  const { data: detail } = useExperimentDetail(experimentId, {
    refetchInterval: (q) =>
      pollIntervalFor(q.state.data?.data?.result?.runs ?? []) ? 3000 : false,
    // L7c g5 (runtray:removed-toast): this read shares the run tray's query
    // (["experiment", id]); an observer without this meta made a deleted
    // experiment's 404 a red toast next to the tray's neutral "removed" one.
    meta: { errorHandled: true },
  });
  const ordered = useMemo(() => {
    const list = detail?.runs ?? [];
    const byId = new Map(list.map((r) => [r.run_id, r]));
    const ids = runs.length ? runs : list.map((r) => r.run_id);
    return ids.map((id) => byId.get(id)).filter(Boolean);
  }, [detail, runs]);
  const waiting = ordered.filter(isExternalWaiting);
  const datasetId = detail?.dataset_id ?? detail?.dataset?.id ?? null;
  const waitingKey = waiting.map((r) => r.run_id).join(",");
  // IT-R1 G1 P4: each waiting run's own command needs its config (R1).
  const { data: configs } = useRunConfigs(datasetId, {
    enabled: Boolean(datasetId && waiting.length),
  });
  const configOf = (id) =>
    (configs?.results ?? []).find((r) => (r.run_id ?? r.id) === id)?.config ??
    null;
  useEffect(() => {
    if (waiting.length) trackRuns(waiting, { datasetId, experimentId });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [waitingKey]);
  // IT-R5 G1 P1: a run still waiting for its terminal stays here even when
  // the wizard's simulated try already landed (`1 of 24 received`).
  // L3: a run bound to a connected machine waits for that machine (or its
  // owner confirming), never for a terminal: its strip says so instead.
  const onMachine = (r) =>
    Boolean((r.config ?? configOf(r.run_id))?.execution?.machine_id);
  const pending = waiting.filter(
    (r) => getRunStatus(r).waiting && !onMachine(r),
  );
  // IT-R5 G1 P8: runs seen waiting here whose rows are all in now.
  const [seenWaiting, setSeenWaiting] = useState([]);
  const [dismissed, setDismissed] = useState(false);
  useEffect(() => {
    const ids = pending.map((r) => r.run_id);
    if (ids.some((id) => !seenWaiting.includes(id)))
      setSeenWaiting((prev) => [...new Set([...prev, ...ids])]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pending.map((r) => r.run_id).join(",")]);
  const landed = ordered.filter(
    (r) =>
      seenWaiting.includes(r.run_id) &&
      !pending.some((p) => p.run_id === r.run_id) &&
      (r.progress?.total ?? 0) > 0 &&
      (r.progress?.scored ?? 0) >= r.progress.total,
  );
  if (!pending.length) {
    if (!landed.length || dismissed || !datasetId) return null;
    return (
      <ResultsLandedBanner
        run={landed[landed.length - 1]}
        datasetId={datasetId}
        onDismiss={() => setDismissed(true)}
      />
    );
  }
  return (
    <Box
      role="status"
      aria-live="polite"
      data-testid="external-run-waiting-banner"
      sx={(theme) => ({
        mx: 2,
        mt: 1,
        mb: 0.5,
        px: 1.5,
        py: 0.75,
        borderRadius: 1,
        border: "1px solid",
        borderColor: alpha(theme.palette.info.main, 0.3),
        bgcolor: alpha(theme.palette.info.main, 0.06),
      })}
    >
      <Stack direction="row" alignItems="center" gap={1} flexWrap="wrap">
        <Iconify icon="mdi:console" width={18} sx={{ color: "info.main" }} />
        <Typography
          typography="s1"
          fontWeight="fontWeightMedium"
          data-testid="waiting-title"
        >
          {waitingTitle(
            pending.map((r) => ({
              ...r,
              config: r.config ?? configOf(r.run_id),
            })),
          )}
        </Typography>
      </Stack>
      <Stack
        direction="row"
        flexWrap="wrap"
        columnGap={2}
        rowGap={0}
        sx={{ mt: 0.25 }}
      >
        {pending.map((run) => (
          <WaitingRunLine
            key={run.run_id}
            run={run}
            config={configOf(run.run_id)}
            experimentId={experimentId}
            datasetId={datasetId}
          />
        ))}
      </Stack>
    </Box>
  );
}

ExternalRunWaitingBanner.propTypes = {
  experimentId: PropTypes.string,
  runs: PropTypes.arrayOf(PropTypes.string),
};
