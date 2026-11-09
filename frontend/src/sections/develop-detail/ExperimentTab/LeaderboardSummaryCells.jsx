// R3 P3 (SVP-P0-2b, MUST 2-3): cells of the locked headline columns —
// Success rate, Cost per task, Time per task, Total cost. One formatter
// (fmt.js), NUM_SX (tabular, right-aligned), value + delta in two fixed
// slots so digits never shift, and every value wrapper carries
// `data-review-clip` so the walk catches a clipped number.
import React from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import {
  EMPTY,
  NUM_SX,
  fmtCost,
  fmtCostDelta,
  fmtDeltaMs,
  fmtPct,
  fmtPts,
  fmtRate,
  fmtNumber,
} from "src/sections/common/format/fmt";
import { ptsDelta } from "src/sections/common/Runs/runNumbers";
import { successSignificance } from "src/sections/common/Runs/oneVerdict";
import { runKindOf, sameOutput } from "src/sections/common/Runs/runKind";
import { useCell } from "./cellContext";
import { getRunStatus } from "src/sections/common/Runs/runStatus";
import { successFrom } from "./leaderboardVersions";
import {
  servedPairedSuccess,
  costPerTask,
  pctChangeText,
  fmtSecs1,
  timePerTaskMs,
} from "./leaderboardHeadline";
import {
  bestPending,
  isRunPending,
  partialNote,
  partialProgress,
  rankPool,
  unrankedNote,
} from "./leaderboardModel";

const isNum = (v) => typeof v === "number" && Number.isFinite(v);
const TONE = {
  good: "green.600",
  bad: "red.600",
  neutral: "text.disabled",
};

// IT-R3 G2 P2: honest deltas. A run still going shows `running` where its
// cost / time delta would be; a run of another kind than the baseline (a
// text-only voice run vs a full call) shows no delta at all.
const RUNNING = "running";
// L7c g5 (ledger:stopped-run-words): a partial run that is not going any
// more reads `stopped`, never `running`.
const partialWord = (run) =>
  getRunStatus(run).key === "Cancelled"
    ? "stopped"
    : isRunPending(run)
      ? RUNNING
      : "partial";
export const DIFFERENT_KIND = "different kind of run";
const differentKind = (run, context) =>
  Boolean(context?.baselineRun) &&
  run?.run_id !== context.baselineId &&
  !sameOutput(run, context.baselineRun);
/** Where a run's cost comes from when it reports none (`—` + why). */
export const NOT_MEASURED_KEY = "Not measured: runs on your key";

const leafRuns = (node) =>
  (node.allLeafChildren ?? []).map((n) => n.data).filter(Boolean);

/** Value slot (left of the delta), right-aligned, never wraps. */
export function NumCell({
  value,
  delta,
  deltaTone,
  tip,
  muted,
  strong,
  after,
}) {
  return (
    <CustomTooltip show={!!tip} title={tip} size="small" placement="top" arrow>
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="flex-end"
        gap={after ? 0.5 : 0.75}
        data-qa-clip
        sx={{ width: "100%", height: "100%", minWidth: 0, overflow: "hidden" }}
      >
        <Typography
          component="span"
          typography="s1"
          color={muted ? "text.disabled" : "text.primary"}
          fontWeight={strong ? "fontWeightMedium" : "fontWeightRegular"}
          sx={{ ...NUM_SX, flexShrink: 0 }}
        >
          {value}
        </Typography>
        {delta ? (
          <Typography
            component="span"
            typography="s2"
            color={TONE[deltaTone] ?? "text.secondary"}
            sx={{ ...NUM_SX, flexShrink: 0 }}
          >
            {delta}
          </Typography>
        ) : null}
        {after}
      </Stack>
    </CustomTooltip>
  );
}

NumCell.propTypes = {
  value: PropTypes.node,
  delta: PropTypes.node,
  deltaTone: PropTypes.string,
  tip: PropTypes.node,
  muted: PropTypes.bool,
  strong: PropTypes.bool,
  after: PropTypes.node,
};

/**
 * EL#11: medal + `#2D best in #2` instead of a bare letter badge;
 * `iconOnly` in narrow columns (the label moves to the tooltip).
 */
export function BestMark({ run, rank = 1, word = "best", iconOnly = false }) {
  if (!run) return null;
  // IT-R1 G2 P1: a group row's best is `best in #2` (the dataset header
  // says `Best overall`), so one word never names two scopes.
  const scoped =
    word === "best" && run.experiment_ordinal != null
      ? `best in #${run.experiment_ordinal}`
      : word;
  if (iconOnly)
    return (
      <Iconify
        icon={rank === 1 ? "mdi:medal" : "mdi:medal-outline"}
        width={14}
        aria-label={`${run.label} ${scoped}`}
        sx={{
          color: rank === 1 ? "amber.500" : "text.disabled",
          flexShrink: 0,
        }}
      />
    );
  // The value never clips: the mark's words give way first (ellipsis).
  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={0.25}
      sx={{ flexShrink: 1, minWidth: 0, color: "text.secondary" }}
    >
      <Iconify
        icon={rank === 1 ? "mdi:medal" : "mdi:medal-outline"}
        width={14}
        sx={{
          color: rank === 1 ? "amber.500" : "text.disabled",
          flexShrink: 0,
        }}
      />
      <Typography
        component="span"
        typography="s3"
        color="text.secondary"
        noWrap
      >
        {scoped === word ? `${run.label} ${word}` : scoped}
      </Typography>
    </Stack>
  );
}

BestMark.propTypes = {
  run: PropTypes.object,
  rank: PropTypes.number,
  word: PropTypes.string,
  iconOnly: PropTypes.bool,
};

const groupTasksText = (runs) => {
  const tasks = Math.max(
    ...runs.map((r) => r.progress?.total ?? r.examples ?? 0),
  );
  const tries = Math.max(...runs.map((r) => r.trials ?? 1));
  return `${tasks} ${tasks === 1 ? "task" : "tasks"} × ${tries} ${tries === 1 ? "try" : "tries"} × ${runs.length} ${runs.length === 1 ? "run" : "runs"}`;
};

const approx = (context) => context.group !== "experiment";
const NOT_PAIRED = "Group averages are not paired per task";

/**
 * Critic r3 #2: an experiment group row ranks FINISHED runs only. With none
 * finished it says so (muted, no medal); `label` is what the cell reads.
 */
function UnrankedGroupCell({ pending, label = EMPTY, emptyTip }) {
  const p = bestPending(pending);
  return (
    <NumCell
      value={p ? label : EMPTY}
      muted
      tip={p ? p.tip : emptyTip}
      after={
        p && label !== EMPTY ? (
          <Iconify
            icon="mdi:timer-sand"
            width={14}
            aria-hidden
            sx={{ color: "text.disabled", flexShrink: 0 }}
          />
        ) : null
      }
    />
  );
}

UnrankedGroupCell.propTypes = {
  pending: PropTypes.array,
  label: PropTypes.string,
  emptyTip: PropTypes.string,
};

const withUnranked = (tip, pending) =>
  [tip, unrankedNote(pending)].filter(Boolean).join(" · ");
const NO_FINISHED_RUN = "No run in this experiment finished all its tasks";

// ---------------------------------------------------------- success rate

const SUCCESS_RULE =
  "Success rate = passed tries ÷ tries that ran (pass@1). Errored tries count as fails; tries that didn't run are not counted.";

export const SuccessRateCell = (params) => {
  const { node, data, context } = useCell(params);
  // R4 (lane Rb, D22): one source for numbers: runRate via the
  // leaderboard's numbers object (server success_rate only as a fallback).
  const successOf = (r) => successFrom(context.numbers, r);
  if (node.group) {
    const all = leafRuns(node);
    const runs = all.filter((r) => successOf(r));
    if (approx(context)) {
      if (!runs.length)
        return <NumCell value={EMPTY} muted tip="No finished tries yet" />;
      const mean =
        runs.reduce((s, r) => s + successOf(r).rate, 0) / runs.length;
      return <NumCell value={`≈ ${fmtPct(mean)}`} tip={NOT_PAIRED} />;
    }
    const { finished, pending } = rankPool(all);
    const ranked = finished.filter((r) => successOf(r));
    if (!ranked.length)
      return (
        <UnrankedGroupCell
          pending={pending}
          label="Best pending"
          emptyTip={runs.length ? NO_FINISHED_RUN : "No finished tries yet"}
        />
      );
    const best = [...ranked].sort(
      (a, b) => successOf(b).rate - successOf(a).rate,
    )[0];
    const s = successOf(best);
    return (
      <NumCell
        value={fmtPct(s.rate)}
        strong
        tip={withUnranked(
          `Best finished run in this experiment: ${best.label} ${fmtRate(s.passed, s.total)}`,
          pending,
        )}
      />
    );
  }
  if (!data) return null;
  const s = successOf(data);
  if (!s)
    return (
      <NumCell
        value={EMPTY}
        muted
        tip={
          data.progress?.done ? "No tries scored yet" : "No finished tries yet"
        }
      />
    );
  const base = context.baselineRun ? successOf(context.baselineRun) : null;
  const isBase = data.run_id === context.baselineId;
  const otherKind = differentKind(data, context);
  const showDelta = context.showDeltas && !isBase && base && !otherKind;
  // A part-done run's delta moves between polls: grey until it finishes.
  const partial = partialProgress(data);
  let delta = null;
  let tone = "neutral";
  let vs = null;
  if (showDelta) {
    // L2 G2 R4: the one noise rule (diffRange95 on the tasks both ran)
    // and the one delta (rounded once from the unrounded rates).
    const tasks = Math.min(
      data.progress?.done ?? s.total,
      context.baselineRun?.progress?.done ?? base.total,
    );
    // integration C3 (review M1): the run list's paired verdict vs this
    // baseline (the Summary's) decides when served; else diffRange95.
    const sig = successSignificance({
      cand: s.rate,
      base: base.rate,
      n: tasks,
      significant: servedPairedSuccess(
        data.success_rate?.vs_baseline,
        context.baselineId,
      )?.significant,
    }).significant;
    const diff = s.rate - base.rate;
    // Consistency #4: a run of another experiment names what its delta is
    // relative to (`+27.7 pts vs #2B`), never a bare cross-experiment delta.
    const crossExp =
      Boolean(context.baselineRun?.experiment_id) &&
      data.experiment_id !== context.baselineRun.experiment_id;
    const pts = fmtPts(ptsDelta(s.rate, base.rate));
    // The reference sits under the delta: on one line `88.9% +23.3 pts vs
    // #2B` overflows the 148 px column and clips the value.
    delta = crossExp ? (
      <DeltaVs pts={pts} label={context.baselineLabel} />
    ) : (
      pts
    );
    tone =
      !sig || partial
        ? "neutral"
        : diff > 0
          ? "good"
          : diff < 0
            ? "bad"
            : "neutral";
    vs = `${fmtPts(ptsDelta(s.rate, base.rate))} vs baseline ${context.baselineLabel} ${fmtRate(base.passed, base.total)}${partial ? " · grey until the run finishes" : sig ? "" : " · within noise (the 95% range of the difference holds 0)"}`;
  }
  // IT-R4 G2 P7: `best in #2` sits on the best run's own row (the
  // experiment row keeps the value only).
  const expRuns = (context.allRuns ?? []).filter(
    (r) => r.experiment_id === data.experiment_id && r.listed !== false,
  );
  const expBest =
    expRuns.length > 1
      ? rankPool(expRuns)
          .finished.filter((r) => successOf(r))
          .sort((a, b) => successOf(b).rate - successOf(a).rate)[0]
      : null;
  const isExpBest = expBest?.run_id === data.run_id;
  return (
    <NumCell
      value={fmtPct(s.rate)}
      strong
      delta={delta}
      deltaTone={tone}
      muted={!!partial}
      after={isExpBest ? <BestMark run={data} iconOnly /> : undefined}
      tip={
        <Box sx={{ maxWidth: 300 }}>
          <Typography typography="s2" fontWeight="fontWeightMedium">
            {`${s.text ?? fmtRate(s.passed, s.total)} tries passed`}
          </Typography>
          {s.note && <Typography typography="s3">{s.note}</Typography>}
          {partial && (
            <Typography typography="s3">{partialNote(data)}</Typography>
          )}
          {vs && <Typography typography="s3">{vs}</Typography>}
          {otherKind && (
            <Typography typography="s3">{`No delta: ${DIFFERENT_KIND} than baseline ${context.baselineLabel}`}</Typography>
          )}
          <Typography typography="s3" color="text.disabled" sx={{ mt: 0.5 }}>
            {SUCCESS_RULE}
          </Typography>
        </Box>
      }
    />
  );
};

/** `+23.3 pts` over `vs #2B`: a delta measured against another experiment's run. */
function DeltaVs({ pts, label }) {
  return (
    <Box
      component="span"
      sx={{
        display: "inline-flex",
        flexDirection: "column",
        alignItems: "flex-end",
        lineHeight: 1.25,
      }}
    >
      <span>{pts}</span>
      <Box component="span" sx={{ typography: "s3", color: "text.secondary" }}>
        {`vs ${label}`}
      </Box>
    </Box>
  );
}

DeltaVs.propTypes = {
  pts: PropTypes.string.isRequired,
  label: PropTypes.string,
};

// ---------------------------------------------------------- cost per task

const costTip = (run) => {
  const c = run?.metrics?.cost_usd;
  const per = costPerTask(run);
  const t = run?.metrics?.tokens;
  return [
    isNum(per) && isNum(c)
      ? `${fmtCost(per)} per task = ${fmtCost(c)} total ÷ ${run.progress.done} ${run.progress.done === 1 ? "task" : "tasks"}`
      : null,
    t
      ? `${fmtNumber(t.prompt ?? 0)} prompt · ${fmtNumber(t.completion ?? 0)} completion tokens`
      : null,
  ]
    .filter(Boolean)
    .join(" · ");
};

const noCostReason = (run) => {
  const kind = runKindOf(run);
  if (kind === "code" || kind === "harness") return NOT_MEASURED_KEY;
  return run?.target_type === "http" || run?.target_type === "external"
    ? "Cost not reported by this target"
    : run?.progress?.done
      ? "No cost reported yet"
      : "No finished tasks yet";
};

export const CostPerTaskCell = (params) => {
  const { node, data, context } = useCell(params);
  if (node.group) {
    const all = leafRuns(node);
    const runs = all.filter((r) => isNum(costPerTask(r)));
    if (!runs.length)
      return <NumCell value={EMPTY} muted tip="No cost reported yet" />;
    if (approx(context)) {
      const mean = runs.reduce((s, r) => s + costPerTask(r), 0) / runs.length;
      return <NumCell value={`≈ ${fmtCost(mean)}`} tip={NOT_PAIRED} />;
    }
    const { finished, pending } = rankPool(all);
    const ranked = finished.filter((r) => isNum(costPerTask(r)));
    if (!ranked.length)
      return <UnrankedGroupCell pending={pending} emptyTip={NO_FINISHED_RUN} />;
    const cheapest = [...ranked].sort(
      (a, b) => costPerTask(a) - costPerTask(b),
    )[0];
    return (
      <NumCell
        value={fmtCost(costPerTask(cheapest))}
        tip={withUnranked(
          `Cheapest per task: ${cheapest.label} (${fmtCost(costPerTask(cheapest))})`,
          pending,
        )}
        after={<BestMark run={cheapest} word="lowest" iconOnly />}
      />
    );
  }
  if (!data) return null;
  const per = costPerTask(data);
  // IT-R3 G2 P2: a cost of 0 is not measured (your code / harness on your
  // key, an endpoint): `—` + why, never `$0.00 −100%`.
  const hasCost = isNum(data.metrics?.cost_usd) && data.metrics.cost_usd > 0;
  if (!isNum(per) || !hasCost)
    return <NumCell value={EMPTY} muted tip={noCostReason(data)} />;
  const baseRun = context.baselineRun;
  const base =
    baseRun && Number(baseRun.metrics?.cost_usd) > 0
      ? costPerTask(baseRun)
      : null;
  const isBase = data.run_id === context.baselineId;
  const partial = partialNote(data);
  const otherKind = differentKind(data, context);
  const change =
    !isBase && context.showDeltas && !partial && !otherKind
      ? pctChangeText(per, base)
      : null;
  // Sub-cent moves are not worth a colour (EL#8).
  const small = isNum(base) && Math.abs(per - base) < 0.005;
  const tone = !change || small ? "neutral" : per > base ? "bad" : "good";
  const tip = [
    costTip(data),
    partial,
    change
      ? `${fmtCostDelta(per - base)} per task (${change}) vs baseline ${context.baselineLabel} ${fmtCost(base)}. Lower is better.`
      : null,
    otherKind
      ? `No delta: ${DIFFERENT_KIND} than baseline ${context.baselineLabel}.`
      : null,
  ]
    .filter(Boolean)
    .join("\n");
  return (
    <NumCell
      value={fmtCost(per)}
      muted={!!partial}
      delta={
        partial && !isBase && context.showDeltas ? partialWord(data) : change
      }
      deltaTone={tone}
      tip={<Box sx={{ whiteSpace: "pre-line" }}>{tip}</Box>}
    />
  );
};

// ---------------------------------------------------------- time per task

const latencyCell = (getMs, label) => {
  const Cell = (params) => {
    const { node, data, context } = useCell(params);
    if (node.group) {
      const all = leafRuns(node);
      const runs = all.filter((r) => isNum(getMs(r)));
      if (!runs.length)
        return <NumCell value={EMPTY} muted tip="No finished tasks yet" />;
      if (approx(context)) {
        const mean = runs.reduce((s, r) => s + getMs(r), 0) / runs.length;
        return <NumCell value={`≈ ${fmtSecs1(mean)}`} tip={NOT_PAIRED} />;
      }
      const { finished, pending } = rankPool(all);
      const ranked = finished.filter((r) => isNum(getMs(r)));
      if (!ranked.length)
        return (
          <UnrankedGroupCell pending={pending} emptyTip={NO_FINISHED_RUN} />
        );
      const fastest = [...ranked].sort((a, b) => getMs(a) - getMs(b))[0];
      return (
        <NumCell
          value={fmtSecs1(getMs(fastest))}
          tip={withUnranked(
            `Fastest ${label}: ${fastest.label} (${fmtSecs1(getMs(fastest))})`,
            pending,
          )}
          after={<BestMark run={fastest} word="fastest" iconOnly />}
        />
      );
    }
    if (!data) return null;
    const ms = getMs(data);
    if (!isNum(ms))
      return <NumCell value={EMPTY} muted tip="No finished tasks yet" />;
    const base = context.baselineRun ? getMs(context.baselineRun) : null;
    const isBase = data.run_id === context.baselineId;
    // One decimal, like the values (`50.0 s`): the delta of the shown values.
    const d = isNum(base)
      ? (Math.round(ms / 100) - Math.round(base / 100)) * 100
      : null;
    const partial = partialNote(data);
    const otherKind = differentKind(data, context);
    // Moves under 10% are noise for wall time: no delta (C4). IT-R3 G2 P2:
    // none while the run is going or when it is another kind of run.
    const show =
      context.showDeltas &&
      !isBase &&
      !partial &&
      !otherKind &&
      isNum(d) &&
      base > 0 &&
      Math.abs(d / base) >= 0.1;
    return (
      <NumCell
        value={fmtSecs1(ms)}
        muted={!!partial}
        delta={
          show
            ? fmtSecs1(d, { signed: true })
            : partial && !isBase && context.showDeltas
              ? partialWord(data)
              : null
        }
        deltaTone={show ? (d > 0 ? "bad" : "good") : "neutral"}
        tip={`${label} ${fmtSecs1(ms)}${isNum(base) && !isBase && !otherKind ? ` · baseline ${context.baselineLabel} ${fmtSecs1(base)} (${fmtDeltaMs(d, { style: "words" })})` : ""}. Lower is better.${partial ? ` ${partial}.` : ""}${otherKind ? ` No delta: ${DIFFERENT_KIND} than baseline ${context.baselineLabel}.` : ""}`}
      />
    );
  };
  Cell.propTypes = { node: PropTypes.object, data: PropTypes.object };
  return Cell;
};

export const TimePerTaskCell = latencyCell(
  timePerTaskMs,
  "Time per task (p50)",
);
export const LatencyP99SummaryCell = latencyCell(
  (r) =>
    isNum(r?.metrics?.latency_p99_s) ? r.metrics.latency_p99_s * 1000 : null,
  "Latency p99",
);

// ---------------------------------------------------------- total cost

export const TotalCostCell = (params) => {
  const { node, data, context } = useCell(params);
  if (node.group) {
    const runs = leafRuns(node).filter((r) => isNum(r.metrics?.cost_usd));
    if (!runs.length)
      return <NumCell value={EMPTY} muted tip="No cost reported yet" />;
    if (approx(context)) {
      const mean =
        runs.reduce((s, r) => s + r.metrics.cost_usd, 0) / runs.length;
      return <NumCell value={`≈ ${fmtCost(mean)}`} tip={NOT_PAIRED} />;
    }
    const total = runs.reduce((s, r) => s + r.metrics.cost_usd, 0);
    return (
      <NumCell
        value={`${fmtCost(total)} total`}
        tip={`${fmtCost(total)} total · ${groupTasksText(runs)}`}
      />
    );
  }
  if (!data) return null;
  const c = data.metrics?.cost_usd;
  if (!isNum(c) || (c === 0 && data.target_type === "http"))
    return <NumCell value={EMPTY} muted tip={noCostReason(data)} />;
  return <NumCell value={fmtCost(c)} tip={costTip(data) || undefined} />;
};
