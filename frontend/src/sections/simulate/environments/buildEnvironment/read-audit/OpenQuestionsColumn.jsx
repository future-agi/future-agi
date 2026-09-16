import PropTypes from "prop-types";
import { Box } from "@mui/material";

import { BUILD_TONES } from "../buildTones";
import { READ_AUDIT_COPY, openQuestionsSubtitle } from "../readAudit.constants";
import SectionHead from "./SectionHead";
import AskUserQuestionCard from "./AskUserQuestionCard";
import { isResolved } from "./readAuditReducer";

// The read-audit's right column: the open-questions head over the one-at-a-time
// AskUserQuestionCard. Ported from the designer's AgentReadReceipt.jsx right
// column (lines 234–280), with the answer state lifted into the reducer and the
// accent hexes taken from BUILD_TONES.
export default function OpenQuestionsColumn({
  questions,
  activeIdx,
  answers,
  openCount,
  resolvedCount,
  skippedCount,
  dispatch,
  onBuild,
}) {
  const total = questions.length;
  const question = questions[activeIdx];
  const answer = answers[question.id];

  return (
    <Box
      sx={{
        minWidth: 0, overflowY: "auto", px: { xs: 3, md: 4 }, py: 3,
        borderLeft: { lg: "1px solid" }, borderColor: { lg: "divider" },
      }}
    >
      <SectionHead
        icon="solar:question-circle-linear"
        title={READ_AUDIT_COPY.questionsTitle}
        subtitle={openQuestionsSubtitle(openCount, total)}
        count={total}
        accent={openCount > 0 ? BUILD_TONES.red : BUILD_TONES.green}
      />
      <AskUserQuestionCard
        step={activeIdx}
        total={total}
        question={question}
        answer={answer}
        onPick={(idx) => {
          dispatch({ type: "pick", id: question.id, idx });
          // Auto-advance on a real choice — but not for "Other" (the user still
          // has to type) and not on the last question (it becomes "Build").
          const optionCount = (question?.options
            || (question?.kind === "boolean" ? [0, 1] : [])).length;
          const pickedOther = idx === optionCount;
          const isLastQ = activeIdx === total - 1;
          if (!pickedOther && !isLastQ) dispatch({ type: "next", total });
        }}
        onOtherChange={(text) => dispatch({ type: "other", id: question.id, text })}
        onBack={activeIdx > 0 ? () => dispatch({ type: "back" }) : null}
        onSkip={() => {
          dispatch({ type: "skip", id: question.id });
          if (activeIdx < total - 1) dispatch({ type: "next", total });
        }}
        onNext={() => dispatch({ type: "next", total })}
        onBuild={() => onBuild?.(answers)}
        canSubmit={isResolved(answer)}
        isLast={activeIdx === total - 1}
        skipped={answer?.skipped}
        openCount={openCount}
        resolvedCount={resolvedCount}
        skippedCount={skippedCount}
      />
    </Box>
  );
}

OpenQuestionsColumn.propTypes = {
  questions: PropTypes.arrayOf(PropTypes.shape({
    id: PropTypes.string,
    kind: PropTypes.string,
    title: PropTypes.string,
    why: PropTypes.string,
    options: PropTypes.arrayOf(PropTypes.shape({
      id: PropTypes.string,
      label: PropTypes.string,
      description: PropTypes.string,
    })),
  })),
  activeIdx: PropTypes.number,
  answers: PropTypes.objectOf(PropTypes.shape({
    pick: PropTypes.number,
    other: PropTypes.string,
    skipped: PropTypes.bool,
  })),
  openCount: PropTypes.number,
  resolvedCount: PropTypes.number,
  skippedCount: PropTypes.number,
  dispatch: PropTypes.func,
  onBuild: PropTypes.func,
};
