import React, { useMemo } from "react";
import PropTypes from "prop-types";
import { Link as RouterLink } from "react-router-dom";
import { Box, Button, Link, Skeleton, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { useDatasetRuns } from "src/api/develop/runs";
import { useRunAnalysis } from "src/api/develop/analysis";
import { useDatasetKind } from "src/sections/common/Datasets/datasetKind";
import { taskCompareHref } from "src/sections/common/Analysis/analysisLinks";
import { rowLabelOf } from "src/sections/common/Runs/runNumbers";
import {
  VerdictText,
  VerdictToneDot,
} from "src/sections/common/ux/VerdictLine";
import { ToneChip } from "src/sections/experiment-detail/ExperimentSummary/Analysis/SummaryVerdicts";
import {
  candidateItem,
  displayCost,
  shortRunName,
  summaryVerdicts,
} from "src/sections/experiment-detail/ExperimentSummary/Analysis/verdictItems";
import {
  answerLines,
  latestVsBaseline,
  pickHeadline,
  shipLine,
} from "src/sections/develop-detail/ExperimentTab/leaderboardHeadline";
import { latestExperiment } from "./EvalsCard/experimentEvals";

const isNum = (v) => typeof v === "number" && Number.isFinite(v);

/** `#2` from `#2D`. */
const experimentTag = (label) =>
  String(label ?? "").match(/^#\d+/)?.[0] ?? null;

/**
 * IT-R5 G2 P4: the dataset Summary answers first — the latest experiment's
 * verdict card, the same words as the experiment Summary (verdictItems
 * `summaryVerdicts`, G2 `shipLine` / `answerLines`): the ship line when a
 * gate fails, the chip and its sentence (wraps, never an ellipsis), the
 * trade-off, the confidence, and `From #2 Five ways · Open experiment
 * Summary`. Renders nothing when no experiment has two scored runs.
 */
export default function DatasetVerdictCard({ datasetId }) {
  const runsQ = useDatasetRuns(datasetId, { include: "metrics" });
  const allRuns = runsQ.data?.results ?? [];
  const pinnedId = runsQ.data?.pinned_run_id ?? null;
  const latestExp = latestExperiment(allRuns, pinnedId);
  // A latest experiment of one run: its run vs the pinned baseline (the
  // Experiments tab's `Latest vs baseline` pair).
  const exp = useMemo(() => {
    // L2 G2 R4 (consistency #5): the same subject as the Experiments tab's
    // headline — pickHeadline's candidate (the newest finished experiment's
    // best run) vs the pinned baseline, with that experiment's runs.
    const pinnedRun = allRuns.find((r) => r.run_id === pinnedId) ?? null;
    const cand = pinnedRun ? pickHeadline(allRuns, pinnedRun)?.candidate : null;
    if (cand && cand.run_id !== pinnedId) {
      const expRuns = allRuns
        .filter(
          (r) => r.experiment_id === cand.experiment_id && r.listed !== false,
        )
        .map((r) => r.run_id);
      return {
        experimentId: cand.experiment_id,
        experimentName: cand.experiment_name ?? null,
        from:
          pinnedRun.experiment_id === cand.experiment_id
            ? null
            : `${cand.label} vs baseline ${pinnedRun.label}`,
        runIds: [...new Set([pinnedId, ...expRuns])],
        baselineId: pinnedId,
        candidateId: cand.run_id,
      };
    }
    if ((latestExp?.runIds?.length ?? 0) > 1) return latestExp;
    const pinned = allRuns.find((r) => r.run_id === pinnedId);
    const lvb = latestVsBaseline(allRuns, pinned ?? null);
    if (!lvb) return null;
    return {
      experimentId: lvb.cand.experiment_id,
      experimentName: null,
      from: `${lvb.cand.label} vs baseline ${lvb.base.label}`,
      runIds: lvb.runIds,
      baselineId: lvb.baselineId,
    };
  }, [latestExp, allRuns, pinnedId]);
  const multi = (exp?.runIds?.length ?? 0) > 1;
  const analysisQ = useRunAnalysis(datasetId, {
    runIds: exp?.runIds ?? [],
    baselineId: multi ? exp.baselineId : undefined,
    enabled: Boolean(exp) && multi,
  });
  const { kind } = useDatasetKind(datasetId);
  const analysis = analysisQ.data;
  const model = useMemo(() => {
    if (!analysis) return null;
    const items = summaryVerdicts(analysis);
    const item = candidateItem(
      items,
      exp?.candidateId ?? analysis.candidate_run_id ?? null,
    );
    if (!item) return null;
    const noun = analysis.noun ?? "task";
    const who = (r) =>
      [r.label, shortRunName(r.name)].filter(Boolean).join(" ");
    const compare = item.run.tasks_vs_baseline?.compare ?? null;
    // the capability contract: a Failed candidate (every row errored) has no ship line
    // and no trade-off/confidence lines; its verdict reads Failed.
    const ship =
      compare && !item.failed
        ? shipLine(compare, {
            cand: who(item.run),
            base: who(item.base),
            rowLabel: (n) => rowLabelOf(n, kind),
            noun,
          })
        : null;
    // The numbers the sentence shows (verdictInput's display rounding), so
    // `64 s slower` reads the same on both lines.
    const pair = (f, show) =>
      isNum(item.run[f]) && isNum(item.base[f])
        ? { cand: show(item.run[f]), base: show(item.base[f]) }
        : undefined;
    const tvb = item.run.tasks_vs_baseline ?? {};
    const lines =
      !item.failed && compare && isNum(tvb.cand_rate) && isNum(tvb.base_rate)
        ? answerLines({
            compare: {
              ...compare,
              candidate: { rate: tvb.cand_rate },
              baseline: { rate: tvb.base_rate },
            },
            verdict: item.verdict,
            cand: { label: item.run.label, name: shortRunName(item.run.name) },
            base: {
              label: item.base.label,
              name: shortRunName(item.base.name),
            },
            noun,
            costPerTask: pair("cost_per_row", displayCost),
            timePerTask: pair("latency_p50_ms", (v) => v),
          })
        : null;
    // The sentence without its trailing `Confidence: …` (said apart).
    const parts = item.verdict.parts ?? [];
    const cut = parts.findIndex((p) => p.t === "term" && p.k === "confidence");
    // Budget (≤ 18 controls): plain words; the confidence line says what
    // it rests on in the text itself.
    const sentenceParts = (cut > 0 ? parts.slice(0, cut) : parts).map((p) =>
      p.t === "term" ? { t: "text", text: p.text } : p,
    );
    return {
      item,
      ship,
      tradeoff: lines?.tradeoff ?? null,
      confidence: lines?.confidence ?? null,
      level: item.verdict.confidence?.text ?? null,
      sentence: {
        tone: item.verdict.tone,
        parts: sentenceParts,
        sentence: sentenceParts.map((p) => p.text).join(""),
      },
    };
  }, [analysis, kind, exp?.candidateId]);

  if (runsQ.isLoading || (multi && analysisQ.isLoading))
    return <Skeleton variant="rounded" height={112} sx={{ mb: 2 }} />;
  if (!exp || !model) return null;

  const tag = experimentTag(model.item.run.label);
  const summaryHref = `/dashboard/develop/experiment/${exp.experimentId}/summary`;
  const rowHref =
    model.ship?.rowId && exp.runIds?.length
      ? taskCompareHref({
          datasetId,
          runIds: [model.item.base.run_id, model.item.run.run_id],
          baselineId: model.item.base.run_id,
          rowId: model.ship.rowId,
        })
      : null;
  const gateFails = model.ship?.tone === "worse";

  return (
    <Box
      data-testid="dataset-verdict-card"
      sx={{
        border: 1,
        borderColor: "divider",
        borderRadius: 1,
        p: 1.5,
        mb: 2,
        bgcolor: "background.paper",
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        gap={1}
        flexWrap="wrap"
        sx={{ mb: 1, minWidth: 0 }}
      >
        <Typography typography="s1" fontWeight="fontWeightSemiBold">
          Verdict
        </Typography>
        <Typography
          typography="s2"
          color="text.secondary"
          sx={{ minWidth: 0, flex: 1 }}
          data-testid="dataset-verdict-from"
        >
          {exp.from
            ? `From ${exp.from}`
            : `From ${tag ?? "the latest experiment"}${exp.experimentName ? ` ${exp.experimentName}` : ""}`}
        </Typography>
        <Button
          size="small"
          variant="outlined"
          component={RouterLink}
          to={summaryHref}
          endIcon={<Iconify icon="mdi:arrow-right" width={16} />}
          sx={{ whiteSpace: "nowrap" }}
        >
          Open experiment Summary
        </Button>
      </Stack>
      <Stack spacing={0.75} sx={{ minWidth: 0 }}>
        {gateFails ? (
          <Stack direction="row" alignItems="flex-start" gap={1}>
            <VerdictToneDot tone="worse" />
            <Typography
              typography="s1"
              fontWeight="fontWeightMedium"
              color="error.main"
              data-testid="dataset-verdict-ship"
              sx={{ minWidth: 0 }}
            >
              {model.ship.text}
              {rowHref ? (
                <>
                  {" "}
                  <Link
                    component={RouterLink}
                    to={rowHref}
                    underline="always"
                    sx={{ whiteSpace: "nowrap" }}
                  >
                    {model.ship.rowText}
                  </Link>
                </>
              ) : null}
            </Typography>
          </Stack>
        ) : null}
        <Stack
          direction="row"
          alignItems="flex-start"
          gap={1}
          sx={{ pl: gateFails ? 3.25 : 0, minWidth: 0 }}
        >
          {gateFails ? null : <VerdictToneDot tone={model.sentence.tone} />}
          <Box sx={{ flexShrink: 0, mt: "1px" }}>
            <ToneChip item={model.item} />
          </Box>
          <VerdictText
            verdict={model.sentence}
            secondary={gateFails}
            sx={{ WebkitLineClamp: "unset", display: "block" }}
          />
        </Stack>
        {model.tradeoff || model.confidence ? (
          <Typography
            typography="s2"
            color="text.secondary"
            data-testid="dataset-verdict-tradeoff"
            sx={{ pl: 3.25 }}
          >
            {model.tradeoff ? `${model.tradeoff} ` : ""}
            {model.confidence ? (
              <Box
                component="span"
                title={model.confidence.extra}
                data-testid="dataset-verdict-confidence"
              >
                {`Confidence: ${model.confidence.level} (${model.confidence.extra})`}
              </Box>
            ) : null}
          </Typography>
        ) : null}
      </Stack>
    </Box>
  );
}

DatasetVerdictCard.propTypes = {
  datasetId: PropTypes.string,
};
