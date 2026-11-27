import PropTypes from "prop-types";
import {
  Box,
  FormControlLabel,
  Slider,
  Stack,
  Switch,
  Typography,
} from "@mui/material";
import { useController } from "react-hook-form";
import { ACTORS_COPY } from "./actors.constants";
import { ACTOR_FORM_SHAPE } from "./actors.shapes";

const F = ACTORS_COPY.worldActors.form;

export default function ActorLoudness({ form, bounds }) {
  const { field } = useController({ name: "loudness_ratio" });
  const { value } = field;
  const problem = form.problems.loudness_ratio;
  const onChange = (next) => form.put("loudness_ratio", next);
  const fixed = typeof value === "number";
  const [low, high] = bounds.automatic;
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
              onChange(event.target.checked ? null : bounds.default)
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
            min={bounds.min}
            max={bounds.max}
            step={bounds.step}
            valueLabelDisplay="auto"
            valueLabelFormat={percent}
            onChange={(_, next) => onChange(next)}
          />
          <Typography sx={{ typography: "s2", minWidth: 120 }}>
            {F.loudnessFixed(percent(value))}
          </Typography>
        </Stack>
      )}
      {problem && (
        <Typography sx={{ typography: "s3", color: "error.main" }}>
          {problem}
        </Typography>
      )}
    </Box>
  );
}

ActorLoudness.propTypes = {
  form: ACTOR_FORM_SHAPE.isRequired,
  bounds: PropTypes.shape({
    automatic: PropTypes.arrayOf(PropTypes.number).isRequired,
    min: PropTypes.number.isRequired,
    max: PropTypes.number.isRequired,
    step: PropTypes.number.isRequired,
    default: PropTypes.number.isRequired,
  }).isRequired,
};
