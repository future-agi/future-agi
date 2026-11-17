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
import {
  NUMERIC_BUTTON_CHOICES_MESSAGE,
  hasTooManyButtonChoices,
} from "../numeric-button-choices";

NumericSettings.propTypes = {
  control: PropTypes.object.isRequired,
};

const handleNumberChange = (field) => (e) => {
  const raw = e.target.value;
  const cleaned = raw.replace(/^(-?)0+(?=\d)/, "$1");
  if (cleaned !== raw) e.target.value = cleaned;
  if (cleaned === "") {
    field.onChange("");
    return;
  }
  field.onChange(Number(cleaned));
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
              Number(value) >= 0 || "Enter a value of 0 or more",
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
              onChange={handleNumberChange(field)}
              inputProps={{ min: 0, step: "any" }}
            />
          )}
        />
        <Controller
          name="settings.max"
          control={control}
          rules={{
            required: "Required",
            validate: (value, formValues) => {
              // The sign comes first, so a Maximum of 0 asks for a value
              // above 0 before comparing it with the Minimum.
              if (Number(value) <= 0) return "Enter a value above 0";
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
              onChange={handleNumberChange(field)}
              inputProps={{ min: 0, step: "any" }}
            />
          )}
        />
      </Stack>

      <Controller
        name="settings.step_size"
        control={control}
        rules={{
          required: "Required",
          validate: (value, formValues) => {
            if (!(Number(value) > 0)) return "Enter a value above 0";
            // A button scale shows at most 11 choices; a label saved earlier
            // with more still draws, but an edit to it is refused until the
            // step or the display type changes.
            const settings = formValues.settings ?? {};
            if (
              settings.display_type === "button" &&
              hasTooManyButtonChoices(settings.min, settings.max, value)
            ) {
              return NUMERIC_BUTTON_CHOICES_MESSAGE;
            }
            return true;
          },
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
            onChange={handleNumberChange(field)}
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
