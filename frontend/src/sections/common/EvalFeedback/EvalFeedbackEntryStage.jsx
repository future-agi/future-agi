import React from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import CellMarkdown from "src/sections/common/CellMarkdown";
import LabeledTextField from "./fields/LabeledTextField";
import LabeledRadioField from "./fields/LabeledRadioField";
import LabeledSelectField from "./fields/LabeledSelectField";
import { OUTPUT_TYPES } from "./constants";

// Stage 1 of the eval-feedback drawer. Renders the target's existing
// explanation (read-only), the output-type-conditional value input, and the
// "what would you like to improve" textarea.
//
// All form interactions go through the `control` prop from useEvalFeedbackFlow.
//
// All keys in `templateData` and `target` are read in their snake_case BE
// wire shape — no FE-side normalization layer.
//
// `templateData` shape:
//   { output_type: "reason" | "score" | "Pass/Fail" | "choices" | "select",
//     choices?: string[] }
// `target` shape:
//   { name, value_infos?: { reason } }

const containerSx = {
  gap: 2,
  display: "flex",
  flexDirection: "column",
  flex: 1,
  overflow: "auto",
  paddingBottom: "10px",
};

const explanationBoxSx = {
  border: "1px solid var(--border-default)",
  bgcolor: "rgba(147, 143, 163, 0.08)",
  borderRadius: 1,
  padding: 1.5,
};

const ValueInput = ({ outputType, choices, control }) => {
  if (outputType === OUTPUT_TYPES.REASON) {
    return (
      <LabeledTextField
        label="Write a right value"
        placeholder="Improve the tone and grammar of the prompt"
        size="small"
        control={control}
        fieldName="value"
        variant="filled"
        multiline
        rows={3}
      />
    );
  }

  if (outputType === OUTPUT_TYPES.SCORE) {
    return (
      <LabeledTextField
        label="Write a right value"
        placeholder="Add Number"
        size="small"
        control={control}
        fieldName="value"
        variant="filled"
        type="number"
        inputProps={{ min: 0, max: 100 }}
        helperText="Enter a number between 0 and 100"
      />
    );
  }

  if (
    outputType === OUTPUT_TYPES.PASS_FAIL ||
    outputType === OUTPUT_TYPES.CHOICES
  ) {
    return (
      <LabeledRadioField
        label="Select a right value"
        control={control}
        fieldName="value"
        options={(choices ?? []).map((value) => ({ label: value, value }))}
      />
    );
  }

  if (outputType === OUTPUT_TYPES.SELECT) {
    // TODO: the original implementation hardcoded a single { value: "user",
    // label: "User" } option and never wired up the dropdown source. Left as
    // a no-op until a follow-up plumbs real options through templateData.
    return (
      <LabeledSelectField
        label="Select a right value"
        control={control}
        options={[{ value: "user", label: "User" }]}
        fieldName="value"
        fullWidth
      />
    );
  }

  return null;
};

ValueInput.propTypes = {
  outputType: PropTypes.string,
  choices: PropTypes.array,
  control: PropTypes.any.isRequired,
};

const EvalFeedbackEntryStage = ({ control, target, templateData }) => {
  return (
    <Box sx={containerSx}>
      <Typography
        sx={{ fontSize: "18px", fontWeight: "600", lineHeight: "26px" }}
      >
        {target?.name}
      </Typography>
      <Typography
        sx={{ fontSize: "14px", fontWeight: "400", lineHeight: "21px" }}
      >
        Help us refine {target?.name ?? "this eval"}. Share any issues, and
        we’ll use your feedback to improve it automatically.
      </Typography>

      <Box sx={explanationBoxSx}>
        <CellMarkdown spacing={0} text={target?.value_infos?.reason} />
      </Box>

      <div style={{ borderBottom: "1px solid var(--border-light)" }} />

      <ValueInput
        outputType={templateData?.output_type}
        choices={templateData?.choices}
        control={control}
      />

      <LabeledTextField
        label="What would you like to improve?"
        placeholder="Enter what would you like to improve in the prompt"
        size="small"
        control={control}
        fieldName="explanation"
        variant="filled"
        multiline
        rows={6}
      />
    </Box>
  );
};

EvalFeedbackEntryStage.propTypes = {
  control: PropTypes.any.isRequired,
  target: PropTypes.shape({
    name: PropTypes.string,
    value_infos: PropTypes.shape({ reason: PropTypes.string }),
  }),
  templateData: PropTypes.shape({
    output_type: PropTypes.string,
    choices: PropTypes.array,
  }),
};

export default EvalFeedbackEntryStage;
