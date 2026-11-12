import { Box, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import FormTextFieldV2 from "../FormTextField/FormTextFieldV2";

const ConversationForm = ({ control, errors }) => {
  return (
    <Box
      py={4}
      sx={{
        display: "flex",
        flexDirection: "column",
        gap: 2,
      }}
    >
      <Box
        sx={{
          display: "flex",
          flexDirection: "row",
          gap: 2,
        }}
      >
        <FormTextFieldV2
          control={control}
          placeholder={"Enter duration"}
          label={
            <Typography typography={"s1"} fontWeight={600}>
              Max call duration (in mins.)
            </Typography>
          }
          onKeyDown={(e) => {
            if (e.target.value === "-" || e.target.value === "e") {
              e.preventDefault();
            }
          }}
          inputProps={{ min: 0 }}
          type="number"
          fieldName={"maxCallDurationInMinutes"}
          fullWidth
          size={"small"}
          helperText={""}
          error={errors.name}
        />
        <FormTextFieldV2
          control={control}
          placeholder={"Enter duration"}
          label={
            <Typography typography={"s1"} fontWeight={600}>
              Initial message delay (in sec)
            </Typography>
          }
          onKeyDown={(e) => {
            if (e.target.value === "-" || e.target.value === "e") {
              e.preventDefault();
            }
          }}
          inputProps={{ min: 0 }}
          type="number"
          fieldName={"initialMessageDelay"}
          fullWidth
          size={"small"}
          helperText={""}
          error={errors.initialMessageDelay}
        />
      </Box>
      <FormTextFieldV2
        control={control}
        placeholder={"Enter message"}
        fieldName={"initialMessage"}
        label={
          <Typography typography={"s1"} fontWeight={600}>
            Initial Message
          </Typography>
        }
        multiline
        rows={12}
        maxRows={12}
      />
    </Box>
  );
};

export default ConversationForm;

ConversationForm.propTypes = {
  control: PropTypes.object,
  errors: PropTypes.object,
};
