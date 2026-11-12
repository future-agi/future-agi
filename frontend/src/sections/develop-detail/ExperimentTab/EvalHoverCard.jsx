import React from "react";
import { intervalOfSummary } from "src/sections/common/Analysis/trialReducers";
import PropTypes from "prop-types";
import { Box, Divider, Link, Stack, Typography } from "@mui/material";
import { Link as RouterLink } from "react-router-dom";
import CustomTooltip from "src/components/tooltip";
import DirectionIcon from "src/sections/common/Metrics/DirectionIcon";
import {
  EMPTY_VALUE,
  LOW_SAMPLE_N,
  MOVEMENT_RULE_TEXT,
  TONE_COLOR,
  deltaTone,
  formatCi,
  formatDelta,
  formatValue,
} from "src/sections/common/Metrics/metricFormat";
import { movementUrl } from "./leaderboardModel";
import { naCountsText } from "src/sections/common/Runs/scoreStatus";
import OpenInEvalsLink from "src/sections/common/EvalHandoffs/OpenInEvalsLink";
import {
  TrustLine,
  useJudgeTrust,
} from "src/sections/common/Analysis/workbench/JudgeTrust";
import { waitingPhraseOf } from "src/sections/common/Runs/runLedger";

const isNum = (v) => typeof v === "number" && Number.isFinite(v);

const fmtStd = (v) => (isNum(v) ? v.toFixed(2) : EMPTY_VALUE);

const versionText = (run) =>
  `v${run?.dataset_version?.number ?? "?"}/${
    run?.splits?.length ? run.splits.map((s) => s.name).join("+") : "all"
  }`;

const Line = ({ children, color = "text.primary", typography = "s2" }) => (
  <Typography typography={typography} color={color} component="div">
    {children}
  </Typography>
);

Line.propTypes = {
  children: PropTypes.node,
  color: PropTypes.string,
  typography: PropTypes.string,
};

const MoveLink = ({ to, count, word, color }) =>
  count > 0 ? (
    <Link
      component={RouterLink}
      to={to}
      underline="hover"
      typography="s2"
      fontWeight="fontWeightMedium"
      sx={{ color, fontVariantNumeric: "tabular-nums" }}
    >
      {count} {word}
    </Link>
  ) : (
    <Typography
      component="span"
      typography="s2"
      color="text.disabled"
      sx={{ fontVariantNumeric: "tabular-nums" }}
    >
      0 {word}
    </Typography>
  );

MoveLink.propTypes = {
  to: PropTypes.string,
  count: PropTypes.number,
  word: PropTypes.string,
  color: PropTypes.string,
};

/**
 * Full detail of one run's eval aggregate (§3.7): the cells stay short and
 * everything else lives here. Movement counts open Compare runs filtered to
 * exactly those rows (a movement pill, never `show=`, C26).
 */
const EvalHoverCard = ({
  def,
  run,
  baselineRun,
  datasetId,
  fromUrl,
  agg = "avg",
}) => {
  const a = run?.metrics?.aggregates?.[def.key];
  const trust = useJudgeTrust(datasetId, def.key);
  const isBaseline = baselineRun?.run_id === run?.run_id;
  const rate = def.output_type === "pass_fail";
  const vsLabel = baselineRun?.label;
  const hasDelta = !isBaseline && isNum(a?.delta);
  const tone = hasDelta
    ? deltaTone(def, a.delta, { significant: a.significant })
    : "neutral";
  const versionsDiffer =
    baselineRun && !isBaseline && versionText(run) !== versionText(baselineRun);
  const link = (dir) =>
    movementUrl(datasetId, {
      baselineId: baselineRun?.run_id,
      runId: run.run_id,
      metricKey: def.key,
      dir,
      from: fromUrl,
    });

  return (
    <Stack spacing={1} sx={{ width: 340, p: 1.5 }}>
      <Stack direction="row" alignItems="center" justifyContent="space-between">
        <Stack direction="row" alignItems="center" gap={0.5}>
          <Typography typography="s1" fontWeight="fontWeightSemiBold">
            {def.name} · {run.label}
          </Typography>
          <DirectionIcon higherIsBetter={def.higher_is_better} />
        </Stack>
        {def.version && (
          <OpenInEvalsLink
            datasetId={datasetId}
            metricKey={def.key}
            def={def}
            name={def.name}
            version={run.eval_versions?.[def.key] ?? def.version}
            showVersion
          />
        )}
      </Stack>

      <TrustLine trust={trust} datasetId={datasetId} runId={run.run_id} />

      {!a || !isNum(a.n) || a.n === 0 ? (
        <Line color="text.secondary">
          {run.status === "WaitingForResults"
            ? `W${waitingPhraseOf(run).slice(1)}. Scores appear as its results arrive.`
            : "No scored rows yet."}
        </Line>
      ) : (
        <>
          <Line>
            {rate
              ? `Pass rate ${formatValue(def, a.avg)}`
              : `Avg ${formatValue(def, a.avg)} · Median ${formatValue(def, a.median)}`}
            {` · Std ${fmtStd(a.std)} · n ${a.n}`}
          </Line>
          {/* W1-8: the value's 95% interval (Wilson for a pass rate). */}
          {intervalOfSummary(a, { rate }) ? (
            <Line color="text.secondary" typography="s3">
              {`95% interval ${intervalOfSummary(a, { rate })
                .map((x) =>
                  formatValue(def, rate ? Math.min(1, Math.max(0, x)) : x),
                )
                .join("–")}`}
            </Line>
          ) : null}
          {naCountsText(a) ? (
            <Line color="text.secondary">
              {`${naCountsText(a)} · left out of the value`}
            </Line>
          ) : null}
          {!rate && isNum(a.pass_rate) && isNum(def.pass_threshold) && (
            <Line color="text.secondary">
              Pass rate {Math.round(a.pass_rate * 1000) / 10}% (≥{" "}
              {def.pass_threshold.toFixed(2)})
            </Line>
          )}
          {agg === "median" && rate && (
            <Line color="text.secondary" typography="s3">
              Pass/fail evals always show the pass rate.
            </Line>
          )}
        </>
      )}

      {hasDelta && (
        <>
          <Divider />
          <Line>
            vs {vsLabel} on {a.n_shared} shared examples:{" "}
            <Box
              component="span"
              sx={{ color: TONE_COLOR[tone], fontWeight: 500 }}
            >
              {formatDelta(def, a.delta)}
            </Box>
            {isNum(a.ci95) && (
              <Box component="span" sx={{ color: "text.secondary" }}>
                {" "}
                (95% CI {formatCi(def, a.ci95)})
              </Box>
            )}
          </Line>
          {a.significant === false && (
            <Line color="text.secondary" typography="s3">
              Within noise: the interval includes zero.
            </Line>
          )}
          <Stack direction="row" flexWrap="wrap" columnGap={1} rowGap={0.25}>
            <MoveLink
              to={link("improved")}
              count={a.improved ?? 0}
              word="improved"
              color="green.600"
            />
            <Typography typography="s2" color="text.disabled">
              ·
            </Typography>
            <MoveLink
              to={link("regressed")}
              count={a.regressed ?? 0}
              word="regressed"
              color="red.600"
            />
            <Typography typography="s2" color="text.secondary">
              · {a.unchanged ?? 0} unchanged
              {a.not_comparable > 0
                ? ` · ${a.not_comparable} not comparable`
                : ""}
            </Typography>
          </Stack>
          <CustomTooltip
            show
            title={MOVEMENT_RULE_TEXT}
            size="small"
            placement="bottom"
            arrow
          >
            <Typography
              typography="s3"
              color="text.disabled"
              sx={{ alignSelf: "flex-start", cursor: "help" }}
            >
              How rows are counted
            </Typography>
          </CustomTooltip>
        </>
      )}

      {!isBaseline &&
        !hasDelta &&
        baselineRun &&
        a?.n > 0 &&
        a.not_comparable_reason === "eval_versions_differ" && (
          <Line color="text.secondary" typography="s3">
            {`Not compared: ${run.label ?? "this run"} scored with ${a.versions_compared?.run}, ${vsLabel} with ${a.versions_compared?.baseline}. Re-score one on the other's version to compare.`}
          </Line>
        )}
      {!isBaseline &&
        !hasDelta &&
        baselineRun &&
        a?.n > 0 &&
        a.not_comparable_reason !== "eval_versions_differ" && (
          <Line color="text.secondary" typography="s3">
            {vsLabel} did not score {def.name}, so there is nothing to compare.
          </Line>
        )}
      {isBaseline && (
        <Line color="text.secondary" typography="s3">
          This is the baseline run. Other runs are measured against it.
        </Line>
      )}
      {a?.n > 0 && a.n < LOW_SAMPLE_N && (
        <Line color="warning.dark" typography="s3">
          Low sample: {a.n} rows, fewer than {LOW_SAMPLE_N}. Treat the number as
          rough.
        </Line>
      )}
      {a?.partial && (
        <Line color="text.secondary" typography="s3">
          Partial · {a.done} of {a.total} rows. The run is still going.
        </Line>
      )}
      {(run.progress?.errors ?? 0) > 0 && (
        // W1-7: how this run's errored tries count in the number above.
        <Line color="text.secondary" typography="s3">
          {`Errored tries: ${
            run.error_policy === "exclude"
              ? "left out"
              : run.error_policy === "fail"
                ? "count as 0 and fail the run"
                : "count as 0"
          } · ${run.progress.errors}`}
        </Line>
      )}
      {versionsDiffer && hasDelta && (
        <Line color="text.secondary" typography="s3">
          Compared on shared examples only (run {versionText(run)}, baseline run{" "}
          {versionText(baselineRun)}).
        </Line>
      )}
    </Stack>
  );
};

EvalHoverCard.propTypes = {
  def: PropTypes.object.isRequired,
  run: PropTypes.object.isRequired,
  baselineRun: PropTypes.object,
  datasetId: PropTypes.string.isRequired,
  fromUrl: PropTypes.string,
  agg: PropTypes.oneOf(["avg", "median"]),
};

export default EvalHoverCard;
