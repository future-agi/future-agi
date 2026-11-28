import { useId } from "react";
import PropTypes from "prop-types";
import { FormControl, FormControlLabel, FormHelperText, FormLabel, MenuItem, Radio, RadioGroup, Stack, TextField, Typography } from "@mui/material";
import Section from "src/components/section";
import { BEHAVIOR_ACTIONS, behaviorPartial } from "./applicationBehavior";

// A short list is one visible choice (one click); a long one stays a select so the dialog never
// grows a column of options. "Not specified" is always the first option.
const RADIO_LIMIT = 5;

function Choice({ label, value, options, onChange, error = false, helperText }) {
  const id = useId();
  const all = [["", "Not specified"], ...options];
  if (all.length > RADIO_LIMIT) {
    return (
      <TextField select size="small" label={label} value={value} error={error} helperText={helperText} onChange={(event) => onChange(event.target.value)}>
        {all.map(([key, text]) => <MenuItem key={key || "none"} value={key}>{text}</MenuItem>)}
      </TextField>
    );
  }
  return (
    <FormControl error={error} sx={{ minWidth: 0 }}>
      <FormLabel id={`${id}-label`}><Typography component="span" variant="s3">{label}</Typography></FormLabel>
      <RadioGroup row aria-labelledby={`${id}-label`} value={value} onChange={(event) => onChange(event.target.value)} sx={{ columnGap: 1 }}>
        {all.map(([key, text]) => <FormControlLabel key={key || "none"} value={key} control={<Radio size="small" />} label={<Typography variant="s1">{text}</Typography>} />)}
      </RadioGroup>
      {helperText && <FormHelperText>{helperText}</FormHelperText>}
    </FormControl>
  );
}
Choice.propTypes = {
  label: PropTypes.string.isRequired,
  value: PropTypes.string.isRequired,
  options: PropTypes.array.isRequired,
  onChange: PropTypes.func.isRequired,
  error: PropTypes.bool,
  helperText: PropTypes.string,
};

export default function ApplicationBehaviorFields({ skills, behavior, onChange }) {
  const partial = behaviorPartial(behavior);
  return (
    <Section
      title="Required application behavior"
      description="The skill the application should open and the action it should take. Leave both as Not specified to check the answer only."
    >
      <Stack spacing={1.5}>
        <Choice
          label="Required skill"
          value={behavior.skillId}
          options={skills.map((skill) => [skill.id, skill.label])}
          onChange={(skillId) => onChange({ skillId })}
        />
        <Choice
          label="Required business action"
          value={behavior.action}
          options={BEHAVIOR_ACTIONS}
          error={partial}
          helperText={partial ? "Choose both a skill and an action, or leave both as Not specified." : undefined}
          onChange={(action) => onChange({ action })}
        />
      </Stack>
    </Section>
  );
}
ApplicationBehaviorFields.propTypes = {
  skills: PropTypes.array.isRequired,
  behavior: PropTypes.shape({ skillId: PropTypes.string, action: PropTypes.string }).isRequired,
  onChange: PropTypes.func.isRequired,
};
