import React, { useDeferredValue, useMemo, useState } from "react";
import PropTypes from "prop-types";
import { MenuItem, TextField, Typography } from "@mui/material";
import { formatCount } from "./askAnswers";
import { buildMatrix } from "./askPivot";
import AnswerMatrix from "./AnswerMatrix";
import { VizCard, quoted, rowsNoun, useRunData } from "./vizParts";

/**
 * One question's answers against another's, for a run with two or more:
 * each row (an answer to the question in focus) split by the other
 * question's answers, as row shares with counts, the cells where a row has
 * clearly more of an answer outlined and said in words. Click a cell to see
 * the rows that gave both answers.
 */
export default function AskCrossTab({
  summary: given,
  records,
  questions: questionList,
  focus,
  onSelect,
  running,
  window,
  unit: unitProp,
}) {
  const { summary, questions, sample, unit } = useRunData({
    summary: given,
    records,
    questions: questionList,
    window,
    unit: unitProp,
    running,
    defer: true,
  });
  const [againstId, setAgainstId] = useState(null);
  const shownFocus = useDeferredValue(focus);
  const question =
    questions.find((q) => q.configId === shownFocus?.configId) || questions[0];
  const matrix = useMemo(
    () =>
      question && questions.length > 1
        ? buildMatrix({
            summary,
            questions,
            question,
            pivot: "questions",
            againstId,
          })
        : null,
    [summary, questions, question, againstId],
  );
  if (!question || questions.length < 2 || !matrix?.against) return null;
  const plural = rowsNoun(unit, sample, 2);
  const answered = matrix.rows.reduce((sum, row) => sum + row.answered, 0);

  return (
    <VizCard
      title="How the answers line up"
      label="Question against question"
      caption={`${quoted(question.question)} against ${quoted(matrix.against.question)} · ${formatCount(answered)} ${plural} answered both · click a cell for its ${plural}`}
      action={
        questions.length > 2 && (
          <TextField
            select
            size="small"
            label="Against"
            value={matrix.against.configId}
            onChange={(event) => setAgainstId(event.target.value)}
            sx={{ width: 260 }}
          >
            {questions
              .filter((q) => q.configId !== question.configId)
              .map((q) => (
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
      {answered ? (
        <AnswerMatrix
          matrix={{ ...matrix, group: false }}
          question={question}
          colorQuestion={matrix.against}
          onSelect={onSelect}
          unit={unit}
          sample={sample}
        />
      ) : (
        <Typography
          variant="body2"
          color="text.secondary"
          sx={{ fontSize: 13 }}
        >
          Both questions need answers before they can be compared.
        </Typography>
      )}
    </VizCard>
  );
}

AskCrossTab.propTypes = {
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
