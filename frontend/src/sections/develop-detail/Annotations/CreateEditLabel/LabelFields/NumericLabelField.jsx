import { Box, Slider, Typography, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import React, { useEffect, useState } from "react";
import { Controller } from "react-hook-form";
import IncrementerButton from "src/components/IncrementerButton/IncrementerButton";

const styles = {
  circular: {
    height: 5,
    "& .MuiSlider-thumb": {
      width: 18,
      height: 18,
      backgroundColor: "action.hover",
      border: "3px solid var(--bg-paper)",
      "&:hover": {
        boxShadow: "none",
      },
      "&.Mui-active": {
        boxShadow: "none",
      },
    },
    "& .MuiSlider-track": {
      border: "none",
    },
    "& .MuiSlider-rail": {
      opacity: 0.5,
      backgroundColor: "action.hover",
    },
  },
};

const STEP_TOLERANCE = 1e-6;

const decimalPlaces = (number) => {
  const [mantissa, exponent] = String(number).toLowerCase().split("e-");
  const fraction = mantissa.split(".")[1] ?? "";
  return fraction.length + (exponent ? parseInt(exponent, 10) : 0);
};

// The number one step up (direction 1) or down (-1) from `value` that the
// server accepts for a number label: on the steps counted from min, within
// min and max. Null when there is none.
const nextStepValue = (value, { min, max, step }, direction) => {
  if (!(step > 0) || min > max) return null;
  if (!Number.isFinite(value)) return direction > 0 ? min : null;
  const position = (value - min) / step;
  const isOnStep = Math.abs(position - Math.round(position)) < STEP_TOLERANCE;
  let count;
  if (direction > 0) {
    count = Math.max(
      isOnStep ? Math.round(position) + 1 : Math.ceil(position),
      0,
    );
  } else {
    const lastCount = Math.floor((max - min) / step + STEP_TOLERANCE);
    count = Math.min(
      isOnStep ? Math.round(position) - 1 : Math.floor(position),
      lastCount,
    );
  }
  const places = Math.max(decimalPlaces(min), decimalPlaces(step));
  const next = Number((min + count * step).toFixed(places));
  return next < min || next > max ? null : next;
};

const SliderBase = ({
  value: initialValue = 0,
  min,
  max,
  step,
  handleSliderChange,
  onStep,
  disabled,
}) => {
  const theme = useTheme();
  const [value, setValue] = useState(initialValue);
  useEffect(() => {
    if (Number.isFinite(initialValue)) {
      setValue(initialValue);
    }
  }, [initialValue]);
  const limits = { min, max, step };
  const valueUp = nextStepValue(value, limits, 1);
  const valueDown = nextStepValue(value, limits, -1);
  // A label with no max (an infinite one here) has no top on the server, so +
  // has none. The slider shows 100 past min, or up to the number when it is
  // higher, with today's marks while it shows its first 100; past that it
  // drops them, so a large number never draws a mark for every step.
  const hasMax = Number.isFinite(max);
  const firstTop = min + 100;
  const sliderMax = hasMax
    ? max
    : Math.max(firstTop, Number.isFinite(value) ? value : min);
  const showMarks = hasMax || sliderMax === firstTop;

  // A press moves the shown number and the form's value together.
  const stepTo = (next) => {
    if (disabled || next === null) return;
    setValue(next);
    onStep?.(next);
  };
  const handleIncrease = () => stepTo(valueUp);
  const handleDecrease = () => stepTo(valueDown);
  return (
    <Box
      sx={{
        width: "100%",
        display: "flex",
        flexDirection: "column",
        // gap: theme.spacing(1),
      }}
    >
      {/* First row: Label on left, Buttons on right */}
      <Box
        sx={{
          display: "flex",
          justifyContent: "flex-end",
          alignItems: "center",
          pr: 2.5,
        }}
      >
        <IncrementerButton
          quantity={value}
          onIncrease={handleIncrease}
          onDecrease={handleDecrease}
          // Off at either end: no accepted step past min or max.
          disabledIncrease={disabled || valueUp === null}
          disabledDecrease={disabled || valueDown === null}
          sx={{
            borderRadius: theme.spacing(1),
          }}
        />
      </Box>

      {/* Second row: Slider */}
      <Box
        sx={{ width: "100%", display: "flex", flexDirection: "column", pl: 1 }}
      >
        <Slider
          value={value}
          onChange={handleSliderChange}
          min={min}
          max={sliderMax}
          step={step}
          valueLabelDisplay="auto"
          marks={showMarks}
          sx={{
            ...styles.circular,
            "& .MuiSlider-track": {
              backgroundColor: "text.secondary",
            },
            "& .MuiSlider-mark": {
              opacity: 0.2,
              height: 3,
              backgroundColor: "text.secondary",
            },
          }}
          disabled={disabled}
        />
        {/* <Box
          sx={{
            display: "flex",
            justifyContent: "space-between",
            marginTop: theme.spacing(-1),
          }}
        >
          <Typography variant="body2" color="textSecondary">
            {min}
          </Typography>
          <Typography variant="body2" color="textSecondary">
            {max}
          </Typography>
        </Box> */}
      </Box>
    </Box>
  );
};

SliderBase.propTypes = {
  value: PropTypes.number,
  min: PropTypes.number,
  max: PropTypes.number,
  step: PropTypes.number,
  handleSliderChange: PropTypes.func,
  onStep: PropTypes.func,
  disabled: PropTypes.bool,
};

const SliderRow = ({
  label,
  value: initialValue = 0,
  min = 0,
  max = 100,
  step = 1,
  sliderContainerStyles,
}) => {
  const [value, setValue] = useState(initialValue);
  const theme = useTheme();
  useEffect(() => {
    if (Number.isFinite(initialValue)) {
      setValue(initialValue);
    }
  }, [initialValue]);

  const handleIncrease = () => {
    setValue((prevValue) => Math.min(prevValue + step, max));
  };

  const handleDecrease = () => {
    setValue((prevValue) => Math.max(prevValue - step, min));
  };

  return (
    <Box>
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
        }}
      >
        <Typography variant="s1" fontWeight={"fontWeightMedium"}>
          {label ? label + ":" : "Label:"}
        </Typography>
        <IncrementerButton
          quantity={value}
          onIncrease={handleIncrease}
          onDecrease={handleDecrease}
          disabledIncrease={value >= max}
          disabledDecrease={value <= min}
          sx={{
            marginBottom: theme.spacing(1),
            borderRadius: theme.spacing(1),
            marginRight: theme.spacing(1),
          }}
        />
      </Box>
      <Box sx={sliderContainerStyles}>
        {/* <ModelOptionSlider
          aria-label="Slider"
          valueLabelDisplay="auto"
          size="medium"
          value={value}
          onChange={(e, newValue) => setValue(newValue)}
          step={step}
          marks={marks}
          min={min}
          max={max}
          sx={{
            "& .MuiSlider-rail": { height: 10 },
            "& .MuiSlider-track": { height: 10 },
            "& .MuiSlider-mark": {
              height: 4,
              width: 2,
              color: theme.palette.text.disabled,
            },
          }}
        /> */}
        <Slider
          value={value}
          onChange={(_, newValue) => setValue(newValue)}
          min={+min}
          max={+max}
          step={+step}
          aria-label={label}
          valueLabelDisplay="auto"
          sx={{
            ...styles.circular,
            "& .MuiSlider-track": {
              backgroundColor: "text.secondary",
            },
          }}
        />

        <Box
          sx={{
            display: "flex",
            justifyContent: "space-between",
            marginTop: theme.spacing(-2),
          }}
        >
          <Typography variant="body2" color="textSecondary">
            {min}
          </Typography>
          <Typography variant="body2" color="textSecondary">
            {max}
          </Typography>
        </Box>
      </Box>
    </Box>
  );
};

SliderRow.propTypes = {
  label: PropTypes.string,
  value: PropTypes.number,
  min: PropTypes.number,
  max: PropTypes.number,
  step: PropTypes.number,
  sliderContainerStyles: PropTypes.object,
  onChange: PropTypes.func,
};

const ControlledNumericLabelField = ({ control, fieldName, ...rest }) => {
  return (
    <>
      <Controller
        control={control}
        name={fieldName}
        render={({ field }) => (
          <Box sx={{ flex: 1 }}>
            <SliderBase
              {...rest}
              value={field.value}
              handleSliderChange={(e, v) => field.onChange(v)}
              onStep={(v) => field.onChange(v)}
            />
          </Box>
        )}
      />
    </>
  );
};

ControlledNumericLabelField.propTypes = {
  control: PropTypes.object,
  fieldName: PropTypes.string,
};

const NumericLabelField = ({ control, label, fieldName, settings }) => {
  const [value, setValue] = useState(0);
  // The label's own max when it has one, 0 included; with none the server
  // takes any number, so the field has no top either.
  const labelMax = parseFloat(settings.max);
  useEffect(() => {
    setValue(parseFloat(settings.min));
  }, [settings.min, settings.max]);

  const isValidSliderSetting = (settings) => {
    const min = parseFloat(settings?.min);
    const max = parseFloat(settings?.max);
    const stepSize = parseFloat(settings?.stepSize);

    return (
      settings?.displayType === "slider" &&
      !isNaN(min) &&
      isFinite(min) &&
      !isNaN(max) &&
      isFinite(max) &&
      !isNaN(stepSize) &&
      isFinite(stepSize) &&
      min < max && // Additional validation to ensure min < max
      stepSize > 0 // Ensure step size is positive
    );
  };

  return (
    <Box
      sx={{
        width: "100%",
        display: "flex",
        flexDirection: "column",
        pb: 1,
      }}
    >
      {control ? (
        <ControlledNumericLabelField
          control={control}
          fieldName={fieldName}
          min={parseFloat(settings.min) || 0}
          max={Number.isFinite(labelMax) ? labelMax : Infinity}
          // A label from the API has step_size; stepSize is the form's name.
          // The slider counts its steps from min, as the server does.
          step={parseFloat(settings.step_size ?? settings.stepSize) || 1}
          disabled={settings.disabled}
        />
      ) : (
        <Box sx={{ flex: 1 }}>
          {isValidSliderSetting(settings) && (
            <SliderRow
              value={value}
              onChange={(e, v) => setValue(v)}
              min={parseFloat(settings.min)}
              max={parseFloat(settings.max)}
              step={parseFloat(settings.stepSize)}
              label={label}
              sliderContainerStyles={{ px: 1 }}
            />
          )}
        </Box>
      )}
    </Box>
  );
};

NumericLabelField.propTypes = {
  control: PropTypes.object,
  label: PropTypes.string,
  fieldName: PropTypes.string,
  settings: PropTypes.object,
};

export default NumericLabelField;
