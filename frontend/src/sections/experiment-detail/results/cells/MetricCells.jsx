import React from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import ScoreCell from "src/sections/common/Metrics/ScoreCell";
import TypedScoreDetails from "src/sections/common/EvalTypes/TypedScoreDetails";
import {
  formatDelta,
  formatValue,
} from "src/sections/common/Metrics/metricFormat";
import {
  MutedText,
  cellBoxSx,
  cellPropTypes,
  notInRunLabel,
  notScoredReason,
  queuedLabel,
} from "./cellCommon";

// Per-run metric cells (PRODUCT-SPEC §3.10.7): eval / human scores through
// the ScoreCell primitive, latency / cost / tokens with a ±10 % tone, agent
// steps, and the Human / LLM judge preference.

/** The pill a score cell's `Filter by this` adds. */
export function filterPillForScore(def, score, movement, runId) {
  if (movement === "improved" || movement === "regressed")
    return { type: "movement", metric: def.key, dir: movement, run: runId };
  const v = score?.value;
  if (!Number.isFinite(v)) return null;
  const threshold =
    def.output_type === "score" ? def.pass_threshold ?? 0.5 : 0.5;
  if (def.higher_is_better !== false && v < threshold)
    return { type: "fails", metric: def.key, run: runId };
  if (def.output_type === "pass_fail")
    return {
      type: "score",
      metric: def.key,
      op: "gte",
      value: 0.5,
      run: runId,
    };
  return {
    type: "score",
    metric: def.key,
    op: def.higher_is_better === false ? "lte" : "gte",
    value: Number(v.toFixed(2)),
    run: runId,
  };
}

const notInRun = notInRunLabel;

function SkippedScore({ reason }) {
  const why = reason || "not scored";
  return (
    <Box sx={cellBoxSx} data-score-skipped="">
      <CustomTooltip
        show
        size="small"
        placement="top"
        arrow
        title={`Skipped: ${why}. A skipped eval is left out of the pass rate; it never counts as a pass.`}
      >
        <Typography typography="s2" color="text.disabled" noWrap>
          {`Skipped · ${why}`}
        </Typography>
      </CustomTooltip>
    </Box>
  );
}
SkippedScore.propTypes = { reason: PropTypes.string };

// W1-4: the judge answered N/A (its own colour: not a fail, not a skip),
// and a score kept as an error (an invalid value, never coerced). Both are
// left out of means and pass rates; the header counts them.
function NaOrErrorScore({ status, reason }) {
  const na = status === "na";
  const word = na ? "N/A" : "Score error";
  const tip = na
    ? `N/A: ${reason || "the judge found nothing to score"}. N/A rows are left out of means and pass rates.`
    : `${/^score error/i.test(reason ?? "") ? `Score error${reason.slice(11)}` : `Score error: ${reason || "invalid score"}`}. The score is kept as an error, never counted as a value.`;
  return (
    <Box sx={cellBoxSx} data-score-status={status}>
      <CustomTooltip show size="small" placement="top" arrow title={tip}>
        <Typography
          typography="s2"
          noWrap
          sx={{ color: na ? "purple.600" : "orange.700", fontWeight: 500 }}
        >
          {word}
        </Typography>
      </CustomTooltip>
    </Box>
  );
}
NaOrErrorScore.propTypes = {
  status: PropTypes.oneOf(["na", "error"]),
  reason: PropTypes.string,
};

export const ScoreCellRenderer = ({ data, context, runId, metricKey }) => {
  const ctx = context ?? {};
  const def = ctx.defsByKey?.[metricKey];
  const result = data?.results?.[runId];
  if (!result || result.status === "missing")
    return (
      <Box sx={cellBoxSx}>
        <MutedText>{notInRun(ctx.runsById?.[runId])}</MutedText>
      </Box>
    );
  if (result.status === "queued" || result.status === "running")
    return (
      <Box sx={cellBoxSx}>
        <MutedText icon="mdi:clock-outline">
          {result.status === "queued"
            ? queuedLabel(ctx.runsById?.[runId])
            : "Scoring…"}
        </MutedText>
      </Box>
    );
  const isHuman = metricKey.startsWith("human:");
  const score = isHuman
    ? { value: result.human?.[metricKey] ?? null }
    : result.scores?.[metricKey];
  // W7-4 (H-A4): a remote scorer's cell waiting for its answer; the page
  // polls while the run reads Evaluating, so it fills in place.
  if (score?.typed?.pending)
    return (
      <Box sx={cellBoxSx} data-testid="score-remote-pending">
        <MutedText icon="mdi:clock-outline">
          {`Scoring at ${score.typed.host}`}
        </MutedText>
      </Box>
    );
  // R4 (lane M, ir-5): a skipped eval is not a pass. It reads
  // `Skipped · <reason>` (muted, reason in the tooltip), never a Pass chip.
  if (score?.status === "skipped")
    return <SkippedScore reason={score.skip_reason} />;
  if (
    !score?.override &&
    (score?.status === "na" ||
      (score?.status === "error" && score.display === "Score error"))
  )
    return <NaOrErrorScore status={score.status} reason={score.reason} />;
  const movement = result.movement?.[metricKey];
  const override = score?.override;
  // P9: `Not scored` always says why.
  if (
    (score?.value ?? null) == null &&
    !override &&
    result.status !== "error" &&
    !score?.display
  ) {
    const why = notScoredReason({
      run: ctx.runsById?.[runId],
      row: data,
      def,
      score,
    });
    return (
      <Box sx={cellBoxSx} data-testid="score-not-scored">
        <CustomTooltip
          show
          size="small"
          placement="top"
          arrow
          title={`Not scored: ${why}. It is left out of the pass rate.`}
        >
          <Typography typography="s2" color="text.disabled" noWrap>
            {`Not scored · ${why}`}
          </Typography>
        </CustomTooltip>
      </Box>
    );
  }
  const pill = def ? filterPillForScore(def, score, movement, runId) : null;
  // integration S8: a typed cell's children or path on hover.
  const typedTip = score?.typed ? (
    <TypedScoreDetails cell={score} compact />
  ) : null;
  const cell = (
    <ScoreCell
      def={def}
      value={override?.value ?? score?.value ?? null}
      display={
        override
          ? formatValue(def, override.value, { agg: "value" })
          : score?.display ?? undefined
      }
      delta={result.delta?.[metricKey]}
      movement={movement}
      flaky={
        score?.flaky
          ? { std: score.std, flakiness: score.flakiness?.score ?? null }
          : false
      }
      override={
        override
          ? {
              display: `${formatValue(def, override.value, { agg: "value" })} (eval said ${score?.display ?? formatValue(def, score?.value, { agg: "value" })})`,
              by: "Human correction",
            }
          : false
      }
      error={
        result.status === "error" ? result.error ?? "Run failed" : undefined
      }
      reason={score?.reason ?? undefined}
      showReason={!!ctx.showReasons}
      onFilter={pill && ctx.onFilter ? () => ctx.onFilter(pill) : undefined}
    />
  );
  return typedTip ? (
    <CustomTooltip show size="small" placement="top" arrow title={typedTip}>
      <Box sx={{ width: "100%", height: "100%" }}>{cell}</Box>
    </CustomTooltip>
  ) : (
    cell
  );
};
ScoreCellRenderer.propTypes = cellPropTypes;

const OPS_FIELD = {
  "ops:latency": "latency_ms",
  "ops:cost": "cost",
  "ops:tokens": "tokens",
};

/** Relative tone of an ops value vs the baseline: ±10 % (C4). */
export function opsTone(value, base) {
  if (!Number.isFinite(value) || !Number.isFinite(base) || base === 0)
    return "neutral";
  const rel = (value - base) / Math.abs(base);
  if (rel <= -0.1) return "good";
  if (rel >= 0.1) return "bad";
  return "neutral";
}

const TONE = {
  good: "green.600",
  bad: "red.600",
  neutral: "text.disabled",
};

export const OpsCell = ({ data, context, runId, metricKey }) => {
  const ctx = context ?? {};
  const def = ctx.defsByKey?.[metricKey];
  const result = data?.results?.[runId];
  const field = OPS_FIELD[metricKey];
  const value = result?.[field];
  if (!result || result.status === "missing" || value == null)
    return (
      <Box sx={cellBoxSx}>
        {result?.status === "missing" || result?.status === "queued" ? (
          <MutedText>
            {result?.status === "missing"
              ? notInRun(ctx.runsById?.[runId])
              : queuedLabel(ctx.runsById?.[runId])}
          </MutedText>
        ) : (
          // P9: an empty ops value says why on hover.
          <CustomTooltip
            show
            size="small"
            placement="top"
            arrow
            title="Not recorded for this run"
          >
            <span>
              <MutedText>—</MutedText>
            </span>
          </CustomTooltip>
        )}
      </Box>
    );
  const base =
    runId !== ctx.baselineId
      ? data?.results?.[ctx.baselineId]?.[field]
      : undefined;
  const tone = opsTone(value, base);
  const d = Number.isFinite(base) ? value - base : null;
  return (
    <Stack sx={{ ...cellBoxSx, gap: 0.25 }}>
      <Typography typography="s2_1" sx={{ fontVariantNumeric: "tabular-nums" }}>
        {formatValue(def, value)}
      </Typography>
      {d != null && d !== 0 && (
        <Typography
          typography="s3"
          fontWeight={500}
          sx={{ color: TONE[tone], fontVariantNumeric: "tabular-nums" }}
        >
          {formatDelta(def, d, { base })}
        </Typography>
      )}
    </Stack>
  );
};
OpsCell.propTypes = cellPropTypes;

export const StepsCell = ({ data, context, runId }) => {
  const ctx = context ?? {};
  const result = data?.results?.[runId];
  const steps = result?.steps;
  if (!steps)
    return (
      <Box sx={cellBoxSx}>
        <MutedText>
          {result?.status === "missing" ? notInRun(ctx.runsById?.[runId]) : "—"}
        </MutedText>
      </Box>
    );
  const base =
    runId !== ctx.baselineId
      ? data?.results?.[ctx.baselineId]?.steps?.steps
      : undefined;
  const more = Number.isFinite(base) ? steps.steps - base : null;
  return (
    <Stack sx={{ ...cellBoxSx, gap: 0.25 }}>
      <Stack direction="row" alignItems="baseline" gap={0.75}>
        <Typography
          typography="s2_1"
          sx={{ fontVariantNumeric: "tabular-nums" }}
        >
          {steps.steps} steps
        </Typography>
        {more != null && Math.abs(more) >= 1 && (
          <Typography
            typography="s3"
            fontWeight={500}
            sx={{ color: more > 0 ? "red.600" : "green.600" }}
          >
            {more > 0 ? `+${more}` : `−${Math.abs(more)}`}
          </Typography>
        )}
      </Stack>
      <Typography typography="s3" color="text.secondary" noWrap>
        {`${steps.llm_calls ?? 0} LLM · ${steps.tool_calls ?? 0} tool calls`}
      </Typography>
      {steps.tool_errors > 0 && (
        <Typography
          typography="s3"
          color="red.600"
          fontWeight={500}
          noWrap
          onClick={(e) => {
            e.stopPropagation();
            ctx.onFilter?.({ type: "tool_error", run: runId });
          }}
          sx={{ cursor: "pointer" }}
        >
          {`${steps.tool_errors} tool ${steps.tool_errors === 1 ? "error" : "errors"}`}
        </Typography>
      )}
    </Stack>
  );
};
StepsCell.propTypes = cellPropTypes;

const labelOf = (ctx, id) => ctx.runsById?.[id]?.label ?? "Run";

/** `#4A better` / `#3A better` / `Tie` / `—` from a preference row. */
export function preferenceText(pref, source, runsById) {
  const winner = source === "judge" ? pref?.judge?.winner : pref?.winner;
  if (!winner) return null;
  if (winner === "tie") return "Tie";
  return `${runsById?.[winner]?.label ?? "Run"} better`;
}

export const PreferenceCell = ({ data, context, source }) => {
  const ctx = context ?? {};
  const pref = data?.preference;
  const text = preferenceText(pref, source, ctx.runsById);
  const winner = source === "judge" ? pref?.judge?.winner : pref?.winner;
  const color = winner && winner !== "tie" ? ctx.colors?.[winner]?.solid : null;
  if (!text)
    return (
      <Box sx={cellBoxSx}>
        <MutedText>
          {source === "judge" ? "No judge pick" : "Not reviewed"}
        </MutedText>
      </Box>
    );
  const votes = pref?.votes;
  const tip =
    source === "judge"
      ? `LLM judge (${pref.judge.model}): ${pref.judge.reason}`
      : votes
        ? `Reviewer votes · ${labelOf(ctx, pref.run_a)} ${votes.A} · ${labelOf(ctx, pref.run_b)} ${votes.B} · Tie ${votes.tie}`
        : "Human preference";
  return (
    <Box sx={cellBoxSx}>
      <CustomTooltip show size="small" placement="top" arrow title={tip}>
        <Stack direction="row" alignItems="center" gap={0.75}>
          <Iconify
            icon={
              source === "judge" ? "mdi:robot-outline" : "mdi:account-outline"
            }
            width={14}
            sx={{ color: "text.secondary", flexShrink: 0 }}
          />
          {color && (
            <Box
              sx={{ width: 3, height: 14, borderRadius: 1, bgcolor: color }}
            />
          )}
          <Typography typography="s2_1" noWrap>
            {text}
          </Typography>
        </Stack>
      </CustomTooltip>
    </Box>
  );
};
PreferenceCell.propTypes = { ...cellPropTypes, source: PropTypes.string };
