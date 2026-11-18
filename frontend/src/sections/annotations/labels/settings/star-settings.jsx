import PropTypes from "prop-types";
import { Stack, TextField } from "@mui/material";
import { Controller } from "react-hook-form";
import { enqueueSnackbar } from "notistack";

const MAX_STARS = 10;

StarSettings.propTypes = {
  control: PropTypes.object.isRequired,
};

export default function StarSettings({ control }) {
  return (
    <Stack spacing={2}>
      <Controller
        name="settings.no_of_stars"
        control={control}
        rules={{
          required: "Required",
          min: { value: 1, message: "Minimum 1 star" },
          max: { value: MAX_STARS, message: `Maximum ${MAX_STARS} stars` },
        }}
        render={({ field, fieldState }) => (
          <TextField
            {...field}
            label="Number of Stars"
            placeholder="5"
            type="number"
            size="small"
            fullWidth
            error={!!fieldState.error}
            helperText={fieldState.error?.message}
            inputProps={{ min: 1, max: MAX_STARS }}
            onChange={(e) => {
              const raw = e.target.value;
              if (raw === "") {
                field.onChange("");
                return;
              }
              const num = Number(raw);
              if (num > MAX_STARS) {
                enqueueSnackbar(`Maximum value is ${MAX_STARS} stars only`, {
                  variant: "warning",
                });
                return;
              }
              field.onChange(num);
            }}
          />
        )}
      />
    </Stack>
  );
}
