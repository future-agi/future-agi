import PropTypes from "prop-types";
import { MenuItem, TextField } from "@mui/material";
import {
  NUMBER_FIELDS,
  OBJECT_FIELDS,
  TEST_CONDITIONS_COPY,
  objectProblem,
} from "./testConditions.constants";

// What the browser may hold a field to, from the tool's bounds.
const inputBounds = (bounds = {}) =>
  "max_length" in bounds
    ? { maxLength: bounds.max_length }
    : { min: bounds.min, max: bounds.max };

/**
 * One field a test condition's mode takes, as the server lists it: a select when the mode serves its choices
 * (`<field>_options`), key/value conditions as JSON, a number, or text.
 */
export default function TestConditionField({
  name,
  mode,
  value,
  bounds,
  onChange,
}) {
  const label = TEST_CONDITIONS_COPY.fields[name] || name;
  const choices = mode[`${name}_options`];
  if (choices)
    return (
      <TextField
        select
        size="small"
        label={label}
        value={value}
        onChange={onChange}
        sx={{ minWidth: 220 }}
      >
        {choices.map((one) => (
          <MenuItem key={one.value} value={one.value}>
            {one.label}
          </MenuItem>
        ))}
      </TextField>
    );
  if (OBJECT_FIELDS.includes(name)) {
    const problem = objectProblem(value);
    return (
      <TextField
        size="small"
        label={label}
        value={value}
        onChange={onChange}
        error={Boolean(problem)}
        helperText={problem || TEST_CONDITIONS_COPY.conditionsHelp}
        sx={{ flex: 1, minWidth: 220 }}
      />
    );
  }
  const number = NUMBER_FIELDS.includes(name);
  return (
    <TextField
      size="small"
      type={number ? "number" : "text"}
      label={label}
      value={value}
      onChange={onChange}
      inputProps={inputBounds(bounds)}
      sx={number ? undefined : { flex: 1 }}
    />
  );
}

TestConditionField.propTypes = {
  // The field's name, as the mode's `fields` lists it.
  name: PropTypes.string.isRequired,
  // The picked mode; `<name>_options` on it are the field's choices, when served.
  mode: PropTypes.object.isRequired,
  value: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  // The tool's bounds for this field, if any.
  bounds: PropTypes.object,
  onChange: PropTypes.func.isRequired,
};
