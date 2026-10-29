import PropTypes from "prop-types";
import { Box, Button, MenuItem, Stack, Typography } from "@mui/material";

import EvaluatorBinding from "./EvaluatorBinding";
import TextField from "./MetricField";
import { newInput } from "./metricForm";

export default function MetricInputs({
  inputs,
  onChange,
  disabled,
  readOnly,
  projectId,
  guided = false,
  selectionUnit = "unit",
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
              ...(key === "source" ? newInput(input.alias) : {}),
              ...input,
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
  return (
    <Stack spacing={2}>
      <Typography variant="subtitle2">
        {guided ? "Data to summarize" : "Inputs · one value per selection unit"}
      </Typography>
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
              {["latest", "mean", "min", "max"].map((value) => (
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
};
