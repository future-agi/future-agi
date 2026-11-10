import React, { useId } from "react";
import { Box, Button, FormHelperText, Typography } from "@mui/material";
import PropTypes from "prop-types";
import { useController, useFieldArray, useFormContext } from "react-hook-form";
import logger from "src/utils/logger";
import { ShowComponent } from "src/components/show";

const OptionSelectors = ({
  label,
  description,
  fieldName,
  options,
  multiple = true,
  showClearButton = false,
}) => {
  const { control } = useFormContext();
  const { append, remove, fields } = useFieldArray({
    control,
    name: fieldName,
  });

  const { field, fieldState } = useController({ control, name: fieldName });
  const errorMessage = fieldState.error?.message;

  const id = useId();
  const labelId = `${id}-label`;
  const descriptionId = `${id}-description`;
  const errorId = `${id}-error`;
  const describedBy =
    [description && descriptionId, errorMessage && errorId]
      .filter(Boolean)
      .join(" ") || undefined;

  const getIsSelected = (value) => {
    logger.debug("getIsSelected", field.value, value);
    if (multiple) {
      return fields.some((field) => field.value === value);
    } else {
      return field.value === value;
    }
  };

  const onToggle = (value, isSelected) => {
    if (multiple) {
      if (isSelected) {
        remove(fields.findIndex((field) => field.value === value));
      } else {
        append({ value });
      }
    } else {
      field.onChange(value);
    }
  };

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
      <Box>
        <Box
          sx={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <Typography id={labelId} variant="s1_2" fontWeight="fontWeightMedium">
            {label}
          </Typography>
          <ShowComponent condition={showClearButton}>
            <Button
              size="small"
              onClick={() => {
                if (multiple) {
                  remove();
                } else {
                  field.onChange(null);
                }
              }}
              sx={{ textDecoration: "underline" }}
            >
              Clear
            </Button>
          </ShowComponent>
        </Box>
        <Typography
          id={descriptionId}
          typography="s1"
          fontWeight="fontWeightRegular"
          color="text.secondary"
        >
          {description}
        </Typography>
      </Box>
      <Box
        role="group"
        aria-labelledby={labelId}
        aria-describedby={describedBy}
        sx={{ display: "flex", gap: 1.5, flexWrap: "wrap" }}
      >
        {options.map(({ label, value }) => {
          const isSelected = getIsSelected(value);

          return (
            <Button
              key={value}
              type="button"
              variant="outlined"
              size="small"
              aria-pressed={isSelected}
              sx={{
                borderColor: isSelected ? "text.primary" : undefined,
                backgroundColor: isSelected ? "action.selected" : undefined,
              }}
              onClick={() => {
                onToggle(value, isSelected);
              }}
            >
              <Typography variant="s1" component="span">{label}</Typography>
            </Button>
          );
        })}
      </Box>
      <ShowComponent condition={Boolean(errorMessage)}>
        <FormHelperText id={errorId} error>{errorMessage}</FormHelperText>
      </ShowComponent>
    </Box>
  );
};

OptionSelectors.propTypes = {
  label: PropTypes.string.isRequired,
  description: PropTypes.string,
  fieldName: PropTypes.string.isRequired,
  options: PropTypes.array.isRequired,
  multiple: PropTypes.bool,
  showClearButton: PropTypes.bool,
};

export default OptionSelectors;
