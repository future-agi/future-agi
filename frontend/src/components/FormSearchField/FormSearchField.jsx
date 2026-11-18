import { InputAdornment, TextField } from "@mui/material";
import React from "react";
import Iconify from "../iconify";
import PropTypes from "prop-types";

const FormSearchField = ({
  searchQuery,
  onChange,
  sx,
  InputProps = {},
  ...rest
}) => {
  const defaultInputProps = {
    startAdornment: (
      <InputAdornment position="start">
        <Iconify
          icon="solar:magnifer-linear"
          width={16}
          sx={{ color: "text.secondary" }}
        />
      </InputAdornment>
    ),
  };

  // Merge InputProps, allowing custom InputProps to override defaults
  const mergedInputProps = {
    ...defaultInputProps,
    ...InputProps,
  };

  return (
    <TextField
      size="small"
      placeholder="Search"
      value={searchQuery}
      onChange={onChange}
      sx={sx}
      InputProps={mergedInputProps}
      {...rest}
    />
  );
};

FormSearchField.propTypes = {
  searchQuery: PropTypes.string,
  onChange: PropTypes.func,
  sx: PropTypes.object,
  InputProps: PropTypes.object,
};

export default FormSearchField;
