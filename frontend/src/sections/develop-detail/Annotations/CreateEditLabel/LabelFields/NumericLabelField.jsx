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

const SliderBase = ({
  value: initialValue = 0,
  min,
  max,
  step,
  handleSliderChange,
  disabled,
}) => {
  const theme = useTheme();
  const [value, setValue] = useState(initialValue);
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
          disabledIncrease={value >= max}
          disabledDecrease={value <= min}
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
          max={max}
          step={step}
          valueLabelDisplay="auto"
          marks
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
          max={parseFloat(settings.max) || 100}
          step={parseFloat(settings.stepSize) || 1}
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
