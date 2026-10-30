import React, { useDeferredValue, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { alpha, getContrastRatio, useTheme } from "@mui/material/styles";
import { UNITS } from "src/sections/projects/observePopulation";
import { formatCount, formatShare, isYesNo, resolveFocus } from "./askAnswers";
import {
  LEVELS,
  MAX_USERS,
  PIVOTS,
  SMALL_N,
  buildMatrix,
  buildModelDayGrid,
  buildTimeGrid,
  gridLowerPeak,
  gridPeak,
  shareLevel,
  wilsonLower,
} from "./askPivot";
import { groupEntries, runTally } from "./askSummary";
import AnswerMatrix from "./AnswerMatrix";
import CellGrid from "./CellGrid";
import { dayText } from "./timelineBins";
import {
  Swatch,
  VizCard,
  answerRamp,
  legendAnswers,
  quoted,
  rowsNoun,
  useAnswerColor,
  useRunData,
  useWidth,
} from "./vizParts";

const LABEL_COL = 112;
const TOTAL_COL = 96;
// Up to this many errored rows, every cell with one is dotted; past it, only
// cells where errors run well above the usual rate.
const FEW_ERRORS = 30;
const GRID_GAP = 3;
const COUNT_FITS = 20;
// "100" in 10px figures.
const TOTAL_FITS = 17;
const EACH = "__each__";
// Small multiples: a panel's day labels, and how many panels share a row.
const PANEL_DAYS = 44;
const PANEL_MODELS = 96;
const PANEL_GAP = 20;

// Thin stripes of a step's colour over the palest tint: a cell with too few
// answers to trust.
const hatch = (color) =>
  `repeating-linear-gradient(135deg, ${alpha(color, 0.6)} 0 1.5px, transparent 1.5px 4.5px)`;

const pct = (share) => `${Math.round(share * 100)}%`;

// The legend of a shading scale: 0% to its top, in the answer's own hue.
function ScaleLegend({ ramp, peak, answer, extra }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={0.75}
      useFlexGap
      flexWrap="wrap"
      sx={{ mt: 1.25 }}
    >
      <Typography variant="caption" color="text.disabled">
        0%
      </Typography>
      {Array.from({ length: LEVELS }, (_, level) => (
        <Box
          key={level}
          aria-hidden
          sx={{
            width: 14,
            height: 12,
            borderRadius: "2px",
            bgcolor: ramp[level],
          }}
        />
      ))}
      <Typography variant="caption" color="text.disabled">
        → {pct(peak)} {answer}
      </Typography>
      {extra}
    </Stack>
  );
}

ScaleLegend.propTypes = {
  ramp: PropTypes.array.isRequired,
  peak: PropTypes.number.isRequired,
  answer: PropTypes.node,
  extra: PropTypes.node,
};

/**
 * One Day × Hour (or Weekday × Hour) grid shaded by the share of one answer:
 * pale (none of a cell's answers gave it) to deep (the top of the scale). A
 * cell with fewer than 3 answered rows is hatched, one with rows still being
 * read is outlined, and one with rows whose trace had an error carries a red
 * dot. Click a cell to select its rows.
 */
function TimeGrid({ grid, question, answer, onSelect, unit, sample, running }) {
  const theme = useTheme();
  const colorOf = useAnswerColor();
  const box = useRef(null);
  const width = useWidth(box);
  const plural = rowsNoun(unit, sample, 2);
  const ramp = answerRamp(colorOf(question, answer), theme.palette.mode);
  const peak = gridPeak(grid, answer);
  const { rows, columns, cells } = grid;
  const matchesOf = (cell) => cell?.byLabel.get(answer) || 0;
  const inkOn = (level, small) =>
    !small && level >= 3 && getContrastRatio(ramp[level], "#fff") >= 3
      ? theme.palette.common.white
      : theme.palette.text.primary;
  const overallShare = grid.overall.answered
    ? (grid.overall.byLabel.get(answer) || 0) / grid.overall.answered
    : null;
  const stats = useMemo(() => {
    const sum = (list) =>
      list.reduce(
        (acc, cell) =>
          cell
            ? {
                answered: acc.answered + cell.answered,
                matches: acc.matches + (cell.byLabel.get(answer) || 0),
              }
            : acc,
        { answered: 0, matches: 0 },
      );
    const rowTotals = rows.map((row) =>
      sum(columns.map((col) => cells.get(`${row.key}|${col.key}`))),
    );
    const colTotals = columns.map((col) =>
      sum(rows.map((row) => cells.get(`${row.key}|${col.key}`))),
    );
    const all = [...cells.values()];
    const errorTotal = all.reduce((s, cell) => s + cell.errors, 0);
    const rowCount = all.reduce((s, cell) => s + cell.n, 0);
    return {
      rowTotals,
      colTotals,
      grand: sum(all),
      errorTotal,
      errorFloor: Math.max(0.05, (3 * errorTotal) / (rowCount || 1)),
      anyPending: all.some((cell) => cell.pending > 0),
    };
  }, [rows, columns, cells, answer]);
  const fewErrors = stats.errorTotal <= FEW_ERRORS;
  const dotted = (cell) =>
    Boolean(cell?.errors) &&
    (fewErrors ||
      (cell.errors >= 2 && cell.errors / cell.n >= stats.errorFloor));
  const cellW =
    width && columns.length
      ? (width - LABEL_COL - TOTAL_COL - columns.length * GRID_GAP) /
        columns.length
      : 0;
  const cellH = rows.length <= 10 ? 20 : rows.length <= 20 ? 16 : 13;
  const showCounts = cellW >= COUNT_FITS && cellH >= 16;
  const tickEvery = grid.step === 1 ? 3 : 1;
  const ticked = (c) =>
    grid.pivot === "modelDay" ? columns[c].tick : c % tickEvery === 0;
  const byWhat =
    {
      weekdayHour: "weekday and hour",
      modelDay: "model and day",
    }[grid.pivot] ||
    `day and ${grid.step === 1 ? "hour" : `${grid.step}-hour block`}`;

  const paint = useMemo(
    () => (r, c) => {
      const cell = cells.get(`${rows[r].key}|${columns[c].key}`);
      if (!cell) return { fill: alpha(theme.palette.text.disabled, 0.08) };
      const small = cell.answered > 0 && cell.answered < SMALL_N;
      const level = cell.answered
        ? shareLevel(matchesOf(cell) / cell.answered, peak)
        : 0;
      return {
        fill: cell.answered ? (small ? ramp[0] : ramp[level]) : null,
        hatch: small ? ramp[Math.max(level, 2)] : null,
        outline: cell.pending ? (cell.answered ? "solid" : "dashed") : null,
        dot: dotted(cell),
        text: showCounts ? formatCount(cell.n) : null,
        ink: cell.answered ? inkOn(level, small) : null,
      };
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [cells, rows, columns, answer, peak, showCounts, theme, stats],
  );
  const footer = useMemo(
    () => ({
      label:
        {
          weekdayHour: "All weeks, %",
          modelDay: "All models, %",
        }[grid.pivot] || "All days, %",
      paint: (c) => {
        const t = stats.colTotals[c];
        if (!t.answered)
          return { fill: alpha(theme.palette.text.disabled, 0.08) };
        const small = t.answered < SMALL_N;
        const level = shareLevel(t.matches / t.answered, peak);
        return {
          fill: small ? ramp[0] : ramp[level],
          hatch: small ? ramp[Math.max(level, 2)] : null,
          text:
            cellW >= TOTAL_FITS
              ? String(Math.round((t.matches / t.answered) * 100))
              : null,
          ink: inkOn(level, small),
        };
      },
      describe: (c) => {
        const t = stats.colTotals[c];
        return t.answered
          ? `${columns[c].title}: ${formatCount(t.matches)} of ${formatCount(t.answered)} ${quoted(answer)} (${formatShare(t.matches / t.answered)}) vs ${formatShare(overallShare)} overall`
          : `${columns[c].title}: no answers yet`;
      },
      rightText: stats.grand.answered
        ? `${formatShare(stats.grand.matches / stats.grand.answered)} of ${formatCount(stats.grand.answered)}`
        : "–",
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [stats, columns, peak, cellW, theme, answer],
  );

  const describe = (r, c) => {
    const row = rows[r];
    const col = columns[c];
    const cell = cells.get(`${row.key}|${col.key}`);
    const where = `${row.title} · ${col.title}`;
    if (!cell) return `${where}: no ${plural}`;
    const error = cell.errors
      ? ` · ${formatCount(cell.errors)} had a trace error`
      : "";
    if (!cell.answered)
      return `${where}: ${formatCount(cell.n)} ${plural}, none answered yet${error}`;
    const pending = cell.pending
      ? ` · ${formatCount(cell.pending)} not answered yet`
      : "";
    return `${where}: ${formatCount(matchesOf(cell))} of ${formatCount(cell.answered)} ${quoted(answer)} (${formatShare(matchesOf(cell) / cell.answered)}) vs ${formatShare(overallShare)} overall${pending}${error}`;
  };

  return (
    <Box ref={box}>
      {cellW > 0 && (
        <CellGrid
          rowCount={rows.length}
          colCount={columns.length}
          cellW={cellW}
          cellH={cellH}
          gap={GRID_GAP}
          radius={3}
          labelW={LABEL_COL}
          rightW={TOTAL_COL}
          topH={16}
          width={width}
          paint={paint}
          describe={describe}
          onPick={(r, c) => {
            const cell = cells.get(`${rows[r].key}|${columns[c].key}`);
            if (cell)
              onSelect?.(cell.filter, `${rows[r].title} · ${columns[c].title}`);
          }}
          rowLabel={(r) => rows[r].label}
          colLabel={(c) => (ticked(c) ? columns[c].label : "")}
          colLabelAt="top"
          rightText={(r) => {
            const t = stats.rowTotals[r];
            return t.answered
              ? `${formatShare(t.matches / t.answered)} of ${formatCount(t.answered)}`
              : "–";
          }}
          footer={footer}
          ariaLabel={`Share of ${quoted(answer)} by ${byWhat}, ${formatShare(overallShare)} overall`}
        />
      )}
      <ScaleLegend
        ramp={ramp}
        peak={peak}
        answer={quoted(answer)}
        extra={
          <>
            <Box
              aria-hidden
              sx={{
                width: 14,
                height: 12,
                ml: 1.5,
                borderRadius: "2px",
                background: `${hatch(ramp[3])}, ${ramp[0]}`,
              }}
            />
            <Typography variant="caption" color="text.disabled">
              Fewer than {SMALL_N} answered
            </Typography>
            {[...cells.values()].some(dotted) && (
              <>
                <Box
                  aria-hidden
                  sx={{
                    width: 6,
                    height: 6,
                    ml: 1.5,
                    borderRadius: "50%",
                    bgcolor: "error.main",
                  }}
                />
                <Typography variant="caption" color="text.disabled">
                  {fewErrors
                    ? "Trace had an error"
                    : `Trace errors in ${pct(stats.errorFloor)} or more of the cell`}
                </Typography>
              </>
            )}
            {(running || stats.anyPending) && (
              <>
                <Box
                  aria-hidden
                  sx={{
                    width: 14,
                    height: 12,
                    ml: 1.5,
                    borderRadius: "2px",
                    outline: `1px dashed ${alpha(theme.palette.text.disabled, 0.6)}`,
                    outlineOffset: -1,
                  }}
                />
                <Typography variant="caption" color="text.disabled">
                  Still being answered
                </Typography>
              </>
            )}
          </>
        }
      />
    </Box>
  );
}

TimeGrid.propTypes = {
  grid: PropTypes.object.isRequired,
  question: PropTypes.object.isRequired,
  answer: PropTypes.string.isRequired,
  onSelect: PropTypes.func,
  unit: PropTypes.string,
  sample: PropTypes.object,
  running: PropTypes.bool,
};

// One answer's panel in the small multiples.
function Panel({
  grid,
  question,
  label,
  entry,
  panelW,
  showDays,
  onSelect,
  plural,
}) {
  const theme = useTheme();
  const colorOf = useAnswerColor();
  const hue = colorOf(question, label);
  const ramp = answerRamp(hue, theme.palette.mode);
  const { rows, columns, cells, overall } = grid;
  const { top, peak } = gridLowerPeak(grid, label);
  const overallShare = overall.answered
    ? (overall.byLabel.get(label) || 0) / overall.answered
    : 0;
  // Shaded by the share a cell surely has (its 95% lower bound), from the
  // answer's usual share (palest, and anything below it) to the panel's
  // peak; no cell surely above the usual leaves the panel flat.
  const flat = top <= overallShare;
  const levelOf = (share) =>
    flat || share <= overallShare
      ? 0
      : shareLevel(
          (share - overallShare) / (Math.max(peak, top) - overallShare),
        );
  const byModel = grid.pivot === "modelDay";
  const x0 = showDays ? (byModel ? PANEL_MODELS : PANEL_DAYS) : 0;
  const gap = 1;
  const cellW = columns.length
    ? (panelW - x0 - (columns.length - 1) * gap) / columns.length
    : 0;
  const cellH = byModel
    ? 14
    : rows.length <= 10
      ? 12
      : rows.length <= 20
        ? 9
        : 7;
  const paint = useMemo(
    () => (r, c) => {
      const cell = cells.get(`${rows[r].key}|${columns[c].key}`);
      if (!cell) return { fill: alpha(theme.palette.text.disabled, 0.07) };
      if (!cell.answered) return { outline: cell.pending ? "dashed" : null };
      // Too few to say anything: hatched, never shaded.
      if (cell.answered < SMALL_N)
        return {
          fill: alpha(theme.palette.text.disabled, 0.06),
          hatch: theme.palette.text.disabled,
        };
      const k = cell.byLabel.get(label) || 0;
      return { fill: ramp[levelOf(wilsonLower(k, cell.answered))] };
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [cells, rows, columns, label, peak, top, overallShare, theme],
  );
  const describe = (r, c) => {
    const row = rows[r];
    const col = columns[c];
    const cell = cells.get(`${row.key}|${col.key}`);
    const where = `${row.title} · ${col.title}`;
    if (!cell) return `${where}: no ${plural}`;
    if (!cell.answered)
      return `${where}: ${formatCount(cell.n)} ${plural}, none answered yet`;
    const k = cell.byLabel.get(label) || 0;
    return `${where}: ${formatCount(k)} of ${formatCount(cell.answered)} ${quoted(label)} (${formatShare(k / cell.answered)}) vs ${formatShare(overallShare)} overall`;
  };
  const dayEvery = rows.length > 14 ? 7 : 1;
  return (
    <Box sx={{ minWidth: 0, mb: 1.5 }}>
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.75}
        sx={{ pl: `${x0}px`, mb: 0.5, minWidth: 0 }}
      >
        <Swatch color={hue} size={10} />
        <Typography
          variant="caption"
          noWrap
          title={label}
          sx={{ fontWeight: 600, fontSize: 12, minWidth: 0, flex: 1 }}
        >
          {label}
        </Typography>
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{
            fontSize: 11.5,
            flexShrink: 0,
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {entry
            ? `${formatShare(entry.share)} · ${formatCount(entry.count)}`
            : ""}
        </Typography>
      </Stack>
      {cellW > 0 && (
        <CellGrid
          rowCount={rows.length}
          colCount={columns.length}
          cellW={cellW}
          cellH={cellH}
          gap={gap}
          radius={1.5}
          labelW={x0}
          bottomH={14}
          width={panelW}
          paint={paint}
          describe={describe}
          onPick={(r, c) => {
            const cell = cells.get(`${rows[r].key}|${columns[c].key}`);
            if (!cell || !(cell.byLabel.get(label) > 0)) return;
            onSelect?.(
              {
                config_id: question.configId,
                answer: label,
                ...cell.filter,
              },
              `${quoted(label)} · ${rows[r].title} · ${columns[c].title}`,
            );
          }}
          rowLabel={
            showDays
              ? (r) =>
                  byModel || r % dayEvery === 0 || r === rows.length - 1
                    ? rows[r].date
                      ? dayText(rows[r].date.getTime())
                      : rows[r].label
                    : ""
              : undefined
          }
          labelSize={Math.min(10, cellH + 2)}
          colLabel={(c) =>
            (byModel ? columns[c].tick : columns[c].from % 6 === 0)
              ? columns[c].label
              : ""
          }
          ariaLabel={`${label}: share of each ${byModel ? "model by day" : `${grid.step === 1 ? "hour" : `${grid.step}-hour block`} by ${grid.pivot === "weekdayHour" ? "weekday" : "day"}`}, ${formatShare(overallShare)} usual`}
        />
      )}
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.5}
        sx={{ pl: `${x0}px`, mt: 0.25 }}
      >
        {ramp.map((step, level) => (
          <Box
            key={level}
            aria-hidden
            sx={{ width: 10, height: 6, borderRadius: "1px", bgcolor: step }}
          />
        ))}
        <Typography
          variant="caption"
          color="text.disabled"
          sx={{ fontSize: 10.5 }}
        >
          {flat
            ? `near usual (${pct(overallShare)}) · no cell clearly more`
            : `near usual (${pct(overallShare)}) → clearly more`}
        </Typography>
      </Stack>
    </Box>
  );
}

Panel.propTypes = {
  grid: PropTypes.object.isRequired,
  question: PropTypes.object.isRequired,
  label: PropTypes.string.isRequired,
  entry: PropTypes.object,
  panelW: PropTypes.number.isRequired,
  showDays: PropTypes.bool,
  onSelect: PropTypes.func,
  plural: PropTypes.string,
};

/**
 * One small Day × Hour grid per answer, side by side on the same axes, each
 * on its own hue: where each answer concentrates, at a glance. Each panel is a
 * few paths, however many cells it has.
 */
function SmallMultiples({ grid, question, summary, onSelect, unit, sample }) {
  const box = useRef(null);
  const width = useWidth(box);
  const plural = rowsNoun(unit, sample, 2);
  const byAnswer = new Map(
    legendAnswers(summary, question).map((entry) => [entry.answer, entry]),
  );
  const perRow = width >= 940 ? 3 : width >= 600 ? 2 : 1;
  const panelW = width ? (width - (perRow - 1) * PANEL_GAP) / perRow : 0;
  return (
    <Box ref={box}>
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: `repeat(${perRow}, minmax(0, 1fr))`,
          columnGap: `${PANEL_GAP}px`,
          rowGap: 2,
        }}
      >
        {panelW > 0 &&
          question.labels.map((label, index) => (
            <Panel
              key={label}
              grid={grid}
              question={question}
              label={label}
              entry={byAnswer.get(label)}
              panelW={panelW}
              showDays={index % perRow === 0}
              onSelect={onSelect}
              plural={plural}
            />
          ))}
      </Box>
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.75}
        sx={{ mt: 0.25 }}
      >
        <Box
          aria-hidden
          sx={{
            width: 14,
            height: 10,
            borderRadius: "2px",
            background: (t) => `${hatch(t.palette.text.disabled)}`,
          }}
        />
        <Typography variant="caption" color="text.disabled">
          Fewer than {SMALL_N} answered: too few to shade
        </Typography>
      </Stack>
    </Box>
  );
}

SmallMultiples.propTypes = {
  grid: PropTypes.object.isRequired,
  question: PropTypes.object.isRequired,
  summary: PropTypes.object,
  onSelect: PropTypes.func,
  unit: PropTypes.string,
  sample: PropTypes.object,
};

/**
 * The answers as grids. Day × Hour by default (GitHub-contribution style):
 * for a category question with no answer in focus, one small grid per answer
 * on its own hue; otherwise one grid shaded by the answer in focus. Also
 * Weekday × Hour, Model × Day, and answers by user, model or turn count, with
 * the cells that stand out outlined. (One question against another is its
 * own card, AskCrossTab.)
 */
export default function AskHeatmap({
  summary: given,
  records,
  questions: questionList,
  focus,
  onFocus,
  onSelect,
  running,
  window: viewWindow,
  unit: unitProp,
  onReadMore,
  readingMore,
}) {
  const { summary, questions, sample, unit } = useRunData({
    summary: given,
    records,
    questions: questionList,
    window: viewWindow,
    unit: unitProp,
    running,
    defer: true,
  });
  const [pivotKey, setPivotKey] = useState("dayHour");
  // Redrawn for a new focus in a background render (see AskPatterns).
  const shownFocus = useDeferredValue(focus);
  const { question, answer, explicit } = resolveFocus(
    shownFocus,
    questions,
    records || [],
  );
  const window = useMemo(
    () =>
      viewWindow ||
      (summary?.window ? [summary.window.from, summary.window.to] : undefined),
    [viewWindow, summary?.window],
  );
  const choices = PIVOTS.filter((p) => {
    if (p.value === "user")
      return unit !== "users" && groupEntries(summary, "user").length > 0;
    if (p.value === "model")
      return groupEntries(summary, "model").some((e) => e.key !== "__none__");
    if (p.value === "turns") return groupEntries(summary, "turns").length > 0;
    if (p.value === "modelDay")
      return groupEntries(summary, "model_day").some(
        (e) => !String(e.key).startsWith("__none__|"),
      );
    return true;
  });
  const pivot = choices.some((p) => p.value === pivotKey)
    ? pivotKey
    : "dayHour";
  const kind = PIVOTS.find((p) => p.value === pivot)?.kind;
  const category = question && !isYesNo(question);
  const each = kind === "time" && category && !explicit;
  const shown = each ? null : explicit ? answer : category ? null : "No";
  const grid = useMemo(
    () =>
      kind === "time" && question
        ? pivot === "modelDay"
          ? buildModelDayGrid({ summary, question, window })
          : buildTimeGrid({
              summary,
              question,
              pivot,
              window,
              resolution: each ? "dense" : undefined,
            })
        : null,
    [kind, summary, question, pivot, window, each],
  );
  const matrix = useMemo(
    () =>
      kind === "matrix" && question
        ? buildMatrix({
            summary,
            questions,
            question,
            pivot,
            maxRows: MAX_USERS,
          })
        : null,
    [kind, summary, questions, question, pivot],
  );
  if (!question) return null;
  const plural = (UNITS[unit] || UNITS.sessions).plural;
  if (
    pivot === "dayHour" &&
    !grid.rows.some((row) =>
      grid.columns.some(
        (column) =>
          (grid.cells.get(`${row.key}|${column.key}`)?.answered || 0) >=
          SMALL_N,
      ),
    )
  )
    return (
      <VizCard title="Heatmap" label="Heatmap of answers">
        <Stack spacing={1} alignItems="flex-start">
          <Typography variant="subtitle2">
            Not enough answers for time patterns
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Each time cell needs at least {SMALL_N} answers. Use the timeline
            and answer split for now.
            {!onReadMore &&
              !running &&
              " Start a new question with a larger scope to read more."}
          </Typography>
          <Button
            size="small"
            variant="outlined"
            onClick={onReadMore}
            disabled={!onReadMore || running || readingMore}
          >
            Read more
          </Button>
        </Stack>
      </VizCard>
    );
  const run = runTally(summary, question);
  const overallShare =
    shown && run.answered ? (run.byLabel.get(shown) || 0) / run.answered : null;

  let caption;
  if (kind === "time") {
    const blocks = grid?.step > 1 ? ` · ${grid.step}-hour blocks` : "";
    caption = each
      ? `One grid per answer · darker where the answer is clearly more common than usual${blocks} · click a cell for its ${plural}`
      : `Share of ${quoted(shown)} per cell${overallShare !== null ? `, ${formatShare(overallShare)} overall` : ""}${blocks} · click a cell for its ${plural}`;
  } else
    caption = `Answers to ${quoted(question.question)} for each ${PIVOTS.find(
      (p) => p.value === pivot,
    )
      ?.label.toLowerCase()
      .replace(/s$/, "")}.`;
  if (sample.isSample)
    caption = `${caption} · counts of the ${formatCount(sample.read)} read`;

  let body;
  if (kind === "time") {
    if (!grid?.rows.length)
      body = (
        <Typography
          variant="body2"
          color="text.secondary"
          sx={{ fontSize: 13 }}
        >
          No {plural} to place yet.
        </Typography>
      );
    else if (each)
      body = (
        <SmallMultiples
          grid={grid}
          question={question}
          summary={summary}
          onSelect={onSelect}
          unit={unit}
          sample={sample}
        />
      );
    else
      body = (
        <TimeGrid
          grid={grid}
          question={question}
          answer={shown}
          onSelect={onSelect}
          unit={unit}
          sample={sample}
          running={running}
        />
      );
  } else if (!matrix?.rows.length || !matrix.rows.some((r) => r.answered))
    body = (
      <Typography variant="body2" color="text.secondary" sx={{ fontSize: 13 }}>
        No {plural} answered yet.
      </Typography>
    );
  else
    body = (
      <AnswerMatrix
        matrix={{ ...matrix, group: true }}
        question={question}
        colorQuestion={question}
        onSelect={onSelect}
        unit={unit}
        sample={sample}
      />
    );

  return (
    <VizCard
      title="Heatmap"
      label="Heatmap of answers"
      caption={caption}
      action={
        <>
          <TextField
            select
            size="small"
            label="Rows"
            value={pivot}
            onChange={(event) => setPivotKey(event.target.value)}
            sx={{ width: 176 }}
          >
            {choices.map((choice) => (
              <MenuItem key={choice.value} value={choice.value}>
                {choice.label}
              </MenuItem>
            ))}
          </TextField>
          {kind === "time" && (
            <TextField
              select
              size="small"
              label="Colour"
              value={shown || EACH}
              onChange={(event) =>
                onFocus?.({
                  configId: question.configId,
                  answer:
                    event.target.value === EACH ? null : event.target.value,
                })
              }
              sx={{ width: 168 }}
              SelectProps={{
                renderValue: (value) =>
                  value === EACH ? "Each answer" : `% ${quoted(value)}`,
              }}
            >
              {category && <MenuItem value={EACH}>Each answer</MenuItem>}
              {question.labels.map((label) => (
                <MenuItem key={label} value={label}>
                  % {quoted(label)}
                </MenuItem>
              ))}
            </TextField>
          )}
        </>
      }
    >
      {body}
    </VizCard>
  );
}

AskHeatmap.propTypes = {
  summary: PropTypes.object,
  records: PropTypes.array,
  questions: PropTypes.array,
  focus: PropTypes.shape({
    configId: PropTypes.string,
    answer: PropTypes.string,
  }),
  onFocus: PropTypes.func,
  onSelect: PropTypes.func,
  onOpen: PropTypes.func,
  running: PropTypes.bool,
  unit: PropTypes.oneOf(["traces", "spans", "sessions", "users"]),
  window: PropTypes.array,
  onReadMore: PropTypes.func,
  readingMore: PropTypes.bool,
};
