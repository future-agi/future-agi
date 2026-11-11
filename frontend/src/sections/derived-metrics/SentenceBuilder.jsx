import PropTypes from "prop-types";
import { useId, useState } from "react";
import {
  Box,
  Button,
  Checkbox,
  FormControlLabel,
  IconButton,
  MenuItem,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import MetricInputs from "./MetricInputs";
import FieldPicker from "./FieldPicker";
import ConditionRow from "./ConditionRow";
import {
  changeKind,
  DEFAULT_LABELS,
  FORMULA_EXAMPLE,
  isBooleanMetric,
  KIND_LABELS,
  LEVEL_LABELS,
  selectSource,
  setRecipeInputs,
  sourceCapability,
} from "./builderSpec";
import { conditionParts, inputLabel } from "./metricPresentation";
import { ConditionText } from "./MetricSentence";

export default function SentenceBuilder({
  form,
  capabilities,
  options,
  loading,
  disabled,
  onChange,
  onSearch,
  rebuilding = false,
  sourceLabels = {},
  projectName,
}) {
  const idBase = useId();
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
  // The same names the preview sentence uses, so both read identically.
  const labelFor = (alias) =>
    inputLabel(alias, { inputs: form.inputs, sourceLabels });
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
      <Box>
        <Typography component="h2" variant="subtitle1" fontWeight={600}>
          What to calculate
        </Typography>
        <Typography variant="body2" color="text.secondary">
          Pick a calculation and the unit it runs over, then the sources it
          reads.
        </Typography>
      </Box>
      <Stack direction="row" gap={1.5} alignItems="flex-end">
        <TextField
          select
          size="small"
          label="Calculation"
          value={form.kind || "custom"}
          disabled={disabled}
          sx={{ flex: 1, minWidth: 0 }}
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
        <Typography
          variant="body2"
          color="text.secondary"
          aria-hidden
          sx={{ height: 32, display: "flex", alignItems: "center" }}
        >
          of
        </Typography>
        <TextField
          select
          size="small"
          label="Unit"
          value={level}
          disabled={disabled || rebuilding}
          sx={{ flex: 1, minWidth: 0 }}
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
          <Stack direction="row" gap={0.75} alignItems="flex-start">
            <Iconify
              icon="solar:info-circle-linear"
              width={14}
              sx={{ color: "text.disabled", mt: "1px", flexShrink: 0 }}
            />
            <Typography variant="caption" color="text.secondary">
              Name each input, then use those names in the formula. Saved
              inputs, weighting and filters are kept as they are; saving creates
              a new version.
            </Typography>
          </Stack>
          <MetricInputs
            inputs={form.inputs}
            capabilities={capabilities}
            projectId={form.project_id}
            // The unit in the words the Unit picker uses, not the wire
            // value ("session").
            selectionUnit={level}
            // Built-in span fields as the source picker names them.
            fieldLabels={Object.fromEntries(
              (options || [])
                .filter(
                  ({ descriptor }) =>
                    descriptor?.source === "span" && !descriptor.attribute_key,
                )
                .map(({ descriptor, label }) => [descriptor.field, label]),
            )}
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
            // The server accepts one ratio, mean, sum or count_if call at
            // the top level (tracer/services/derived_metrics/formula.py).
            placeholder={FORMULA_EXAMPLE}
            helperText="Start with ratio(numerator, denominator), mean(value), sum(value) or count_if(condition), using the input names above. The formula is checked when you save."
            sx={{ "& textarea": { fontFamily: "monospace" } }}
          />
        </>
      ) : (
        <Stack spacing={1.5}>
          {boolean && (
            <Typography variant="subtitle2" color="text.secondary">
              {`Count a ${LEVEL_LABELS[level] ? level : "unit"} when all of these conditions match`}
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
            const removable = form.inputs.length > 1;
            const sentenceId = `${idBase}-condition-${index}`;
            return (
              <Stack
                key={input.alias}
                gap={1.5}
                role="group"
                aria-label={boolean ? `Condition ${index + 1}` : "Source"}
                aria-describedby={boolean ? sentenceId : undefined}
                sx={{
                  p: 1.5,
                  border: 1,
                  borderColor: "divider",
                  borderRadius: 1,
                }}
              >
                {boolean && (
                  <Stack
                    direction="row"
                    alignItems="flex-start"
                    justifyContent="space-between"
                    gap={1}
                    sx={{ minHeight: 24, mb: -0.25 }}
                  >
                    {/* The condition as one sentence, in the preview's words;
                        the fields below edit each part of it. */}
                    <Typography
                      id={sentenceId}
                      variant="body2"
                      sx={{ pt: 0.25, minWidth: 0, overflowWrap: "anywhere" }}
                    >
                      <Box component="span" sx={{ color: "text.secondary" }}>
                        {index === 0 ? "Where" : "And"}
                      </Box>{" "}
                      <Box
                        component="span"
                        sx={{ color: "text.primary", fontWeight: 500 }}
                      >
                        <ConditionText
                          parts={conditionParts(form, input, labelFor)}
                        />
                      </Box>
                    </Typography>
                    {removable && (
                      // The span keeps the tooltip working (and quiet) while
                      // the button is disabled.
                      <Tooltip title="Remove condition">
                        <span>
                          <IconButton
                            size="small"
                            aria-label={`Remove condition ${index + 1}`}
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
                            <Iconify
                              icon="solar:trash-bin-minimalistic-linear"
                              width={16}
                            />
                          </IconButton>
                        </span>
                      </Tooltip>
                    )}
                  </Stack>
                )}
                <Stack direction="row" gap={1.5} alignItems="flex-end">
                  <Box sx={{ flex: 1, minWidth: 0 }}>
                    <FieldPicker
                      input={input}
                      options={options}
                      disabled={disabled}
                      loading={loading}
                      label={`Source ${index + 1}`}
                      projectName={projectName}
                      onSearch={onSearch}
                      onChange={(option) =>
                        onChange(
                          selectSource(form, index, option, capabilities),
                        )
                      }
                    />
                  </Box>
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
                    sx={{ width: 300, flexShrink: 0 }}
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
                      (capability?.observation_reducers || []).map(
                        (reducer) => (
                          <MenuItem
                            key={`observation:${reducer}`}
                            value={`observation:${reducer}`}
                          >
                            {reducer} observation matches the condition
                          </MenuItem>
                        ),
                      )}
                  </TextField>
                </Stack>
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
                {!boolean && removable && (
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
            <Box
              sx={{
                p: 1.5,
                border: 1,
                borderStyle: "dashed",
                borderColor: "divider",
                borderRadius: 1,
              }}
            >
              <FieldPicker
                options={options}
                loading={loading}
                disabled={disabled || !form.project_id}
                label="Choose a source"
                helperText={
                  form.project_id
                    ? undefined
                    : "Choose a project to see its fields"
                }
                projectName={projectName}
                onSearch={onSearch}
                onChange={(option) => {
                  onChange(
                    selectSource(
                      form,
                      form.inputs.length,
                      option,
                      capabilities,
                    ),
                  );
                  setAdding(false);
                }}
              />
            </Box>
          )}
          {boolean && form.inputs.length > 0 && !adding && (
            <Button
              color="inherit"
              variant="outlined"
              size="small"
              startIcon={<Iconify icon="solar:add-circle-linear" width={16} />}
              sx={{ alignSelf: "flex-start" }}
              disabled={disabled || form.inputs.length >= 16}
              onClick={() => setAdding(true)}
            >
              Add condition
            </Button>
          )}
          <Stack direction="row" gap={0.75} alignItems="flex-start">
            <Iconify
              icon="solar:info-circle-linear"
              width={14}
              sx={{ color: "text.disabled", mt: "1px", flexShrink: 0 }}
            />
            <Typography variant="caption" color="text.secondary">
              Each source is reduced independently before conditions are
              combined. Different sources need not occur on the same span.
              Missing values never become zero or false.
            </Typography>
          </Stack>
        </Stack>
      )}
      {boolean && (
        <Box>
          <FormControlLabel
            sx={{ ml: -0.75, mr: 0 }}
            control={
              <Checkbox
                size="small"
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
            label={
              <Typography variant="body2" fontWeight={500}>
                Customize match labels
              </Typography>
            }
          />
          {customLabels ? (
            <Stack direction="row" gap={1.5} sx={{ mt: 1 }}>
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
            <Typography
              variant="caption"
              color="text.secondary"
              component="p"
              sx={{ pl: 3.5 }}
            >
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
  sourceLabels: PropTypes.objectOf(PropTypes.string),
  // Names the project in the source picker's empty message.
  projectName: PropTypes.string,
};
