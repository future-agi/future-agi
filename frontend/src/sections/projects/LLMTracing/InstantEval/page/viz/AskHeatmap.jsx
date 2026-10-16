import React, { useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  MenuItem,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import { alpha, getContrastRatio, useTheme } from "@mui/material/styles";
import { UNITS } from "src/sections/projects/observePopulation";
import { formatCount, formatShare, resolveFocus } from "./askAnswers";
import { LEVELS, PIVOTS, SMALL_N, buildPivot, shareLevel } from "./askPivot";
import {
  VizCard,
  answerRamp,
  quoted,
  useAnswerColor,
  useWidth,
} from "./vizParts";

const LABEL_COL = 112;
const TOTAL_COL = 72;
const GRID_GAP = 3;
const COUNT_FITS = 20;
// "100" in 10px figures.
const TOTAL_FITS = 17;

// Thin stripes of a step's colour over the palest tint: a cell with too few
// answers to trust.
const hatch = (color) =>
  `repeating-linear-gradient(135deg, ${alpha(color, 0.6)} 0 1.5px, transparent 1.5px 4.5px)`;

/**
 * The answers as a grid. Day × Hour by default (GitHub-contribution style);
 * also the busiest users by day, models by day, and one question's answers
 * against another's. A cell's colour is the share of its answered rows that
 * gave the answer in focus; a cell with fewer than 3 answered rows is drawn
 * lighter, one with rows still being read is outlined, and one with rows
 * whose run hit an error carries a red dot. Click a cell to select its rows.
 */
export default function AskHeatmap({
  records,
  questions,
  focus,
  onFocus,
  onSelect,
  running,
  window,
  unit: unitProp,
}) {
  const theme = useTheme();
  const colorOf = useAnswerColor();
  const box = useRef(null);
  const width = useWidth(box);
  const [pivotKey, setPivotKey] = useState("dayHour");
  const [againstId, setAgainstId] = useState(null);
  const { question, answer } = resolveFocus(focus, questions, records);
  const unit = unitProp || records[0]?.unit || "sessions";
  const plural = (UNITS[unit] || UNITS.sessions).plural;
  const choices = PIVOTS.filter(
    (p) =>
      (p.value !== "questions" || questions.length > 1) &&
      (p.value !== "userDay" || unit !== "users") &&
      (p.value !== "modelDay" || records.some((r) => r.facets?.model)),
  );
  const pivotValue = choices.some((p) => p.value === pivotKey)
    ? pivotKey
    : "dayHour";
  const pivot = useMemo(
    () =>
      buildPivot({
        records,
        questions,
        configId: question?.configId,
        answer,
        pivot: pivotValue,
        againstId,
        window,
      }),
    [records, questions, question, answer, pivotValue, againstId, window],
  );
  if (!question) return null;

  const crossing = pivotValue === "questions";
  // The focused answer's own hue, pale (none of a cell's answers gave it)
  // to deep (all of them did).
  const ramp = answerRamp(colorOf(question, answer));
  const levelColor = (level) => ramp[level];
  // A cell with too few answers to trust is hatched; its count and error dot
  // stay readable.
  const cellPaint = (level, small) =>
    small
      ? { bgcolor: ramp[0], backgroundImage: hatch(ramp[level]) }
      : { bgcolor: ramp[level] };
  const inkOn = (level, small) =>
    !small && level >= 3 && getContrastRatio(ramp[level], "#fff") >= 3
      ? "common.white"
      : "text.primary";
  const overallShare = pivot.overall.answered
    ? pivot.overall.matches / pivot.overall.answered
    : null;
  const rowTotals = new Map(
    pivot.rows.map((row) => {
      let total = 0;
      let matches = 0;
      let answered = 0;
      pivot.columns.forEach((col) => {
        const cell = pivot.cells.get(`${row.key}|${col.key}`);
        if (!cell) return;
        total += cell.total;
        matches += cell.matches;
        answered += cell.answered;
      });
      return [row.key, { total, matches, answered }];
    }),
  );
  // Every column's totals, for the line along the bottom.
  const columnTotals = new Map(
    pivot.columns.map((col) => {
      let total = 0;
      let matches = 0;
      let answered = 0;
      pivot.rows.forEach((row) => {
        const cell = pivot.cells.get(`${row.key}|${col.key}`);
        if (!cell) return;
        total += cell.total;
        matches += cell.matches;
        answered += cell.answered;
      });
      return [col.key, { total, matches, answered }];
    }),
  );
  const grand = [...columnTotals.values()].reduce(
    (sum, t) => ({
      total: sum.total + t.total,
      matches: sum.matches + t.matches,
      answered: sum.answered + t.answered,
    }),
    { total: 0, matches: 0, answered: 0 },
  );
  const totalsLabel =
    {
      dayHour: "All days",
      userDay: "These users",
      modelDay: "All models",
    }[pivotValue] || "All";
  const cellW =
    width && pivot.columns.length
      ? (width -
          LABEL_COL -
          TOTAL_COL -
          (pivot.columns.length + 1) * GRID_GAP) /
        pivot.columns.length
      : 0;
  const showCounts = cellW >= COUNT_FITS;
  const columnTick = (index, col) => {
    if (pivotValue === "dayHour") return index % 3 === 0 ? col.label : "";
    const every = Math.max(1, Math.ceil(40 / Math.max(cellW, 1)));
    return index % every === 0 ? col.label : "";
  };

  const anyErrors = records.some((record) => record.facets?.error === true);
  const errorNote = (cell) =>
    cell?.errors ? ` · ${formatCount(cell.errors)} hit an error` : "";
  const describe = (row, col, cell) =>
    `${describeShare(row, col, cell)}${errorNote(cell)}`;
  const describeShare = (row, col, cell) => {
    const where = `${row.title} · ${col.title}`;
    if (!cell) return `${where}: no ${plural}`;
    if (crossing) {
      const totals = rowTotals.get(row.key);
      return `Of ${formatCount(totals.total)} that answered ${quoted(row.key)} to ${quoted(question.question)}, ${formatCount(cell.total)} (${formatShare(cell.total / totals.total)}) answered ${quoted(col.key)} to ${quoted(pivot.against.question)}`;
    }
    if (!cell.answered)
      return `${where}: ${formatCount(cell.total)} ${plural}, none answered yet`;
    const pending = cell.total - cell.answered;
    return `${where}: ${formatCount(cell.matches)} of ${formatCount(cell.answered)} ${quoted(answer)} (${formatShare(cell.matches / cell.answered)}) vs ${formatShare(overallShare)} overall${pending ? ` · ${formatCount(pending)} not answered yet` : ""}`;
  };

  return (
    <VizCard
      title="Heatmap"
      label="Heatmap of answers"
      caption={
        crossing
          ? `How answers to ${quoted(question.question)} line up with answers to ${quoted(pivot.against?.question || "")}. Each row adds up to 100%.`
          : `Share of ${quoted(answer)} in each cell${overallShare !== null ? `, against ${formatShare(overallShare)} overall` : ""}. Click a cell to see its ${plural}.`
      }
      action={
        <>
          <TextField
            select
            size="small"
            label="Rows"
            value={pivotValue}
            onChange={(event) => setPivotKey(event.target.value)}
            sx={{ width: 168 }}
          >
            {choices.map((choice) => (
              <MenuItem key={choice.value} value={choice.value}>
                {choice.label}
              </MenuItem>
            ))}
          </TextField>
          {crossing && questions.length > 2 && (
            <TextField
              select
              size="small"
              label="Against"
              value={pivot.against?.configId || ""}
              onChange={(event) => setAgainstId(event.target.value)}
              sx={{ width: 220 }}
            >
              {questions
                .filter((q) => q.configId !== question.configId)
                .map((q) => (
                  <MenuItem key={q.configId} value={q.configId}>
                    {q.question}
                  </MenuItem>
                ))}
            </TextField>
          )}
          {!crossing && (
            <TextField
              select
              size="small"
              label="Colour"
              value={answer}
              onChange={(event) =>
                onFocus?.({
                  configId: question.configId,
                  answer: event.target.value,
                })
              }
              sx={{ width: 140 }}
              SelectProps={{ renderValue: (value) => `% ${quoted(value)}` }}
            >
              {(question.labels || []).map((label) => (
                <MenuItem key={label} value={label}>
                  % {quoted(label)}
                </MenuItem>
              ))}
            </TextField>
          )}
        </>
      }
    >
      <Box ref={box}>
        {pivot.rows.length === 0 ? (
          <Typography
            variant="body2"
            color="text.secondary"
            sx={{ fontSize: 13 }}
          >
            {crossing
              ? "Both questions need answers before they can be compared."
              : `No ${plural} to place yet.`}
          </Typography>
        ) : (
          <Box
            role="grid"
            aria-label={`Share of ${answer} by ${PIVOTS.find((p) => p.value === pivotValue)?.label}`}
            sx={{
              display: "grid",
              gridTemplateColumns: `${LABEL_COL}px repeat(${pivot.columns.length}, minmax(0, 1fr)) ${TOTAL_COL}px`,
              columnGap: `${GRID_GAP}px`,
              rowGap: `${GRID_GAP}px`,
              alignItems: "center",
            }}
          >
            <Box />
            {pivot.columns.map((col, index) => (
              <Typography
                key={col.key}
                variant="caption"
                color="text.disabled"
                noWrap
                sx={{ fontSize: 10, overflow: "visible" }}
              >
                {columnTick(index, col)}
              </Typography>
            ))}
            <Typography
              variant="caption"
              color="text.disabled"
              sx={{ fontSize: 10, textAlign: "right" }}
            >
              {crossing ? "rows" : `% ${quoted(answer)}`}
            </Typography>

            {pivot.rows.map((row) => {
              const totals = rowTotals.get(row.key);
              return (
                <React.Fragment key={row.key}>
                  <Typography
                    variant="caption"
                    color="text.secondary"
                    noWrap
                    title={row.title}
                    sx={{ fontSize: 11, pr: 1 }}
                  >
                    {row.label}
                  </Typography>
                  {pivot.columns.map((col) => {
                    const cell = pivot.cells.get(`${row.key}|${col.key}`);
                    const share = !cell
                      ? 0
                      : crossing
                        ? cell.total / (totals.total || 1)
                        : cell.answered
                          ? cell.matches / cell.answered
                          : 0;
                    const level =
                      cell && (crossing || cell.answered)
                        ? shareLevel(share)
                        : 0;
                    const small = Boolean(
                      cell &&
                        !crossing &&
                        cell.answered > 0 &&
                        cell.answered < SMALL_N,
                    );
                    const pending =
                      cell && !crossing && cell.answered < cell.total;
                    // The count is the cell's rows; the colour is the share.
                    const value = cell?.total;
                    const painted = cell && (crossing || cell.answered);
                    const label = describe(row, col, cell);
                    return (
                      <Tooltip
                        key={col.key}
                        title={label}
                        placement="top"
                        disableInteractive
                      >
                        <Box
                          role="gridcell"
                          aria-label={label}
                          tabIndex={cell ? 0 : -1}
                          onClick={() =>
                            cell &&
                            onSelect?.(cell.ids, `${row.title} · ${col.title}`)
                          }
                          onKeyDown={(event) => {
                            if (
                              cell &&
                              (event.key === "Enter" || event.key === " ")
                            ) {
                              event.preventDefault();
                              onSelect?.(
                                cell.ids,
                                `${row.title} · ${col.title}`,
                              );
                            }
                          }}
                          sx={{
                            position: "relative",
                            height: crossing ? 34 : 20,
                            borderRadius: "3px",
                            display: "grid",
                            placeItems: "center",
                            fontSize: 10,
                            fontWeight: 600,
                            fontVariantNumeric: "tabular-nums",
                            color: painted
                              ? inkOn(level, small)
                              : "text.primary",
                            // A count over stripes keeps a halo to read.
                            textShadow: small
                              ? `0 0 2px ${theme.palette.background.paper}, 0 0 3px ${theme.palette.background.paper}`
                              : "none",
                            ...(!cell
                              ? {
                                  bgcolor: alpha(
                                    theme.palette.text.disabled,
                                    0.08,
                                  ),
                                }
                              : painted
                                ? cellPaint(level, small)
                                : { bgcolor: "transparent" }),
                            outline: pending
                              ? `1px ${cell.answered ? "solid" : "dashed"} ${alpha(theme.palette.text.disabled, 0.6)}`
                              : "none",
                            outlineOffset: -1,
                            cursor: cell ? "pointer" : "default",
                            transition: "background-color 400ms ease",
                            "&:hover": cell
                              ? {
                                  boxShadow: `0 0 0 2px ${theme.palette.text.primary}`,
                                }
                              : undefined,
                            "&:focus-visible": {
                              outline: `2px solid ${theme.palette.primary.main}`,
                              outlineOffset: 1,
                            },
                          }}
                        >
                          {showCounts && value >= 1 ? formatCount(value) : ""}
                          {cell?.errors > 0 && (
                            <Box
                              component="span"
                              aria-hidden
                              sx={{
                                position: "absolute",
                                top: 2,
                                right: 2,
                                width: 5,
                                height: 5,
                                borderRadius: "50%",
                                bgcolor: "error.main",
                                boxShadow: `0 0 0 1px ${theme.palette.background.paper}`,
                              }}
                            />
                          )}
                        </Box>
                      </Tooltip>
                    );
                  })}
                  <Typography
                    variant="caption"
                    color="text.secondary"
                    sx={{
                      fontSize: 11,
                      textAlign: "right",
                      fontVariantNumeric: "tabular-nums",
                    }}
                  >
                    {crossing
                      ? formatCount(totals.total)
                      : totals.answered
                        ? `${formatShare(totals.matches / totals.answered)} of ${formatCount(totals.answered)}`
                        : "–"}
                  </Typography>
                </React.Fragment>
              );
            })}

            <Typography
              variant="caption"
              color="text.disabled"
              noWrap
              sx={{ fontSize: 11, pr: 1 }}
            >
              {crossing ? totalsLabel : `${totalsLabel}, %`}
            </Typography>
            {pivot.columns.map((col) => {
              const totals = columnTotals.get(col.key);
              const share = crossing
                ? grand.total
                  ? totals.total / grand.total
                  : null
                : totals.answered
                  ? totals.matches / totals.answered
                  : null;
              const small =
                !crossing && totals.answered > 0 && totals.answered < SMALL_N;
              const level = share === null ? 0 : shareLevel(share);
              // A shaded cell like the ones above it, with the number only:
              // the row is labelled "%" (or rows, for two questions).
              const text = crossing
                ? formatCount(totals.total)
                : share === null
                  ? ""
                  : String(Math.round(share * 100));
              const title = crossing
                ? `${col.title}: ${formatCount(totals.total)} ${plural}`
                : share === null
                  ? `${col.title}: no answers yet`
                  : `${col.title}: ${formatCount(totals.matches)} of ${formatCount(totals.answered)} ${quoted(answer)} (${formatShare(share)}) vs ${formatShare(overallShare)} overall`;
              return (
                <Tooltip
                  key={`total-${col.key}`}
                  title={title}
                  placement="bottom"
                  disableInteractive
                >
                  <Box
                    aria-label={title}
                    sx={{
                      height: 18,
                      borderRadius: "3px",
                      display: "grid",
                      placeItems: "center",
                      fontSize: 10,
                      fontWeight: 600,
                      fontVariantNumeric: "tabular-nums",
                      overflow: "hidden",
                      color:
                        share === null ? "text.disabled" : inkOn(level, small),
                      textShadow: small
                        ? `0 0 2px ${theme.palette.background.paper}, 0 0 3px ${theme.palette.background.paper}`
                        : "none",
                      ...(share === null
                        ? {
                            bgcolor: alpha(theme.palette.text.disabled, 0.08),
                          }
                        : cellPaint(level, small)),
                    }}
                  >
                    {cellW >= TOTAL_FITS ? text : ""}
                  </Box>
                </Tooltip>
              );
            })}
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{
                fontSize: 11,
                textAlign: "right",
                fontVariantNumeric: "tabular-nums",
              }}
            >
              {crossing
                ? formatCount(grand.total)
                : grand.answered
                  ? `${formatShare(grand.matches / grand.answered)} of ${formatCount(grand.answered)}`
                  : "–"}
            </Typography>
          </Box>
        )}
        {pivot.hidden > 0 && (
          <Typography
            variant="caption"
            color="text.disabled"
            component="p"
            sx={{ m: 0, mt: 0.75 }}
          >
            The {formatCount(pivot.rows.length)} busiest users;{" "}
            {formatCount(pivot.hidden)} more are left out.
          </Typography>
        )}
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
                bgcolor: levelColor(level),
              }}
            />
          ))}
          <Typography variant="caption" color="text.disabled">
            100% {crossing ? "of the row" : quoted(answer)}
          </Typography>
          {!crossing && (
            <>
              <Box
                aria-hidden
                sx={{
                  width: 14,
                  height: 12,
                  ml: 1.5,
                  borderRadius: "2px",
                  bgcolor: levelColor(0),
                  backgroundImage: hatch(levelColor(3)),
                }}
              />
              <Typography variant="caption" color="text.disabled">
                Fewer than {SMALL_N} answered
              </Typography>
              {anyErrors && (
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
                    Run hit an error
                  </Typography>
                </>
              )}
              {(running || pivot.overall.answered < pivot.overall.total) && (
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
          )}
        </Stack>
      </Box>
    </VizCard>
  );
}

AskHeatmap.propTypes = {
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
