import PropTypes from "prop-types";
import { Box, Button, MenuItem, Stack, Typography } from "@mui/material";
import { visuallyHidden } from "@mui/utils";

import Iconify from "src/components/iconify";
import EvaluatorBinding from "./EvaluatorBinding";
import TextField from "./MetricField";
import { newInput } from "./metricForm";
import {
  enumLabel,
  REDUCER_LABELS,
  spanFieldLabel,
} from "./metricPresentation";

export default function MetricInputs({
  inputs,
  onChange,
  disabled,
  readOnly,
  projectId,
  guided = false,
  selectionUnit = "unit",
  capabilities,
  fieldLabels = {},
}) {
  const guidedLabel = (label, index) =>
    inputs.length === 1 ? label : `${label} · input ${index + 1}`;
  const update = (index, key, value) =>
    !disabled &&
    !readOnly &&
    onChange(
      inputs.map((input, i) =>
        i === index
          ? {
              ...(key === "source"
                ? value === "eval"
                  ? {
                      alias: input.alias,
                      source: "eval",
                      reducer: "latest",
                      config_id: "",
                      config_hash: "",
                    }
                  : newInput(input.alias)
                : input),
              ...(key === "field" && input.value_type
                ? {
                    value_type:
                      value === "attribute_bool"
                        ? "boolean"
                        : value === "attribute_text"
                          ? "text"
                          : "number",
                  }
                : {}),
              [key]: value,
            }
          : input,
      ),
    );
  const bind = (index, patch) =>
    !disabled &&
    !readOnly &&
    onChange(
      inputs.map((input, i) => (i === index ? { ...input, ...patch } : input)),
    );
  const reducers = (input) =>
    capabilities?.sources?.find(
      (source) => source.value_type === (input.value_type || "number"),
    )?.reducers || ["latest", "mean", "min", "max"];
  const spanFields = [
    "attribute_number",
    "attribute_bool",
    ...(capabilities ? ["attribute_text"] : []),
    "latency_ms",
    "cost",
    "total_tokens",
    "prompt_tokens",
    "completion_tokens",
  ];
  if (!guided)
    return (
      <Stack spacing={1.5} component="section" aria-label="Formula inputs">
        <Box>
          <Typography variant="subtitle2">Inputs</Typography>
          <Typography variant="caption" color="text.secondary">
            One value per {selectionUnit}. Refer to each input by its name in
            the formula.
          </Typography>
        </Box>
        {inputs.map((input, index) => (
          <Stack
            key={index}
            role="group"
            aria-label={`Input ${index + 1}`}
            spacing={1.5}
            sx={{ border: 1, borderColor: "divider", borderRadius: 1, p: 1.5 }}
          >
            <Box
              sx={{
                display: "grid",
                gridTemplateColumns:
                  "minmax(0, 1fr) minmax(0, 1fr) minmax(0, 1fr) auto",
                gap: 1.5,
                alignItems: "flex-end",
              }}
            >
              <TextField
                size="small"
                label={`Input ${index + 1} alias`}
                value={input.alias}
                disabled={disabled}
                readOnly={readOnly}
                onChange={(e) => update(index, "alias", e.target.value)}
                sx={{ "& input": { fontFamily: "monospace" } }}
              />
              <TextField
                size="small"
                select
                label={`Input ${index + 1} source`}
                value={input.source}
                disabled={disabled}
                readOnly={readOnly}
                onChange={(e) => update(index, "source", e.target.value)}
              >
                <MenuItem value="span">Span field</MenuItem>
                <MenuItem value="eval">Evaluation result</MenuItem>
              </TextField>
              <TextField
                size="small"
                select
                label={`Input ${index + 1} reduction`}
                value={input.reducer}
                disabled={disabled}
                readOnly={readOnly}
                onChange={(e) => update(index, "reducer", e.target.value)}
              >
                {/* Named as the Definition tab names them; the stored
                    reducer stays the item's value. */}
                {reducers(input).map((value) => (
                  <MenuItem key={value} value={value}>
                    {enumLabel(REDUCER_LABELS, value)}
                  </MenuItem>
                ))}
              </TextField>
              <Button
                color="inherit"
                size="small"
                startIcon={
                  <Iconify
                    icon="solar:trash-bin-minimalistic-linear"
                    width={16}
                  />
                }
                disabled={disabled || readOnly || inputs.length === 1}
                onClick={() => onChange(inputs.filter((_, i) => i !== index))}
                sx={{ whiteSpace: "nowrap" }}
              >
                {/* The visible verb; the input number is for assistive tech. */}
                <span aria-hidden="true">Remove</span>
                <Box component="span" sx={visuallyHidden}>
                  Remove input {index + 1}
                </Box>
              </Button>
            </Box>
            {input.source === "eval" ? (
              <Stack direction="row" gap={1.5}>
                <EvaluatorBinding
                  index={index}
                  input={input}
                  projectId={projectId}
                  disabled={disabled}
                  readOnly={readOnly}
                  onBind={(patch) => bind(index, patch)}
                  onField={(key, value) => update(index, key, value)}
                />
              </Stack>
            ) : (
              <Box
                sx={{
                  display: "grid",
                  gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)",
                  gap: 1.5,
                }}
              >
                <TextField
                  fullWidth
                  size="small"
                  select
                  label={`Input ${index + 1} field`}
                  value={input.field}
                  disabled={disabled}
                  readOnly={readOnly}
                  onChange={(e) => update(index, "field", e.target.value)}
                >
                  <MenuItem value="" disabled>
                    Select field
                  </MenuItem>
                  {spanFields.map((value) => (
                    <MenuItem key={value} value={value}>
                      {spanFieldLabel(value, fieldLabels)}
                    </MenuItem>
                  ))}
                </TextField>
                {(input.field || "").startsWith("attribute_") && (
                  <TextField
                    fullWidth
                    size="small"
                    label={`Input ${index + 1} attribute key`}
                    value={input.attribute_key}
                    disabled={disabled}
                    readOnly={readOnly}
                    onChange={(e) =>
                      update(index, "attribute_key", e.target.value)
                    }
                  />
                )}
              </Box>
            )}
          </Stack>
        ))}
        <Button
          color="inherit"
          variant="outlined"
          size="small"
          startIcon={<Iconify icon="solar:add-circle-linear" width={16} />}
          sx={{ alignSelf: "flex-start" }}
          disabled={disabled || readOnly || inputs.length >= 16}
          onClick={() =>
            onChange([...inputs, newInput(`value_${inputs.length + 1}`)])
          }
        >
          Add input
        </Button>
      </Stack>
    );
  return (
    <Stack spacing={2}>
      <Typography variant="subtitle2">Data to summarize</Typography>
      {inputs.map((input, index) => (
        <Box
          key={index}
          sx={{ border: 1, borderColor: "divider", borderRadius: 1, p: 2 }}
        >
          <Stack direction="column" gap={2}>
            {!guided && (
              <TextField
                size="small"
                label={`Input ${index + 1} alias`}
                value={input.alias}
                disabled={disabled}
                readOnly={readOnly}
                onChange={(e) => update(index, "alias", e.target.value)}
              />
            )}
            <TextField
              size="small"
              select
              label={
                guided
                  ? guidedLabel("What to summarize", index)
                  : `Input ${index + 1} source`
              }
              value={input.source}
              disabled={disabled}
              readOnly={readOnly}
              onChange={(e) => update(index, "source", e.target.value)}
              fullWidth
            >
              <MenuItem value="span">Span field</MenuItem>
              <MenuItem value="eval">Evaluation result</MenuItem>
            </TextField>
            <TextField
              size="small"
              select
              label={
                guided
                  ? guidedLabel(
                      `If a ${selectionUnit} has several values, use`,
                      index,
                    )
                  : `Input ${index + 1} reduction`
              }
              value={input.reducer}
              disabled={disabled}
              readOnly={readOnly}
              onChange={(e) => update(index, "reducer", e.target.value)}
              fullWidth
            >
              {(
                capabilities?.sources?.find(
                  (source) =>
                    source.value_type === (input.value_type || "number"),
                )?.reducers || ["latest", "mean", "min", "max"]
              ).map((value) => (
                <MenuItem key={value} value={value}>
                  {value}
                </MenuItem>
              ))}
            </TextField>
            {!guided && (
              <Button
                color="inherit"
                disabled={disabled || readOnly || inputs.length === 1}
                onClick={() => onChange(inputs.filter((_, i) => i !== index))}
              >
                Remove input {index + 1}
              </Button>
            )}
          </Stack>
          <Stack direction={{ xs: "column", md: "row" }} gap={1} sx={{ mt: 1 }}>
            {input.source === "eval" ? (
              <EvaluatorBinding
                index={index}
                input={input}
                projectId={projectId}
                disabled={disabled}
                readOnly={readOnly}
                onBind={(patch) => bind(index, patch)}
                onField={(key, value) => update(index, key, value)}
              />
            ) : (
              <>
                <TextField
                  fullWidth
                  size="small"
                  select
                  label={
                    guided
                      ? guidedLabel("Span value", index)
                      : `Input ${index + 1} field`
                  }
                  value={input.field}
                  disabled={disabled}
                  readOnly={readOnly}
                  onChange={(e) => update(index, "field", e.target.value)}
                >
                  <MenuItem value="" disabled>
                    Select field
                  </MenuItem>
                  {[
                    "attribute_number",
                    "attribute_bool",
                    ...(capabilities ? ["attribute_text"] : []),
                    "latency_ms",
                    "cost",
                    "total_tokens",
                    "prompt_tokens",
                    "completion_tokens",
                  ].map((value) => (
                    <MenuItem key={value} value={value}>
                      {value.replaceAll("_", " ")}
                    </MenuItem>
                  ))}
                </TextField>
                {(input.field || "").startsWith("attribute_") && (
                  <TextField
                    fullWidth
                    size="small"
                    label={
                      guided
                        ? guidedLabel("Attribute name", index)
                        : `Input ${index + 1} attribute key`
                    }
                    value={input.attribute_key}
                    disabled={disabled}
                    readOnly={readOnly}
                    onChange={(e) =>
                      update(index, "attribute_key", e.target.value)
                    }
                  />
                )}
              </>
            )}
          </Stack>
        </Box>
      ))}
      {!guided && (
        <Button
          sx={{ alignSelf: "flex-start" }}
          disabled={disabled || readOnly || inputs.length >= 16}
          onClick={() =>
            onChange([...inputs, newInput(`value_${inputs.length + 1}`)])
          }
        >
          Add input
        </Button>
      )}
    </Stack>
  );
}

MetricInputs.propTypes = {
  inputs: PropTypes.array.isRequired,
  onChange: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
  readOnly: PropTypes.bool,
  projectId: PropTypes.string,
  guided: PropTypes.bool,
  selectionUnit: PropTypes.string,
  capabilities: PropTypes.object,
  // Built-in span field -> the project catalog's display name, when read.
  fieldLabels: PropTypes.objectOf(PropTypes.string),
};
