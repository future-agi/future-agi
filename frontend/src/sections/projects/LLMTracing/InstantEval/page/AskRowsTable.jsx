import React from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Chip,
  CircularProgress,
  Skeleton,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
} from "@mui/material";
import { keyframes, useTheme } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import { answerText, isFailure } from "../instantEvalAnswers";
import { formatWhen, unitMeta } from "../instantEvalUnits";
import { answerColor } from "../results/answerColors";
import { answerState } from "./viz";
import { facetLine } from "./askFormat";

// The table never holds more rows than this; past it, export them.
const TABLE_MAX_ROWS = 1500;
// Each question has its own answer column; past the page's width the
// table scrolls sideways. The row column keeps the room it has with three.
const ANSWER_COLUMN_WIDTH = 120;
const tableMinWidth = (answerColumns) =>
  Math.max(760, 400 + ANSWER_COLUMN_WIDTH * answerColumns);

const pulse = keyframes`
  0%, 100% { opacity: 0.35; }
  50% { opacity: 1; }
`;

const percent = (value) => `${Math.round(value * 100)}%`;

// An export button spins while its file is on its way.
const exportIcon = (busy) =>
  busy ? (
    <CircularProgress size={14} color="inherit" />
  ) : (
    <Iconify icon="mdi:download-outline" width={16} />
  );

// A user's answer is the most common one across their sessions: "4 of 5
// sessions" says how many gave it, of the sessions Jev read; the title
// lists every answer they got. A tie is low-confidence, and says why.
function sessionsBehind(entry, answer) {
  const counts = entry?.counts;
  if (!counts || typeof counts !== "object") return null;
  const n = Object.values(counts).reduce((sum, v) => sum + (v || 0), 0);
  if (!n) return null;
  const k = counts[answer] || 0;
  const answers = Object.entries(counts)
    .filter(([, v]) => v > 0)
    .sort((a, b) => b[1] - a[1]);
  const all = answers
    .map(([label, v]) => `${answerText(label)} ${v}`)
    .join(" · ");
  return {
    text: `${k} of ${n} session${n === 1 ? "" : "s"}`,
    title: `Sessions read: ${all}`,
    tied: answers.some(
      ([label, v]) => label !== answer && !isFailure(label) && v === k,
    ),
    all,
  };
}

function AnswerCell({ record, question, isUser }) {
  const theme = useTheme();
  const { state, answer, unsure, confidence } = answerState(
    record,
    question.configId,
  );
  const behind =
    isUser && state === "answered" && !isFailure(answer)
      ? sessionsBehind(record.answers?.[question.configId], answer)
      : null;
  if (state === "waiting" || state === "reading")
    return (
      <Typography
        variant="caption"
        color="text.disabled"
        sx={
          state === "reading"
            ? {
                animation: `${pulse} 1.6s ease-in-out infinite`,
                "@media (prefers-reduced-motion: reduce)": {
                  animation: "none",
                },
              }
            : undefined
        }
      >
        {state === "reading" ? "Reading…" : "Not yet"}
      </Typography>
    );
  return (
    <Stack
      direction="row"
      spacing={0.75}
      alignItems="baseline"
      sx={{ flexWrap: "wrap" }}
    >
      <Box
        sx={{
          width: 8,
          height: 8,
          borderRadius: "2px",
          flexShrink: 0,
          bgcolor: answerColor(question, answer, theme),
          opacity: unsure ? 0.5 : 1,
        }}
      />
      <Typography
        variant="body2"
        fontWeight={600}
        title={answerText(answer)}
        color={state === "answered" ? "text.primary" : "text.secondary"}
        sx={{ lineHeight: 1.3, wordBreak: "break-word" }}
      >
        {answerText(answer)}
      </Typography>
      {behind && (
        <Typography
          variant="caption"
          color="text.secondary"
          title={behind.title}
          sx={{
            lineHeight: 1.3,
            whiteSpace: "nowrap",
            fontVariantNumeric: "tabular-nums",
          }}
        >
          · {behind.text}
        </Typography>
      )}
      {unsure && (
        <Tooltip
          title={
            behind?.tied
              ? `Tied across sessions (${behind.all}), so it counts as low-confidence`
              : `Jev was unsure (${percent(confidence)} confident)`
          }
        >
          <Box component="span" sx={{ display: "inline-flex" }}>
            <Iconify
              icon="mdi:help-circle-outline"
              width={14}
              sx={{ color: "warning.main" }}
              aria-label="Jev was unsure"
            />
          </Box>
        </Tooltip>
      )}
    </Stack>
  );
}

AnswerCell.propTypes = {
  record: PropTypes.object.isRequired,
  question: PropTypes.object.isRequired,
  isUser: PropTypes.bool,
};

/**
 * The rows behind the charts, newest first, a page at a time from the
 * server: when, who, what the row was about, and each answer. The filter
 * chip names what the charts selected; Export writes every selected row.
 * A user question lists users, each with the answer their sessions most
 * often got and how many did; it exports them, or their sessions
 * (`onExport({ sessions: true })`). An export under way (`exporting`:
 * "rows" or "sessions") shows on its button, and neither export takes a
 * click until it ends. A click opens the row in its view.
 */
export default function AskRowsTable({
  records,
  questions,
  unit,
  onOpen,
  filterLabel,
  onClearFilter,
  total,
  runTotal,
  loading,
  error,
  onRetry,
  hasMore,
  loadingMore,
  onLoadMore,
  onExport,
  exporting,
}) {
  const meta = unitMeta(unit);
  const isUser = unit === "users";
  const answerColumns = questions;
  const count = total ?? records.length;
  const heading =
    filterLabel && runTotal != null
      ? `${count.toLocaleString()} of ${runTotal.toLocaleString()} ${meta.plural}`
      : `${count.toLocaleString()} ${count === 1 ? meta.noun : meta.plural}`;
  // Nothing is counted yet while the first rows load: no "0 traces".
  const counting = loading && total == null && !records.length;
  const full = records.length >= TABLE_MAX_ROWS;

  return (
    <Box
      component="section"
      aria-label={`${meta.title} asked about`}
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        bgcolor: "background.paper",
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{ px: 2, pt: 1.75, pb: 1.25 }}
      >
        <Typography variant="subtitle2" component="h2" sx={{ fontSize: 15 }}>
          {meta.title}
        </Typography>
        {!counting && (
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ fontVariantNumeric: "tabular-nums" }}
          >
            {heading}
          </Typography>
        )}
        {filterLabel && (
          <Chip
            size="small"
            label={filterLabel}
            onDelete={onClearFilter}
            title={filterLabel}
            sx={{ maxWidth: 420 }}
          />
        )}
        <Box sx={{ flex: 1 }} />
        {onExport && (
          <Button
            size="small"
            color="inherit"
            startIcon={exportIcon(exporting === "rows")}
            onClick={() => onExport()}
            disabled={!count || Boolean(exporting)}
            title={isUser ? "A line per user" : undefined}
          >
            {exporting === "rows" ? "Exporting CSV…" : "Export CSV"}
          </Button>
        )}
        {onExport && isUser && (
          <Button
            size="small"
            color="inherit"
            startIcon={exportIcon(exporting === "sessions")}
            onClick={() => onExport({ sessions: true })}
            disabled={!count || Boolean(exporting)}
            title="A line per session read, for these users"
          >
            {exporting === "sessions"
              ? "Exporting sessions…"
              : "Export sessions"}
          </Button>
        )}
      </Stack>
      <Box sx={{ overflowX: "auto" }}>
        <Table
          size="small"
          sx={{
            tableLayout: "fixed",
            minWidth: tableMinWidth(answerColumns.length),
          }}
        >
          <TableHead>
            <TableRow>
              <TableCell sx={{ width: 132 }}>When</TableCell>
              {!isUser && <TableCell sx={{ width: 150 }}>User</TableCell>}
              <TableCell>
                {isUser
                  ? "User"
                  : `${meta.noun[0].toUpperCase()}${meta.noun.slice(1)}`}
              </TableCell>
              {answerColumns.map((question, index) => (
                <TableCell
                  key={question.configId || index}
                  sx={{
                    width: answerColumns.length > 1 ? ANSWER_COLUMN_WIDTH : 150,
                    whiteSpace: "nowrap",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                  }}
                  title={question.fullQuestion || question.question}
                >
                  {answerColumns.length > 1
                    ? `${index + 1} · ${question.question}`
                    : "Answer"}
                </TableCell>
              ))}
            </TableRow>
          </TableHead>
          <TableBody>
            {loading &&
              !records.length &&
              [0, 1, 2].map((key) => (
                <TableRow key={`loading-${key}`}>
                  <TableCell colSpan={3 + answerColumns.length}>
                    <Skeleton variant="text" />
                  </TableCell>
                </TableRow>
              ))}
            {records.slice(0, TABLE_MAX_ROWS).map((record) => (
              <TableRow
                key={record.id}
                hover
                onClick={() => onOpen(record.id)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") onOpen(record.id);
                }}
                tabIndex={0}
                sx={{ cursor: "pointer", verticalAlign: "top" }}
              >
                <TableCell
                  sx={{ color: "text.secondary", whiteSpace: "nowrap" }}
                >
                  {record.time ? formatWhen(record.time) : "–"}
                </TableCell>
                {!isUser && (
                  <TableCell>
                    <Typography variant="body2" noWrap title={record.user_id}>
                      {record.user_id || "–"}
                    </Typography>
                  </TableCell>
                )}
                <TableCell>
                  <Typography
                    variant="body2"
                    fontWeight={isUser ? 600 : 400}
                    sx={{
                      display: "-webkit-box",
                      WebkitLineClamp: 2,
                      WebkitBoxOrient: "vertical",
                      overflow: "hidden",
                      wordBreak: "break-word",
                    }}
                  >
                    {isUser
                      ? record.user_id || record.label || record.id
                      : record.excerpt || record.label || "Reading the row…"}
                  </Typography>
                  <Typography
                    variant="caption"
                    color="text.disabled"
                    noWrap
                    component="p"
                  >
                    {[
                      isUser && record.excerpt && `Latest: ${record.excerpt}`,
                      isUser &&
                        record.sessions != null &&
                        `${record.sessions} session${record.sessions === 1 ? "" : "s"}`,
                      facetLine(record.facets),
                    ]
                      .filter(Boolean)
                      .join(" · ")}
                  </Typography>
                </TableCell>
                {answerColumns.map((question, index) => (
                  <TableCell key={question.configId || index}>
                    <AnswerCell
                      record={record}
                      question={question}
                      isUser={isUser}
                    />
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Box>
      {!records.length && !loading && error && (
        <Box
          role="alert"
          sx={{ px: 2, py: 2.5, display: "flex", alignItems: "center", gap: 1 }}
        >
          <Typography variant="body2" color="error.main">
            These {meta.plural} couldn&apos;t be loaded.
          </Typography>
          {onRetry && (
            <Button size="small" onClick={() => onRetry()}>
              Retry
            </Button>
          )}
        </Box>
      )}
      {!records.length && !loading && !error && filterLabel && (
        // The selection hides every row: say so, and offer the way back
        // (the selection bar, with its Clear, shows only when rows match).
        <Box
          sx={{ px: 2, py: 2.5, display: "flex", alignItems: "center", gap: 1 }}
        >
          <Typography variant="body2" color="text.secondary">
            No {meta.plural} match this selection.
          </Typography>
          {onClearFilter && (
            <Button size="small" onClick={() => onClearFilter()}>
              Clear
            </Button>
          )}
        </Box>
      )}
      {!records.length && !loading && !error && !filterLabel && (
        <Typography
          variant="body2"
          color="text.secondary"
          sx={{ px: 2, py: 2.5 }}
        >
          No {meta.plural} match.
        </Typography>
      )}
      {hasMore && !full && (
        <Box sx={{ px: 2, py: 1 }}>
          <Button
            size="small"
            disabled={loadingMore}
            onClick={() => onLoadMore?.()}
            startIcon={
              loadingMore ? (
                <CircularProgress size={14} color="inherit" />
              ) : null
            }
          >
            Show {Math.min(50, count - records.length).toLocaleString()} more
            {" · "}
            {records.length.toLocaleString()} of {count.toLocaleString()} shown
          </Button>
        </Box>
      )}
      {hasMore && full && (
        <Typography
          variant="caption"
          color="text.secondary"
          component="p"
          sx={{ px: 2, py: 1.25 }}
        >
          Showing the first {TABLE_MAX_ROWS.toLocaleString()}. Export CSV for
          all {count.toLocaleString()}, or narrow them on the charts.
        </Typography>
      )}
    </Box>
  );
}

AskRowsTable.propTypes = {
  records: PropTypes.array.isRequired,
  questions: PropTypes.array.isRequired,
  unit: PropTypes.string.isRequired,
  onOpen: PropTypes.func.isRequired,
  filterLabel: PropTypes.string,
  onClearFilter: PropTypes.func,
  total: PropTypes.number,
  runTotal: PropTypes.number,
  loading: PropTypes.bool,
  error: PropTypes.bool,
  onRetry: PropTypes.func,
  hasMore: PropTypes.bool,
  loadingMore: PropTypes.bool,
  onLoadMore: PropTypes.func,
  onExport: PropTypes.func,
  exporting: PropTypes.oneOf(["rows", "sessions"]),
};
