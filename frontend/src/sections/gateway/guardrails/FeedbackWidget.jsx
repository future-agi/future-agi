import React, { useState } from "react";
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
import { useCreateGuardrailFeedback } from "./hooks/useGuardrailFeedback";

const FEEDBACK_OPTIONS = [
  { value: "correct", label: "Correct", color: "success" },
  { value: "false_positive", label: "False Positive", color: "warning" },
  { value: "false_negative", label: "False Negative", color: "error" },
  { value: "unsure", label: "Unsure", color: "default" },
];

const FeedbackWidget = ({ requestLogId, checkName, existingFeedback }) => {
  const [expanded, setExpanded] = useState(false);
  const [feedback, setFeedback] = useState(existingFeedback?.feedback || "");
  const [comment, setComment] = useState(existingFeedback?.comment || "");
  const [selectedCheck, setSelectedCheck] = useState(checkName || "");
  const createMutation = useCreateGuardrailFeedback();
  const [submitted, setSubmitted] = useState(Boolean(existingFeedback));

  const handleSubmit = () => {
    if (!feedback || !requestLogId) return;
    createMutation.mutate(
      {
        request_log: requestLogId,
        check_name: selectedCheck,
        feedback,
        comment,
      },
      {
        onSuccess: () => {
          enqueueSnackbar("Feedback submitted", { variant: "success" });
          setSubmitted(true);
          setExpanded(false);
        },
        onError: () =>
          enqueueSnackbar("Failed to submit feedback", { variant: "error" }),
      },
    );
  };

  if (submitted && !expanded) {
    return (
      <Stack direction="row" spacing={1} alignItems="center">
        <Iconify
          icon="solar:check-circle-bold"
          width={20}
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
              disabled={!feedback || createMutation.isPending}
            >
              {createMutation.isPending ? "Submitting..." : "Submit Feedback"}
            </Button>
            <Button size="small" onClick={() => setExpanded(false)}>
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
