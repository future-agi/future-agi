import React from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import LabeledRadioField from "./fields/LabeledRadioField";

// Stage 2 of the eval-feedback drawer. Renders the confirmation summary +
// a radio group of caller-supplied retune options.
//
// `retuneOptions` shape:
//   Array<{
//     value: string,                         // sent as action_type to the BE
//     title: string,                         // bold header line
//     description: string,                   // grey body line
//     disabled?: boolean,                    // greys the radio out
//     disabledTooltip?: string,              // shown when disabled
//   }>
//
// Title + description are wrapper-specific copy. Develop says "for this row /
// for this dataset"; Observe says "for this span / for this eval run".

const containerSx = {
  gap: 2,
  display: "flex",
  flexDirection: "column",
  flex: 1,
  overflow: "auto",
  paddingBottom: "10px",
};

const summaryBoxSx = {
  padding: "12px",
  backgroundColor: "background.neutral",
  borderRadius: "12px",
};

const OptionLabel = ({ title, description }) => (
  <Box>
    <Typography sx={{ fontSize: "14px", fontWeight: "600" }}>
      {title}
    </Typography>
    <Typography sx={{ fontSize: "12px" }}>{description}</Typography>
  </Box>
);

OptionLabel.propTypes = {
  title: PropTypes.string.isRequired,
  description: PropTypes.string.isRequired,
};

const EvalFeedbackActionStage = ({ control, target, retuneOptions }) => {
  return (
    <Box sx={containerSx}>
      <Typography
        sx={{ fontSize: "18px", fontWeight: "600", lineHeight: "26px" }}
      >
        Your feedback is submitted.
      </Typography>

      <Box sx={summaryBoxSx}>
        <Typography sx={{ fontSize: "14px", fontWeight: "600" }}>
          {target?.name}
        </Typography>
        <Typography sx={{ fontSize: "12px" }}>
          {target?.value_infos?.reason}
        </Typography>
        <Typography sx={{ fontSize: "12px", fontWeight: "600" }}>
          1 row received your feedback.
        </Typography>
      </Box>

      <Typography
        sx={{ fontSize: "16px", fontWeight: "600", lineHeight: "21px" }}
      >
        Select one of the options
      </Typography>

      <LabeledRadioField
        control={control}
        fieldName="value"
        label=""
        options={retuneOptions.map((opt) => ({
          value: opt.value,
          label: <OptionLabel title={opt.title} description={opt.description} />,
        }))}
      />
    </Box>
  );
};

EvalFeedbackActionStage.propTypes = {
  control: PropTypes.any.isRequired,
  target: PropTypes.shape({
    name: PropTypes.string,
    value_infos: PropTypes.shape({ reason: PropTypes.string }),
  }),
  retuneOptions: PropTypes.arrayOf(
    PropTypes.shape({
      value: PropTypes.string.isRequired,
      title: PropTypes.string.isRequired,
      description: PropTypes.string.isRequired,
    })
  ).isRequired,
};

export default EvalFeedbackActionStage;
