import React, { useEffect, useState } from "react";
import {
  Box,
  Typography,
  Stack,
  TextField,
  Button,
  Chip,
  Collapse,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { enqueueSnackbar } from "notistack";
import PropTypes from "prop-types";
import {
  useCreateGuardrailFeedback,
  useMyGuardrailFeedback,
  useUpdateGuardrailFeedback,
} from "./hooks/useGuardrailFeedback";

const FEEDBACK_OPTIONS = [
  { value: "correct", label: "Correct", color: "success" },
  { value: "false_positive", label: "False Positive", color: "warning" },
  { value: "false_negative", label: "False Negative", color: "error" },
  { value: "unsure", label: "Unsure", color: "default" },
];

// One reviewer's answer on one guardrail check of one request: the
// widget loads the answer this reviewer saved, shows it, and Edit changes
// that answer (PATCH) instead of adding another.
const FeedbackWidget = ({ requestLogId, checkName, existingFeedback }) => {
  const [expanded, setExpanded] = useState(false);
  const [feedback, setFeedback] = useState(existingFeedback?.feedback || "");
  const [comment, setComment] = useState(existingFeedback?.comment || "");
  const [selectedCheck, setSelectedCheck] = useState(checkName || "");
  const [submitted, setSubmitted] = useState(Boolean(existingFeedback));
  const [savedId, setSavedId] = useState(existingFeedback?.id || null);
  // The answer as last saved, which Cancel goes back to.
  const [savedAnswer, setSavedAnswer] = useState({
    feedback: existingFeedback?.feedback || "",
    comment: existingFeedback?.comment || "",
  });
  const createMutation = useCreateGuardrailFeedback();
  const updateMutation = useUpdateGuardrailFeedback();
  const { answer } = useMyGuardrailFeedback(requestLogId, checkName);

  // Show the saved answer once it loads, unless the reviewer is editing.
  useEffect(() => {
    if (!answer || expanded) return;
    setSavedId(answer.id);
    setFeedback(answer.feedback || "");
    setComment(answer.comment || "");
    setSavedAnswer({
      feedback: answer.feedback || "",
      comment: answer.comment || "",
    });
    setSubmitted(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [answer?.id, answer?.feedback, answer?.comment]);

  const isPending = createMutation.isPending || updateMutation.isPending;

  const handleSubmit = () => {
    if (!feedback || !requestLogId) return;
    const onSuccess = (result) => {
      if (result?.id) setSavedId(result.id);
      setSavedAnswer({ feedback, comment });
      enqueueSnackbar(savedId ? "Feedback updated" : "Feedback submitted", {
        variant: "success",
      });
      setSubmitted(true);
      setExpanded(false);
    };
    const onError = () =>
      enqueueSnackbar("Failed to submit feedback", { variant: "error" });

    if (savedId) {
      updateMutation.mutate(
        { id: savedId, feedback, comment },
        { onSuccess, onError },
      );
      return;
    }
    createMutation.mutate(
      {
        request_log: requestLogId,
        check_name: selectedCheck,
        feedback,
        comment,
      },
      { onSuccess, onError },
    );
  };

  if (submitted && !expanded) {
    return (
      <Stack direction="row" spacing={1} alignItems="center">
        <Iconify
          icon="mdi:check-circle"
          width={18}
          sx={{ color: "success.main" }}
        />
        <Typography variant="caption" color="success.main">
          Feedback:{" "}
          {FEEDBACK_OPTIONS.find((f) => f.value === feedback)?.label ||
            feedback}
        </Typography>
        <Button
          size="small"
          onClick={() => {
            setExpanded(true);
            setSubmitted(false);
          }}
        >
          Edit
        </Button>
      </Stack>
    );
  }

  return (
    <Box>
      <Stack direction="row" spacing={1} alignItems="center">
        <Typography variant="caption" color="text.secondary">
          Guardrail Feedback:
        </Typography>
        {FEEDBACK_OPTIONS.map((opt) => (
          <Chip
            key={opt.value}
            label={opt.label}
            color={feedback === opt.value ? opt.color : "default"}
            variant={feedback === opt.value ? "filled" : "outlined"}
            size="small"
            onClick={() => {
              setFeedback(opt.value);
              setExpanded(true);
            }}
            sx={{ cursor: "pointer" }}
          />
        ))}
      </Stack>

      <Collapse in={expanded}>
        <Stack spacing={1.5} sx={{ mt: 1.5 }}>
          {!checkName && (
            <TextField
              size="small"
              label="Check Name"
              value={selectedCheck}
              onChange={(e) => setSelectedCheck(e.target.value)}
              placeholder="e.g. pii_detection, toxicity"
            />
          )}
          <TextField
            size="small"
            label="Comment (optional)"
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            multiline
            rows={2}
            placeholder="Describe why this is correct/incorrect..."
          />
          <Stack direction="row" spacing={1}>
            <Button
              size="small"
              variant="contained"
              onClick={handleSubmit}
              disabled={!feedback || isPending}
            >
              {isPending ? "Submitting..." : "Submit Feedback"}
            </Button>
            <Button
              size="small"
              onClick={() => {
                setExpanded(false);
                if (savedId) {
                  setFeedback(savedAnswer.feedback);
                  setComment(savedAnswer.comment);
                  setSubmitted(true);
                }
              }}
            >
              Cancel
            </Button>
          </Stack>
        </Stack>
      </Collapse>
    </Box>
  );
};

FeedbackWidget.propTypes = {
  requestLogId: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  checkName: PropTypes.string,
  existingFeedback: PropTypes.object,
};

export default FeedbackWidget;
