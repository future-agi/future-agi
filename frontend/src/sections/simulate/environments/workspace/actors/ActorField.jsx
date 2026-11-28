import PropTypes from "prop-types";
import { MenuItem, TextField, Typography } from "@mui/material";
import { useController } from "react-hook-form";
import { ACTOR_FORM_SHAPE, OPTIONS_SHAPE } from "./actors.shapes";
import { withStart } from "./actorUtils";

// One field of the actor form, given its value and change handler: for a choice worked out from other fields.
export function ActorFieldView({
  form,
  name,
  label,
  options,
  startLabel,
  helperText,
  value,
  onChange,
  ...extra
}) {
  const problem = form.problems[name];
  const common = {
    size: "small",
    label,
    value,
    onChange,
    error: Boolean(problem),
    ...extra,
    helperText: problem || helperText,
  };
  if (!options) return <TextField {...common} />;
  return (
    <TextField select SelectProps={{ displayEmpty: true }} {...common}>
      {withStart(options, form.initial[name], startLabel).map((one) => (
        <MenuItem
          key={one.value || "empty"}
          value={one.value}
          disabled={Boolean(one.disabled)}
          sx={one.note ? { display: "block", whiteSpace: "normal" } : undefined}
        >
          {one.label}
          {one.note && (
            <Typography
              component="span"
              sx={{
                display: "block",
                typography: "s3",
                color: "text.secondary",
              }}
            >
              {one.note}
            </Typography>
          )}
        </MenuItem>
      ))}
    </TextField>
  );
}

// One field of the actor form, bound to its value there. Writes go through `form.put`, which marks the field edited.
export default function ActorField({ form, name, ...rest }) {
  const { field } = useController({ name });
  return (
    <ActorFieldView
      form={form}
      name={name}
      value={field.value}
      onChange={(event) => form.put(name, event.target.value)}
      inputRef={field.ref}
      {...rest}
    />
  );
}

ActorField.propTypes = {
  form: ACTOR_FORM_SHAPE.isRequired,
  name: PropTypes.string.isRequired,
};

ActorFieldView.propTypes = {
  form: ACTOR_FORM_SHAPE.isRequired,
  name: PropTypes.string.isRequired,
  label: PropTypes.string.isRequired,
  // Absent for a text input.
  options: OPTIONS_SHAPE,
  // Label for a starting value the options don't list.
  startLabel: PropTypes.string,
  helperText: PropTypes.node,
  value: PropTypes.any,
  onChange: PropTypes.func.isRequired,
};
