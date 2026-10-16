import React, { memo, useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  MenuItem,
  Paper,
  Popper,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { alpha, keyframes, useTheme } from "@mui/material/styles";
import { UNITS } from "src/sections/projects/observePopulation";
import { answerText } from "../../instantEvalAnswers";
import {
  answerCounts,
  formatCount,
  formatShare,
  questionLabels,
  resolveFocus,
} from "./askAnswers";
import { hourLabel } from "../../results/heatmap";
import {
  BADGE_H,
  DAY,
  binText,
  buildTimeline,
  columnBudget,
  dayText,
  rangeText,
  sampleStack,
  selectColumns,
  squareLayout,
  stackCap,
} from "./timelineBins";
import {
  Swatch,
  VizCard,
  deepColor,
  quoted,
  useAnswerColor,
  useWidth,
} from "./vizParts";

const TOP = 8;
const AXIS = 26;
// With hour ticks the axis has two lines: hours, then days.
const AXIS_WITH_HOURS = 40;
const RUG = 3;
// Hour ticks show once a day is this wide: every 6 hours, then every 3.
const HOURS_DAY_PX = 120;
const HOURS_EVERY_3_PX = 300;
// Rough width of 11px axis text, to keep labels on the chart.
const CHAR_PX = 6.2;
const DRAG_PX = 4;

const pulse = keyframes`
  0%, 100% { opacity: 1; }
  50% { opacity: 0.35; }
`;
const pop = keyframes`
  0% { transform: scale(1.3); }
  100% { transform: scale(1); }
`;

const timeOf = (iso) =>
  new Date(iso).toLocaleString("en-US", {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });

// "21 sessions · 14 No · 7 Yes", for a column cut short.
function columnSummary(column, unit) {
  const { noun, plural } = UNITS[unit] || UNITS.sessions;
  const answers = new Map();
  const other = { failed: 0, skipped: 0, reading: 0, waiting: 0 };
  column.items.forEach((item) => {
    if (item.state === "answered")
      answers.set(item.answer, (answers.get(item.answer) || 0) + 1);
    else other[item.state] += 1;
  });
  const n = column.items.length;
  return [
    `${formatCount(n)} ${n === 1 ? noun : plural}`,
    ...[...answers.entries()]
      .sort((a, b) => b[1] - a[1])
      .map(([answer, count]) => `${formatCount(count)} ${answerText(answer)}`),
    other.failed && `${formatCount(other.failed)} failed`,
    other.skipped && `${formatCount(other.skipped)} skipped`,
    other.reading && `${formatCount(other.reading)} being read`,
    other.waiting && `${formatCount(other.waiting)} not yet`,
  ]
    .filter(Boolean)
    .join(" · ");
}

// The answer most rows of a column gave, if any.
function dominantAnswer(column) {
  const counts = new Map();
  column.items.forEach((item) => {
    if (item.state === "answered")
      counts.set(item.answer, (counts.get(item.answer) || 0) + 1);
  });
  return [...counts.entries()].sort((a, b) => b[1] - a[1])[0]?.[0] ?? null;
}

// The squares, drawn once per data change; hover and drag draw on top.
const Squares = memo(function Squares({
  columns,
  geometry,
  fillOf,
  dimmed,
  popped,
}) {
  const { xOf, yOf, w, h } = geometry;
  return columns.map((column) =>
    column.items.map((item, idx) => {
      const style = fillOf(item);
      const classes = [
        item.state === "reading" ? "reading" : "",
        popped.has(item.id) ? "pop" : "",
      ]
        .filter(Boolean)
        .join(" ");
      return (
        <rect
          key={item.id}
          className={classes || undefined}
          x={xOf(column.index)}
          y={yOf(idx)}
          width={w}
          height={h}
          rx={Math.min(2, h / 4)}
          fill={style.fill}
          stroke={style.stroke || "none"}
          strokeWidth={style.stroke ? 1.25 : 0}
          opacity={dimmed(item) ? 0.18 : 1}
        />
      );
    }),
  );
});

Squares.propTypes = {
  columns: PropTypes.arrayOf(PropTypes.object).isRequired,
  geometry: PropTypes.shape({
    xOf: PropTypes.func.isRequired,
    yOf: PropTypes.func.isRequired,
    w: PropTypes.number.isRequired,
    h: PropTypes.number.isRequired,
  }).isRequired,
  fillOf: PropTypes.func.isRequired,
  dimmed: PropTypes.func.isRequired,
  popped: PropTypes.instanceOf(Set).isRequired,
};

/**
 * Every row of the run on a time axis, one square per row, stacked in time
 * columns by answer: Jev's progress while it reads, and where the answers
 * cluster once it is done. Hover a square for the row, click it to open the
 * row, drag across columns to select a time range, click the legend to focus
 * an answer.
 */
export default function AskTimeline({
  records,
  questions,
  focus,
  onFocus,
  onSelect,
  onOpen,
  running,
  window,
  unit: unitProp,
}) {
  const theme = useTheme();
  const colorOf = useAnswerColor();
  const box = useRef(null);
  const svg = useRef(null);
  const width = useWidth(box);
  const {
    question,
    explicit,
    answer: focusAnswer,
  } = resolveFocus(focus, questions, records);
  const configId = question?.configId;
  const maxColumns = columnBudget(records.length, width);
  const timeline = useMemo(
    () => buildTimeline({ records, configId, question, window, maxColumns }),
    [records, configId, question, window, maxColumns],
  );
  const counts = useMemo(
    () => answerCounts(records, question),
    [records, question],
  );
  const [hover, setHover] = useState(null); // {col, idx}
  const [drag, setDrag] = useState(null); // {from, to, x, moved}
  const [picked, setPicked] = useState(null); // {from, to}
  const [cursor, setCursor] = useState(null); // keyboard column
  const unit = unitProp || records[0]?.unit || "sessions";
  const noun = (UNITS[unit] || UNITS.sessions).noun;

  // Squares whose answer just landed get a short pop.
  const seen = useRef(null);
  const popped = useMemo(() => {
    const changed = new Set();
    if (seen.current)
      timeline.columns.forEach((column) =>
        column.items.forEach((item) => {
          const before = seen.current.get(item.id);
          if (before && before !== "answered" && item.state === "answered")
            changed.add(item.id);
        }),
      );
    return changed;
  }, [timeline]);
  useEffect(() => {
    seen.current = new Map(
      timeline.columns.flatMap((column) =>
        column.items.map((item) => [item.id, item.state]),
      ),
    );
  }, [timeline]);

  // Geometry: columns share the width; squares shrink so the tallest column
  // stays under the chart's height (see squareLayout).
  const columns = timeline.columns;
  const cap = useMemo(() => stackCap(columns), [columns]);
  const layout = squareLayout(width, columns.length, timeline.maxStack, cap);
  const { colW, w, h, rowGap, pitch, plotH } = layout;
  // What each column draws: all its rows, or an even sample of `cap` with the
  // rest counted in a "+N" badge.
  const stacks = useMemo(
    () =>
      columns.map((column) => ({
        index: column.index,
        items: sampleStack(column.items, layout.cap),
        hidden: Math.max(0, column.items.length - layout.cap),
      })),
    [columns, layout.cap],
  );
  const stackTop = (col) => baseY - (stacks[col]?.items.length || 0) * pitch;
  const baseY = TOP + plotH;
  const dayW = colW * (DAY / timeline.binMs);
  const hourStep = dayW >= HOURS_EVERY_3_PX ? 3 : dayW >= HOURS_DAY_PX ? 6 : 0;
  const height = baseY + (hourStep ? AXIS_WITH_HOURS : AXIS);
  const geometry = useMemo(
    () => ({
      w,
      h,
      xOf: (col) => col * colW + (colW - w) / 2,
      yOf: (idx) => baseY - (idx + 1) * pitch + rowGap,
    }),
    [w, h, colW, baseY, pitch, rowGap],
  );

  const labels = questionLabels(question);
  const fillOf = useMemo(() => {
    const cache = new Map(
      labels.map((label) => [label, colorOf(question, label)]),
    );
    return (item) => {
      if (item.state === "answered") {
        const color = cache.get(item.answer) || colorOf(question, item.answer);
        return { fill: item.unsure ? alpha(color, 0.42) : color };
      }
      if (item.state === "failed")
        return {
          fill: theme.palette.background.paper,
          stroke: theme.palette.error.main,
        };
      if (item.state === "skipped") return { fill: theme.palette.grey[300] };
      if (item.state === "reading")
        return { fill: alpha(theme.palette.primary.main, 0.3) };
      return {
        fill: theme.palette.background.paper,
        stroke: alpha(theme.palette.text.disabled, 0.55),
      };
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [question, theme, labels.join("|")]);
  const dimmed = useMemo(
    () => (item) => explicit && item.answer !== focusAnswer,
    [explicit, focusAnswer],
  );

  const locate = (event) => {
    const rect = svg.current?.getBoundingClientRect();
    if (!rect || !colW) return null;
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    const col = Math.min(columns.length - 1, Math.max(0, Math.floor(x / colW)));
    // A cut column answers as a whole, badge included.
    if (stacks[col]?.hidden && y <= baseY && y >= stackTop(col) - BADGE_H)
      return { x, col, idx: null, item: null, column: true };
    const idx = Math.floor((baseY - y) / pitch);
    const item = idx >= 0 && y <= baseY ? stacks[col]?.items[idx] : null;
    return { x, col, idx, item };
  };

  useEffect(() => {
    if (!drag) return undefined;
    // Finish a drag that ends outside the chart.
    const end = () => setDrag(null);
    globalThis.addEventListener?.("mouseup", end);
    return () => globalThis.removeEventListener?.("mouseup", end);
  }, [drag]);

  const select = (from, to) => {
    const { ids, label } = selectColumns(columns, from, to);
    if (!ids.length) return;
    setPicked({ from: Math.min(from, to), to: Math.max(from, to) });
    onSelect?.(ids, label);
  };

  const hoverColumn = hover?.column ? columns[hover.col] : null;
  const hoverItem =
    hover && !hover.column ? stacks[hover.col]?.items[hover.idx] : null;
  const anchor = useMemo(() => {
    if ((!hoverItem && !hoverColumn) || !svg.current) return null;
    const rect = svg.current.getBoundingClientRect();
    const box = hoverColumn
      ? {
          x: rect.left + hover.col * colW,
          y: rect.top + stackTop(hover.col) - BADGE_H,
          width: colW,
          height: BADGE_H,
        }
      : {
          x: rect.left + geometry.xOf(hover.col),
          y: rect.top + geometry.yOf(hover.idx),
          width: w,
          height: h,
        };
    return {
      getBoundingClientRect: () => ({
        top: box.y,
        left: box.x,
        bottom: box.y + box.height,
        right: box.x + box.width,
        width: box.width,
        height: box.height,
        x: box.x,
        y: box.y,
        toJSON: () => null,
      }),
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hoverItem, hoverColumn, hover, geometry, w, h, colW]);

  const rangeLabel =
    columns.length > 0
      ? rangeText(columns[0].start, columns[columns.length - 1].end)
      : "";
  // Faint hour ticks under each day once the days are wide enough, so a
  // stretch like 2–5 PM can be read off the chart.
  const hourTicks = [];
  if (hourStep && columns.length) {
    const startMs = columns[0].start;
    const endMs = columns[columns.length - 1].end;
    const first = new Date(startMs);
    for (
      let day = new Date(
        first.getFullYear(),
        first.getMonth(),
        first.getDate(),
      );
      day.getTime() < endMs;
      day = new Date(day.getFullYear(), day.getMonth(), day.getDate() + 1)
    ) {
      for (let hour = hourStep; hour < 24; hour += hourStep) {
        const ms = new Date(
          day.getFullYear(),
          day.getMonth(),
          day.getDate(),
          hour,
        ).getTime();
        const x = ((ms - startMs) / timeline.binMs) * colW;
        if (x > 8 && x < width - 8) hourTicks.push({ ms, x, hour });
      }
    }
  }
  const dayLabelY = baseY + (hourStep ? 32 : 18);
  // Day names under their midnight; the last one turns to end at the right
  // edge rather than run off it, and goes when there is no room for it.
  const dayLabels = [];
  timeline.dayTicks.forEach((tick) => {
    const previous = dayLabels[dayLabels.length - 1];
    // "Thu, Oct 15", or "Oct 15" when the full name has no room.
    [true, false].some((withWeekday) => {
      const text = dayText(tick.ms, withWeekday);
      const textW = text.length * CHAR_PX;
      let x = tick.at * colW + 4;
      let anchor = "start";
      if (x + textW > width) {
        x = width - 1;
        anchor = "end";
      }
      const left = anchor === "end" ? x - textW : x;
      if (previous && left < previous.right + 8) return false;
      dayLabels.push({ ms: tick.ms, text, x, anchor, right: left + textW });
      return true;
    });
  });
  const band = drag?.moved
    ? { from: Math.min(drag.from, drag.to), to: Math.max(drag.from, drag.to) }
    : picked;

  const legendItems = [
    ...counts.labels.map((entry) => ({
      key: entry.answer,
      answer: entry.answer,
      label: entry.answer,
      count: entry.count,
      color: colorOf(question, entry.answer),
      focusable: true,
    })),
    counts.failed > 0 && {
      key: "failed",
      label: "Failed",
      count: counts.failed,
      color: theme.palette.error.main,
      hollow: true,
    },
    counts.skipped > 0 && {
      key: "skipped",
      label: "Skipped",
      count: counts.skipped,
      color: theme.palette.grey[300],
    },
    (running || counts.reading > 0) && {
      key: "reading",
      label: "Reading",
      count: counts.reading,
      color: alpha(theme.palette.primary.main, 0.3),
    },
    (running || counts.waiting > 0) && {
      key: "waiting",
      label: "Not yet",
      count: counts.waiting,
      color: alpha(theme.palette.text.disabled, 0.55),
      hollow: true,
    },
  ].filter(Boolean);
  const errors = columns.reduce((sum, column) => sum + column.errors, 0);
  const errorRows = errors > 0 && {
    key: "errors",
    label: "Run hit an error",
    count: errors,
    color: theme.palette.error.main,
    swatch: { width: 12, height: RUG, borderRadius: "1px" },
  };
  const unsure = counts.labels.reduce((sum, entry) => sum + entry.unsure, 0);

  return (
    <VizCard
      title="Timeline"
      label="Timeline of answers"
      caption={
        columns.length
          ? `One square per ${noun} · ${binText(timeline.binMs)} · ${rangeLabel}.${layout.capped ? ` Columns stop at ${layout.cap} squares, the rest shown as +N.` : ""} Drag across the chart to select a time range.`
          : `No ${UNITS[unit]?.plural || "rows"} to show yet.`
      }
      action={
        questions.length > 1 && (
          <TextField
            select
            size="small"
            label="Colour by"
            value={configId || ""}
            onChange={(event) =>
              onFocus?.({ configId: event.target.value, answer: null })
            }
            sx={{ width: 260 }}
            SelectProps={{
              renderValue: (value) =>
                questions.find((q) => q.configId === value)?.question || "",
            }}
          >
            {questions.map((q) => (
              <MenuItem
                key={q.configId}
                value={q.configId}
                sx={{ whiteSpace: "normal", maxWidth: 360 }}
              >
                {q.question}
              </MenuItem>
            ))}
          </TextField>
        )
      }
    >
      <Box ref={box} sx={{ position: "relative", height }}>
        {width > 0 && columns.length > 0 && (
          <Box
            component="svg"
            ref={svg}
            width={width}
            height={height}
            role="img"
            tabIndex={0}
            aria-label={`Timeline of ${formatCount(records.length)} ${UNITS[unit]?.plural || "rows"} ${rangeLabel}: ${counts.labels
              .map((entry) => `${entry.count} ${entry.answer}`)
              .join(
                ", ",
              )}${counts.waiting ? `, ${counts.waiting} not answered yet` : ""}${errors ? `; ${errors} hit an error` : ""}. Use the arrow keys to move between columns and Enter to select one.`}
            onMouseMove={(event) => {
              const at = locate(event);
              if (!at) return;
              if (drag) {
                setDrag(
                  (current) =>
                    current && {
                      ...current,
                      to: at.col,
                      moved:
                        current.moved || Math.abs(at.x - current.x) > DRAG_PX,
                    },
                );
                setHover(null);
                return;
              }
              setHover(
                at.column
                  ? { col: at.col, column: true }
                  : at.item
                    ? { col: at.col, idx: at.idx }
                    : null,
              );
            }}
            onMouseLeave={() => setHover(null)}
            onMouseDown={(event) => {
              if (event.button !== 0) return;
              const at = locate(event);
              if (!at) return;
              event.preventDefault();
              setDrag({ from: at.col, to: at.col, x: at.x, moved: false });
            }}
            onMouseUp={(event) => {
              const at = locate(event);
              const current = drag;
              setDrag(null);
              if (!at || !current) return;
              if (current.moved) select(current.from, at.col);
              else if (at.column) select(at.col, at.col);
              else if (at.item) onOpen?.(at.item.id);
              else setPicked(null);
            }}
            onKeyDown={(event) => {
              const last = columns.length - 1;
              if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
                event.preventDefault();
                const step = event.key === "ArrowRight" ? 1 : -1;
                setCursor((value) =>
                  Math.min(
                    last,
                    Math.max(0, (value ?? (step > 0 ? -1 : last + 1)) + step),
                  ),
                );
              } else if (
                (event.key === "Enter" || event.key === " ") &&
                cursor !== null
              ) {
                event.preventDefault();
                select(cursor, cursor);
              } else if (event.key === "Escape") {
                setPicked(null);
                setCursor(null);
              }
            }}
            onBlur={() => setCursor(null)}
            sx={{
              display: "block",
              cursor: drag?.moved ? "col-resize" : "crosshair",
              outline: "none",
              userSelect: "none",
              "& rect.reading": {
                animation: `${pulse} 1.4s ease-in-out infinite`,
              },
              "& rect.pop": {
                transformBox: "fill-box",
                transformOrigin: "center",
                animation: `${pop} 360ms ease-out`,
              },
              "@media (prefers-reduced-motion: reduce)": {
                "& rect.reading, & rect.pop": { animation: "none" },
              },
              "&:focus-visible": {
                outline: `2px solid ${theme.palette.primary.main}`,
                outlineOffset: 2,
                borderRadius: "4px",
              },
            }}
          >
            {band && (
              <rect
                x={band.from * colW}
                y={TOP - 4}
                width={(band.to - band.from + 1) * colW}
                height={plotH + 4}
                fill={alpha(theme.palette.primary.main, 0.08)}
                stroke={alpha(theme.palette.primary.main, 0.5)}
                strokeWidth={1}
                rx={3}
              />
            )}
            {timeline.dayTicks.map((tick) => (
              <line
                key={tick.ms}
                x1={tick.at * colW}
                x2={tick.at * colW}
                y1={TOP - 2}
                y2={baseY + (hourStep ? 22 : 6)}
                stroke={theme.palette.divider}
                strokeWidth={1}
              />
            ))}
            <line
              x1={0}
              x2={width}
              y1={baseY + 0.5}
              y2={baseY + 0.5}
              stroke={theme.palette.divider}
              strokeWidth={1}
            />
            {/* Under the baseline, a mark for each column with rows whose run
                hit an error. */}
            {columns.map(
              (column) =>
                column.errors > 0 && (
                  <rect
                    key={`error-${column.start}`}
                    x={geometry.xOf(column.index)}
                    y={baseY + 2}
                    width={w}
                    height={RUG}
                    rx={1}
                    fill={theme.palette.error.main}
                  >
                    <title>
                      {`${formatCount(column.errors)} hit an error`}
                    </title>
                  </rect>
                ),
            )}
            <Squares
              columns={stacks}
              geometry={geometry}
              fillOf={fillOf}
              dimmed={dimmed}
              popped={popped}
            />
            {stacks.map((stack) => {
              if (!stack.hidden) return null;
              const answer = dominantAnswer(columns[stack.index]);
              const text = `+${formatCount(stack.hidden)}`;
              const badgeW = Math.max(w + 4, text.length * 6 + 8);
              const cx = stack.index * colW + colW / 2;
              const y = stackTop(stack.index) - BADGE_H + 2;
              return (
                <g key={`more-${stack.index}`} pointerEvents="none">
                  <rect
                    x={cx - badgeW / 2}
                    y={y}
                    width={badgeW}
                    height={BADGE_H - 3}
                    rx={(BADGE_H - 3) / 2}
                    fill={
                      answer
                        ? deepColor(colorOf(question, answer))
                        : theme.palette.text.secondary
                    }
                  />
                  <text
                    x={cx}
                    y={y + 9.5}
                    textAnchor="middle"
                    fill={theme.palette.common.white}
                    fontSize={9.5}
                    fontWeight={700}
                    fontFamily={theme.typography.fontFamily}
                  >
                    {text}
                  </text>
                </g>
              );
            })}
            {hoverColumn && (
              <rect
                x={hover.col * colW + 0.75}
                y={stackTop(hover.col) - BADGE_H}
                width={colW - 1.5}
                height={baseY - stackTop(hover.col) + BADGE_H}
                rx={3}
                fill="none"
                stroke={theme.palette.text.primary}
                strokeWidth={1.5}
                pointerEvents="none"
              />
            )}
            {hoverItem && (
              <rect
                x={geometry.xOf(hover.col) - 1.5}
                y={geometry.yOf(hover.idx) - 1.5}
                width={w + 3}
                height={h + 3}
                rx={2}
                fill="none"
                stroke={theme.palette.text.primary}
                strokeWidth={1.5}
                pointerEvents="none"
              />
            )}
            {cursor !== null && (
              <rect
                x={cursor * colW}
                y={TOP - 4}
                width={colW}
                height={plotH + 4}
                fill="none"
                stroke={theme.palette.primary.main}
                strokeWidth={1.5}
                rx={2}
              />
            )}
            {dayLabels.map((label) => (
              <text
                key={`label-${label.ms}`}
                x={label.x}
                y={dayLabelY}
                textAnchor={label.anchor}
                fill={theme.palette.text.secondary}
                fontSize={11}
                fontFamily={theme.typography.fontFamily}
              >
                {label.text}
              </text>
            ))}
            {hourTicks.map((tick) => (
              <g key={`hour-${tick.ms}`}>
                <line
                  x1={tick.x}
                  x2={tick.x}
                  y1={baseY + 1}
                  y2={baseY + 5}
                  stroke={theme.palette.divider}
                  strokeWidth={1}
                />
                <text
                  x={tick.x}
                  y={baseY + 16}
                  textAnchor="middle"
                  fill={theme.palette.text.disabled}
                  fontSize={10}
                  fontFamily={theme.typography.fontFamily}
                >
                  {hourLabel(tick.hour)}
                </text>
              </g>
            ))}
          </Box>
        )}
        <Popper
          open={Boolean(anchor) && !drag?.moved}
          anchorEl={anchor}
          placement="top"
          sx={{ zIndex: theme.zIndex.modal + 1, pointerEvents: "none" }}
          modifiers={[{ name: "offset", options: { offset: [0, 8] } }]}
        >
          {hoverItem && (
            <SquareCard
              item={hoverItem}
              question={question}
              colorOf={colorOf}
            />
          )}
          {hoverColumn && (
            <Paper
              elevation={0}
              sx={{
                maxWidth: 300,
                p: 1.5,
                border: "1px solid",
                borderColor: "divider",
                borderRadius: 1,
                boxShadow: (t) => t.customShadows?.dropdown,
              }}
            >
              <Typography variant="caption" color="text.secondary">
                {rangeText(hoverColumn.start, hoverColumn.end)}
              </Typography>
              <Typography variant="body2" sx={{ fontSize: 13, mt: 0.25 }}>
                {columnSummary(hoverColumn, unit)}
              </Typography>
              <Typography
                variant="caption"
                color="text.disabled"
                component="p"
                sx={{ m: 0, mt: 0.75 }}
              >
                Click to select them
              </Typography>
            </Paper>
          )}
        </Popper>
      </Box>

      <Stack
        direction="row"
        alignItems="center"
        useFlexGap
        flexWrap="wrap"
        columnGap={2}
        rowGap={0.5}
        sx={{ mt: 1.25 }}
      >
        {[...legendItems, errorRows].filter(Boolean).map((item) => {
          const active = explicit && focusAnswer === item.answer;
          const faded = explicit && !active;
          const content = (
            <>
              <Swatch
                color={item.color}
                hollow={item.hollow}
                sx={item.swatch}
              />
              <Typography
                variant="caption"
                sx={{ color: "text.primary", fontSize: 12 }}
              >
                {item.label}
              </Typography>
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ fontVariantNumeric: "tabular-nums", fontSize: 12 }}
              >
                {formatCount(item.count)}
              </Typography>
            </>
          );
          return item.focusable ? (
            <Stack
              key={item.key}
              component="button"
              type="button"
              direction="row"
              spacing={0.75}
              alignItems="center"
              aria-pressed={active}
              onClick={() =>
                onFocus?.({ configId, answer: active ? null : item.answer })
              }
              sx={{
                px: 0.75,
                py: 0.25,
                ml: -0.75,
                border: "1px solid",
                borderColor: active ? "text.primary" : "transparent",
                borderRadius: 1,
                bgcolor: "transparent",
                font: "inherit",
                cursor: "pointer",
                opacity: faded ? 0.5 : 1,
                "&:hover": { bgcolor: "action.hover" },
                "&:focus-visible": {
                  outline: `2px solid ${theme.palette.primary.main}`,
                },
              }}
            >
              {content}
            </Stack>
          ) : (
            <Stack
              key={item.key}
              direction="row"
              spacing={0.75}
              alignItems="center"
            >
              {content}
            </Stack>
          );
        })}
        {unsure > 0 && (
          <Stack direction="row" spacing={0.75} alignItems="center">
            <Swatch color={alpha(colorOf(question, labels[0]), 0.42)} />
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ fontSize: 12 }}
            >
              Lighter: Jev unsure ({formatCount(unsure)})
            </Typography>
          </Stack>
        )}
        <Typography
          variant="caption"
          color="text.disabled"
          sx={{ ml: "auto", fontSize: 12 }}
        >
          {explicit
            ? `Showing ${quoted(focusAnswer)}. Click it again to show all.`
            : "Click an answer to focus it."}
        </Typography>
      </Stack>
    </VizCard>
  );
}

function SquareCard({ item, question, colorOf }) {
  const record = item.record;
  const entry = record.answers?.[question?.configId] || {};
  const explanation = String(entry.explanation || "").split(/\n|(?<=\.)\s/)[0];
  const status =
    item.state === "answered"
      ? null
      : item.state === "reading"
        ? "Jev is reading it"
        : item.state === "failed"
          ? "Jev could not answer"
          : item.state === "skipped"
            ? "Nothing to read"
            : "Not answered yet";
  return (
    <Paper
      elevation={0}
      sx={{
        width: 300,
        p: 1.5,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        boxShadow: (theme) => theme.customShadows?.dropdown,
      }}
    >
      <Typography
        variant="caption"
        color="text.secondary"
        component="div"
        noWrap
      >
        {[timeOf(record.time), record.user_id].filter(Boolean).join(" · ")}
      </Typography>
      {(record.label || record.excerpt) && (
        <Typography
          variant="body2"
          sx={{
            mt: 0.5,
            fontSize: 13,
            display: "-webkit-box",
            WebkitLineClamp: 2,
            WebkitBoxOrient: "vertical",
            overflow: "hidden",
          }}
        >
          {record.excerpt || record.label}
        </Typography>
      )}
      <Stack direction="row" spacing={0.75} alignItems="center" sx={{ mt: 1 }}>
        {status ? (
          <Typography variant="caption" color="text.secondary">
            {status}
          </Typography>
        ) : (
          <>
            <Swatch color={colorOf(question, item.answer)} />
            <Typography variant="caption" fontWeight={600}>
              {answerText(item.answer)}
            </Typography>
            {Number.isFinite(entry.confidence) && (
              <Typography variant="caption" color="text.secondary">
                · {formatShare(entry.confidence)} confident
                {item.unsure ? " (unsure)" : ""}
              </Typography>
            )}
          </>
        )}
      </Stack>
      {record.facets?.error === true && (
        <Typography
          variant="caption"
          color="error.main"
          component="p"
          sx={{ m: 0, mt: 0.5 }}
        >
          The run hit an error.
        </Typography>
      )}
      {explanation && !status && (
        <Typography
          variant="caption"
          color="text.secondary"
          component="p"
          sx={{ m: 0, mt: 0.5 }}
        >
          {explanation}
        </Typography>
      )}
      <Typography
        variant="caption"
        color="text.disabled"
        component="p"
        sx={{ m: 0, mt: 0.75 }}
      >
        Click to open
      </Typography>
    </Paper>
  );
}

SquareCard.propTypes = {
  item: PropTypes.object.isRequired,
  question: PropTypes.object,
  colorOf: PropTypes.func.isRequired,
};

AskTimeline.propTypes = {
  records: PropTypes.array.isRequired,
  questions: PropTypes.array.isRequired,
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
};
