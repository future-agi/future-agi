import PropTypes from "prop-types";
import { useState } from "react";
import {
  Alert,
  Box,
  Button,
  Checkbox,
  FormControlLabel,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import MetricInputs from "./MetricInputs";
import FieldPicker from "./FieldPicker";
import ConditionRow from "./ConditionRow";
import {
  changeKind,
  DEFAULT_LABELS,
  isBooleanMetric,
  KIND_LABELS,
  LEVEL_LABELS,
  selectSource,
  setRecipeInputs,
  sourceCapability,
} from "./builderSpec";

export default function SentenceBuilder({
  form,
  capabilities,
  options,
  loading,
  disabled,
  onChange,
  onSearch,
  rebuilding = false,
}) {
  const [customLabels, setCustomLabels] = useState(
    Object.entries(form.value_labels || {}).some(
      ([key, value]) => value !== DEFAULT_LABELS[key],
    ),
  );
  const [adding, setAdding] = useState(false);
  const boolean = isBooleanMetric(form);
  const custom = !form.kind || form.kind === "custom";
  const conditions = form.field_spec?.conditions || [];
  const level =
    form.level ||
    (form.selection_unit === "session" ? "conversation" : form.selection_unit);
  const updateInput = (index, patch) =>
    onChange(
      setRecipeInputs(
        form,
        form.inputs.map((input, i) =>
          i === index ? { ...input, ...patch } : input,
        ),
      ),
    );
  const updateCondition = (input, condition) => {
    if (input.condition_stage === "observation") {
      updateInput(form.inputs.indexOf(input), {
        condition: { operator: condition.operator, value: condition.value },
      });
    } else
      onChange(
        setRecipeInputs(
          form,
          form.inputs,
          conditions.map((old) =>
            old.alias === input.alias ? condition : old,
          ),
        ),
      );
  };
  const chooseReduction = (input, index, value) => {
    const [stage, reducer] = value.includes(":")
      ? value.split(":")
      : ["after_reduction", value];
    const previous =
      input.condition_stage === "observation"
        ? input.condition
        : conditions.find((condition) => condition.alias === input.alias);
    const { condition: _condition, condition_stage: _stage, ...source } = input;
    const next = {
      ...source,
      reducer,
      ...(stage === "observation"
        ? {
            condition_stage: stage,
            condition: {
              operator: previous?.operator || "eq",
              value: previous?.value ?? null,
            },
          }
        : {}),
    };
    const nextConditions = conditions.filter(
      (condition) => condition.alias !== input.alias,
    );
    if (boolean)
      nextConditions.push({
        alias: input.alias,
        operator: stage === "observation" ? "eq" : previous?.operator || "eq",
        value: stage === "observation" ? true : previous?.value ?? null,
      });
    onChange(
      setRecipeInputs(
        form,
        form.inputs.map((item, i) => (i === index ? next : item)),
        nextConditions,
      ),
    );
  };
  return (
    <Stack spacing={2.5} component="section" aria-label="What to calculate">
      <Typography component="h2" variant="subtitle2">
        What to calculate
      </Typography>
      <Stack direction="row" gap={1} alignItems="center">
        <TextField
          select
          size="small"
          label="Calculation"
          value={form.kind || "custom"}
          disabled={disabled}
          sx={{ flex: 1 }}
          onChange={(event) => {
            setAdding(false);
            onChange(changeKind(form, event.target.value));
          }}
        >
          {(capabilities?.kinds || [])
            .filter(
              (kind) =>
                KIND_LABELS[kind] &&
                (level !== "user" || capabilities.user_rules?.[kind]?.length),
            )
            .map((kind) => (
              <MenuItem key={kind} value={kind}>
                {KIND_LABELS[kind]}
              </MenuItem>
            ))}
          {form.kind && !KIND_LABELS[form.kind] && (
            <MenuItem value={form.kind}>
              {form.kind} (saved calculation)
            </MenuItem>
          )}
        </TextField>
        <Typography variant="body2">of</Typography>
        <TextField
          select
          size="small"
          label="Unit"
          value={level}
          disabled={disabled || rebuilding}
          sx={{ flex: 1 }}
          onChange={(event) =>
            onChange({
              ...form,
              level: event.target.value,
              selection_unit:
                event.target.value === "span" ? "span" : "session",
            })
          }
        >
          {(capabilities?.levels || [])
            .filter((value) => value !== "user" || level === "user")
            .map((value) => (
              <MenuItem key={value} value={value}>
                {LEVEL_LABELS[value] || value}
              </MenuItem>
            ))}
          {level && !capabilities?.levels?.includes(level) && (
            <MenuItem value={level}>
              {LEVEL_LABELS[level] || level} (not enabled)
            </MenuItem>
          )}
        </TextField>
      </Stack>
      {level === "user" && (
        <TextField
          select
          size="small"
          label="Across a user's conversations"
          value={form.user_rule || ""}
          disabled={disabled}
          onChange={(event) =>
            onChange({ ...form, user_rule: event.target.value })
          }
        >
          {(capabilities?.user_rules?.[form.kind] || []).map((rule) => (
            <MenuItem key={rule} value={rule}>
              {rule}
            </MenuItem>
          ))}
        </TextField>
      )}
      {custom ? (
        <>
          <Alert severity="info">
            Existing inputs, weighting, filters and custom formulas are
            preserved. Saving creates a new version; it does not replace the
            active history immediately.
          </Alert>
          <MetricInputs
            inputs={form.inputs}
            capabilities={capabilities}
            projectId={form.project_id}
            selectionUnit={form.selection_unit}
            disabled={disabled}
            onChange={(inputs) =>
              onChange({
                ...form,
                inputs,
                ...(form.field_spec?.inputs
                  ? { field_spec: { ...form.field_spec, inputs } }
                  : {}),
              })
            }
          />
          <TextField
            label="Formula"
            multiline
            minRows={3}
            value={form.formula || ""}
            disabled={disabled}
            onChange={(event) =>
              onChange({ ...form, formula: event.target.value })
            }
            helperText="The server validates custom formulas before saving."
          />
        </>
      ) : (
        <>
          {boolean && (
            <Typography variant="body2" color="text.secondary">
              where all of these conditions match
            </Typography>
          )}
          {form.inputs.map((input, index) => {
            const capability = sourceCapability(
              capabilities,
              input.value_type || "number",
            );
            const condition =
              input.condition_stage === "observation"
                ? { alias: input.alias, ...input.condition }
                : conditions.find((item) => item.alias === input.alias);
            return (
              <Stack
                key={input.alias}
                gap={1.5}
                sx={{ pb: 2, borderBottom: 1, borderColor: "divider" }}
              >
                <FieldPicker
                  input={input}
                  options={options}
                  disabled={disabled}
                  loading={loading}
                  label={`Source ${index + 1}`}
                  onSearch={onSearch}
                  onChange={(option) =>
                    onChange(selectSource(form, index, option, capabilities))
                  }
                />
                {input.source === "eval" && (
                  <TextField
                    select
                    size="small"
                    label={`Evaluation output for ${input.alias}`}
                    value={input.value_type || "number"}
                    disabled={disabled}
                    helperText="Choose the stored output type. A missing output remains No value."
                    onChange={(event) =>
                      onChange(
                        selectSource(
                          form,
                          index,
                          {
                            descriptor: {
                              source: "eval",
                              config_id: input.config_id,
                              config_hash: input.config_hash,
                              ordering: "sentence",
                              value_type: event.target.value,
                            },
                          },
                          capabilities,
                        ),
                      )
                    }
                  >
                    {(capabilities?.sources || []).map((source) => (
                      <MenuItem
                        key={source.value_type}
                        value={source.value_type}
                      >
                        {source.value_type}
                      </MenuItem>
                    ))}
                  </TextField>
                )}
                <TextField
                  select
                  size="small"
                  label={`When a ${level} has several values, use`}
                  value={
                    input.condition_stage === "observation"
                      ? `observation:${input.reducer}`
                      : input.reducer
                  }
                  disabled={disabled}
                  onChange={(event) =>
                    chooseReduction(input, index, event.target.value)
                  }
                >
                  {(capability?.reducers || []).map((reducer) => (
                    <MenuItem key={reducer} value={reducer}>
                      {reducer}
                    </MenuItem>
                  ))}
                  {boolean &&
                    (capability?.observation_reducers || []).map((reducer) => (
                      <MenuItem
                        key={`observation:${reducer}`}
                        value={`observation:${reducer}`}
                      >
                        {reducer} observation matches the condition
                      </MenuItem>
                    ))}
                </TextField>
                {boolean && condition && (
                  <ConditionRow
                    key={`${form.project_id}:${input.alias}:${input.field}:${input.reducer}:${input.condition_stage}`}
                    input={input}
                    condition={condition}
                    capabilities={capabilities}
                    projectId={form.project_id}
                    disabled={disabled}
                    onChange={(next) => updateCondition(input, next)}
                  />
                )}
                {form.inputs.length > 1 && (
                  <Button
                    color="inherit"
                    size="small"
                    sx={{ alignSelf: "flex-start" }}
                    disabled={disabled}
                    onClick={() =>
                      onChange(
                        setRecipeInputs(
                          form,
                          form.inputs.filter((_, i) => i !== index),
                          conditions.filter(
                            (item) => item.alias !== input.alias,
                          ),
                        ),
                      )
                    }
                  >
                    Remove condition {index + 1}
                  </Button>
                )}
              </Stack>
            );
          })}
          {(!form.inputs.length || adding) && (
            <FieldPicker
              options={options}
              loading={loading}
              disabled={disabled}
              label="Choose a source"
              onSearch={onSearch}
              onChange={(option) => {
                onChange(
                  selectSource(form, form.inputs.length, option, capabilities),
                );
                setAdding(false);
              }}
            />
          )}
          {boolean && form.inputs.length > 0 && !adding && (
            <Button
              color="inherit"
              sx={{ alignSelf: "flex-start" }}
              disabled={disabled || form.inputs.length >= 16}
              onClick={() => setAdding(true)}
            >
              Add condition
            </Button>
          )}
          <Typography variant="caption" color="text.secondary">
            Each source is reduced independently before conditions are combined.
            Different sources need not occur on the same span. Missing values
            never become zero or false.
          </Typography>
        </>
      )}
      {boolean && (
        <Box>
          <FormControlLabel
            control={
              <Checkbox
                checked={customLabels}
                disabled={disabled}
                onChange={(event) => {
                  setCustomLabels(event.target.checked);
                  onChange({
                    ...form,
                    value_labels: event.target.checked
                      ? { ...DEFAULT_LABELS }
                      : {},
                  });
                }}
              />
            }
            label="Customize match labels"
          />
          {customLabels ? (
            <Stack direction="row" gap={1} sx={{ mt: 1 }}>
              <TextField
                fullWidth
                size="small"
                label="Show matches as"
                disabled={disabled}
                value={form.value_labels?.match ?? DEFAULT_LABELS.match}
                onChange={(event) =>
                  onChange({
                    ...form,
                    value_labels: {
                      ...DEFAULT_LABELS,
                      ...form.value_labels,
                      match: event.target.value,
                    },
                  })
                }
              />
              <TextField
                fullWidth
                size="small"
                label="And the rest as"
                disabled={disabled}
                value={form.value_labels?.other ?? DEFAULT_LABELS.other}
                onChange={(event) =>
                  onChange({
                    ...form,
                    value_labels: {
                      ...DEFAULT_LABELS,
                      ...form.value_labels,
                      other: event.target.value,
                    },
                  })
                }
              />
            </Stack>
          ) : (
            <Typography variant="caption" color="text.secondary">
              Matches show as Counts; the rest as Doesn’t count. Missing inputs
              show No value.
            </Typography>
          )}
        </Box>
      )}
    </Stack>
  );
}
SentenceBuilder.propTypes = {
  form: PropTypes.object.isRequired,
  capabilities: PropTypes.object,
  options: PropTypes.array.isRequired,
  loading: PropTypes.bool,
  disabled: PropTypes.bool,
  onChange: PropTypes.func.isRequired,
  onSearch: PropTypes.func,
  rebuilding: PropTypes.bool,
};
