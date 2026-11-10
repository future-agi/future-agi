import PropTypes from "prop-types";
import { useRef, useState } from "react";
import { Autocomplete, TextField } from "@mui/material";
import { sourceMatches } from "./builderSpec";

// Use the app's accessible combobox: selected sources are identities, not labels.
export default function FieldPicker({
  input,
  options,
  onChange,
  onSearch,
  disabled,
  loading,
  label = "Field",
}) {
  const selected =
    options.find((option) => sourceMatches(input, option)) ||
    (input?.source
      ? {
          id: "saved",
          label:
            input.attribute_key ||
            input.field ||
            `Saved evaluation ${input.config_id}`,
          group: "Saved source",
          descriptor: input,
        }
      : null);
  const [text, setText] = useState(selected?.label || "");
  const searching = useRef(false);
  return (
    <Autocomplete
      fullWidth
      size="small"
      disabled={disabled}
      loading={loading}
      options={
        selected && !options.includes(selected)
          ? [selected, ...options]
          : options
      }
      value={selected}
      inputValue={text}
      onBlur={() => {
        searching.current = false;
        setText(selected?.label || "");
      }}
      groupBy={(option) => option.group}
      getOptionLabel={(option) => option.label}
      isOptionEqualToValue={(option, value) => option.id === value.id}
      filterOptions={(available) => available}
      onInputChange={(_, next, reason) => {
        if (reason === "input" || reason === "clear") {
          searching.current = true;
          setText(next);
          onSearch?.(next);
        } else if (reason === "reset" && !searching.current) setText(next);
      }}
      onChange={(_, option) => {
        if (option && option.id !== "saved") {
          searching.current = false;
          setText(option.label);
          onChange(option);
        }
      }}
      disableClearable
      noOptionsText="No supported sources found"
      renderOption={(props, option) => (
        <li {...props} key={option.id}>
          {option.label}
        </li>
      )}
      renderInput={(params) => (
        <TextField
          {...params}
          label={label}
          placeholder="Search span attributes and saved evaluations"
        />
      )}
    />
  );
}
FieldPicker.propTypes = {
  input: PropTypes.object,
  options: PropTypes.array.isRequired,
  onChange: PropTypes.func.isRequired,
  onSearch: PropTypes.func,
  disabled: PropTypes.bool,
  loading: PropTypes.bool,
  label: PropTypes.string,
};
