import PropTypes from "prop-types";
import {
  Box,
  Button,
  Checkbox,
  FormControlLabel,
  IconButton,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { Controller, useFieldArray } from "react-hook-form";
import Iconify from "src/components/iconify";

CategoricalSettings.propTypes = {
  control: PropTypes.object.isRequired,
};

export default function CategoricalSettings({ control }) {
  const { fields, append, remove } = useFieldArray({
    control,
    name: "settings.options",
  });

  return (
    <Stack spacing={2}>
      <Typography variant="subtitle2">Options</Typography>

      {fields.map((field, index) => (
        <Stack key={field.id} direction="row" spacing={1} alignItems="center">
          <Controller
            name={`settings.options.${index}.label`}
            control={control}
            rules={{ required: "Option label is required" }}
            render={({ field: inputField, fieldState }) => (
              <TextField
                {...inputField}
                size="small"
                fullWidth
                placeholder={`Option ${index + 1}`}
                error={!!fieldState.error}
                helperText={fieldState.error?.message}
              />
            )}
          />
          {fields.length > 1 && (
            <IconButton size="small" onClick={() => remove(index)}>
              <Iconify icon="solar:close-linear" width={20} />
            </IconButton>
          )}
        </Stack>
      ))}

      <Box>
        <Button
          size="small"
          variant="outlined"
          color="primary"
          startIcon={<Iconify icon="solar:add-linear" />}
          onClick={() => append({ label: "" })}
        >
          Add Option
        </Button>
      </Box>

      <Controller
        name="settings.multi_choice"
        control={control}
        render={({ field }) => (
          <FormControlLabel
            control={
              <Checkbox
                checked={field.value || false}
                onChange={(e) => field.onChange(e.target.checked)}
              />
            }
            label="Allow multiple selection"
          />
        )}
      />
    </Stack>
  );
}
