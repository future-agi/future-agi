// @ts-nocheck
import { Box } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";

// Placeholder Text is required unless the label was stored without one.
const TextLabelForm = ({ control, placeholderRequired = true }) => {
  return (
    <>
      <FormTextFieldV2
        label="Placeholder Text"
        size="small"
        control={control}
        required={placeholderRequired}
        fieldName="settings.placeholder"
        placeholder="Type here"
      />
      <Box sx={{ display: "flex", gap: 1 }}>
        <Box sx={{ flex: 1 }}>
          <FormTextFieldV2
            label="Min"
            size="small"
            control={control}
            fieldName="settings.minLength"
            fullWidth
            required
            placeholder="Enter min value"
            fieldType="number"
          />
        </Box>
        <Box sx={{ flex: 1 }}>
          <FormTextFieldV2
            label="Max"
            size="small"
            control={control}
            placeholder="Enter max value"
            fieldName="settings.maxLength"
            fullWidth
            required
            fieldType="number"
          />
        </Box>
      </Box>
    </>
  );
};

TextLabelForm.propTypes = {
  control: PropTypes.any,
  placeholderRequired: PropTypes.bool,
};

export default TextLabelForm;
