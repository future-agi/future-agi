import React from "react";
import { Box, Button, Typography } from "@mui/material";
import { LoadingButton } from "@mui/lab";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import PropTypes from "prop-types";

import { FeedBackForm } from "./AddEvaluationFeeback";
import { feedbackFormSchema } from "./validation";
import { FEEDBACK_OUTPUT_TYPES as OUTPUT } from "./feedback_value";
import logger from "src/utils/logger";

/**
 * Backend-free harness for the eval-feedback drawer body. Mirrors the real
 * EvaluationFeeback wrapper (schema, single-page submit) but logs the payload
 * instead of calling the API — so the UI can be exercised in Storybook with
 * no server.
 */
const Harness = ({ feedbackData, data }) => {
  const outputType = feedbackData?.output_type;
  const isMulti =
    outputType === OUTPUT.CHOICES && Boolean(feedbackData?.multi_choice);

  const { control, handleSubmit } = useForm({
    defaultValues: { value: isMulti ? [] : "", explanation: "", actionType: "" },
    resolver: zodResolver(feedbackFormSchema),
  });

  const onSubmit = (formData) => {
    logger.debug("[feedback-story] submitted", formData);
  };

  return (
    <Box
      component="form"
      onSubmit={handleSubmit(onSubmit)}
      sx={{
        width: 600,
        p: 2.5,
        display: "flex",
        flexDirection: "column",
        gap: 2,
        border: "1px solid var(--border-default)",
        borderRadius: "10px",
      }}
    >
      <Typography fontWeight={700} color="text.primary">
        Add feedback
      </Typography>
      <div style={{ borderBottom: "1px solid var(--border-light)" }} />
      <FeedBackForm
        control={control}
        data={data}
        feedbackData={feedbackData}
        outputType={outputType}
        isMulti={isMulti}
      />
      <Box display="flex" gap={1} justifyContent="flex-end">
        <Button
          variant="outlined"
          color="inherit"
          size="small"
          sx={{ minWidth: 160 }}
        >
          Cancel
        </Button>
        <LoadingButton
          variant="contained"
          color="primary"
          type="submit"
          size="small"
          sx={{ minWidth: 160 }}
        >
          Submit feedback
        </LoadingButton>
      </Box>
    </Box>
  );
};

Harness.propTypes = {
  feedbackData: PropTypes.object,
  data: PropTypes.object,
};

const reason = (text) => ({ valueInfos: { reason: text } });

export default {
  title: "Develop/Eval Feedback Drawer",
  component: FeedBackForm,
  parameters: { layout: "centered" },
};

// Pass/Fail — the eval returned "Passed"; the form renders both choices so the
// reviewer can pick the corrected value.
export const PassFail = {
  render: () => (
    <Harness
      feedbackData={{
        output_type: "Pass/Fail",
        choices: ["Passed", "Failed"],
        eval_name: "ground_truth_match",
      }}
      data={{
        name: "ground_truth_match",
        cell_value: "Passed",
        ...reason(
          "The generated answer matched the expected ground-truth value for this row.",
        ),
      }}
    />
  ),
};

// Multi-choice — renders checkboxes (because multi_choice is true); the current
// value ["Billing"] is pre-selected.
export const MultiChoice = {
  render: () => (
    <Harness
      feedbackData={{
        output_type: "choices",
        multi_choice: true,
        choices: ["Billing", "Technical", "Account", "Other"],
        eval_name: "ticket_category",
      }}
      data={{
        name: "ticket_category",
        cell_value: '["Billing"]',
        ...reason("The ticket was categorised as Billing."),
      }}
    />
  ),
};

// Single-choice — renders radios (multi_choice false); current value "A" is
// pre-selected.
export const SingleChoice = {
  render: () => (
    <Harness
      feedbackData={{
        output_type: "choices",
        multi_choice: false,
        choices: ["A", "B", "C"],
        eval_name: "category_match",
      }}
      data={{
        name: "category_match",
        cell_value: "A",
        ...reason("Classified as category A."),
      }}
    />
  ),
};

// Numeric — renders a numeric input seeded with the current value 59;
// non-numeric input shows a "Numbers only" hint.
export const Score = {
  render: () => (
    <Harness
      feedbackData={{ output_type: "score", eval_name: "bleu_score" }}
      data={{
        name: "bleu_score",
        cell_value: 59,
        ...reason("Similarity score 0.59, above the 0.5 threshold."),
      }}
    />
  ),
};

// Free-text right value.
export const Text = {
  render: () => (
    <Harness
      feedbackData={{ output_type: "reason", eval_name: "tone_check" }}
      data={{
        name: "tone_check",
        ...reason("The tone was assessed as professional throughout."),
      }}
    />
  ),
};
