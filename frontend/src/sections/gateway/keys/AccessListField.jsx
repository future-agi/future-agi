import React from "react";
import PropTypes from "prop-types";
import { Autocomplete, Chip, TextField } from "@mui/material";

import { LABEL_ABOVE_STYLE } from "./labelAbove";

// A key's allow-list (models or providers): pick from what the org has, or
// type any id and press Enter. Empty means everything is allowed.
export default function AccessListField({
  label,
  value,
  onChange,
  options,
  emptyMeaning,
}) {
  return (
    <Autocomplete
      multiple
      freeSolo
      size="small"
      options={options}
      value={value}
      onChange={(_, next) =>
        onChange(
          Array.from(
            new Set(next.map((item) => String(item).trim()).filter(Boolean)),
          ),
        )
      }
      renderTags={(tags, getTagProps) =>
        tags.map((option, index) => (
          <Chip
            variant="outlined"
            label={option}
            size="small"
            {...getTagProps({ index })}
            key={option}
          />
        ))
      }
      renderInput={(params) => (
        <TextField
          {...params}
          label={label}
          placeholder={value.length ? "" : "Pick or type, then Enter"}
          helperText={emptyMeaning}
          InputLabelProps={{
            ...params.InputLabelProps,
            style: LABEL_ABOVE_STYLE,
          }}
        />
      )}
    />
  );
}

AccessListField.propTypes = {
  label: PropTypes.string.isRequired,
  value: PropTypes.arrayOf(PropTypes.string).isRequired,
  onChange: PropTypes.func.isRequired,
  options: PropTypes.arrayOf(PropTypes.string).isRequired,
  emptyMeaning: PropTypes.string,
};
