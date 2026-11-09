import React from "react";
import PropTypes from "prop-types";
import { Autocomplete, Chip, TextField } from "@mui/material";

import { useKeyProviderOptions } from "./hooks/useKeyProviderOptions";

// A key's "Allowed providers": the org's providers and, when credits are
// enabled for the organization and offered by the deployment, Future AGI
// credits, or any provider id typed in. Rendered
// inside an open dialog only, so its queries run when it is shown.
const AllowedProvidersField = ({
  value,
  onChange,
  gatewayId,
  label = "Allowed providers",
}) => {
  const { options, labelFor } = useKeyProviderOptions(gatewayId);
  return (
    <Autocomplete
      multiple
      freeSolo
      size="small"
      options={options}
      getOptionLabel={labelFor}
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
            label={labelFor(option)}
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
          placeholder="Pick or type a provider (empty = all)"
          helperText="Leave empty to allow all providers"
        />
      )}
    />
  );
};

AllowedProvidersField.propTypes = {
  gatewayId: PropTypes.string,
  label: PropTypes.string,
  value: PropTypes.arrayOf(PropTypes.string).isRequired,
  onChange: PropTypes.func.isRequired,
};

export default AllowedProvidersField;
