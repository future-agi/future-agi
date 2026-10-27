import PropTypes from "prop-types";
import { Stack, TextField } from "@mui/material";
import { Controller } from "react-hook-form";

TextSettings.propTypes = {
  control: PropTypes.object.isRequired,
};

export default function TextSettings({ control }) {
  return (
    <Stack spacing={2}>
      <Controller
        name="settings.placeholder"
        control={control}
        render={({ field }) => (
          <TextField
            {...field}
            label="Placeholder Text"
            placeholder="Enter text..."
            size="small"
            fullWidth
          />
        )}
      />

      <Stack direction="row" spacing={2}>
        <Controller
          name="settings.min_length"
          control={control}
          render={({ field }) => (
            <TextField
              {...field}
              label="Min Length"
              placeholder="0"
              type="number"
              size="small"
              fullWidth
              onChange={(e) => field.onChange(Number(e.target.value))}
            />
          )}
        />
        <Controller
          name="settings.max_length"
          control={control}
          render={({ field }) => (
            <TextField
              {...field}
              label="Max Length"
              placeholder="500"
              type="number"
              size="small"
              fullWidth
              onChange={(e) => field.onChange(Number(e.target.value))}
            />
          )}
        />
      </Stack>
    </Stack>
  );
}
