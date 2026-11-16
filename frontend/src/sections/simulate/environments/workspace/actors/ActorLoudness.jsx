import PropTypes from "prop-types";
import {
  Box,
  FormControlLabel,
  Slider,
  Stack,
  Switch,
  Typography,
} from "@mui/material";
import { ACTORS_COPY } from "./actors.constants";

const F = ACTORS_COPY.worldActors.form;

// How loud a voice beside the caller is: automatic (each call picks within the release's range) or a fixed share.
export default function ActorLoudness({ value, automatic, onChange }) {
  const fixed = typeof value === "number";
  const [low, high] = automatic;
  const percent = (ratio) => `${Math.round(ratio * 100)}%`;
  return (
    <Box>
      <Typography sx={{ typography: "s2", fontWeight: 600 }}>
        {F.loudness}
      </Typography>
      <FormControlLabel
        control={
          <Switch
            checked={!fixed}
            onChange={(event) =>
              onChange(
                event.target.checked
                  ? null
                  : Math.round(((low + high) / 2) * 20) / 20,
              )
            }
          />
        }
        label={F.loudnessAutomatic(percent(low), percent(high))}
      />
      {fixed && (
        <Stack direction="row" spacing={2} alignItems="center" sx={{ px: 1 }}>
          <Slider
            aria-label={F.loudness}
            value={value}
            min={0.05}
            max={1}
            step={0.05}
            valueLabelDisplay="auto"
            valueLabelFormat={percent}
            onChange={(_, next) => onChange(next)}
          />
          <Typography sx={{ typography: "s2", minWidth: 120 }}>
            {F.loudnessFixed(percent(value))}
          </Typography>
        </Stack>
      )}
    </Box>
  );
}

ActorLoudness.propTypes = {
  // A fixed share of the caller's voice, or null for automatic.
  value: PropTypes.number,
  automatic: PropTypes.arrayOf(PropTypes.number).isRequired,
  onChange: PropTypes.func,
};
