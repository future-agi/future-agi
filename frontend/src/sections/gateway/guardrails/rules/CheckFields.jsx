import React from "react";
import PropTypes from "prop-types";
import Autocomplete from "@mui/material/Autocomplete";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import MenuItem from "@mui/material/MenuItem";
import TextField from "@mui/material/TextField";
import { useQuery } from "@tanstack/react-query";

import axios, { endpoints } from "src/utils/axios";
import { invalidSelectValue } from "../catalog/form-model";

// Options an async field fetches (today only Future AGI's Protect templates).
function useAsyncOptions(fetchKey) {
  return useQuery({
    queryKey: ["agentcc-guardrail-field-options", fetchKey],
    queryFn: async () => {
      const { data } = await axios.get(endpoints.gateway[fetchKey]);
      const list = data?.result || data?.data || data || [];
      return (Array.isArray(list) ? list : []).map((item) => ({
        value: String(item.evalId ?? item.eval_id ?? item.id ?? item.value),
        label:
          item.name ||
          item.label ||
          String(item.evalId ?? item.eval_id ?? item.id),
      }));
    },
    enabled: Boolean(fetchKey),
    staleTime: 300000,
  });
}

function AsyncMultiSelect({ field, value, onChange, disabled }) {
  const {
    data: options = [],
    isLoading,
    isError,
  } = useAsyncOptions(field.fetchKey);
  const labels = new Map(options.map((option) => [option.value, option.label]));
  const values = options.map((option) => option.value);
  return (
    <Autocomplete
      multiple
      size="small"
      disabled={disabled}
      loading={isLoading}
      options={values}
      getOptionLabel={(option) => labels.get(option) || option}
      value={(Array.isArray(value) ? value : []).filter((v) => labels.has(v))}
      onChange={(_, next) => onChange(next)}
      renderTags={(selected, getTagProps) =>
        selected.map((option, index) => (
          <Chip
            variant="outlined"
            key={option}
            size="small"
            label={labels.get(option) || option}
            {...getTagProps({ index })}
          />
        ))
      }
      renderInput={(params) => (
        <TextField
          {...params}
          label={field.label}
          required={field.required}
          error={isError}
          helperText={isError ? "The list could not be loaded." : undefined}
          InputProps={{
            ...params.InputProps,
            endAdornment: (
              <>
                {isLoading ? <CircularProgress size={16} /> : null}
                {params.InputProps.endAdornment}
              </>
            ),
          }}
        />
      )}
    />
  );
}

AsyncMultiSelect.propTypes = {
  field: PropTypes.object.isRequired,
  value: PropTypes.any,
  onChange: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
};

/** One of a check's own settings, by its catalog field type. */
export default function CheckField({
  field,
  value,
  onChange,
  stored,
  disabled,
}) {
  const policyOmitted = field.key === "mode" && value === undefined;
  const common = {
    label: field.label,
    required: field.required,
    disabled,
    fullWidth: true,
    size: "small",
  };
  switch (field.type) {
    case "password":
      return (
        <TextField
          {...common}
          type="password"
          autoComplete="new-password"
          value={value || ""}
          onChange={(event) => onChange(event.target.value)}
          required={field.required && !stored}
          placeholder={stored ? "Saved" : field.placeholder}
          helperText={
            stored
              ? "Saved. Leave blank to keep it, or type a new one."
              : undefined
          }
        />
      );
    case "select":
      return (
        <TextField
          {...common}
          select
          value={value ?? ""}
          SelectProps={{
            displayEmpty: policyOmitted || invalidSelectValue(field, value),
          }}
          InputLabelProps={
            policyOmitted || invalidSelectValue(field, value)
              ? { shrink: true }
              : undefined
          }
          error={invalidSelectValue(field, value)}
          helperText={
            invalidSelectValue(field, value)
              ? "Invalid stored setting. Restore the intended policy from a known configuration before saving."
              : policyOmitted
                ? "No policy mode is stored. Saving without selecting one keeps it omitted."
                : field.helperText
          }
          onChange={(event) => onChange(event.target.value)}
        >
          {policyOmitted && (
            <MenuItem value="" disabled>
              Not stored
            </MenuItem>
          )}
          {invalidSelectValue(field, value) && (
            <MenuItem value={value ?? ""} disabled>
              Invalid stored value:{" "}
              {value === undefined
                ? "(missing)"
                : value === ""
                  ? "(empty)"
                  : String(value)}
            </MenuItem>
          )}
          {!invalidSelectValue(field, value) &&
            typeof value === "string" &&
            value !== "" &&
            !field.options?.includes(value) && (
              <MenuItem value={value}>{value}</MenuItem>
            )}
          {(field.options || []).map((option) => (
            <MenuItem key={option} value={option}>
              {option}
            </MenuItem>
          ))}
        </TextField>
      );
    case "multiselect":
      return (
        <Autocomplete
          multiple
          size="small"
          disabled={disabled}
          options={field.options || []}
          value={Array.isArray(value) ? value : []}
          onChange={(_, next) => onChange(next)}
          renderTags={(selected, getTagProps) =>
            selected.map((option, index) => (
              <Chip
                variant="outlined"
                key={option}
                size="small"
                label={option}
                {...getTagProps({ index })}
              />
            ))
          }
          renderInput={(params) => (
            <TextField
              {...params}
              label={field.label}
              required={field.required}
            />
          )}
        />
      );
    case "async-multiselect":
      return (
        <AsyncMultiSelect
          field={field}
          value={value}
          onChange={onChange}
          disabled={disabled}
        />
      );
    case "number":
      return (
        <TextField
          {...common}
          type="number"
          value={value ?? ""}
          onChange={(event) => onChange(event.target.value)}
          inputProps={{ min: field.min, max: field.max, step: field.step }}
          placeholder={field.placeholder}
          helperText={field.helperText}
        />
      );
    case "multiline-list":
    case "topic-list":
      return (
        <TextField
          {...common}
          multiline
          minRows={3}
          value={value || ""}
          onChange={(event) => onChange(event.target.value)}
          placeholder={field.placeholder}
          helperText={field.helperText}
        />
      );
    default:
      return (
        <TextField
          {...common}
          value={value ?? ""}
          onChange={(event) => onChange(event.target.value)}
          placeholder={field.placeholder || field.defaultValue}
          helperText={field.helperText}
        />
      );
  }
}

CheckField.propTypes = {
  field: PropTypes.shape({
    key: PropTypes.string.isRequired,
    label: PropTypes.string.isRequired,
    type: PropTypes.string,
    required: PropTypes.bool,
    placeholder: PropTypes.string,
    helperText: PropTypes.string,
    defaultValue: PropTypes.any,
    options: PropTypes.arrayOf(PropTypes.string),
    closedOptions: PropTypes.bool,
    min: PropTypes.number,
    max: PropTypes.number,
    step: PropTypes.number,
    fetchKey: PropTypes.string,
  }).isRequired,
  value: PropTypes.any,
  onChange: PropTypes.func.isRequired,
  stored: PropTypes.bool,
  disabled: PropTypes.bool,
};
