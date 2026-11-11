import PropTypes from "prop-types";
import { MenuItem, Stack, TextField } from "@mui/material";
import Section from "src/components/section";

// Reserved fixture read by the hosted application run. The picker writes it so a
// reviewer never types a skill identity or the JSON envelope by hand.
export const BEHAVIOR_FIXTURE = "fi.application.expectations";
export const BEHAVIOR_ACTIONS = [
  ["issue_refund", "Issue a refund"],
  ["open_chargeback", "Open a chargeback dispute"],
  ["track_shipment", "Track the shipment"],
  ["none", "No business action"],
];
export const readBehavior = (fixtures) => {
  const item = fixtures.find((fixture) => fixture.name === BEHAVIOR_FIXTURE);
  try {
    const parsed = JSON.parse(item?.text ?? "");
    return { skillId: parsed.skill_id || "", action: parsed.action || "" };
  } catch {
    return { skillId: "", action: "" };
  }
};
export const behaviorPartial = (behavior) =>
  Boolean(behavior.skillId) !== Boolean(behavior.action);
/** Fixture list with the reserved expectation fixture set (or removed when incomplete). */
export const withBehavior = (fixtures, behavior, newId) => {
  const others = fixtures.filter((fixture) => fixture.name !== BEHAVIOR_FIXTURE);
  const existing = fixtures.find((fixture) => fixture.name === BEHAVIOR_FIXTURE);
  if (!behavior.skillId || !behavior.action) return others;
  return [
    ...others,
    {
      id: existing?.id || newId(),
      name: BEHAVIOR_FIXTURE,
      text: JSON.stringify({ schema: 1, skill_id: behavior.skillId, action: behavior.action }),
    },
  ];
};

export function ApplicationBehaviorFields({ skills, behavior, onChange }) {
  const partial = behaviorPartial(behavior);
  return (
    <Section
      title="Required application behavior"
      description="Which skill the application must load and which business action it must take. Chosen from this context, not typed."
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
            partial ? "Choose both the skill and the action, or neither." : undefined
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
