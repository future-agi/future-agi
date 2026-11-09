import React, { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Box, Paper, Popper, Stack, Typography } from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import DeltaBadge from "src/sections/common/Metrics/DeltaBadge";
import {
  EMPTY_VALUE,
  LOW_SAMPLE_N,
  formatValue,
} from "src/sections/common/Metrics/metricFormat";
import { fmtCount } from "src/sections/common/format/fmt";
import EvalHoverCard from "./EvalHoverCard";
import {
  aggValueOf,
  bestPending,
  bestRunFor,
  meanValue,
  partialNote,
  rankPool,
  unrankedNote,
} from "./leaderboardModel";
import { useCell } from "./cellContext";
import { BestMark } from "./LeaderboardSummaryCells";
import { waitingPhraseOf } from "src/sections/common/Runs/runLedger";

const isNum = (v) => typeof v === "number" && Number.isFinite(v);
const OPEN_DELAY = 250;
const CLOSE_DELAY = 150;

/** Hover-intent popper that stays open while the pointer is on the card. */
function useHoverCard() {
  const [anchor, setAnchor] = useState(null);
  const timer = useRef(null);
  const clear = () => clearTimeout(timer.current);
  useEffect(() => clear, []);
  return {
    anchor,
    anchorProps: {
      onMouseEnter: (e) => {
        clear();
        const target = e.currentTarget;
        timer.current = setTimeout(() => setAnchor(target), OPEN_DELAY);
      },
      onMouseLeave: () => {
        clear();
        timer.current = setTimeout(() => setAnchor(null), CLOSE_DELAY);
      },
    },
    cardProps: {
      onMouseEnter: clear,
      onMouseLeave: () => {
        clear();
        timer.current = setTimeout(() => setAnchor(null), CLOSE_DELAY);
      },
    },
  };
}

const Value = ({ def, value, partial, prefix = "" }) => (
  <Typography
    component="span"
    typography="s1"
    fontWeight="fontWeightMedium"
    color={partial ? "text.secondary" : "text.primary"}
    sx={{ fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap" }}
  >
    {prefix}
    {formatValue(def, value)}
  </Typography>
);

Value.propTypes = {
  def: PropTypes.object,
  value: PropTypes.number,
  partial: PropTypes.bool,
  prefix: PropTypes.string,
};

/**
 * MUST 2: every eval cell says how many results it scored (`n=88`), so a
 * pass rate over scored tries never reads as the run's Success rate.
 */
const LowN = ({ n, low = true }) => (
  <CustomTooltip
    show
    title={[
      `${fmtCount(n, "scored result")}. Errored tries are not scored.`,
      low ? `Low sample: fewer than ${LOW_SAMPLE_N} rows.` : null,
    ]
      .filter(Boolean)
      .join(" ")}
    size="small"
    placement="top"
    arrow
  >
    <Typography
      component="span"
      typography="s3"
      color={low ? "text.secondary" : "text.disabled"}
      sx={{ fontVariantNumeric: "tabular-nums", flexShrink: 0 }}
    >
      n={n}
    </Typography>
  </CustomTooltip>
);

LowN.propTypes = { n: PropTypes.number, low: PropTypes.bool };

const Muted = ({ title, children = EMPTY_VALUE }) => (
  <CustomTooltip
    show={!!title}
    title={title}
    size="small"
    placement="top"
    arrow
  >
    <Typography component="span" typography="s1" color="text.disabled">
      {children}
    </Typography>
  </CustomTooltip>
);

Muted.propTypes = { title: PropTypes.string, children: PropTypes.node };

const emptyReason = (run, a) => {
  if (!a) return "Not scored by this run";
  // L7c g5: the one waiting noun (`Waiting for results`).
  if (run.status === "WaitingForResults")
    return `W${waitingPhraseOf(run).slice(1)}`;
  if (a.partial) return `Partial · ${a.done ?? 0} of ${a.total ?? 0} rows`;
  return "No scored rows";
};

/** One run's aggregate + delta vs the baseline run; hover for the card. */
function RunEvalValue({ def, run, context }) {
  const hover = useHoverCard();
  const a = run.metrics?.aggregates?.[def.key];
  const value = aggValueOf(run, def, context.agg);
  if (!isNum(value)) return <Muted title={emptyReason(run, a)} />;
  const isBaseline = run.run_id === context.baselineId;
  const showDelta =
    context.showDeltas && !isBaseline && isNum(a.delta) && a.n_shared > 0;
  // Low sample is about the run's own rows (J7: `n=20`); the shared-row
  // count behind the delta lives in the hover card.
  const lowN = a.n < LOW_SAMPLE_N;
  // Critic r3 #2: a part-done run's delta moves between polls, so it reads
  // grey (never green / red) until the run finishes.
  const runNote = partialNote(run, "rows");
  const partial = Boolean(a.partial || runNote);
  const partialTip =
    runNote ?? (a.partial ? `Partial · ${a.done} of ${a.total} rows` : null);
  return (
    <>
      <Stack
        direction="row"
        alignItems="baseline"
        gap={0.75}
        sx={{ height: "100%", alignItems: "center", cursor: "default" }}
        {...hover.anchorProps}
      >
        <CustomTooltip
          show={!!partialTip}
          title={partialTip}
          size="small"
          placement="top"
          arrow
        >
          <span>
            <Value def={def} value={value} partial={partial} />
          </span>
        </CustomTooltip>
        {showDelta && (
          <Box
            component="span"
            sx={
              partial
                ? {
                    display: "inline-flex",
                    "& [data-tone] > .MuiTypography-root:first-of-type": {
                      color: "text.disabled",
                    },
                  }
                : { display: "inline-flex" }
            }
          >
            <DeltaBadge
              def={def}
              delta={a.delta}
              ci95={a.ci95}
              significant={a.significant}
              vsLabel={context.baselineLabel}
              partial={partial}
            />
          </Box>
        )}
        {isNum(a.n) && <LowN n={a.n} low={lowN} />}
      </Stack>
      <Popper
        open={!!hover.anchor}
        anchorEl={hover.anchor}
        placement="bottom-start"
        sx={{ zIndex: 1300 }}
        modifiers={[{ name: "offset", options: { offset: [0, 4] } }]}
      >
        <Paper
          data-eval-card
          elevation={8}
          sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1 }}
          {...hover.cardProps}
        >
          <EvalHoverCard
            def={def}
            run={run}
            baselineRun={context.baselineRun}
            datasetId={context.datasetId}
            fromUrl={context.fromUrl}
            agg={context.agg}
          />
        </Paper>
      </Popper>
    </>
  );
}

RunEvalValue.propTypes = {
  def: PropTypes.object.isRequired,
  run: PropTypes.object.isRequired,
  context: PropTypes.object.isRequired,
};

/**
 * Eval column cell. Run row: value, then the delta (grey within noise; low
 * sample adds `n=20`). Experiment group row: the best run by direction with
 * its letter. Any other group: the mean of the runs, prefixed `≈`.
 */
const EvalCell = (params) => {
  const { node, data, context } = useCell(params);
  const { colDef } = params;
  const def = colDef?.cellRendererParams?.def;
  if (!def) return null;
  if (node.group) {
    const runs = (node.allLeafChildren ?? [])
      .map((n) => n.data)
      .filter(Boolean);
    if (context.group === "experiment") {
      // Critic r3 #2: only finished runs are ranked (no medal on a run
      // whose numbers still move between polls).
      const { finished, pending } = rankPool(runs);
      const best = bestRunFor(finished, def, context.agg);
      if (!best) {
        const p = bestPending(pending);
        if (p) return <Muted title={p.tip}>{p.label}</Muted>;
        return (
          <Muted
            title={
              bestRunFor(runs, def, context.agg)
                ? "No run in this experiment finished all its tasks"
                : "No run scored this eval yet"
            }
          />
        );
      }
      const a = best.metrics.aggregates[def.key];
      const showDelta =
        context.showDeltas &&
        best.run_id !== context.baselineId &&
        isNum(a.delta) &&
        a.n_shared > 0;
      return (
        <Stack
          direction="row"
          alignItems="center"
          gap={0.75}
          sx={{ height: "100%" }}
        >
          <CustomTooltip
            show
            title={[
              `Best finished run by ${def.name}: ${best.label}`,
              unrankedNote(pending),
            ]
              .filter(Boolean)
              .join(" · ")}
            size="small"
            placement="top"
            arrow
          >
            <Stack direction="row" alignItems="center" gap={0.5}>
              <Value
                def={def}
                value={aggValueOf(best, def, context.agg)}
                partial={a.partial}
              />
              <BestMark run={best} iconOnly />
            </Stack>
          </CustomTooltip>
          {showDelta && (
            <DeltaBadge
              def={def}
              delta={a.delta}
              ci95={a.ci95}
              significant={a.significant}
              vsLabel={context.baselineLabel}
            />
          )}
        </Stack>
      );
    }
    const mean = meanValue(runs, (r) => aggValueOf(r, def, context.agg));
    if (!isNum(mean)) return <Muted title="No run in this group scored it" />;
    return (
      <CustomTooltip
        show
        title="Group averages are not paired per example"
        size="small"
        placement="top"
        arrow
      >
        <Stack direction="row" alignItems="center" sx={{ height: "100%" }}>
          <Value def={def} value={mean} prefix="≈ " />
        </Stack>
      </CustomTooltip>
    );
  }
  if (!data) return null;
  return <RunEvalValue def={def} run={data} context={context} />;
};

EvalCell.propTypes = {
  node: PropTypes.object,
  data: PropTypes.object,
  context: PropTypes.object,
  colDef: PropTypes.object,
};

export default EvalCell;
