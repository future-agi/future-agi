import React from "react";
import { Box, Button, Typography } from "@mui/material";
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

  const { field } = useController({ control, name: fieldName });

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
          <Typography variant="s1_2" fontWeight="fontWeightMedium">
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
          typography="s1"
          fontWeight="fontWeightRegular"
          color="text.secondary"
        >
          {description}
        </Typography>
      </Box>
      <Box sx={{ display: "flex", gap: "12px", flexWrap: "wrap" }}>
        {options.map(({ label, value }) => {
          const isSelected = getIsSelected(value);

          return (
            <Box
              key={value}
              sx={{
                padding: "4px 12px",
                borderRadius: 0.5,
                border: "1px solid",
                borderColor: isSelected ? "primary.main" : "divider",
                cursor: "pointer",
                boxShadow: "2px 2px 4px 0px #00000014",
                backgroundColor: isSelected
                  ? "action.hover"
                  : "background.paper",
                transition: "background-color, border-color, 200ms ease-in-out",
              }}
              onClick={() => {
                onToggle(value, isSelected);
              }}
            >
              <Typography variant="s1">{label}</Typography>
            </Box>
          );
        })}
      </Box>
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
