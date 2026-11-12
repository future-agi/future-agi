import { Box } from "@mui/material";
import React from "react";
import IncrementerButton from "src/components/IncrementerButton/IncrementerButton";
import PropTypes from "prop-types";
import { useController } from "react-hook-form";
import InputSection from "./InputSection";
import { ModelOptionSlider } from "./ModelOptionSlider";

function generateMarks(rest) {
  const { min, max, step } = rest;
  const steps = (max - min) / step;
  return Array.from({ length: steps }, (_, i) => ({
    value: parseFloat((min + i * step).toFixed(6)), // prevent float errors
    label: "", // optional: add `${value}` if you want visible labels
  }));
}

const PARAM_TOOLTIPS = {
  temperature:
    "Controls randomness in responses. Lower values produce more focused output, higher values increase creativity.",
  topp: "Nucleus sampling threshold. Limits token selection to the most probable tokens whose cumulative probability reaches this value.",
  maxtokens:
    "Maximum number of tokens the model can generate in a single response.",
  presencepenalty:
    "Penalizes tokens that have already appeared, encouraging the model to introduce new topics.",
  frequencypenalty:
    "Penalizes tokens based on how often they appear, reducing repetition in the output.",
};

const getParamTooltip = (label) => {
  if (!label) return undefined;
  const key = label.toLowerCase().replace(/[\s_]/g, "");
  return PARAM_TOOLTIPS[key];
};

const SliderRow = ({
  label,
  control,
  fieldName,
  inputSectionStyles,
  sliderContainerStyles,
  disabled = false,
  ...rest
}) => {
  const { field } = useController({ control, name: fieldName });

  const isNull = field.value == null;

  const handleIncrease = () => {
    if (isNull) {
      field.onChange(rest.min ?? 0);
      return;
    }
    field.onChange(
      Math.min(field.value + (rest.step || 1), rest.max ?? Infinity),
    );
  };

  const handleDecrease = () => {
    if (isNull) return;
    field.onChange(
      Math.max(field.value - (rest.step || 1), rest.min ?? -Infinity),
    );
  };

  return (
    <Box
      sx={{
        paddingBottom: "20px",
        ...(disabled && { opacity: 0.5, pointerEvents: "none" }),
      }}
    >
      <InputSection
        label={label}
        sx={inputSectionStyles || {}}
        icon={getParamTooltip(label) ? "solar:info-circle-bold" : undefined}
        tooltipText={getParamTooltip(label)}
      >
        <IncrementerButton
          quantity={isNull ? null : parseFloat(field?.value?.toFixed(2))}
          onIncrease={handleIncrease}
          onDecrease={handleDecrease}
          disabledIncrease={
            disabled ||
            (rest.max !== undefined && !isNull && field.value >= rest.max)
          }
          disabledDecrease={
            disabled ||
            isNull ||
            (rest.min !== undefined && field.value <= rest.min)
          }
          sx={{ height: "25px", borderRadius: "4px", mr: 0 }}
        />
      </InputSection>
      <Box sx={sliderContainerStyles}>
        <ModelOptionSlider
          {...rest}
          aria-label={label || "Slider"}
          valueLabelDisplay={isNull ? "off" : "auto"}
          valueLabelFormat={(value) =>
            value.toString().includes(".") ? value?.toFixed(2) : value
          }
          size="medium"
          value={isNull ? rest?.min ?? 0 : field?.value}
          // Initialize value before drag starts when null.
          // Without this, the value prop source changes mid-drag (fallback → field.value),
          // which disrupts MUI Slider's pointer tracking and closes the parent popover.
          onPointerDown={() => {
            if (isNull) {
              field.onChange(rest?.min ?? 0);
            }
          }}
          onChange={(_, val) => field.onChange(val)}
          disabled={disabled}
          {...(rest.showMark && {
            marks: generateMarks(rest),
            sx: {
              ...rest?.sx,
            },
          })}
          sx={{
            "& .MuiSlider-thumb::after": {
              width: "8px !important",
              height: "8px !important",
            },
          }}
        />
      </Box>
    </Box>
  );
};

SliderRow.propTypes = {
  label: PropTypes.string,
  control: PropTypes.object,
  fieldName: PropTypes.string,
  inputSectionStyles: PropTypes.any,
  sliderContainerStyles: PropTypes.any,
  disabled: PropTypes.bool,
};

export default SliderRow;
