// the interface contract (lane w3-rows-in A7, D11): the sheet's last step can keep the import
// as a saved source on the dataset. `Refresh now` (dataset header › Sources)
// then adds only what is new. Only a manual refresh exists until schedules
// land, so no cadence is offered.
import React from "react";
import PropTypes from "prop-types";
import {
  Checkbox,
  FormControlLabel,
  MenuItem,
  Radio,
  RadioGroup,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { IMPORT_WINDOW_OPTIONS } from "./mappingModel";

export default function KeepAsSource({ value, onChange, noun }) {
  const set = (patch) => onChange({ ...value, ...patch });
  return (
    <Stack spacing={1} data-testid="keep-as-source">
      <FormControlLabel
        sx={{ m: 0 }}
        control={
          <Checkbox
            size="small"
            checked={value.keep}
            onChange={(e) => set({ keep: e.target.checked })}
            inputProps={{ "data-testid": "keep-as-source-toggle" }}
          />
        }
        label={<Typography typography="s2">Keep this as a source</Typography>}
      />
      {value.keep ? (
        <Stack spacing={1} sx={{ pl: 3.5 }}>
          <TextField
            size="small"
            label="Source name"
            value={value.name}
            onChange={(e) => set({ name: e.target.value })}
            error={!value.name.trim()}
            helperText={!value.name.trim() ? "Name the source" : undefined}
            inputProps={{ "data-testid": "keep-as-source-name" }}
          />
          <Stack direction="row" alignItems="center" gap={1}>
            <Typography typography="s2" color="text.secondary" noWrap>
              {`Window: ${noun} from the`}
            </Typography>
            <TextField
              select
              size="small"
              value={value.window}
              onChange={(e) => set({ window: e.target.value })}
              SelectProps={{
                SelectDisplayProps: { "data-testid": "keep-as-source-window" },
              }}
              sx={{ minWidth: 150 }}
            >
              {IMPORT_WINDOW_OPTIONS.map((w) => (
                <MenuItem key={w.id} value={w.id}>
                  {w.label.replace(/^Last /, "last ")}
                </MenuItem>
              ))}
            </TextField>
          </Stack>
          <Stack spacing={0.25}>
            <Typography typography="s2" color="text.secondary">
              Each refresh
            </Typography>
            <RadioGroup
              value={value.onRefresh}
              onChange={(e) => set({ onRefresh: e.target.value })}
            >
              <FormControlLabel
                value="pending"
                control={<Radio size="small" />}
                label={
                  <Typography typography="s2">
                    adds new rows as pending changes
                  </Typography>
                }
              />
              <FormControlLabel
                value="version"
                control={<Radio size="small" />}
                label={
                  <Typography typography="s2">saves a new version</Typography>
                }
              />
            </RadioGroup>
          </Stack>
        </Stack>
      ) : null}
    </Stack>
  );
}

KeepAsSource.propTypes = {
  value: PropTypes.shape({
    keep: PropTypes.bool,
    name: PropTypes.string,
    window: PropTypes.string,
    onRefresh: PropTypes.string,
  }).isRequired,
  onChange: PropTypes.func.isRequired,
  noun: PropTypes.string.isRequired,
};
