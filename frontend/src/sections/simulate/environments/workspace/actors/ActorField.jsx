import PropTypes from "prop-types";
import { MenuItem, TextField } from "@mui/material";
import { ACTOR_FORM_SHAPE, OPTIONS_SHAPE } from "./actors.shapes";
import { withStart } from "./actorUtils";

export default function ActorField({
  form,
  name,
  label,
  options,
  startLabel,
  helperText,
  ...extra
}) {
  const problem = form.problems[name];
  const common = {
    size: "small",
    label,
    value: form.value(name),
    onChange: form.set(name),
    error: Boolean(problem),
    ...extra,
    helperText: problem || helperText,
  };
  if (!options) return <TextField {...common} />;
  return (
    <TextField select SelectProps={{ displayEmpty: true }} {...common}>
      {withStart(options, form.initial[name], startLabel).map((one) => (
        <MenuItem key={one.value || "empty"} value={one.value}>
          {one.label}
        </MenuItem>
      ))}
    </TextField>
  );
}

ActorField.propTypes = {
  form: ACTOR_FORM_SHAPE.isRequired,
  name: PropTypes.string.isRequired,
  label: PropTypes.string.isRequired,
  // Absent for a text input.
  options: OPTIONS_SHAPE,
  // Label for a starting value the options don't list.
  startLabel: PropTypes.string,
  helperText: PropTypes.node,
};
