import { Box, FormHelperText } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import Iconify from "src/components/iconify";

const ErrorMessage = ({ isError, errorMessage }) => {
  return (
    <FormHelperText error={isError} sx={{ display: "flex", gap: "4px" }}>
      <Iconify icon="solar:danger-triangle-linear" />
      <Box>{errorMessage}</Box>
    </FormHelperText>
  );
};

export default ErrorMessage;

ErrorMessage.propTypes = {
  isError: PropTypes.bool,
  errorMessage: PropTypes.string,
};
