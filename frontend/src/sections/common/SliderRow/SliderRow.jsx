import { Box, Button, Typography } from "@mui/material";
import React from "react";
import IncrementerButton from "src/components/IncrementerButton/IncrementerButton";
import PropTypes from "prop-types";
import { useController } from "react-hook-form";
import InputSection from "./InputSection";
import { ModelOptionSlider } from "./ModelOptionSlider";
import { ShowComponent } from "src/components/show";

function generateMarks(rest) {
  const { min, max, step } = rest;
  const steps = (max - min) / step;
  return Array.from({ length: steps }, (_, i) => ({
    value: parseFloat((min + i * step).toFixed(6)), // prevent float errors
    label: "", // optional: add `${value}` if you want visible labels
  }));
}

const SliderRow = ({
  label,
  description,
  control,
  fieldName,
  inputSectionStyles,
  sliderContainerStyles,
  labelProps = {},
  showClearButton = false,
  ...rest
}) => {
  const { field } = useController({ control, name: fieldName });

  const handleIncrease = () => {
    field.onChange(field.value + (rest.step || 1));
  };

  const handleDecrease = () => {
    field.onChange(field.value - (rest.step || 1));
  };

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        // rowGap: (theme) => theme.spacing(0.25)
      }}
    >
      <InputSection
        label={label}
        sx={inputSectionStyles || {}}
        labelProps={labelProps}
      >
        <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
          <IncrementerButton
            quantity={parseFloat(field?.value?.toFixed(2))}
            onIncrease={handleIncrease}
            onDecrease={handleDecrease}
            disabledIncrease={rest.max !== undefined && field.value >= rest.max}
            disabledDecrease={rest.min !== undefined && field.value <= rest.min}
            sx={{ height: "25px", borderRadius: "4px", m: 0 }}
          />
          <ShowComponent condition={showClearButton}>
            <Button
              size="small"
              onClick={() => field.onChange(null)}
              sx={{ textDecoration: "underline" }}
            >
              Clear
            </Button>
          </ShowComponent>
        </Box>
      </InputSection>
      <Typography
        typography="s1"
        fontWeight="fontWeightRegular"
        color="text.secondary"
      >
        {description}
      </Typography>
      <Box sx={sliderContainerStyles}>
        <ModelOptionSlider
          {...rest}
          aria-label="Temperature"
          valueLabelDisplay="auto"
          valueLabelFormat={(value) => value?.toFixed(2)}
          size="medium"
          {...field}
          {...(rest.showMark && {
            marks: generateMarks(rest),
            sx: {
              ...rest?.sx,
            },
          })}
        />
        <ShowComponent condition={rest.minDescription || rest.maxDescription}>
          <Box
            sx={{
              display: "flex",
              justifyContent: "space-between",
              mt: 0.5,
            }}
          >
            <Box>
              <Typography typography="s1" color="text.primary">
                {rest.min}
              </Typography>
              <Typography
                fontWeight="fontWeightRegular"
                typography="s2_1"
                color="text.secondary"
              >
                {rest.minDescription}
              </Typography>
            </Box>

            <Box>
              <Typography
                typography="s1"
                color="text.primary"
                sx={{ textAlign: "right" }}
              >
                {rest.max}
              </Typography>
              <Typography
                fontWeight="fontWeightRegular"
                typography="s2_1"
                color="text.secondary"
              >
                {rest.maxDescription}
              </Typography>
            </Box>
          </Box>
        </ShowComponent>
      </Box>
    </Box>
  );
};

SliderRow.propTypes = {
  label: PropTypes.string,
  description: PropTypes.string,
  control: PropTypes.object,
  fieldName: PropTypes.string,
  inputSectionStyles: PropTypes.any,
  sliderContainerStyles: PropTypes.any,
  labelProps: PropTypes.object,
  showClearButton: PropTypes.bool,
};

export default SliderRow;
