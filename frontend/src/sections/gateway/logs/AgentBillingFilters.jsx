import React from "react";
import PropTypes from "prop-types";
import {
  Autocomplete,
  Box,
  Checkbox,
  Chip,
  MenuItem,
  TextField,
  Typography,
} from "@mui/material";
import {
  BILLING_MODES,
  ERROR_CLASSES,
  POLICY_VARIANTS,
  STEPS,
  isKnownValue,
  labelOf,
} from "./explorerLabels";

// The filter panel's agent and billing fields (explorerFilters.js). The panel
// owns the values; each field reports a patch of the keys it edits.

const ERROR_CLASS_KEYS = Object.keys(ERROR_CLASSES);

const Field = ({ label, children }) => (
  <Box>
    <Typography variant="subtitle2" gutterBottom>
      {label}
    </Typography>
    {children}
  </Box>
);

Field.propTypes = {
  label: PropTypes.string.isRequired,
  children: PropTypes.node.isRequired,
};

// A value from a shared URL that is not a known option is still listed, so
// each field shows what is applied and Apply keeps it.
function ChoiceField({ label, options, value, onChange }) {
  const entries = Object.entries(options);
  if (value && !isKnownValue(options, value)) entries.push([value, value]);
  return (
    <Field label={label}>
      <TextField
        select
        size="small"
        fullWidth
        value={value}
        onChange={(event) => onChange(event.target.value)}
        SelectProps={{ displayEmpty: true }}
      >
        <MenuItem value="">Any</MenuItem>
        {entries.map(([optionValue, optionLabel]) => (
          <MenuItem key={optionValue} value={optionValue}>
            {optionLabel}
          </MenuItem>
        ))}
      </TextField>
    </Field>
  );
}

ChoiceField.propTypes = {
  label: PropTypes.string.isRequired,
  options: PropTypes.object.isRequired,
  value: PropTypes.string.isRequired,
  onChange: PropTypes.func.isRequired,
};

function ManyField({
  label,
  options,
  getLabel,
  value,
  onChange,
  placeholder,
  helperText,
}) {
  const listed = [...options, ...value.filter((v) => !options.includes(v))];
  return (
    <Field label={label}>
      <Autocomplete
        multiple
        disableCloseOnSelect
        size="small"
        options={listed}
        value={value}
        getOptionLabel={getLabel}
        onChange={(_event, picked) => onChange(picked)}
        renderOption={({ key, ...optionProps }, option, { selected }) => (
          <li key={key} {...optionProps}>
            <Checkbox size="small" checked={selected} sx={{ mr: 1 }} />
            {getLabel(option)}
          </li>
        )}
        renderTags={(picked, getTagProps) =>
          picked.map((option, index) => (
            <Chip
              label={getLabel(option)}
              size="small"
              {...getTagProps({ index })}
              key={option}
            />
          ))
        }
        renderInput={(params) => (
          <TextField
            {...params}
            placeholder={value.length ? "" : placeholder}
            helperText={helperText}
          />
        )}
      />
    </Field>
  );
}

ManyField.propTypes = {
  label: PropTypes.string.isRequired,
  options: PropTypes.arrayOf(PropTypes.string).isRequired,
  getLabel: PropTypes.func.isRequired,
  value: PropTypes.arrayOf(PropTypes.string).isRequired,
  onChange: PropTypes.func.isRequired,
  placeholder: PropTypes.string,
  helperText: PropTypes.string,
};

const asIs = (value) => value;
const errorClassLabel = (value) => labelOf(ERROR_CLASSES, value);

const AgentBillingFilters = ({ value, onChange }) => (
  <>
    <ChoiceField
      label="Billing mode"
      options={BILLING_MODES}
      value={value.billingMode}
      onChange={(billingMode) => onChange({ billingMode })}
    />
    <ChoiceField
      label="Policy variant"
      options={POLICY_VARIANTS}
      value={value.policyVariant}
      onChange={(policyVariant) => onChange({ policyVariant })}
    />
    <Field label="Run ID">
      <TextField
        size="small"
        fullWidth
        placeholder="e.g., run_42"
        value={value.runId}
        onChange={(event) => onChange({ runId: event.target.value })}
      />
    </Field>
    <Field label="Agent ID">
      <TextField
        size="small"
        fullWidth
        placeholder="e.g., planner"
        value={value.agentId}
        onChange={(event) => onChange({ agentId: event.target.value })}
      />
    </Field>
    <ManyField
      label="Step"
      options={STEPS}
      getLabel={asIs}
      value={value.step}
      onChange={(step) => onChange({ step })}
      placeholder="Any step"
    />
    <ManyField
      label="Error class"
      options={ERROR_CLASS_KEYS}
      getLabel={errorClassLabel}
      value={value.errorClass}
      onChange={(errorClass) => onChange({ errorClass })}
      placeholder="Any error class"
      helperText="Requests with at least one attempt in a picked class"
    />
  </>
);

AgentBillingFilters.propTypes = {
  value: PropTypes.shape({
    billingMode: PropTypes.string.isRequired,
    policyVariant: PropTypes.string.isRequired,
    runId: PropTypes.string.isRequired,
    agentId: PropTypes.string.isRequired,
    step: PropTypes.arrayOf(PropTypes.string).isRequired,
    errorClass: PropTypes.arrayOf(PropTypes.string).isRequired,
  }).isRequired,
  onChange: PropTypes.func.isRequired,
};

export default AgentBillingFilters;
