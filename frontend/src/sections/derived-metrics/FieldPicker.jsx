import PropTypes from "prop-types";
import { useRef, useState } from "react";
import { Autocomplete, Box, TextField } from "@mui/material";
import { sourceMatches } from "./builderSpec";

// What each option holds, so a person can tell a number from a yes/no field
// before picking it. Saved evaluations read their numeric channel by default.
const TYPE_HINTS = { number: "Number", boolean: "Yes/no", text: "Text" };
const typeHint = (option) =>
  option.descriptor?.source === "eval"
    ? "Evaluation"
    : TYPE_HINTS[option.descriptor?.value_type] || "";

// Use the app's accessible combobox: selected sources are identities, not labels.
export default function FieldPicker({
  input,
  options,
  onChange,
  onSearch,
  disabled,
  loading,
  label = "Field",
  helperText,
  projectName,
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
  const where = projectName ? ` in ${projectName}` : "";
  const search = searching.current ? text.trim() : "";
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
      loadingText="Loading fields…"
      noOptionsText={
        // The search covers span fields and saved evaluations alike.
        search
          ? `No fields or saved evaluations match “${search}”${where}`
          : `No fields or saved evaluations${where}`
      }
      renderOption={(props, option) => (
        <li {...props} key={option.id}>
          <Box
            component="span"
            sx={{
              display: "flex",
              alignItems: "baseline",
              gap: 1,
              width: "100%",
              minWidth: 0,
            }}
          >
            <Box
              component="span"
              sx={{
                minWidth: 0,
                flex: 1,
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
            >
              {option.label}
            </Box>
            {/* Not part of the option's name: assistive tech reads the
                field's name, as a picked value shows it. */}
            <Box
              component="span"
              aria-hidden="true"
              sx={{ flexShrink: 0, typography: "s3", color: "text.secondary" }}
            >
              {typeHint(option)}
            </Box>
          </Box>
        </li>
      )}
      renderInput={(params) => (
        <TextField
          {...params}
          label={label}
          helperText={helperText}
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
  helperText: PropTypes.string,
  projectName: PropTypes.string,
};
