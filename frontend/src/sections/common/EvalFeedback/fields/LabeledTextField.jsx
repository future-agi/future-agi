import React from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import { fieldLabelSx } from "./labelSx";

const LabeledTextField = ({ label, ...rest }) => {
  return (
    <Box sx={{ width: "100%" }}>
      {label && <Typography sx={fieldLabelSx}>{label}</Typography>}
      <FormTextFieldV2
        {...rest}
        fullWidth
        hiddenLabel
        sx={{ border: "1px solid var(--border-default)", borderRadius: "8px" }}
      />
    </Box>
  );
};

LabeledTextField.propTypes = {
  label: PropTypes.string,
};

export default LabeledTextField;
