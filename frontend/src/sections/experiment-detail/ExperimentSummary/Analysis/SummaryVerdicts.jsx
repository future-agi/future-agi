import React, { useMemo } from "react";
import PropTypes from "prop-types";
import { alpha, Box, IconButton, Stack, Typography } from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import VerdictLine from "src/sections/common/ux/VerdictLine";
import CopyButton from "src/sections/common/ux/CopyButton";
import AnalysisCard from "src/sections/common/Analysis/AnalysisCard";
import { Link as RouterLink } from "react-router-dom";
import Iconify from "src/components/iconify";
import {
  pageShipLine,
  whoText,
} from "src/sections/experiment-detail/matrix/pageShip";
import useShortcuts from "src/sections/common/ux/useShortcuts";
import { StatusChip } from "src/sections/common/Runs/RunStatusChip";
import { getRunStatus } from "src/sections/common/Runs/runStatus";
import { ExpandAllButton, useOutline } from "src/sections/common/ux/oneview";
import {
  chipLabel,
  chipTone,
  confidenceHelp,
  bandOf,
  failedRunSentence,
  intervalText,
  isFailedRun,
  TIE_WORD,
  summaryVerdicts,
  wordCounts,
  toneHelp,
  verdictMarkdown,
} from "./verdictItems";

const TONE_COLOR = {
  better: (t) => t.palette.success.main,
  mixed: (t) => t.palette.warning.main,
  tie: (t) => t.palette.text.secondary,
  warn: (t) => t.palette.warning.main,
  worse: (t) => t.palette.error.main,
  error: (t) => t.palette.error.main,
};
const TONE_TEXT = {
  better: (t) => t.palette.success.dark,
  mixed: (t) => t.palette.warning.dark,
  tie: (t) => t.palette.text.secondary,
  warn: (t) => t.palette.warning.dark,
  worse: (t) => t.palette.error.dark,
  error: (t) => t.palette.error.dark,
};

/**
 * `Better · +25.4 pts` — tone word + the winning dimension, icon + colour.
 * A level run with more than 3 tasks worse reads `Level · 14 tasks worse`
 * in the warning tone. The tooltip states both 95% ranges and what
 * Confidence means.
 */
export function ToneChip({ item }) {
  const verdict = item?.verdict;
  const tone = chipTone(verdict);
  return (
    <CustomTooltip
      show
      size="small"
      placement="bottom-start"
      title={
        <>
          <Box component="span" sx={{ display: "block" }}>
            {toneHelp(item, { noun: item?.noun })}
          </Box>
          <Box
            component="span"
            sx={{ display: "block", mt: 0.75, opacity: 0.85 }}
          >
            {confidenceHelp(verdict, { noun: item?.noun })}
          </Box>
        </>
      }
    >
      <Box
        component="span"
        data-testid="verdict-tone-chip"
        sx={{
          display: "inline-flex",
          alignItems: "center",
          px: 0.75,
          height: 24,
          borderRadius: 0.75,
          typography: "s2",
          fontWeight: "fontWeightSemiBold",
          whiteSpace: "nowrap",
          flexShrink: 0,
          // IT-R5 G6 P3: never over the sentence (the column is 196 px).
          maxWidth: 168,
          overflow: "hidden",
          fontVariantNumeric: "tabular-nums",
          // dark: the main tone (≥ 4.5:1 on paper); light: the dark shade.
          color: (t) =>
            t.palette.mode === "dark"
              ? TONE_COLOR[tone](t)
              : TONE_TEXT[tone](t),
          bgcolor: (t) => alpha(TONE_COLOR[tone](t), 0.12),
        }}
      >
        <Box
          component="span"
          sx={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis" }}
        >
          {chipLabel(verdict)}
        </Box>
        <Whisker item={item} />
      </Box>
    </CustomTooltip>
  );
}
ToneChip.propTypes = { item: PropTypes.object };

/**
 * IT-R4 G6 P5: the 95% interval of the success difference as a tiny whisker
 * beside the chip word (0 = the centre tick). the interface contract C3: the verdict's ONE
 * interval (`verdict.interval`: the paired CI, or the unpaired ranges only
 * where no paired data exists), the same numbers as the band, the tooltip
 * and Confidence.
 */
function Whisker({ item }) {
  const iv = item?.verdict?.interval;
  const d = item?.verdict?.deltaPts;
  if (!iv || typeof d !== "number") return null;
  const { lo, hi } = iv;
  const span = Math.max(Math.abs(lo), Math.abs(hi), 1);
  const W = 36;
  const x = (v) => W / 2 + (v / span) * (W / 2 - 2);
  return (
    <Box
      component="svg"
      width={W}
      height={10}
      viewBox={`0 0 ${W} 10`}
      aria-label={`95% CI of the difference: ${lo.toFixed(1)} to ${hi.toFixed(1)} pts${iv.paired ? "" : " (unpaired ranges)"}`}
      role="img"
      data-testid="verdict-whisker"
      sx={{ ml: 0.75, flexShrink: 0, color: "currentColor", opacity: 0.9 }}
    >
      <line
        x1={W / 2}
        x2={W / 2}
        y1={1}
        y2={9}
        stroke="currentColor"
        strokeOpacity={0.35}
      />
      <line
        x1={x(lo)}
        x2={x(hi)}
        y1={5}
        y2={5}
        stroke="currentColor"
        strokeWidth={1.5}
      />
      <line x1={x(lo)} x2={x(lo)} y1={3} y2={7} stroke="currentColor" />
      <line x1={x(hi)} x2={x(hi)} y1={3} y2={7} stroke="currentColor" />
      <circle cx={x(d)} cy={5} r={2} fill="currentColor" />
    </Box>
  );
}
Whisker.propTypes = { item: PropTypes.object };

const SHIP_ICON = {
  worse: "mdi:alert-circle",
  better: "mdi:check-circle-outline",
  tie: "mdi:information-outline",
};

/**
 * IT-R4 G6 P1: the candidate's ship line under its verdict — the same
 * words as the Experiments tab and the Matrix (G2 `shipLine`):
 * `Don't ship the coding harness Code (#2D) yet: it fails T25, which Support Agent v3
 * (#2B) passes.  Open T25`.
 */
export function ShipRow({ ship, href }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={0.75}
      data-testid="summary-ship-line"
      data-tone={ship.tone}
      sx={{ mb: 0.5, minHeight: 24, minWidth: 0, flexWrap: "wrap" }}
    >
      <Iconify
        icon={SHIP_ICON[ship.tone] ?? SHIP_ICON.tie}
        width={16}
        sx={{
          flexShrink: 0,
          color:
            ship.tone === "worse"
              ? "error.main"
              : ship.tone === "better"
                ? "success.main"
                : "text.secondary",
        }}
      />
      <Typography typography="s1" color="text.primary">
        {ship.text}
      </Typography>
      {href && ship.rowText ? (
        <Typography
          component={RouterLink}
          to={href}
          typography="s2"
          fontWeight="fontWeightMedium"
          color="primary.main"
          sx={{
            textDecoration: "none",
            "&:hover": { textDecoration: "underline" },
          }}
        >
          {ship.rowText}
        </Typography>
      ) : null}
    </Stack>
  );
}
ShipRow.propTypes = {
  ship: PropTypes.object.isRequired,
  href: PropTypes.string,
};

// Caveats worth a glance before opening the trade-offs.
const NOTE_KINDS = new Set(["evaluator_version", "version_drift", "errors"]);

/**
 * Verdict first (R3 P5 MUST 1; one view A5-01): one line per run vs the
 * baseline, best first, every run shown, each with a tone chip (`Mixed ·
 * +25.4 pts`), the G0 sentence and two doors: the tasks that got worse
 * (rows whose count equals the sentence) and side by side. Under the chip,
 * the Excel outline toggle `+ Trade-offs` opens that run's trade-offs in
 * place (`Better: … · Worse: … · No clear change: …`, caveats, Re-score);
 * remembered per experiment / comparison (`useOutline`), `Expand all` (E) /
 * `Collapse all` (⇧E). `footer` (Most failed evals) closes the card. Each
 * run line is a data row (`data-oneview-row`).
 */
/**
 * IT-R5 G6 P3: with more than 2 candidates a collapsed run reads one line
 * (run, delta, cost), its 95% interval on hover (the interface contract C3: the verdict's
 * one interval, on the tasks both ran); the caret opens the
 * full sentence and the trade-offs.
 */
function OneLineVerdict({ item }) {
  const v = item.verdict ?? {};
  const parts = v.parts ?? [];
  const who = parts.find((p) => p.t === "run")?.text ?? item.run?.label ?? "";
  const delta = parts.find(
    (p) => p.t === "term" && (p.k === "delta" || p.k === "withinNoise"),
  )?.text;
  const cost = (v.chips ?? []).find((c) => c.key === "cost")?.label;
  // The tasks both ran (the headline's population): runNumbers.compareRuns'
  // rates and count, as the Experiments headline and the Matrix read them.
  const tv = item.run?.tasks_vs_baseline;
  const bothN = tv?.both ?? v.confidence?.tasks ?? 0;
  // the interface contract C3: the verdict's one interval (the chip's whisker and tooltip).
  const ivText = intervalText(v);
  const range = ivText ? { text: ivText } : null;
  return (
    <CustomTooltip
      show
      size="small"
      placement="top"
      title={
        range
          ? `${range.text} on the ${bothN} tasks both ran · ${v.sentence ?? ""}`
          : v.sentence ?? ""
      }
    >
      <Typography
        typography="s2"
        noWrap
        data-testid="summary-verdict-oneline"
        sx={{ pt: "3px" }}
      >
        <Box component="span" sx={{ fontWeight: "fontWeightSemiBold" }}>
          {who}
        </Box>
        {[delta, cost].filter(Boolean).map((x) => ` · ${x}`)}
      </Typography>
    </CustomTooltip>
  );
}

OneLineVerdict.propTypes = { item: PropTypes.object.isRequired };

/**
 * the capability contract: a candidate run that Failed (every row it played errored)
 * reads Failed with its own sentence, never a `Worse` chip, a ship line or
 * a `See why` door as if it had produced output.
 */
function FailedVerdictRow({ item }) {
  return (
    <Stack
      direction="row"
      alignItems="flex-start"
      gap={1.25}
      data-run={item.run.label}
      data-oneview-row
      data-testid="summary-failed-run"
      sx={{ minHeight: 40 }}
    >
      <Stack
        direction="row"
        alignItems="center"
        sx={{ width: 196, flexShrink: 0, pt: "1px", pl: "22px" }}
      >
        <StatusChip status={getRunStatus(item.run).key} />
      </Stack>
      <Typography
        typography="s1"
        color="text.primary"
        sx={{ flex: 1, minWidth: 0 }}
      >
        {failedRunSentence(item.run, item.base)}
      </Typography>
    </Stack>
  );
}

FailedVerdictRow.propTypes = { item: PropTypes.object.isRequired };

const SummaryVerdicts = ({
  analysis,
  items: given,
  rowsHref,
  sideBySideHref,
  title,
  outlineScope,
  renderTradeoffs,
  footer,
  candidateId,
  datasetKind,
  shipHref,
  headerActions,
}) => {
  const computed = useMemo(
    () => (given ? null : summaryVerdicts(analysis)),
    [given, analysis],
  );
  const candId = candidateId ?? analysis?.candidate_run_id ?? null;
  // IT-R5 G6 P2: sorted like the Matrix (success vs the baseline, highest
  // first); the page candidate's ship line (G2 `shipLine`, the Matrix's
  // words) is the first row of its own line.
  const items = useMemo(() => given ?? computed ?? [], [given, computed]);
  const ship = useMemo(() => {
    const item = items.find((x) => x.run.run_id === candId);
    const compare = item?.run?.tasks_vs_baseline?.compare;
    // the capability contract: a failed candidate has no ship line (no output to ship).
    if (!item || !compare || isFailedRun(item.run)) return null;
    const s = pageShipLine(compare, {
      cand: whoText(item.run),
      base: whoText(item.base),
      datasetKind,
      noun: analysis?.noun ?? "task",
    });
    return s ? { ...s, runId: item.run.run_id } : null;
  }, [items, candId, datasetKind, analysis?.noun]);
  const byRun = useMemo(
    () =>
      Object.fromEntries((analysis?.verdicts ?? []).map((v) => [v.run_id, v])),
    [analysis?.verdicts],
  );
  const withTradeoffs = renderTradeoffs
    ? items.filter((i) => byRun[i.run.run_id]).map((i) => i.run.run_id)
    : [];
  // Trade-offs open by default when the candidate's ship line is red.
  const shipRed = ship?.tone === "worse" ? ship.runId : null;
  const defaultOpen = useMemo(() => (id) => id === shipRed, [shipRed]);
  const outline = useOutline({
    surface: "summary",
    scopeId: outlineScope,
    ids: withTradeoffs,
    defaultOpen,
  });
  useShortcuts(
    "summary",
    {
      expandAllSteps: () => outline.expandAll(),
      collapseAllSteps: () => outline.collapseAll(),
    },
    { register: false, enabled: withTradeoffs.length > 0 },
  );
  // Runs with a verdict sentence but no line here (no success rate yet).
  const others = renderTradeoffs
    ? (analysis?.verdicts ?? []).filter(
        (v) => !items.some((i) => i.run.run_id === v.run_id),
      )
    : [];
  if (!items.length && !others.length) return null;
  // the capability contract: a run that Failed (every row errored) is named Failed, not
  // counted or ranked as a regression of the baseline.
  const failedCount = items.filter((i) => isFailedRun(i.run)).length;
  const scoredItems = items.filter((i) => !isFailedRun(i.run));
  const counts = wordCounts(scoredItems);
  const base = items[0]?.base ?? {
    label: analysis?.baseline_label ?? "",
  };
  // IT-R4 G6 P5: the tally names the chip words (`1 better with
  // trade-offs`), never `1 mixed`.
  const TALLY_WORD = {
    Better: "better",
    Mixed: "better with trade-offs",
    [TIE_WORD]: "too close to call",
    Worse: "worse",
  };
  const tally = [
    ...["Better", "Mixed", TIE_WORD, "Worse"]
      .filter((w) => counts[w])
      .map((w) => `${counts[w]} ${TALLY_WORD[w]}`),
    failedCount ? `${failedCount} failed` : null,
  ]
    .filter(Boolean)
    .join(" · ");
  // IT-R5 G6 P2: the noise band, once, at the top (the rule every chip
  // uses): `Differences inside ±19 pts on 30 tasks × 3 tries are too close
  // to call`.
  // the interface contract C3: the band is the verdicts' own interval half-widths (one
  // number for one candidate, `±a–b` across several), never a second rule.
  const halves = scoredItems
    .map((i) => bandOf(i.verdict))
    .filter((v) => Number.isFinite(v));
  const lowHalf = halves.length ? Math.min(...halves) : null;
  const highHalf = halves.length ? Math.max(...halves) : null;
  const band =
    lowHalf == null
      ? null
      : lowHalf === highHalf
        ? lowHalf.toFixed(1)
        : `${lowHalf.toFixed(1)}–${highHalf.toFixed(1)}`;
  const bandTasks = scoredItems[0]?.verdict?.confidence?.tasks ?? null;
  const bandTries = scoredItems[0]?.run?.trials ?? null;
  const bandNoun = analysis?.noun ?? "task";
  const bandText =
    band != null && scoredItems.length
      ? `Differences inside ±${band} pts${
          Number.isFinite(bandTasks)
            ? ` on ${bandTasks} ${bandTasks === 1 ? bandNoun : `${bandNoun}s`}${bandTries > 1 ? ` × ${bandTries} tries` : ""}`
            : ""
        } are too close to call`
      : null;

  return (
    <AnalysisCard
      id="analysis-verdict"
      icon="mdi:gavel"
      title={
        <>
          {/* IT-R5 G6 P3: plain words (the header's Baseline picker is the
              term's first use on this page). */}
          Verdict vs baseline {base.label}
        </>
      }
      titleAdornment={
        <Typography typography="s2" color="text.secondary" noWrap>
          {[
            tally,
            items.length > 1 ? `sorted by success vs ${base.label}` : null,
          ]
            .filter(Boolean)
            .join(" · ")}
        </Typography>
      }
      actions={
        <Stack direction="row" alignItems="center" gap={0.5}>
          {headerActions}
          {withTradeoffs.length > 1 && (
            // A bulk action: shown while the pointer is on the card, on
            // keyboard focus, or once a trade-off is open (then it reads
            // `Collapse all`); E / ⇧E and the + toggles work regardless.
            // IT-R5 G6 P3: on the Compare card (Explore / More beside it)
            // it shows on hover or focus only, never by default.
            <Box
              sx={{
                opacity: outline.anyOpen && !headerActions ? 1 : 0,
                transition: "opacity 120ms ease",
                "#analysis-verdict:hover &, &:focus-within": { opacity: 1 },
                "@media (hover: none)": { opacity: 1 },
              }}
            >
              <ExpandAllButton
                outline={outline}
                noun="trade-offs"
                expandAction="expandAllSteps"
                collapseAction="collapseAllSteps"
              />
            </Box>
          )}
          {/* IT-R5 G6 P3: with headerActions the copy is a More item. */}
          {headerActions ? null : (
            <CopyButton
              value={() => verdictMarkdown(analysis, items, { title })}
              tooltip="Copy summary (Markdown with the numbers)"
              toast="Summary copied"
            />
          )}
        </Stack>
      }
    >
      <Stack gap={1} data-testid="summary-verdicts">
        {bandText ? (
          <Typography
            typography="s3"
            color="text.secondary"
            data-testid="summary-noise-band"
            sx={{ mt: -0.5 }}
          >
            {bandText}
          </Typography>
        ) : null}
        {items.map((item) => {
          if (isFailedRun(item.run))
            return <FailedVerdictRow key={item.key} item={item} />;
          const n = item.worseIds.length;
          const runId = item.run.run_id;
          const v = byRun[runId];
          const open = Boolean(v) && outline.isOpen(runId);
          const detailId = `verdict-tradeoffs-${runId}`;
          const note = v?.caveats?.find((c) => NOTE_KINDS.has(c.kind));
          const actions = [];
          const noun = analysis?.noun ?? "task";
          if (n > 0 && rowsHref)
            actions.push({
              label: `See the ${n} ${n === 1 ? noun : `${noun}s`} that got worse`,
              href: rowsHref(item),
              tooltip: `${noun === "task" ? "Tasks" : "Rows"} whose success rate on ${item.run.label} is lower than on ${base.label}, across their tries`,
            });
          // IT-R1 G2 P4: verdict → reason in one click.
          if (sideBySideHref)
            actions.push({
              label: n ? "See why" : "Side by side",
              icon: "mdi:compare-horizontal",
              href: sideBySideHref(item),
              tooltip: n
                ? `Open ${item.run.label} next to ${base.label} on the task that got worse most`
                : `Open ${item.run.label} next to ${base.label}, task by task`,
            });
          // IT-R5 G6 P2: the counts the sentence prints (`7 better · 2
          // worse`), never a second `7 got worse`.
          const nb = Number.isFinite(item.verdict?.better)
            ? item.verdict.better
            : item.betterIds?.length ?? 0;
          const short = actions.map((x) => ({
            ...x,
            label: x.label.startsWith("See the ")
              ? `${nb ? `${nb} better · ` : ""}${n} worse`
              : x.label,
          }));
          return (
            <Stack
              key={item.key}
              direction="row"
              alignItems="flex-start"
              gap={1.25}
              data-run={item.run.label}
              data-oneview-row
              sx={{
                minHeight: 40,
                // IT-R4 G6 P5: the row's links show on hover / focus.
                "& .sv-links": { opacity: 0, transition: "opacity 120ms" },
                "&:hover .sv-links, &:focus-within .sv-links": { opacity: 1 },
                "@media (hover: none)": { "& .sv-links": { opacity: 1 } },
              }}
            >
              <Stack
                gap={0.25}
                alignItems="flex-start"
                sx={{ width: 196, flexShrink: 0, pt: "1px" }}
              >
                <Stack direction="row" alignItems="center" gap={0.25}>
                  {v && renderTradeoffs ? (
                    <CustomTooltip
                      show
                      size="small"
                      placement="top"
                      title={`${open ? "Hide" : "Show"} the trade-offs of ${item.run.label}`}
                    >
                      <IconButton
                        size="small"
                        aria-expanded={open}
                        aria-controls={detailId}
                        aria-label={`Trade-offs of ${item.run.label}`}
                        onClick={() => outline.toggle(runId)}
                        data-testid="verdict-tradeoffs-caret"
                        sx={{ p: 0.25, color: "text.secondary" }}
                      >
                        <Iconify
                          icon={open ? "mdi:chevron-down" : "mdi:chevron-right"}
                          width={18}
                        />
                      </IconButton>
                    </CustomTooltip>
                  ) : (
                    <Box sx={{ width: 22 }} />
                  )}
                  <ToneChip item={item} />
                  {note && !open ? (
                    <CustomTooltip
                      show
                      size="small"
                      placement="top"
                      title={note.text}
                    >
                      <Box
                        component="span"
                        role="img"
                        aria-label={`Note: ${note.text}`}
                        sx={{
                          ml: 0.5,
                          width: 6,
                          height: 6,
                          borderRadius: "50%",
                          bgcolor: "warning.main",
                        }}
                      />
                    </CustomTooltip>
                  ) : null}
                </Stack>
                {short.length ? (
                  <Stack
                    direction="row"
                    gap={1}
                    className="sv-links"
                    sx={{ pl: "26px" }}
                  >
                    {short.map((x) => (
                      <CustomTooltip
                        key={x.label}
                        show={Boolean(x.tooltip)}
                        size="small"
                        placement="bottom"
                        title={x.tooltip ?? ""}
                      >
                        <Typography
                          component={RouterLink}
                          to={x.href}
                          typography="s3"
                          color="primary.main"
                          noWrap
                          sx={{
                            textDecoration: "none",
                            "&:hover, &:focus-visible": {
                              textDecoration: "underline",
                            },
                          }}
                        >
                          {x.label}
                        </Typography>
                      </CustomTooltip>
                    ))}
                  </Stack>
                ) : null}
              </Stack>
              <Box sx={{ flex: 1, minWidth: 0 }}>
                {ship && ship.runId === runId ? (
                  <ShipRow ship={ship} href={shipHref?.(ship)} />
                ) : null}
                {items.length > 2 &&
                v &&
                renderTradeoffs &&
                !open &&
                !(ship && ship.runId === runId) ? (
                  <OneLineVerdict item={item} />
                ) : (
                  <VerdictLine
                    verdict={item.verdict}
                    actions={[]}
                    sx={{ "& p": { typography: "s2", WebkitLineClamp: 2 } }}
                  />
                )}
                {open ? (
                  <Box sx={{ ml: "26px" }}>{renderTradeoffs(v, detailId)}</Box>
                ) : null}
              </Box>
            </Stack>
          );
        })}
        {others.map((v) => (
          <Box key={v.run_id} data-oneview-row>
            {renderTradeoffs(v, `verdict-tradeoffs-${v.run_id}`)}
          </Box>
        ))}
        {footer ? <Box sx={{ mt: 0.25 }}>{footer}</Box> : null}
      </Stack>
    </AnalysisCard>
  );
};

SummaryVerdicts.propTypes = {
  analysis: PropTypes.object.isRequired,
  items: PropTypes.array,
  rowsHref: PropTypes.func,
  sideBySideHref: PropTypes.func,
  title: PropTypes.string,
  // one view A5-01: the outline scope (experiment id or compare key), the
  // trade-offs of one analysis verdict `(verdict, id) => node`, the footer
  outlineScope: PropTypes.string,
  renderTradeoffs: PropTypes.func,
  footer: PropTypes.node,
  // IT-R4 G6 P1: the page candidate (`cand=`; default the analysis'),
  // the dataset kind for row labels (`T25`), and the `Open T25` link
  // `({runId, rowId}) => href`.
  candidateId: PropTypes.string,
  datasetKind: PropTypes.string,
  shipHref: PropTypes.func,
  // IT-R5 G6 P3: Explore / More beside Expand all (Compare Summary).
  headerActions: PropTypes.node,
};

export default SummaryVerdicts;
