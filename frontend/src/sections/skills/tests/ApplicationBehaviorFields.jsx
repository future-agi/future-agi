import PropTypes from "prop-types";
import { MenuItem, Stack, TextField } from "@mui/material";
import Section from "src/components/section";
import { BEHAVIOR_ACTIONS, behaviorPartial } from "./applicationBehavior";

export default function ApplicationBehaviorFields({ skills, behavior, onChange }) {
  const partial = behaviorPartial(behavior);
  return (
    <Section
      title="Required application behavior"
      description="The skill the application should open and the action it should take. Leave both as Not specified to check the answer only."
    >
      <Stack spacing={1.5}>
        <TextField
          select
          size="small"
          label="Required skill"
          value={behavior.skillId}
          onChange={(event) => onChange({ skillId: event.target.value })}
        >
          <MenuItem value="">Not specified</MenuItem>
          {skills.map((skill) => (
            <MenuItem key={skill.id} value={skill.id}>
              {skill.label}
            </MenuItem>
          ))}
        </TextField>
        <TextField
          select
          size="small"
          label="Required business action"
          value={behavior.action}
          error={partial}
          helperText={
            partial ? "Choose both a skill and an action, or leave both as Not specified." : undefined
          }
          onChange={(event) => onChange({ action: event.target.value })}
        >
          <MenuItem value="">Not specified</MenuItem>
          {BEHAVIOR_ACTIONS.map(([action, label]) => (
            <MenuItem key={action} value={action}>
              {label}
            </MenuItem>
          ))}
        </TextField>
      </Stack>
    </Section>
  );
}
ApplicationBehaviorFields.propTypes = {
  skills: PropTypes.array.isRequired,
  behavior: PropTypes.shape({ skillId: PropTypes.string, action: PropTypes.string }).isRequired,
  onChange: PropTypes.func.isRequired,
};
