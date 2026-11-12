import React, { useEffect, useMemo, useRef } from "react";
import PropTypes from "prop-types";
import { Box, Stack } from "@mui/material";
import { useLocation, useNavigate } from "react-router-dom";
import { useDatasetKind } from "src/sections/common/Datasets/datasetKind";
import usePagePair from "../paths/usePagePair";
import { bannerPairOf } from "../iterate/bannerPair";
import { useIterateStore } from "../iterate/iterateStore";
import { headerCandidate } from "./pageCandidate";
import { configKeyOf, useMatrix, useRunConfigs } from "src/api/develop/r2";
import { fmtCount, fmtPct, fmtPts } from "src/sections/common/format/fmt";
import { compareRuns, runRate } from "src/sections/common/Runs/runNumbers";
import { parentExperimentRuns } from "../matrix/useMatrixData";
import { buildMatrixModel, verdictInputs } from "../matrix/matrixModel";
import { scopeVerdictInputs, scopedVerdict } from "../matrix/verdictScope";
import { pageShipLine, whoText } from "../matrix/pageShip";
import VerdictLine from "src/sections/common/ux/VerdictLine";
import HoverCard from "src/sections/common/ux/oneview/HoverCard";
import Term from "src/sections/common/ux/Term";
import { sortVerdicts } from "src/sections/common/ux/verdictModel";

/**
 * The verdict above the results grid (R3 MUST 1, SVP-P0-1b): one line per
 * run vs the baseline from the same Matrix read (same cache key as the
 * Matrix tab, so the numbers are the Matrix's). One view (A3-08, §3.11):
 * the most important line and `+N more` (the grid keeps its rows), the same
 * fold as the Matrix (VerdictFold, remembered under `results`).
 *
 * iterate r2 G3 (P2, rule 12): the line that leads is the run the page
 * header shows (headerCandidate: `cand=` › `run=` › pageCandidate), never
 * a pick of its own; when the fix-run banner already speaks about that
 * pair, this line hides (one verdict on screen).
 *
 * @param {string} props.datasetId
 * @param {string[]} props.runIds
 * @param {string} props.baselineId
 * @param {(runId, rowIds, label) => void} props.onWorse  `See the 7 tasks that got worse`
 * @param {(runId, rowIds) => void} props.onWhy
 */
/**
 * iterate r3 G3 (P2): one count per word — the verdict's `worse 2` names
 * its eval (`worse 2 (Task success)`), the same number as the toolbar's
 * `Task success worse (2)`.
 */
export function namedWorse(verdict, metricName, anyEval) {
  if (!verdict || !metricName) return verdict;
  // IT-R5 G3 (P2): Worse means one thing — `worse on Correctness 3 · on
  // any eval 9` (the Worse pill's two numbers).
  const any = Number.isFinite(anyEval) ? ` · on any eval ${anyEval}` : "";
  const name = (p) => {
    if (p?.t !== "term" || p.k !== "worse" || String(p.text).includes(" on "))
      return [p];
    const n = String(p.text).match(/\d+/)?.[0];
    return n == null
      ? [p]
      : [
          { ...p, text: `worse on ${metricName} ${n}` },
          { t: "text", text: any },
        ];
  };
  return {
    ...verdict,
    ...(Array.isArray(verdict.parts)
      ? { parts: verdict.parts.flatMap(name).filter((x) => x.text !== "") }
      : {}),
    ...(Array.isArray(verdict.counts)
      ? { counts: verdict.counts.flatMap(name).filter((x) => x.text !== "") }
      : {}),
  };
}

/**
 * IT-R5 G3 (P1 budget): the strip's lead sentence without its trailing
 * `Confidence: medium (30 tasks × 3 tries).` — that clause moves into the
 * `+N` card (the sentence keeps `across 30 tasks`). Returns `{verdict,
 * confidence}`; `confidence` = the clause's text after `Confidence`, or
 * null when the verdict has none. Pure.
 */
export function splitConfidence(verdict) {
  const parts = verdict?.parts;
  const at = Array.isArray(parts)
    ? parts.findIndex((p) => p?.t === "term" && p.k === "confidence")
    : -1;
  if (at < 0) return { verdict, confidence: null };
  const tail = parts[at + 1]?.t === "text" ? parts[at + 1].text : "";
  const kept = parts.filter((_, i) => i !== at && !(tail && i === at + 1));
  const last = kept[kept.length - 1];
  if (last?.t === "text")
    kept[kept.length - 1] = { ...last, text: last.text.trimEnd() };
  return {
    verdict: {
      ...verdict,
      parts: kept,
      sentence: kept.map((p) => p.text).join(""),
    },
    confidence: tail.replace(/^:\s*/, "").replace(/\.$/, "") || null,
  };
}

export default function ResultsVerdict({
  datasetId,
  runIds,
  baselineId,
  mode,
  experimentId,
  frozen = false,
  onWorse,
  onWhy,
  primaryName,
  onLead,
  // eslint-disable-next-line no-unused-vars
  hideLeadWhy = false,
  after = null,
  worseAnyOf,
}) {
  const enabled = (runIds ?? []).length > 1 && Boolean(baselineId);
  const q = useMatrix(
    { datasetId, runIds, baselineId, measure: "pass_at_1" },
    { enabled },
  );
  const runsQ = useRunConfigs(datasetId, { enabled });
  const runsById = useMemo(
    () =>
      Object.fromEntries((runsQ.data?.results ?? []).map((r) => [r.run_id, r])),
    [runsQ.data],
  );
  const { kind } = useDatasetKind(datasetId);
  const noun = kind === "task_suite" ? "task" : "row";
  const navigate = useNavigate();
  // P8: the baseline's own move vs the same setup in the experiment before
  // (`#2B is the baseline. vs its previous version #1B: −1.1 pts`).
  const prevId = useMemo(() => {
    const all = runsQ.data?.results ?? [];
    const base = all.find((r) => r.run_id === baselineId);
    if (!base) return null;
    const inView = all.filter((r) => (runIds ?? []).includes(r.run_id));
    const key = configKeyOf(base);
    return (
      parentExperimentRuns(all, inView).find((r) => configKeyOf(r) === key)
        ?.run_id ?? null
    );
  }, [runsQ.data, baselineId, runIds]);
  const prevQ = useMatrix(
    {
      datasetId,
      runIds: prevId ? [prevId, baselineId] : [],
      baselineId: prevId,
    },
    { enabled: enabled && Boolean(prevId) },
  );
  const items = useMemo(() => {
    if (!q.data) return [];
    const model = buildMatrixModel(q.data, { runIds, baselineId });
    const base =
      runsById[baselineId]?.label ??
      model.columnsById[baselineId]?.label ??
      "the baseline";
    // 8b: the numbers and scope from runNumbers.compareRuns,
    // the same call as the Matrix and the Iterations chain.
    return scopeVerdictInputs(verdictInputs(model, runsById), {
      matrix: q.data,
      baselineId,
      noun,
    }).map((v) => {
      const actions = [];
      // P8: one regression word — single-try rows say what happened
      // (`pass → fail`); several tries keep `got worse` (fewer tries pass).
      const oneTry = (q.data.rows ?? []).every(
        (r) =>
          (r.cells?.[v.runId]?.trials?.length ?? 1) <= 1 &&
          (r.cells?.[baselineId]?.trials?.length ?? 1) <= 1,
      );
      if (v.worse > 0)
        actions.push({
          label: oneTry
            ? `See the ${fmtCount(v.worse, noun)} that went pass → fail`
            : `See the ${fmtCount(v.worse, noun)} that got worse`,
          tooltip: `Show only the tasks where ${v.label} passes fewer tries than ${base}`,
          onClick: () =>
            onWorse?.(v.runId, v.worseRows, `${v.label} worse than ${base}`),
        });
      // IT-R1 G2 P4: the same words as every verdict line.
      actions.push({
        label: "See why",
        tooltip: `Open the first ${noun} that got worse side by side`,
        disabled: !v.worseRows?.length,
        onClick: () => onWhy?.(v.runId, v.worseRows),
      });
      const verdict = namedWorse(
        scopedVerdict(v),
        primaryName,
        worseAnyOf?.(v.runId),
      );
      // L2 G2 R4 (consistency #1): the ship warning the Experiments tab and
      // Compare Summary show (pageShip.pageShipLine, one builder).
      const shipped = v.compare
        ? pageShipLine(v.compare, {
            cand: whoText(runsById[v.runId], v.label),
            base: whoText(runsById[baselineId], base),
            datasetKind: kind,
            noun,
          })
        : null;
      return {
        key: v.runId,
        verdict,
        ship: shipped?.tone === "worse" ? shipped.text : null,
        actions,
        meta: {
          runId: v.runId,
          label: v.label,
          worse: v.worse ?? 0,
          worseRows: v.worseRows ?? [],
          tone: verdict?.tone ?? null,
          // IT-R4 G3 (P1, rule 28): the population the verdict reads on,
          // for the footer's one population line.
          bothRan: v.compare
            ? v.compare.better + v.compare.worse + v.compare.same
            : null,
          notRun: v.compare?.notRun ?? 0,
          partial: v.compare?.partial ?? null,
        },
      };
    });
  }, [
    q.data,
    runIds,
    baselineId,
    runsById,
    onWorse,
    onWhy,
    noun,
    kind,
    primaryName,
    worseAnyOf,
  ]);
  const baseItem = useMemo(() => {
    if (!prevQ.data || !prevId) return null;
    const cmp = compareRuns({
      matrix: prevQ.data,
      baselineId: prevId,
      candidateId: baselineId,
      noun,
    });
    if (!Number.isFinite(cmp.deltaPts)) return null;
    const base = runsById[baselineId]?.label ?? cmp.candidate.label;
    const prev = runsById[prevId]?.label ?? cmp.baseline.label;
    // IT-R4 G3 (P1, rule 28): when the two ran different rows, the delta
    // names its rows and both runs' own rates follow (`±0.0 pts on the 24
    // tasks both ran · 65.6% vs 66.7% on all rows`).
    const both = cmp.better + cmp.worse + cmp.same;
    const ownRate = (id) => runRate({ matrix: prevQ.data, runId: id }).rate;
    const text = cmp.notRun
      ? `: ${fmtPts(cmp.deltaPts)} on the ${fmtCount(both, noun)} both ran · ${fmtPct(ownRate(baselineId))} vs ${fmtPct(ownRate(prevId))} on all rows`
      : `: ${fmtPts(cmp.deltaPts)} ${cmp.scopeText}`;
    return {
      key: `${baselineId}:previous`,
      verdict: {
        tone:
          cmp.deltaPts < -0.05
            ? "worse"
            : cmp.deltaPts > 0.05
              ? "better"
              : "tie",
        deltaPts: cmp.deltaPts,
        sentence: `${base} is the baseline. vs ${prev} (previous version)${text}`,
        parts: [
          { t: "run", text: base },
          { t: "text", text: " is the " },
          { t: "term", k: "baseline", text: "baseline" },
          { t: "text", text: ". vs " },
          { t: "run", text: prev },
          { t: "text", text: ` (previous version)${text}` },
        ],
      },
      actions: [
        {
          label: `Compare ${base} with ${prev}`,
          tooltip: `Open ${prev} and ${base} side by side, ${prev} as the baseline`,
          onClick: () =>
            navigate(
              `/dashboard/develop/${datasetId}/compare/data?runs=${prevId},${baselineId}&baseline=${prevId}&cand=${baselineId}`,
            ),
        },
      ],
    };
  }, [prevQ.data, prevId, baselineId, runsById, noun, navigate, datasetId]);

  // P2: the header's candidate leads (the same pick as the header).
  const { pathname, search } = useLocation();
  const sp = new URLSearchParams(search);
  const pagePair = usePagePair({
    datasetId: enabled ? datasetId : null,
    experimentId: mode === "experiment" ? experimentId : undefined,
    runIds,
    baselineId,
    search,
  });
  const leadKey =
    headerCandidate({
      mode,
      cand: sp.get("cand"),
      run: sp.get("run"),
      pageCand: pagePair.candidateId,
      baselineId,
      runIds,
    }) ?? undefined;
  // The viewed run IS the baseline (`run=` on it, or IT-R4 G3 P2: a fix
  // loop whose subject is the baseline, `cand=` on it): its own move leads
  // and no other run's verdict feeds the toolbar.
  const onBase =
    sp.get("run") === baselineId ||
    (Boolean(baselineId) && sp.get("cand") === baselineId);
  // P2: the lead line's numbers reach the toolbar (one count per word).
  const leadMeta = onBase
    ? null
    : items.find((i) => i.key === leadKey)?.meta ?? null;
  const leadSig = leadMeta
    ? `${leadMeta.runId}:${leadMeta.worse}:${leadMeta.worseRows.join(",")}:${leadMeta.tone}:${leadMeta.bothRan}:${leadMeta.partial?.scored}/${leadMeta.partial?.total}`
    : "";
  useEffect(() => {
    onLead?.(leadMeta);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [leadSig]);
  const held = useRef(null);
  const last = useIterateStore((st) => st.lastFinished);
  const dismissed = useIterateStore((st) => st.dismissed);
  const banner = bannerPairOf({
    pathname,
    search,
    datasetId,
    runIds,
    baselineId,
    last,
    dismissed,
  });
  if (banner && banner.baseId === baselineId && banner.candId === leadKey)
    return null;
  // Otherwise the baseline's own move is one more line under `+N`.
  const fresh = baseItem
    ? [...items, onBase ? baseItem : { ...baseItem, last: true }]
    : items;
  // P7: `Pause updates` holds the verdict with the rows.
  if (!frozen || !held.current?.length) held.current = fresh;
  const all = frozen ? held.current : fresh;
  const lead = onBase && baseItem ? baseItem.key : leadKey;
  // IT-R5 G3 (P1, S6): `Why ▾` (after) replaces every line's own `See why`.
  const shown = all.map((i) => ({
    ...i,
    actions: (i.actions ?? []).filter((a) => a.label !== "See why"),
  }));
  const ship = onBase
    ? null
    : items.find((i) => i.key === leadKey)?.ship ?? null;
  return (
    <VerdictStrip items={shown} leadKey={lead} after={after} ship={ship} />
  );
}

/**
 * IT-R5 G3 (P1): the verdict strip — its own full-width row. The lead
 * sentence wraps to at most 2 lines (never an ellipsis), keeps ONE inline
 * text action (`See the 1 task that got worse`), then `+4` (a chip whose
 * hover card lists the other lines) and `after` (`Why ▾`).
 */
export function VerdictStrip({ items, leadKey, after, ship = null }) {
  const ranked = sortVerdicts((items ?? []).filter((i) => !i?.last));
  const leadItem = leadKey ? ranked.find((i) => i?.key === leadKey) : null;
  const sorted = [
    ...(leadItem
      ? [leadItem, ...ranked.filter((i) => i !== leadItem)]
      : ranked),
    ...(items ?? []).filter((i) => i?.last),
  ];
  if (!sorted.length) return after ?? null;
  const [first, ...rest] = sorted;
  // The confidence clause rides in the `+N` card (when there is one).
  const split = rest.length ? splitConfidence(first.verdict) : null;
  const top = split ? { ...first, verdict: split.verdict } : first;
  // One paragraph flow: the sentence, its action, `+4` and `Why ▾` wrap
  // together like words, so the sentence gets the full width (≤ 2 lines
  // at 1280) and nothing is ever cut.
  return (
    <Box
      data-testid="results-verdict"
      sx={{ minWidth: 0, typography: "s2", lineHeight: "22px", py: "1px" }}
    >
      <VerdictLine
        verdict={top.verdict}
        actions={(top.actions ?? []).slice(0, 1)}
        compact
        sx={FLOW_SX}
      />
      {rest.length ? (
        <HoverCard
          trigger={`+${rest.length}`}
          title={`${rest.length} more ${rest.length === 1 ? "finding" : "findings"}`}
          maxWidth={560}
          placement="bottom-end"
          triggerProps={{
            "data-testid": "verdict-more",
            "aria-label": `${rest.length} more ${rest.length === 1 ? "finding" : "findings"}`,
          }}
          cardProps={{ "data-testid": "verdict-more-popover" }}
          triggerSx={MORE_CHIP_SX}
        >
          <Stack gap={1} sx={{ pt: 0.5, "& .MuiButton-root": { height: 24 } }}>
            {split?.confidence ? (
              <Box
                data-testid="verdict-confidence"
                sx={{ typography: "s2", color: "text.secondary" }}
              >
                <Term k="confidence">Confidence</Term>
                {` of the lead line: ${split.confidence}`}
              </Box>
            ) : null}
            {rest.map((item, i) => (
              <VerdictLine
                key={item.key ?? i}
                verdict={item.verdict}
                actions={item.actions ?? []}
                compact
                sx={STRIP_SX}
              />
            ))}
          </Stack>
        </HoverCard>
      ) : null}
      {after ? (
        <Box
          component="span"
          sx={{ display: "inline-flex", verticalAlign: "middle", ml: 1 }}
        >
          {after}
        </Box>
      ) : null}
      {ship ? (
        <Box
          data-testid="results-ship-line"
          sx={{ typography: "s2", color: "error.main", fontWeight: 500 }}
        >
          {ship}
        </Box>
      ) : null}
    </Box>
  );
}
VerdictStrip.propTypes = {
  items: PropTypes.array,
  leadKey: PropTypes.string,
  after: PropTypes.node,
  /** L2 G2 R4: the lead pair's `Don't ship … yet` line. */
  ship: PropTypes.string,
};

// Two lines that wrap by word, never a cut (`slower....`).
const STRIP_SX = {
  "& p.MuiTypography-root": {
    WebkitLineClamp: "unset",
    display: "block",
    overflow: "visible",
  },
  "& .MuiButton-root": { height: 24, py: 0 },
  "& > svg, & > span:first-of-type": { mt: "2px" },
};

// The lead line as inline text: dot, sentence, then its one action.
const FLOW_SX = {
  display: "inline",
  "& > :first-of-type": {
    display: "inline-flex",
    verticalAlign: "-3px",
    mr: 0.75,
  },
  "& > .MuiStack-root": { display: "inline" },
  "& p.MuiTypography-root": {
    display: "inline",
    WebkitLineClamp: "unset",
    overflow: "visible",
  },
  "& p + .MuiStack-root": {
    display: "inline-flex",
    verticalAlign: "middle",
    ml: 0.5,
  },
  "& .MuiButton-root": { height: 22, py: 0 },
};

const MORE_CHIP_SX = {
  display: "inline-flex",
  verticalAlign: "middle",
  ml: 0.5,
  alignItems: "center",
  flexShrink: 0,
  height: 22,
  px: 0.75,
  borderRadius: 11,
  border: "1px solid",
  borderColor: "divider",
  typography: "s2",
  fontWeight: "fontWeightMedium",
  color: "text.secondary",
  cursor: "pointer",
  fontVariantNumeric: "tabular-nums",
  "&:hover, &[aria-expanded='true']": { bgcolor: "action.hover" },
  "&:focus-visible": { outline: "2px solid", outlineColor: "primary.main" },
};

ResultsVerdict.propTypes = {
  datasetId: PropTypes.string,
  runIds: PropTypes.arrayOf(PropTypes.string),
  baselineId: PropTypes.string,
  mode: PropTypes.oneOf(["experiment", "compare"]),
  experimentId: PropTypes.string,
  frozen: PropTypes.bool,
  onWorse: PropTypes.func,
  onWhy: PropTypes.func,
  /** P2: the primary eval's name (`worse 2 (Task success)`). */
  primaryName: PropTypes.string,
  /** P2: `{runId, label, worse, worseRows}` of the line that leads. */
  onLead: PropTypes.func,
  /** IT-R4 G3 (P4): the strip's `See why ▾` stands for the lead's. */
  hideLeadWhy: PropTypes.bool,
  /** IT-R5 G3 (P1): controls right of the strip (`Why ▾`). */
  after: PropTypes.node,
  /** IT-R5 G3 (P2): `(runId) => rows worse on any eval` (`on any eval 9`). */
  worseAnyOf: PropTypes.func,
};
