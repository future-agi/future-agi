import { Box, Button, FormHelperText } from "@mui/material";
import React from "react";
import PropTypes from "prop-types";
import { useController } from "react-hook-form";

export const MetricOptionType = ({ control, fieldName }) => {
  const {
    formState,
    field: { onChange, value },
  } = useController({ name: fieldName, control });
  const isError = Boolean(formState.errors?.["datasets"]?.message);

  return (
    <Box
      sx={{ backgroundColor: "action.hover" }}
      className="define-metric-bottom-toolbar"
    >
      <Box className="define-metric-bottom-toolbar-container">
        <Box sx={{ display: "flex", gap: 2 }}>
          <Button
            variant="soft"
            size="small"
            onClick={() => onChange(1)}
            color={value === 1 ? "primary" : "inherit"}
          >
            Whole conversation
          </Button>
          <Button
            variant="soft"
            size="small"
            onClick={() => onChange(2)}
            color={value === 2 ? "primary" : "inherit"}
          >
            Step by step outputs
          </Button>
        </Box>
        {isError && (
          <FormHelperText error={true}>
            {formState.errors?.["datasets"]?.message}
          </FormHelperText>
        )}
      </Box>
    </Box>
  );
};

MetricOptionType.propTypes = {
  control: PropTypes.any,
  fieldName: PropTypes.string.isRequired,
};
