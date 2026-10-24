import React, { useState } from "react";
import PropTypes from "prop-types";
import { Box, Paper, Popper, Stack, Typography } from "@mui/material";
import { alpha, useTheme } from "@mui/material/styles";
import { formatCount, formatShare } from "./askAnswers";
import { MIN_DELTA_TABLE } from "./askStats";
import { Swatch, quoted, rowsNoun, useAnswerColor } from "./vizParts";

// A table of answers: rows (a group's entries, or one question's answers)
// against answers, used by the heatmap's Users/Models/Turns and by the
// Question × Question card.

const MATRIX_LABEL_COL = 176;
const TOTAL_COL = 72;
const GRID_GAP = 3;

// One tooltip for a whole grid, at the hovered cell.
function HoverTip({ hover }) {
  const theme = useTheme();
  return (
    <Popper
      open={Boolean(hover)}
      anchorEl={hover?.el}
      placement="top"
      sx={{ zIndex: theme.zIndex.modal + 1, pointerEvents: "none" }}
      modifiers={[{ name: "offset", options: { offset: [0, 6] } }]}
    >
      {hover && (
        <Paper
          elevation={0}
          sx={{
            maxWidth: 320,
            px: 1.25,
            py: 1,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 1,
            boxShadow: (t) => t.customShadows?.dropdown,
          }}
        >
          <Typography variant="body2" sx={{ fontSize: 12.5 }}>
            {hover.text}
          </Typography>
        </Paper>
      )}
    </Popper>
  );
}

HoverTip.propTypes = { hover: PropTypes.object };

// A row's share of an answer on a 0–100% track, with a tick at the other
// rows' share: the bar is the answer's hue, the rest is quiet.
function ShareBar({ share, rest, color }) {
  const theme = useTheme();
  return (
    <span
      aria-hidden
      style={{
        position: "relative",
        display: "block",
        width: "100%",
        height: 4,
        borderRadius: 2,
        background: alpha(theme.palette.text.disabled, 0.14),
      }}
    >
      <span
        style={{
          position: "absolute",
          left: 0,
          top: 0,
          bottom: 0,
          width: `${Math.max(share > 0 ? 2 : 0, share * 100)}%`,
          borderRadius: 2,
          background: color,
          transition: "width 400ms ease",
        }}
      />
      {rest !== null && (
        <span
          style={{
            position: "absolute",
            top: -3,
            bottom: -3,
            left: `calc(${rest * 100}% - 1px)`,
            width: 2,
            borderRadius: 1,
            background: theme.palette.text.primary,
            boxShadow: `0 0 0 1px ${theme.palette.background.paper}`,
          }}
        />
      )}
    </span>
  );
}

ShareBar.propTypes = {
  share: PropTypes.number.isRequired,
  rest: PropTypes.number,
  color: PropTypes.string,
};

/**
 * Rows (a group's entries, or one question's answers) against answers: each
 * cell the row's share of that answer and its count, with a bar in the
 * answer's hue and a tick at the other rows' share. Cells where a row has
 * clearly more of an answer than the other rows (Fisher's exact test,
 * Benjamini–Hochberg across the table, 5 points or more) are outlined in the
 * answer's hue, marked ↑, and named one per line below.
 */
export default function AnswerMatrix({
  matrix,
  question,
  colorQuestion,
  onSelect,
  unit,
  sample,
}) {
  const theme = useTheme();
  const colorOf = useAnswerColor();
  const [hover, setHover] = useState(null);
  const plural = rowsNoun(unit, sample, 2);
  const { rows, columns, standOut } = matrix;
  const everyone = matrix.allRows || rows;
  // Each answer's count over every row, for the other rows' share.
  const colTotals = new Map(
    columns.map((col) => [
      col.key,
      everyone.reduce((sum, row) => sum + (row.counts.get(col.key) || 0), 0),
    ]),
  );
  const answeredTotal = everyone.reduce((sum, row) => sum + row.answered, 0);
  // The row labels' column fits short labels ("Yes", "No") so the answer
  // columns get the room.
  const labelCol = Math.max(
    64,
    Math.min(
      MATRIX_LABEL_COL,
      28 + 7 * Math.max(0, ...rows.map((row) => String(row.label).length)),
    ),
  );
  const restShare = (row, col) => {
    const restN = answeredTotal - row.answered;
    return restN > 0
      ? (colTotals.get(col.key) - (row.counts.get(col.key) || 0)) / restN
      : null;
  };
  const describe = (row, col, k) => {
    const test = standOut.get(`${row.key}|${col.key}`);
    const rest = restShare(row, col);
    const base = `${row.title}: ${formatCount(k)} of ${formatCount(row.answered)} answered ${quoted(col.label)} (${formatShare(row.answered ? k / row.answered : 0)})${rest !== null ? ` vs ${formatShare(rest)} in the other rows` : ""}`;
    return test
      ? `${base}. Stands out (q ${test.q < 0.001 ? "< 0.001" : `= ${test.q.toFixed(3)}`}).`
      : base;
  };
  // In words, under the table: every outlined cell, in reading order.
  const outlined = rows.flatMap((row) =>
    columns
      .filter((col) => standOut.has(`${row.key}|${col.key}`))
      .map((col) => ({ row, col, t: standOut.get(`${row.key}|${col.key}`) })),
  );

  return (
    <Box>
      <Box
        role="grid"
        aria-label={`${matrix.against ? "Answers against answers" : "Answers by group"}`}
        onMouseLeave={() => setHover(null)}
        sx={{
          display: "grid",
          gridTemplateColumns: `${labelCol}px repeat(${columns.length}, minmax(0, 1fr)) ${TOTAL_COL}px`,
          columnGap: `${GRID_GAP}px`,
          rowGap: `${GRID_GAP}px`,
          alignItems: "stretch",
        }}
      >
        <Box />
        {columns.map((col) => (
          <Stack
            key={col.key}
            direction="row"
            spacing={0.5}
            alignItems="flex-start"
            sx={{ minWidth: 0, pb: 0.25, px: 0.75, alignSelf: "end" }}
            title={col.title || col.label}
          >
            <Swatch
              color={colorOf(colorQuestion, col.key)}
              size={8}
              sx={{ mt: "3px" }}
            />
            {/* A long answer wraps to two lines rather than being cut. */}
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{
                fontSize: 11,
                lineHeight: "14px",
                minWidth: 0,
                overflowWrap: "anywhere",
                display: "-webkit-box",
                WebkitLineClamp: 2,
                WebkitBoxOrient: "vertical",
                overflow: "hidden",
              }}
            >
              {col.label}
            </Typography>
          </Stack>
        ))}
        <Typography
          variant="caption"
          color="text.disabled"
          sx={{ fontSize: 10, textAlign: "right", alignSelf: "end" }}
        >
          answered
        </Typography>
        {rows.map((row) => (
          <React.Fragment key={row.key}>
            <Stack
              direction="row"
              spacing={0.75}
              alignItems="center"
              sx={{ minWidth: 0, pr: 1 }}
              title={row.title}
            >
              {!matrix.group && (
                <Swatch color={colorOf(question, row.key)} size={8} />
              )}
              <Typography
                variant="caption"
                color="text.secondary"
                noWrap
                sx={{ fontSize: 11.5 }}
              >
                {row.label}
              </Typography>
            </Stack>
            {columns.map((col) => {
              const k = row.counts.get(col.key) || 0;
              const share = row.answered ? k / row.answered : 0;
              const stands = standOut.has(`${row.key}|${col.key}`);
              const hue = colorOf(colorQuestion, col.key);
              const label = describe(row, col, k);
              const pick = () =>
                k > 0 &&
                onSelect?.(
                  matrix.cellFilter(row, col),
                  `${row.label} · ${quoted(col.label)}`,
                );
              return (
                <div
                  key={col.key}
                  role="gridcell"
                  aria-label={label}
                  data-stands-out={stands || undefined}
                  tabIndex={k > 0 ? 0 : -1}
                  onMouseEnter={(event) =>
                    setHover({ el: event.currentTarget, text: label })
                  }
                  onFocus={(event) =>
                    setHover({ el: event.currentTarget, text: label })
                  }
                  onBlur={() => setHover(null)}
                  onClick={pick}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      pick();
                    }
                  }}
                  style={{
                    padding: "5px 8px 7px",
                    borderRadius: 4,
                    display: "flex",
                    flexDirection: "column",
                    gap: 4,
                    background: stands
                      ? alpha(hue, 0.14)
                      : alpha(theme.palette.text.disabled, 0.05),
                    boxShadow: stands ? `inset 0 0 0 2px ${hue}` : "none",
                    cursor: k > 0 ? "pointer" : "default",
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  <span
                    style={{
                      display: "flex",
                      alignItems: "baseline",
                      gap: 6,
                      color: theme.palette.text.primary,
                    }}
                  >
                    <span style={{ fontSize: 12, fontWeight: 600 }}>
                      {row.answered ? formatShare(share) : "–"}
                      {stands && <span aria-hidden> ↑</span>}
                    </span>
                    <span
                      style={{
                        fontSize: 10.5,
                        color: theme.palette.text.secondary,
                      }}
                    >
                      {formatCount(k)}
                    </span>
                  </span>
                  <ShareBar
                    share={share}
                    rest={row.answered ? restShare(row, col) : null}
                    color={hue}
                  />
                </div>
              );
            })}
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{
                fontSize: 11,
                textAlign: "right",
                alignSelf: "center",
                fontVariantNumeric: "tabular-nums",
              }}
            >
              {formatCount(row.answered)}
            </Typography>
          </React.Fragment>
        ))}
      </Box>
      <HoverTip hover={hover} />
      {matrix.hidden > 0 && (
        <Typography
          variant="caption"
          color="text.disabled"
          component="p"
          sx={{ m: 0, mt: 0.75 }}
        >
          The {formatCount(rows.length)} busiest;{" "}
          {formatCount(matrix.hiddenRows)} more {plural} are in the comparison
          but not listed.
        </Typography>
      )}
      {outlined.length > 0 && (
        <Box
          component="ul"
          aria-label="Outlined cells"
          sx={{ m: 0, mt: 1.25, p: 0, listStyle: "none" }}
        >
          {outlined.map(({ row, col, t }) => (
            <Stack
              key={`${row.key}|${col.key}`}
              component="li"
              direction="row"
              spacing={1}
              alignItems="center"
              sx={{ py: 0.25 }}
            >
              <Box
                aria-hidden
                sx={{
                  width: 14,
                  height: 10,
                  borderRadius: "2px",
                  bgcolor: alpha(colorOf(colorQuestion, col.key), 0.14),
                  boxShadow: `inset 0 0 0 2px ${colorOf(colorQuestion, col.key)}`,
                  flexShrink: 0,
                }}
              />
              <Typography
                variant="body2"
                component="p"
                sx={{
                  m: 0,
                  minWidth: 0,
                  fontSize: 12.5,
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                <Box component="span" sx={{ fontWeight: 600 }}>
                  {row.label} · {quoted(col.label)}
                </Box>
                : {formatShare(t.rate)} vs {formatShare(t.restRate)} in the
                other rows ({formatCount(t.k)} of {formatCount(t.n)})
              </Typography>
            </Stack>
          ))}
        </Box>
      )}
      <Typography
        variant="caption"
        color="text.disabled"
        component="p"
        sx={{ m: 0, mt: 1 }}
      >
        Each row adds up to 100% of its answered {plural}; the tick is the other
        rows&apos; share. Outlined (↑): the row has clearly more of that answer
        than the other rows, at least {MIN_DELTA_TABLE * 100} points more and
        holding after correcting across the table (Fisher&apos;s exact test).
      </Typography>
    </Box>
  );
}

AnswerMatrix.propTypes = {
  matrix: PropTypes.object.isRequired,
  question: PropTypes.object.isRequired,
  colorQuestion: PropTypes.object.isRequired,
  onSelect: PropTypes.func,
  unit: PropTypes.string,
  sample: PropTypes.object,
};
