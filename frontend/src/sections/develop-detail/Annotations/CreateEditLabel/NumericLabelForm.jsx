// @ts-nocheck
import { Box } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { FormSelectField } from "src/components/FormSelectField";
import { getNumericLabelDisplayTypes } from "./common";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";

const NumericLabelForm = ({ control, storedDisplayType }) => {
  return (
    <>
      <FormSelectField
        label="Display Options*"
        size="small"
        control={control}
        fieldName="settings.displayType"
        options={getNumericLabelDisplayTypes(storedDisplayType)}
        sx={{
          "& .MuiSvgIcon-root.MuiSelect-icon": {
            height: "20px",
            width: "20px",
          },
        }}
      />
      <Box sx={{ display: "flex", gap: 1 }}>
        <Box sx={{ flex: 1 }}>
          <FormTextFieldV2
            label="Min"
            size="small"
            control={control}
            required
            fieldName="settings.min"
            placeholder="Enter min value"
            fullWidth
            fieldType="number"
            allowNegative
          />
        </Box>
        <Box sx={{ flex: 1 }}>
          <FormTextFieldV2
            label="Max"
            size="small"
            control={control}
            required
            fieldName="settings.max"
            placeholder="Enter max value"
            fullWidth
            fieldType="number"
            allowNegative
          />
        </Box>
      </Box>
      <FormTextFieldV2
        label="Step Size"
        size="small"
        control={control}
        required
        fieldName="settings.stepSize"
        placeholder="Enter stepsize"
        fullWidth
        fieldType="number"
      />
    </>
  );
};

NumericLabelForm.propTypes = {
  control: PropTypes.any,
  storedDisplayType: PropTypes.string,
};

export default NumericLabelForm;
