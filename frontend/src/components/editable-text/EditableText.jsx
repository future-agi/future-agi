import React, { useState, useRef } from "react";
import { Box, Typography, IconButton, Stack, TextField } from "@mui/material";
import { Controller } from "react-hook-form";
import PropTypes from "prop-types";
import SvgColor from "../svg-color";
import CustomTooltip from "../tooltip/CustomTooltip";

/**
 * A reusable editable text component that integrates with react-hook-form
 * Displays text with an edit icon, switches to TextField on edit click
 * Submits on Enter, resets on Escape
 */
const EditableText = React.forwardRef(
  (
    {
      control,
      fieldName,
      placeholder = "Enter text...",
      typographyProps = {},
      textFieldProps = {},
      iconSx = {},
      sx = {},
      onFocus = () => {},
      onChange: propOnChange = () => {},
      defaultValue = "",
      onSubmit = () => {},
      reset = () => {},
      readOnly = false,
      ...rest
    },
    ref,
  ) => {
    const [isEditing, setIsEditing] = useState(false);
    const textFieldRef = useRef(null);
    const shouldFocusRef = useRef(false);
    const isSubmittingRef = useRef(false);

    const handleEditClick = () => {
      if (readOnly) return;
      shouldFocusRef.current = true;
      setIsEditing(true);
    };

    const handleBlur = () => {
      if (isSubmittingRef.current) {
        isSubmittingRef.current = false;
        return;
      }
      shouldFocusRef.current = false;
      setIsEditing(false);
      reset();
    };

    const handleKeyDown = (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        isSubmittingRef.current = true;
        setIsEditing(false);
        onSubmit();
      } else if (event.key === "Escape") {
        event.preventDefault();
        reset();
        setIsEditing(false);
      }
    };

    return (
      <Controller
        control={control}
        name={fieldName}
        defaultValue={defaultValue}
        render={({ field: { onChange, value }, formState: { errors } }) => {
          const displayValue = value || "";
          const fieldError = fieldName
            .split(".")
            .reduce((obj, key) => obj?.[key], errors);
          const hasError = !!fieldError;

          if (isEditing && !readOnly) {
            return (
              <Box
                sx={{
                  display: "flex",
                  alignItems: "center",
                  width: "100%",
                  ...sx,
                }}
              >
                <TextField
                  variant="outlined"
                  inputRef={(el) => {
                    textFieldRef.current = el;
                    if (ref) {
                      ref.current = el;
                    }
                    // Focus and select only when entering edit mode
                    if (el && shouldFocusRef.current) {
                      shouldFocusRef.current = false;
                      setTimeout(() => {
                        el.focus();
                        el.select();
                      }, 0);
                    }
                  }}
                  value={displayValue}
                  onChange={(e) => {
                    onChange(e.target.value);
                    propOnChange?.(e);
                  }}
                  onKeyDown={handleKeyDown}
                  onFocus={onFocus}
                  onBlur={handleBlur}
                  placeholder={placeholder}
                  fullWidth
                  error={hasError}
                  helperText={fieldError?.message || ""}
                  sx={{
                    "& .MuiInputBase-root": {
                      padding: "0px 8px",
                      fontSize: typographyProps.fontSize || "inherit",
                      fontWeight: typographyProps.fontWeight || "inherit",
                    },
                  }}
                  size="small"
                  {...textFieldProps}
                  {...rest}
                />
              </Box>
            );
          }

          return (
            <Stack
              direction="row"
              alignItems="center"
              spacing={0.5}
              sx={{
                "&:hover .editable-text-icon": {
                  opacity: 1,
                },
                ...sx,
              }}
            >
              <CustomTooltip
                size="small"
                show
                title={displayValue}
                arrow
                placement="top"
              >
                <Typography
                  sx={{
                    borderBottom: "1px solid",
                    borderColor: "primary.main",
                    minHeight: "1.5em",
                    ...typographyProps.sx,
                  }}
                  {...typographyProps}
                >
                  {displayValue || placeholder}
                </Typography>
              </CustomTooltip>
              {!readOnly && (
                <IconButton
                  size="small"
                  title="Edit"
                  onClick={handleEditClick}
                  className="editable-text-icon"
                  sx={{
                    padding: 0.5,
                    ...iconSx,
                  }}
                >
                  <SvgColor
                    src="/assets/icons/ic_edit.svg"
                    sx={{
                      width: 16,
                      height: 16,
                      color: "text.secondary",
                    }}
                  />
                </IconButton>
              )}
            </Stack>
          );
        }}
      />
    );
  },
);

EditableText.displayName = "EditableText";

EditableText.propTypes = {
  control: PropTypes.object.isRequired,
  fieldName: PropTypes.string.isRequired,
  placeholder: PropTypes.string,
  typographyProps: PropTypes.object,
  textFieldProps: PropTypes.object,
  iconSx: PropTypes.object,
  sx: PropTypes.object,
  onFocus: PropTypes.func,
  onChange: PropTypes.func,
  defaultValue: PropTypes.string,
  onSubmit: PropTypes.func,
  reset: PropTypes.func,
  readOnly: PropTypes.bool,
};

export default EditableText;
