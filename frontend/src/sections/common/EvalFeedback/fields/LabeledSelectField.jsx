import React from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import { FormSelectField } from "src/components/FormSelectField";
import { fieldLabelSx } from "./labelSx";

const selectFieldSx = {
  backgroundColor: "rgba(147, 143, 163, 0.08)",
  "& .MuiOutlinedInput-root": {
    "&:hover .MuiOutlinedInput-notchedOutline": {
      border: "1px solid var(--border-default)",
    },
    "&.Mui-focused .MuiOutlinedInput-notchedOutline": {
      border: "1px solid var(--border-default)",
    },
  },
  "& .MuiOutlinedInput-notchedOutline": {
    border: "1px solid var(--border-default)",
  },
  "& .MuiSelect-select": {
    border: "1px solid var(--border-default)",
  },
};

const LabeledSelectField = ({ label, ...rest }) => {
  return (
    <Box sx={{ width: "100%" }}>
      {label && <Typography sx={fieldLabelSx}>{label}</Typography>}
      <FormSelectField {...rest} fullWidth sx={selectFieldSx} />
    </Box>
  );
};

LabeledSelectField.propTypes = {
  label: PropTypes.string,
};

export default LabeledSelectField;
