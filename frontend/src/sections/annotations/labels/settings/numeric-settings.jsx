import PropTypes from "prop-types";
import {
  FormControl,
  FormControlLabel,
  Radio,
  RadioGroup,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { Controller } from "react-hook-form";

NumericSettings.propTypes = {
  control: PropTypes.object.isRequired,
};

export default function NumericSettings({ control }) {
  return (
    <Stack spacing={2}>
      <Stack direction="row" spacing={2}>
        <Controller
          name="settings.min"
          control={control}
          rules={{
            required: "Required",
            validate: (value) =>
              Number(value) >= 0 || "Minimum cannot be negative",
          }}
          render={({ field, fieldState }) => (
            <TextField
              {...field}
              label="Minimum"
              placeholder="0"
              type="number"
              size="small"
              fullWidth
              error={!!fieldState.error}
              helperText={fieldState.error?.message}
              onChange={(e) => field.onChange(Number(e.target.value))}
              inputProps={{ min: 0 }}
            />
          )}
        />
        <Controller
          name="settings.max"
          control={control}
          rules={{
            required: "Required",
            validate: (value, formValues) => {
              if (Number(value) < 0) return "Maximum cannot be negative";
              return (
                Number(value) > Number(formValues.settings?.min) ||
                "Max must be greater than min"
              );
            },
          }}
          render={({ field, fieldState }) => (
            <TextField
              {...field}
              label="Maximum"
              placeholder="10"
              type="number"
              size="small"
              fullWidth
              error={!!fieldState.error}
              helperText={fieldState.error?.message}
              onChange={(e) => field.onChange(Number(e.target.value))}
              inputProps={{ min: 0 }}
            />
          )}
        />
      </Stack>

      <Controller
        name="settings.step_size"
        control={control}
        rules={{
          required: "Required",
          validate: (value) =>
            Number(value) > 0 || "Step size must be positive",
        }}
        render={({ field, fieldState }) => (
          <TextField
            {...field}
            label="Step Size"
            placeholder="1"
            type="number"
            size="small"
            fullWidth
            error={!!fieldState.error}
            helperText={fieldState.error?.message}
            onChange={(e) => field.onChange(Number(e.target.value))}
            inputProps={{ min: 0.000001, step: "any" }}
          />
        )}
      />

      <FormControl>
        <Typography variant="subtitle2" sx={{ mb: 1 }}>
          Display Type
        </Typography>
        <Controller
          name="settings.display_type"
          control={control}
          render={({ field }) => (
            <RadioGroup {...field} row>
              <FormControlLabel
                value="slider"
                control={<Radio />}
                label="Slider"
              />
              <FormControlLabel
                value="button"
                control={<Radio />}
                label="Buttons"
              />
            </RadioGroup>
          )}
        />
      </FormControl>
    </Stack>
  );
}
