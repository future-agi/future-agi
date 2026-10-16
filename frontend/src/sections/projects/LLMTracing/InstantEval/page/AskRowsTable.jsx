import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Chip,
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
import { answerText } from "../instantEvalAnswers";
import { formatWhen, unitMeta } from "../instantEvalUnits";
import { answerColor } from "../results/answerColors";
import { answerState } from "./viz";
import { facetLine } from "./askFormat";

const PAGE = 50;
// More questions than this share one column.
const MAX_ANSWER_COLUMNS = 3;

const pulse = keyframes`
  0%, 100% { opacity: 0.35; }
  50% { opacity: 1; }
`;

const percent = (value) => `${Math.round(value * 100)}%`;

function AnswerCell({ record, question }) {
  const theme = useTheme();
  const { state, answer, unsure, confidence } = answerState(
    record,
    question.configId,
  );
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
    <Stack direction="row" spacing={0.75} alignItems="center">
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
        noWrap
        color={state === "answered" ? "text.primary" : "text.secondary"}
      >
        {answerText(answer)}
      </Typography>
      {unsure && (
        <Tooltip title={`Jev was unsure (${percent(confidence)} confident)`}>
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
};

/**
 * The rows behind the charts, newest first: when, who, what the row was
 * about, and each answer. A click opens the row in its view.
 */
export default function AskRowsTable({
  records,
  questions,
  unit,
  onOpen,
  filterLabel,
  onClearFilter,
  total,
}) {
  const [shown, setShown] = useState(PAGE);
  const meta = unitMeta(unit);
  const isUser = unit === "users";
  const answerColumns = questions.slice(0, MAX_ANSWER_COLUMNS);
  const heading = filterLabel
    ? `${records.length.toLocaleString()} of ${total.toLocaleString()} ${meta.plural}`
    : `${records.length.toLocaleString()} ${records.length === 1 ? meta.noun : meta.plural}`;

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
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ fontVariantNumeric: "tabular-nums" }}
        >
          {heading}
        </Typography>
        {filterLabel && (
          <Chip
            size="small"
            label={filterLabel}
            onDelete={onClearFilter}
            sx={{ maxWidth: 420 }}
          />
        )}
      </Stack>
      <Box sx={{ overflowX: "auto" }}>
        <Table size="small" sx={{ tableLayout: "fixed", minWidth: 760 }}>
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
                  sx={{ width: answerColumns.length > 1 ? 120 : 150 }}
                  title={question.question}
                >
                  {answerColumns.length > 1 ? `Answer ${index + 1}` : "Answer"}
                </TableCell>
              ))}
            </TableRow>
          </TableHead>
          <TableBody>
            {records.slice(0, shown).map((record) => (
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
                    <AnswerCell record={record} question={question} />
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Box>
      {!records.length && (
        <Typography
          variant="body2"
          color="text.secondary"
          sx={{ px: 2, py: 2.5 }}
        >
          No {meta.plural} match.
        </Typography>
      )}
      {records.length > shown && (
        <Box sx={{ px: 2, py: 1 }}>
          <Button
            size="small"
            onClick={() => setShown((count) => count + PAGE)}
          >
            Show {Math.min(PAGE, records.length - shown)} more
          </Button>
        </Box>
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
  total: PropTypes.number.isRequired,
};
