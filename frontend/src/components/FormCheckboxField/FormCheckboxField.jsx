import {
  Box,
  Checkbox,
  FormControl,
  FormControlLabel,
  FormHelperText,
  Typography,
  useTheme,
} from "@mui/material";
import _ from "lodash";
import PropTypes from "prop-types";
import React from "react";
import { Controller } from "react-hook-form";
import Iconify from "../iconify";

/**
 * A wrapper component for displaying MUI Checkbox with react-hook-form
 */
export const FormCheckboxField = ({
  isCustom = false,
  control,
  fieldName,
  label,
  labelPlacement = "start",
  helperText,
  defaultValue,
  size = "medium",
  ...props
}) => {
  const theme = useTheme();

  return (
    <Controller
      render={({ field: { onChange, value }, formState: { errors } }) => {
        const errorMessage = _.get(errors, `${fieldName}.message`);
        const isError = !!errorMessage;

        const handleToggle = (event) => {
          const newValue = !value;
          onChange(newValue);
          props.onChange?.(event);
        };

        return (
          <FormControl>
            <Box
              sx={{
                ...(isCustom && {
                  display: "flex",
                  flexDirection: "row",
                  alignItems: "flex-start",
                  py: 1.2,
                  cursor: "pointer",
                  borderRadius: "8px",
                  backgroundColor: "background.paper",
                  border: "1px solid",
                  borderColor: "border.default",

                  transition: "all 0.2s ease",
                }),
              }}
              onClick={isCustom ? handleToggle : undefined}
            >
              <FormControlLabel
                sx={{
                  marginLeft: 0,
                  ...props.labelProps,
                  "& .MuiFormControlLabel-label": {
                    color:
                      theme.palette.mode === "light"
                        ? "text.primary"
                        : "common.white",
                  },
                }}
                control={
                  <Checkbox
                    checked={value}
                    onChange={(e) => {
                      onChange(e);
                      props.onChange?.(e);
                    }}
                    inputProps={{ "aria-label": "controlled" }}
                    icon={
                      <Iconify
                        icon="solar:square-linear"
                        sx={{
                          color:
                            theme.palette.mode === "light"
                              ? theme.palette.black?.o20
                              : theme.palette.grey?.[600],
                        }}
                        width={isCustom ? 22 : 20}
                      />
                    }
                    checkedIcon={
                      <Iconify
                        icon="famicons:checkbox"
                        sx={{
                          color: theme.palette.purple?.[300],
                        }}
                        width={isCustom ? 22 : 20}
                      />
                    }
                    size={size}
                    sx={{ ...props.checkboxSx }}
                  />
                }
                label={isCustom ? "" : label}
                labelPlacement={labelPlacement}
                {...props}
              />
              <>
                {isCustom && (
                  <Box
                    sx={{
                      display: "flex",
                      flexDirection: "column",
                      marginLeft: -1,
                    }}
                  >
                    <Typography sx={{ fontWeight: 500, fontSize: "13px" }}>
                      {label}
                    </Typography>
                    <Typography
                      sx={{
                        fontWeight: 400,
                        fontSize: "13px",
                        color: "text.disabled",
                      }}
                    >
                      {helperText}
                    </Typography>
                  </Box>
                )}
              </>
            </Box>

            {(isError || (helperText && !isCustom)) && (
              <FormHelperText sx={{ marginTop: 0, marginLeft: 0 }}>
                {errorMessage || helperText}
              </FormHelperText>
            )}
          </FormControl>
        );
      }}
      control={control}
      name={fieldName}
      defaultValue={defaultValue}
    />
  );
};

FormCheckboxField.propTypes = {
  control: PropTypes.object,
  fieldName: PropTypes.string,
  label: PropTypes.string,
  labelPlacement: PropTypes.oneOf(["end", "start", "top", "bottom"]),
  helperText: PropTypes.node,
  defaultValue: PropTypes.bool,
  labelProps: PropTypes.object,
  checkboxSx: PropTypes.object,
  size: PropTypes.string,
  onChange: PropTypes.func,
  isCustom: PropTypes.bool,
};
