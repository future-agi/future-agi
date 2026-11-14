import PropTypes from "prop-types";
import { useState } from "react";
import { Button, Slider, Stack, TextField, Typography } from "@mui/material";
import { useController } from "react-hook-form";
import { isValidSamplingRate } from "./simpleSamplingPolicy";

export function RowLimitField({ control }) {
  const {
    field: { value, onChange },
    fieldState: { error },
  } = useController({ control, name: "spansLimit" });
  const presets = [100, 1000, 10000, 100000];
  const [customSelected, setCustom] = useState(false);
  const custom = customSelected || !presets.includes(Number(value));
  return (
    <Stack spacing={1}>
      <Typography component="h3" variant="subtitle2">
        Historical processing limit
      </Typography>
      <Typography variant="caption" color="text.secondary">
        Stop after processing this many matching rows. This is not an exact
        sample size or a spending limit.
      </Typography>
      <Stack
        direction="row"
        gap={0.75}
        flexWrap="wrap"
        aria-label="Processing limit presets"
      >
        {presets.map((number) => (
          <Button
            key={number}
            size="small"
            variant="outlined"
            aria-pressed={!custom && Number(value) === number}
            sx={{
              bgcolor:
                !custom && Number(value) === number
                  ? "action.selected"
                  : "transparent",
            }}
            onClick={() => {
              onChange(number);
              setCustom(false);
            }}
          >
            {number.toLocaleString()}
          </Button>
        ))}
        <Button
          size="small"
          variant="outlined"
          aria-pressed={custom}
          onClick={() => setCustom(true)}
        >
          Custom limit
        </Button>
      </Stack>
      {custom && (
        <TextField
          size="small"
          label="Matching rows to process"
          type="number"
          value={value ?? ""}
          onChange={(event) => onChange(event.target.value)}
          inputProps={{ min: 1, max: 1000000, step: 1 }}
          error={Boolean(error)}
          helperText={error?.message}
          sx={{ maxWidth: 240 }}
        />
      )}
      {error && !custom && (
        <Typography variant="caption" color="error.main">
          {error.message}
        </Typography>
      )}
    </Stack>
  );
}
RowLimitField.propTypes = { control: PropTypes.object.isRequired };

export function SamplingRateField({ control }) {
  const {
    field: { value, onChange },
    fieldState: { error },
  } = useController({ control, name: "samplingRate" });
  const rate = Number(value) || 0;
  const rateError = isValidSamplingRate(value)
    ? ""
    : "Enter a percentage from 1 to 100.";
  return (
    <Stack spacing={1}>
      <Stack
        direction="row"
        gap={0.75}
        flexWrap="wrap"
        aria-label="Percentage presets"
      >
        {[10, 25, 50, 100].map((number) => (
          <Button
            key={number}
            size="small"
            variant="outlined"
            aria-pressed={rate === number}
            sx={{
              bgcolor: rate === number ? "action.selected" : "transparent",
            }}
            onClick={() => onChange(number)}
          >
            {number === 100 ? "All" : `${number}%`}
          </Button>
        ))}
      </Stack>
      <Stack
        direction="row"
        spacing={2}
        alignItems="center"
        flexWrap="wrap"
        useFlexGap
      >
        <Slider
          aria-label="Sampling rate"
          value={rate}
          onChange={(_, number) => onChange(number)}
          min={1}
          max={100}
          step={1}
          valueLabelDisplay="auto"
          size="small"
          sx={{
            mx: 1,
            color: "text.primary",
            flex: "1 1 120px",
            width: "auto",
          }}
        />
        <TextField
          size="small"
          label="Custom percentage (%)"
          type="number"
          value={value ?? ""}
          onChange={(event) =>
            onChange(
              event.target.value === "" ? "" : Number(event.target.value),
            )
          }
          inputProps={{ min: 1, max: 100, step: 1 }}
          error={Boolean(error) || Boolean(rateError)}
          helperText={error?.message || rateError}
          sx={{ width: 190, flexShrink: 0, maxWidth: "100%" }}
        />
      </Stack>
    </Stack>
  );
}
SamplingRateField.propTypes = { control: PropTypes.object.isRequired };
