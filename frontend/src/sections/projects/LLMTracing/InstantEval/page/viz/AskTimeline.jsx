import React, {
  memo,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react";
import PropTypes from "prop-types";
import {
  Box,
  MenuItem,
  Paper,
  Popper,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import { alpha, keyframes, useTheme } from "@mui/material/styles";
import { UNITS } from "src/sections/projects/observePopulation";
import { answerText } from "../../instantEvalAnswers";
import {
  answerCounts,
  formatCount,
  formatShare,
  isYesNo,
  questionLabels,
  resolveFocus,
} from "./askAnswers";
import { hourLabel } from "../../results/heatmap";
import { runTally, tally } from "./askSummary";
import {
  BADGE_H,
  DAY,
  binText,
  buildTimeline,
  columnBudget,
  dayText,
  densityColumns,
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
  legendAnswers,
  quoted,
  rowsNoun,
  useAnswerColor,
  useRunData,
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
// Density columns: the plot's height by count or by share, the volume strip
// above the shares, the y-axis labels' gutter, the widest bar, the gap between
// stacked parts, and the rounding of a bar's top.
const COUNT_H = 168;
const SHARE_H = 150;
const VOLUME_H = 24;
const VOLUME_GAP = 8;
const FAILED = "__failed__";
const GUTTER = 36;
const MAX_BAR = 24;
const GAP = 2;
const RADIUS = 4;

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

const COMPACT = new Intl.NumberFormat("en-US", {
  notation: "compact",
  maximumFractionDigits: 1,
});
const compact = (n) => (n >= 1000 ? COMPACT.format(n) : String(n));

// "21 sessions · 14 No · 7 Yes", for a column of squares cut short.
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

// The answer most rows of a column of squares gave, if any.
function dominantAnswer(column) {
  const counts = new Map();
  column.items.forEach((item) => {
    if (item.state === "answered")
      counts.set(item.answer, (counts.get(item.answer) || 0) + 1);
  });
  return [...counts.entries()].sort((a, b) => b[1] - a[1])[0]?.[0] ?? null;
}

// A bar part: square at the bottom, its top rounded when it is the bar's top.
function barPath(x, y, w, h, r) {
  if (h <= 0 || w <= 0) return "";
  const rr = Math.min(r, w / 2, h);
  if (rr <= 0) return `M${x},${y}h${w}v${h}h${-w}Z`;
  return `M${x},${y + h}V${y + rr}Q${x},${y} ${x + rr},${y}H${x + w - rr}Q${x + w},${y} ${x + w},${y + rr}V${y + h}Z`;
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

// Day names under their midnights, and faint hour ticks once the days are
// wide enough, for columns `colW` wide starting at `x0`.
function axisMarks({ dayTicks, columns, binMs, colW, x0, width }) {
  const dayW = colW * (DAY / binMs);
  const hourStep = dayW >= HOURS_EVERY_3_PX ? 3 : dayW >= HOURS_DAY_PX ? 6 : 0;
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
        const x = x0 + ((ms - startMs) / binMs) * colW;
        if (x > x0 + 8 && x < width - 8) hourTicks.push({ ms, x, hour });
      }
    }
  }
  // "Thu, Oct 15", or "Oct 15" when the full name has no room; the last one
  // turns to end at the right edge rather than run off it.
  const dayLabels = [];
  dayTicks.forEach((tick) => {
    const previous = dayLabels[dayLabels.length - 1];
    [true, false].some((withWeekday) => {
      const text = dayText(tick.ms, withWeekday);
      const textW = text.length * CHAR_PX;
      let x = x0 + tick.at * colW + 4;
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
  return { hourStep, hourTicks, dayLabels };
}

function AxisLayer({ marks, dayTicks, colW, x0, baseY, theme }) {
  const { hourStep, hourTicks, dayLabels } = marks;
  const dayLabelY = baseY + (hourStep ? 32 : 18);
  return (
    <>
      {dayTicks.map((tick) => (
        <line
          key={tick.ms}
          x1={x0 + tick.at * colW}
          x2={x0 + tick.at * colW}
          y1={TOP - 2}
          y2={baseY + (hourStep ? 22 : 6)}
          stroke={theme.palette.divider}
          strokeWidth={1}
        />
      ))}
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
    </>
  );
}

AxisLayer.propTypes = {
  marks: PropTypes.object.isRequired,
  dayTicks: PropTypes.array.isRequired,
  colW: PropTypes.number.isRequired,
  x0: PropTypes.number.isRequired,
  baseY: PropTypes.number.isRequired,
  theme: PropTypes.object.isRequired,
};

// Dragging across columns, clicking one, and the keyboard cursor, shared by
// both kinds of plot. `locate(event)` gives {x, col, ...}; `onClick(at)` runs
// for a click that did not drag.
function useColumnSelection({ columns, onSelect, onClick }) {
  const [drag, setDrag] = useState(null); // {from, to, x, moved}
  const [picked, setPicked] = useState(null); // {from, to}
  const [cursor, setCursor] = useState(null); // keyboard column
  useEffect(() => {
    if (!drag) return undefined;
    // Finish a drag that ends outside the chart.
    const end = () => setDrag(null);
    globalThis.addEventListener?.("mouseup", end);
    return () => globalThis.removeEventListener?.("mouseup", end);
  }, [drag]);
  const select = (from, to) => {
    const { count, filter, label } = selectColumns(columns, from, to);
    if (!count) return;
    setPicked({ from: Math.min(from, to), to: Math.max(from, to) });
    onSelect?.(filter, label);
  };
  const handlers = (locate, setHover) => ({
    onMouseMove: (event) => {
      const at = locate(event);
      if (!at) return;
      if (drag) {
        setDrag(
          (current) =>
            current && {
              ...current,
              to: at.col,
              moved: current.moved || Math.abs(at.x - current.x) > DRAG_PX,
            },
        );
        setHover(null);
        return;
      }
      setHover(at.hover || null);
    },
    onMouseLeave: () => setHover(null),
    onMouseDown: (event) => {
      if (event.button !== 0) return;
      const at = locate(event);
      if (!at) return;
      event.preventDefault();
      setDrag({ from: at.col, to: at.col, x: at.x, moved: false });
    },
    onMouseUp: (event) => {
      const at = locate(event);
      const current = drag;
      setDrag(null);
      if (!at || !current) return;
      if (current.moved) select(current.from, at.col);
      else if (!onClick(at)) setPicked(null);
    },
    onKeyDown: (event) => {
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
    },
    onBlur: () => setCursor(null),
  });
  const band = drag?.moved
    ? { from: Math.min(drag.from, drag.to), to: Math.max(drag.from, drag.to) }
    : picked;
  return { drag, band, cursor, select, handlers };
}

const svgSx = (theme, drag) => ({
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
  "& path.part": { transition: "d 400ms ease" },
  "@media (prefers-reduced-motion: reduce)": {
    "& rect.reading, & rect.pop": { animation: "none" },
    "& path.part": { transition: "none" },
  },
  "&:focus-visible": {
    outline: `2px solid ${theme.palette.primary.main}`,
    outlineOffset: 2,
    borderRadius: "4px",
  },
});

// An anchor for the tooltip, at a box in the SVG's coordinates.
const anchorAt = (svg, box) => {
  if (!svg || !box) return null;
  const rect = svg.getBoundingClientRect();
  const x = rect.left + box.x;
  const y = rect.top + box.y;
  return {
    getBoundingClientRect: () => ({
      top: y,
      left: x,
      bottom: y + box.height,
      right: x + box.width,
      width: box.width,
      height: box.height,
      x,
      y,
      toJSON: () => null,
    }),
  };
};

/**
 * One square per row in time columns, stacked by answer: Jev's progress while
 * it reads, and where the answers cluster once it is done.
 */
function SquaresPlot({
  records,
  question,
  order,
  window,
  width,
  focusAnswer,
  explicit,
  onSelect,
  onOpen,
  unit,
  onModel,
}) {
  const theme = useTheme();
  const colorOf = useAnswerColor();
  const svg = useRef(null);
  const configId = question?.configId;
  const maxColumns = columnBudget(records.length, width);
  // Stacked in the timeline's order; the colours stay the question's.
  const orderKey = order.join("|");
  const stacking = useMemo(
    () => ({ ...question, labels: order }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [question, orderKey],
  );
  const timeline = useMemo(
    () =>
      buildTimeline({
        records,
        configId,
        question: stacking,
        window,
        maxColumns,
      }),
    [records, configId, stacking, window, maxColumns],
  );
  const [hover, setHover] = useState(null); // {col, idx} or {col, column}

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
  const baseY = TOP + plotH;
  const stackTop = (col) => baseY - (stacks[col]?.items.length || 0) * pitch;
  const marks = axisMarks({
    dayTicks: timeline.dayTicks,
    columns,
    binMs: timeline.binMs,
    colW,
    x0: 0,
    width,
  });
  const height = baseY + (marks.hourStep ? AXIS_WITH_HOURS : AXIS);
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
          fill: alpha(theme.palette.error.main, 0.12),
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
      return { x, col, column: true, hover: { col, column: true } };
    const idx = Math.floor((baseY - y) / pitch);
    const item = idx >= 0 && y <= baseY ? stacks[col]?.items[idx] : null;
    return { x, col, item, hover: item ? { col, idx } : null };
  };
  const { drag, band, cursor, select, handlers } = useColumnSelection({
    columns,
    onSelect,
    onClick: (at) => {
      if (at.column) {
        select(at.col, at.col);
        return true;
      }
      if (at.item) {
        onOpen?.(at.item.id);
        return true;
      }
      return false;
    },
  });

  // The caption's facts go up to the card.
  useEffect(() => {
    onModel({
      binMs: timeline.binMs,
      start: columns[0]?.start,
      end: columns[columns.length - 1]?.end,
      capped: layout.capped ? layout.cap : null,
      errors: columns.reduce((sum, column) => sum + column.errors, 0),
    });
  }, [onModel, timeline.binMs, columns, layout.capped, layout.cap]);

  const hoverColumn = hover?.column ? columns[hover.col] : null;
  const hoverItem =
    hover && !hover.column ? stacks[hover.col]?.items[hover.idx] : null;
  const anchor = useMemo(
    () =>
      anchorAt(
        svg.current,
        hoverColumn
          ? {
              x: hover.col * colW,
              y: stackTop(hover.col) - BADGE_H,
              width: colW,
              height: BADGE_H,
            }
          : hoverItem
            ? {
                x: geometry.xOf(hover.col),
                y: geometry.yOf(hover.idx),
                width: w,
                height: h,
              }
            : null,
      ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [hoverItem, hoverColumn, hover, geometry, w, h, colW],
  );
  const counts = useMemo(
    () => answerCounts(records, question),
    [records, question],
  );
  const errors = columns.reduce((sum, column) => sum + column.errors, 0);

  return (
    <Box sx={{ position: "relative", height }}>
      {width > 0 && columns.length > 0 && (
        <Box
          component="svg"
          ref={svg}
          width={width}
          height={height}
          role="img"
          tabIndex={0}
          aria-label={`Timeline of ${formatCount(records.length)} ${UNITS[unit]?.plural || "rows"} ${columns.length ? rangeText(columns[0].start, columns[columns.length - 1].end) : ""}: ${counts.labels
            .map((entry) => `${entry.count} ${entry.answer}`)
            .join(
              ", ",
            )}${counts.waiting ? `, ${counts.waiting} not answered yet` : ""}${errors ? `; ${errors} had a trace error` : ""}. Use the arrow keys to move between columns and Enter to select one.`}
          {...handlers(locate, setHover)}
          sx={svgSx(theme, drag)}
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
          <line
            x1={0}
            x2={width}
            y1={baseY + 0.5}
            y2={baseY + 0.5}
            stroke={theme.palette.divider}
            strokeWidth={1}
          />
          {/* Under the baseline, a mark for each column with rows whose run
              had an error. */}
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
                  <title>{`${formatCount(column.errors)} had a trace error`}</title>
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
          <AxisLayer
            marks={marks}
            dayTicks={timeline.dayTicks}
            colW={colW}
            x0={0}
            baseY={baseY}
            theme={theme}
          />
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
          <SquareCard item={hoverItem} question={question} colorOf={colorOf} />
        )}
        {hoverColumn && (
          <TipCard>
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
          </TipCard>
        )}
      </Popper>
    </Box>
  );
}

SquaresPlot.propTypes = {
  records: PropTypes.array.isRequired,
  question: PropTypes.object.isRequired,
  order: PropTypes.arrayOf(PropTypes.string).isRequired,
  window: PropTypes.array,
  width: PropTypes.number.isRequired,
  focusAnswer: PropTypes.string,
  explicit: PropTypes.bool,
  onSelect: PropTypes.func,
  onOpen: PropTypes.func,
  unit: PropTypes.string,
  onModel: PropTypes.func.isRequired,
};

// Round a count up to a clean axis top: 1, 2, 2.5 or 5 times a power of 10.
function niceTop(value) {
  if (value <= 4) return Math.max(1, Math.ceil(value));
  const power = 10 ** Math.floor(Math.log10(value));
  const step = [1, 2, 2.5, 5, 10].find((s) => s * power >= value);
  return step * power;
}

// Clean count ticks up to `top`: multiples of 1, 2 or 5 times a power of
// 10, about two of them.
function countTicks(top) {
  if (top <= 4) return [top];
  const rough = top / 2;
  const power = 10 ** Math.floor(Math.log10(rough));
  const step = [5, 2, 1].map((s) => s * power).find((s) => s <= rough);
  const ticks = [];
  for (let tick = step; tick <= top + 1e-9; tick += step) ticks.push(tick);
  return ticks;
}

// Three strengths of the error mark, by a column's error rate against the
// highest: drawn as three paths rather than one mark per column.
const ERROR_STEPS = [0.25, 0.55, 1];

/**
 * Each time column as one bar. "Share": the answers as parts of 100% of the
 * column's answered rows (failures hatched on top), with the column's volume
 * as a thin grey strip above, where the rows not answered yet are a hollow
 * outline that fills in. "Count": the same parts stacked on a count axis.
 * Answers stack in `order`, bottom first. Each answer is one path across all
 * columns, so a 100,000-row run draws a few dozen nodes.
 */
function DensityPlot({
  summary,
  question,
  order,
  scale,
  width,
  focusAnswer,
  explicit,
  onSelect,
  unit,
  sample,
  onModel,
}) {
  const theme = useTheme();
  const colorOf = useAnswerColor();
  const svg = useRef(null);
  const hatchId = `failed-${useId().replace(/:/g, "")}`;
  const labels = question.labels;
  const configId = question.configId;
  const model = useMemo(
    () =>
      densityColumns({
        timeline: summary?.timeline,
        tallyOf: (bin) => tally(bin, configId, labels),
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [summary, configId, labels.join("|")],
  );
  const { columns, dayTicks, binMs, maxN } = model;
  const [hover, setHover] = useState(null); // {col}
  const share = scale === "share";
  const plotW = Math.max(0, width - GUTTER);
  const colW = columns.length ? plotW / columns.length : 0;
  const barW = Math.max(
    1,
    Math.min(MAX_BAR, colW - (colW >= 6 ? GAP : colW >= 3 ? 1 : 0)),
  );
  const gapPx = barW >= 4 ? GAP : 1;
  const plotTop = TOP + (share ? VOLUME_H + VOLUME_GAP : 0);
  const plotH = share ? SHARE_H : COUNT_H;
  const baseY = plotTop + plotH;
  const marks = axisMarks({
    dayTicks,
    columns,
    binMs,
    colW,
    x0: GUTTER,
    width,
  });
  const height = baseY + (marks.hourStep ? AXIS_WITH_HOURS : AXIS);
  const yTop = share ? 1 : niceTop(maxN);
  const yOf = (value) => baseY - (value / yTop) * plotH;
  const xOf = (col) => GUTTER + col * colW + (colW - barW) / 2;
  const orderKey = order.join("|");

  // Every part's path, and the volume strip's: one path per answer.
  const paths = useMemo(() => {
    const parts = new Map([...order, FAILED].map((key) => [key, []]));
    const pending = [];
    const volume = [];
    const volumeOpen = [];
    const errorRate = (c) => (c.n ? c.errors / c.n : 0);
    const topErrorRate = Math.max(0, ...columns.map(errorRate));
    const errors = ERROR_STEPS.map(() => []);
    columns.forEach((column) => {
      const x = xOf(column.index);
      const failed = column.failed + column.skipped;
      const settled = column.answered + failed;
      const stack = [
        ...order.map((label) => [label, column.byLabel.get(label) || 0]),
        [FAILED, failed],
      ].filter(([, count]) => count > 0);
      const denominator = share ? settled : yTop;
      let y = baseY;
      const drawn = stack.map(([key, count]) => {
        const h = denominator ? (count / denominator) * plotH : 0;
        const part = { key, y: y - h, h };
        y -= h;
        return part;
      });
      const pendingH =
        !share && column.pending ? (column.pending / yTop) * plotH : 0;
      drawn.forEach((part, i) => {
        const last = i === drawn.length - 1;
        const h = part.h - (last && !pendingH ? 0 : gapPx);
        if (h <= 0.25) return;
        parts
          .get(part.key)
          .push(
            barPath(
              x,
              part.y + (part.h - h),
              barW,
              h,
              last && !pendingH ? RADIUS : 0,
            ),
          );
      });
      if (pendingH >= 1)
        pending.push(
          barPath(
            x + 0.5,
            y - pendingH + 0.5,
            barW - 1,
            pendingH - 0.5,
            RADIUS,
          ),
        );
      if (share && column.n) {
        // The strip: settled rows filled, rows still to read hollow above.
        const full = (column.n / (maxN || 1)) * VOLUME_H;
        const done = (settled / (maxN || 1)) * VOLUME_H;
        const bottom = TOP + VOLUME_H;
        if (done >= 0.5)
          volume.push(
            barPath(x, bottom - done, barW, done, column.pending ? 0 : 2),
          );
        if (full - done >= 1)
          volumeOpen.push(
            barPath(
              x + 0.5,
              bottom - full + 0.5,
              barW - 1,
              full - done - 0.5,
              2,
            ),
          );
      }
      if (column.errors > 0) {
        const strength = errorRate(column) / (topErrorRate || 1);
        const step = ERROR_STEPS.findIndex((s) => strength <= s + 1e-9);
        errors[step].push(`M${x},${baseY + 2}h${barW}v${RUG}h${-barW}Z`);
      }
    });
    return {
      parts: [...parts.entries()].map(([key, list]) => [key, list.join("")]),
      pending: pending.join(""),
      volume: volume.join(""),
      volumeOpen: volumeOpen.join(""),
      errors: errors.map((list) => list.join("")),
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [columns, orderKey, share, yTop, baseY, plotH, barW, colW, gapPx, maxN]);

  const locate = (event) => {
    const rect = svg.current?.getBoundingClientRect();
    if (!rect || !colW) return null;
    const x = event.clientX - rect.left;
    const col = Math.min(
      columns.length - 1,
      Math.max(0, Math.floor((x - GUTTER) / colW)),
    );
    return { x, col, hover: columns[col]?.n ? { col } : null };
  };
  const { drag, band, cursor, select, handlers } = useColumnSelection({
    columns,
    onSelect,
    onClick: (at) => {
      if (!columns[at.col]?.n) return false;
      select(at.col, at.col);
      return true;
    },
  });
  useEffect(() => {
    onModel({
      binMs,
      start: columns[0]?.start,
      end: columns[columns.length - 1]?.end,
      errors: columns.reduce((sum, column) => sum + column.errors, 0),
    });
  }, [onModel, binMs, columns]);

  const hoverColumn = hover ? columns[hover.col] : null;
  const anchor = useMemo(
    () =>
      hoverColumn
        ? anchorAt(svg.current, {
            x: GUTTER + hover.col * colW,
            y: TOP,
            width: colW,
            height: 4,
          })
        : null,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [hoverColumn, hover, colW],
  );
  const run = runTally(summary, question);
  const errors = columns.reduce((sum, column) => sum + column.errors, 0);
  const noun = rowsNoun(unit, sample, 2);
  const ticks = share ? [0.5, 1] : countTicks(yTop);
  const tickText = (tick) => (share ? `${tick * 100}%` : compact(tick));
  const colorFor = (key) => colorOf(question, key);

  return (
    <Box sx={{ position: "relative", height }}>
      {width > 0 && columns.length > 0 && (
        <Box
          component="svg"
          ref={svg}
          width={width}
          height={height}
          role="img"
          tabIndex={0}
          aria-label={`Timeline of ${formatCount(summary?.total || 0)} ${noun}, ${share ? "each column's answers as shares" : "a count per column"}, ${binText(binMs).replace(/ columns$/, "")} columns: ${labels
            .map(
              (label) => `${formatCount(run.byLabel.get(label) || 0)} ${label}`,
            )
            .join(
              ", ",
            )}${run.pending ? `, ${formatCount(run.pending)} not answered yet` : ""}${errors ? `; ${formatCount(errors)} had a trace error` : ""}. Use the arrow keys to move between columns and Enter to select one.`}
          {...handlers(locate, setHover)}
          sx={svgSx(theme, drag)}
        >
          <defs>
            <pattern
              id={hatchId}
              width={4}
              height={4}
              patternUnits="userSpaceOnUse"
              patternTransform="rotate(45)"
            >
              <rect
                width={4}
                height={4}
                fill={alpha(theme.palette.error.main, 0.1)}
              />
              <line
                x1={0}
                y1={0}
                x2={0}
                y2={4}
                stroke={theme.palette.error.main}
                strokeWidth={1.5}
              />
            </pattern>
          </defs>
          {share && (
            <text
              x={GUTTER - 6}
              y={TOP + VOLUME_H / 2 + 3.5}
              textAnchor="end"
              fill={theme.palette.text.disabled}
              fontSize={10}
              fontFamily={theme.typography.fontFamily}
            >
              {compact(maxN)}
            </text>
          )}
          {ticks.map((tick) => (
            <g key={`y-${tick}`}>
              <line
                x1={GUTTER}
                x2={width}
                y1={yOf(tick) + 0.5}
                y2={yOf(tick) + 0.5}
                stroke={theme.palette.divider}
                strokeWidth={1}
                opacity={0.6}
              />
              <text
                x={GUTTER - 6}
                y={yOf(tick) + 3.5}
                textAnchor="end"
                fill={theme.palette.text.disabled}
                fontSize={10}
                fontFamily={theme.typography.fontFamily}
                style={{ fontVariantNumeric: "tabular-nums" }}
              >
                {tickText(tick)}
              </text>
            </g>
          ))}
          <text
            x={GUTTER - 6}
            y={baseY + 3.5}
            textAnchor="end"
            fill={theme.palette.text.disabled}
            fontSize={10}
            fontFamily={theme.typography.fontFamily}
          >
            0
          </text>
          {band && (
            <rect
              x={GUTTER + band.from * colW}
              y={TOP - 4}
              width={(band.to - band.from + 1) * colW}
              height={baseY - TOP + 4}
              fill={alpha(theme.palette.primary.main, 0.08)}
              stroke={alpha(theme.palette.primary.main, 0.5)}
              strokeWidth={1}
              rx={3}
            />
          )}
          {hoverColumn && (
            <rect
              x={GUTTER + hover.col * colW}
              y={TOP - 4}
              width={colW}
              height={baseY - TOP + 4}
              fill={alpha(theme.palette.text.primary, 0.05)}
              pointerEvents="none"
            />
          )}
          <line
            x1={GUTTER}
            x2={width}
            y1={baseY + 0.5}
            y2={baseY + 0.5}
            stroke={theme.palette.divider}
            strokeWidth={1}
          />
          {share && (
            <>
              <path
                className="part"
                d={paths.volume}
                fill={alpha(theme.palette.text.disabled, 0.45)}
              />
              <path
                className="part"
                d={paths.volumeOpen}
                fill="none"
                stroke={alpha(theme.palette.text.disabled, 0.8)}
                strokeWidth={1}
              />
            </>
          )}
          {paths.parts.map(([key, d]) =>
            d ? (
              <path
                key={key}
                className="part"
                data-answer={key}
                d={d}
                fill={key === FAILED ? `url(#${hatchId})` : colorFor(key)}
                opacity={explicit && key !== focusAnswer ? 0.18 : 1}
              />
            ) : null,
          )}
          {paths.pending && (
            <path
              className="part"
              data-answer="__pending__"
              d={paths.pending}
              fill="none"
              stroke={alpha(theme.palette.text.disabled, 0.7)}
              strokeWidth={1}
            />
          )}
          {paths.errors.map((d, i) =>
            d ? (
              <path
                key={`errors-${ERROR_STEPS[i]}`}
                d={d}
                fill={theme.palette.error.main}
                opacity={ERROR_STEPS[i]}
              />
            ) : null,
          )}
          {cursor !== null && (
            <rect
              x={GUTTER + cursor * colW}
              y={TOP - 4}
              width={colW}
              height={baseY - TOP + 4}
              fill="none"
              stroke={theme.palette.primary.main}
              strokeWidth={1.5}
              rx={2}
            />
          )}
          <AxisLayer
            marks={marks}
            dayTicks={dayTicks}
            colW={colW}
            x0={GUTTER}
            baseY={baseY}
            theme={theme}
          />
        </Box>
      )}
      <Popper
        open={Boolean(anchor) && !drag?.moved}
        anchorEl={anchor}
        placement="top"
        sx={{ zIndex: theme.zIndex.modal + 1, pointerEvents: "none" }}
        modifiers={[{ name: "offset", options: { offset: [0, 8] } }]}
      >
        {hoverColumn && (
          <ColumnCard
            column={hoverColumn}
            question={question}
            colorOf={colorFor}
            unit={unit}
            sample={sample}
          />
        )}
      </Popper>
    </Box>
  );
}

DensityPlot.propTypes = {
  summary: PropTypes.object.isRequired,
  question: PropTypes.object.isRequired,
  order: PropTypes.arrayOf(PropTypes.string).isRequired,
  scale: PropTypes.oneOf(["share", "count"]).isRequired,
  width: PropTypes.number.isRequired,
  focusAnswer: PropTypes.string,
  explicit: PropTypes.bool,
  onSelect: PropTypes.func,
  unit: PropTypes.string,
  sample: PropTypes.object,
  onModel: PropTypes.func.isRequired,
};

function TipCard({ children, width = 280 }) {
  return (
    <Paper
      elevation={0}
      sx={{
        width,
        p: 1.5,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        boxShadow: (t) => t.customShadows?.dropdown,
      }}
    >
      {children}
    </Paper>
  );
}

TipCard.propTypes = { children: PropTypes.node, width: PropTypes.number };

// A density column's counts: the value leads, the answer follows, keyed by a
// short stroke of its colour.
function ColumnCard({ column, question, colorOf, unit, sample }) {
  const theme = useTheme();
  const rows = question.labels
    .map((label) => ({ label, count: column.byLabel.get(label) || 0 }))
    .filter((row) => row.count)
    .sort((a, b) => b.count - a.count);
  const line = (color, value, text, key) => (
    <Stack key={key} direction="row" spacing={1} alignItems="center">
      <Box
        aria-hidden
        sx={{ width: 10, height: 2, borderRadius: 1, bgcolor: color }}
      />
      <Typography
        variant="body2"
        sx={{
          fontSize: 13,
          fontWeight: 600,
          minWidth: 40,
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {formatCount(value)}
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ fontSize: 13 }}>
        {text}
      </Typography>
    </Stack>
  );
  return (
    <TipCard>
      <Typography variant="caption" color="text.secondary">
        {rangeText(column.start, column.end)}
      </Typography>
      <Typography variant="body2" sx={{ fontSize: 13, mb: 0.75 }}>
        {formatCount(column.n)} {rowsNoun(unit, sample, column.n)}
      </Typography>
      <Stack spacing={0.25}>
        {rows.map((row) =>
          line(
            colorOf(row.label),
            row.count,
            `${row.label} (${formatShare(row.count / (column.answered || 1))})`,
            row.label,
          ),
        )}
        {column.failed + column.skipped > 0 &&
          line(
            theme.palette.error.main,
            column.failed + column.skipped,
            "couldn't be answered",
            "failed",
          )}
        {column.pending > 0 &&
          line(
            theme.palette.text.disabled,
            column.pending,
            "not answered yet",
            "pending",
          )}
        {column.errors > 0 &&
          line(
            theme.palette.error.main,
            column.errors,
            "had a trace error",
            "errors",
          )}
      </Stack>
      <Typography
        variant="caption"
        color="text.disabled"
        component="p"
        sx={{ m: 0, mt: 0.75 }}
      >
        Click to select them
      </Typography>
    </TipCard>
  );
}

ColumnCard.propTypes = {
  column: PropTypes.object.isRequired,
  question: PropTypes.object.isRequired,
  colorOf: PropTypes.func.isRequired,
  unit: PropTypes.string,
  sample: PropTypes.object,
};

/**
 * The order answers stack in, bottom first: the answer in focus (or "No" for
 * a Yes/No question) at the bottom so its share reads against the baseline,
 * then the rest most common first.
 */
export function stackOrder(summary, question, focusAnswer, explicit) {
  const byCount = legendAnswers(summary, question)
    .slice()
    .sort((a, b) => b.count - a.count || a.index - b.index)
    .map((entry) => entry.answer);
  const first = explicit ? focusAnswer : isYesNo(question) ? "No" : null;
  return first && byCount.includes(first)
    ? [first, ...byCount.filter((answer) => answer !== first)]
    : byCount;
}

// "Sep 16 – Oct 16", or one day.
const dayRange = (start, end) => {
  if (!Number.isFinite(start) || !Number.isFinite(end)) return "";
  const a = dayText(start);
  const b = dayText(end - 1);
  return a === b ? a : `${a} – ${b}`;
};

function LegendItem({ item, active, faded, onClick, quiet }) {
  const theme = useTheme();
  const content = (
    <>
      <Swatch
        color={item.color}
        hollow={item.hollow}
        sx={{
          ...(item.background ? { background: item.background } : {}),
          ...item.swatch,
        }}
      />
      <Typography
        variant="caption"
        sx={{
          color: quiet ? "text.secondary" : "text.primary",
          fontSize: quiet ? 11.5 : 12,
        }}
      >
        {item.label}
      </Typography>
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{ fontVariantNumeric: "tabular-nums", fontSize: quiet ? 11.5 : 12 }}
      >
        {item.share !== null && item.share !== undefined
          ? `${formatShare(item.share)} · ${formatCount(item.count)}`
          : formatCount(item.count)}
      </Typography>
    </>
  );
  if (!onClick)
    return (
      <Stack direction="row" spacing={0.75} alignItems="center">
        {content}
      </Stack>
    );
  return (
    <Stack
      component="button"
      type="button"
      direction="row"
      spacing={0.75}
      alignItems="center"
      aria-pressed={active}
      onClick={onClick}
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
  );
}

LegendItem.propTypes = {
  item: PropTypes.object.isRequired,
  active: PropTypes.bool,
  faded: PropTypes.bool,
  onClick: PropTypes.func,
  quiet: PropTypes.bool,
};

/**
 * Every row of the run on a time axis: one square per row while there are
 * few enough (1,500 or fewer), else a column per time bin, as shares of its
 * answers (with its volume above) or as counts. Stacked by answer, with the
 * rows not answered yet filling in as Jev reads, and the rows whose run hit an
 * error marked under the axis. Hover for the counts, click a square to open
 * its row, drag across columns to select a time range, click the legend to
 * focus an answer.
 */
export default function AskTimeline({
  summary: given,
  records,
  questions: questionList,
  focus,
  onFocus,
  onSelect,
  onOpen,
  running,
  window: viewWindow,
  unit: unitProp,
}) {
  const theme = useTheme();
  const colorOf = useAnswerColor();
  const box = useRef(null);
  const width = useWidth(box);
  const { summary, questions, sample, squares, unit } = useRunData({
    summary: given,
    records,
    questions: questionList,
    window: viewWindow,
    unit: unitProp,
    running,
  });
  const window = useMemo(
    () =>
      viewWindow ||
      (summary?.window ? [summary.window.from, summary.window.to] : undefined),
    [viewWindow, summary?.window],
  );
  const {
    question,
    explicit,
    answer: focusAnswer,
  } = resolveFocus(focus, questions, records || []);
  const configId = question?.configId;
  const [model, setModel] = useState(null);
  const [scale, setScale] = useState("share");
  const counts = useMemo(
    () => (squares && question ? answerCounts(records, question) : null),
    [squares, records, question],
  );
  const order = useMemo(
    () =>
      question ? stackOrder(summary, question, focusAnswer, explicit) : [],
    [summary, question, focusAnswer, explicit],
  );
  if (!question) return null;
  const noun = UNITS[unit]?.noun || "row";
  const legend = legendAnswers(summary, question);
  const run = runTally(summary, question);

  const answers = legend.map((entry) => ({
    key: entry.answer,
    answer: entry.answer,
    label: entry.answer,
    count: entry.count,
    share: run.answered ? entry.share : null,
    color: colorOf(question, entry.answer),
  }));
  const errors = model?.errors || 0;
  const unsure = legend.reduce((sum, entry) => sum + entry.unsure, 0);
  // Every legend count comes from the summary the page's header and
  // progress read, so they agree while answers land. The squares, read less
  // often, only split the rows still to answer into those Jev is reading
  // and those it hasn't reached.
  const reading = counts ? Math.min(counts.reading, run.pending) : 0;
  const status = [
    run.failed + run.skipped > 0 && {
      key: "failed",
      label: "Couldn't answer",
      count: run.failed + run.skipped,
      color: theme.palette.error.main,
      ...(squares
        ? { hollow: true }
        : {
            background: `repeating-linear-gradient(135deg, ${theme.palette.error.main} 0 1.5px, ${alpha(theme.palette.error.main, 0.1)} 1.5px 4px)`,
          }),
    },
    ...(counts
      ? [
          (running || reading > 0) && {
            key: "reading",
            label: "Reading",
            count: reading,
            color: alpha(theme.palette.primary.main, 0.3),
          },
          (running || run.pending > reading) && {
            key: "waiting",
            label: "Not yet",
            count: run.pending - reading,
            color: alpha(theme.palette.text.disabled, 0.55),
            hollow: true,
          },
        ]
      : [
          (running || run.pending > 0) && {
            key: "pending",
            label: "Not answered yet",
            count: run.pending,
            color: alpha(theme.palette.text.disabled, 0.7),
            hollow: true,
          },
        ]),
    errors > 0 && {
      key: "errors",
      label: "Trace had an error",
      count: errors,
      color: theme.palette.error.main,
      swatch: { width: 12, height: RUG, borderRadius: "1px" },
    },
    unsure > 0 && {
      key: "unsure",
      label: squares ? "Jev unsure (lighter)" : "Jev unsure",
      count: unsure,
      color: alpha(colorOf(question, question.labels[0]), 0.42),
    },
  ].filter(Boolean);

  const captionText =
    model && Number.isFinite(model.start)
      ? [
          squares ? `One square per ${noun}` : "",
          binText(model.binMs),
          dayRange(model.start, model.end),
          model.capped ? `tallest cut at ${model.capped}, +N` : "",
          "drag to select",
        ]
          .filter(Boolean)
          .join(" · ")
      : summary?.preparing
        ? "Picking the rows to read…"
        : `No ${UNITS[unit]?.plural || "rows"} to show yet.`;

  return (
    <VizCard
      title="Timeline"
      label="Timeline of answers"
      caption={captionText}
      action={
        <>
          {!squares && (
            <ToggleButtonGroup
              size="small"
              exclusive
              value={scale}
              onChange={(_, value) => value && setScale(value)}
              aria-label="Column height"
            >
              <ToggleButton
                value="share"
                sx={{ px: 1.25, py: 0.4, fontSize: 12 }}
              >
                Share
              </ToggleButton>
              <ToggleButton
                value="count"
                sx={{ px: 1.25, py: 0.4, fontSize: 12 }}
              >
                Count
              </ToggleButton>
            </ToggleButtonGroup>
          )}
          {questions.length > 1 && (
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
          )}
        </>
      }
    >
      <Box ref={box}>
        {width > 0 &&
          (squares ? (
            <SquaresPlot
              records={records}
              question={question}
              order={order}
              window={window}
              width={width}
              focusAnswer={focusAnswer}
              explicit={explicit}
              onSelect={onSelect}
              onOpen={onOpen}
              unit={unit}
              onModel={setModel}
            />
          ) : (
            <DensityPlot
              summary={summary}
              question={question}
              order={order}
              scale={scale}
              width={width}
              focusAnswer={focusAnswer}
              explicit={explicit}
              onSelect={onSelect}
              unit={unit}
              sample={sample}
              onModel={setModel}
            />
          ))}
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
        {answers.map((item) => {
          const active = explicit && focusAnswer === item.answer;
          return (
            <LegendItem
              key={item.key}
              item={item}
              active={active}
              faded={explicit && !active}
              onClick={() =>
                onFocus?.({ configId, answer: active ? null : item.answer })
              }
            />
          );
        })}
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
      {status.length > 0 && (
        <Stack
          direction="row"
          alignItems="center"
          useFlexGap
          flexWrap="wrap"
          columnGap={2}
          rowGap={0.5}
          aria-label="Status"
          sx={{ mt: 0.5 }}
        >
          {status.map((item) => (
            <LegendItem key={item.key} item={item} quiet />
          ))}
        </Stack>
      )}
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
    <TipCard width={300}>
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
          The trace had an error.
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
    </TipCard>
  );
}

SquareCard.propTypes = {
  item: PropTypes.object.isRequired,
  question: PropTypes.object,
  colorOf: PropTypes.func.isRequired,
};

AskTimeline.propTypes = {
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
};
