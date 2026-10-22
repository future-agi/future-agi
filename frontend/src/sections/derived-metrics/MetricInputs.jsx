import PropTypes from "prop-types";
import {
  Box,
  Button,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";

import { newInput } from "./metricForm";

export default function MetricInputs({ inputs, onChange, disabled }) {
  const update = (index, key, value) =>
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
  return (
    <Stack spacing={2}>
      <Typography variant="subtitle2">
        Inputs · one value per selection unit
      </Typography>
      {inputs.map((input, index) => (
        <Box
          key={index}
          sx={{ border: 1, borderColor: "divider", borderRadius: 1, p: 2 }}
        >
          <Stack direction={{ xs: "column", md: "row" }} gap={1}>
            <TextField
              size="small"
              label={`Input ${index + 1} alias`}
              value={input.alias}
              disabled={disabled}
              onChange={(e) => update(index, "alias", e.target.value)}
            />
            <TextField
              size="small"
              select
              label={`Input ${index + 1} source`}
              value={input.source}
              disabled={disabled}
              onChange={(e) => update(index, "source", e.target.value)}
              sx={{ minWidth: 150 }}
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
              onChange={(e) => update(index, "reducer", e.target.value)}
              sx={{ minWidth: 130 }}
            >
              {["latest", "mean", "min", "max"].map((value) => (
                <MenuItem key={value} value={value}>
                  {value}
                </MenuItem>
              ))}
            </TextField>
            <Button
              color="inherit"
              disabled={disabled || inputs.length === 1}
              onClick={() => onChange(inputs.filter((_, i) => i !== index))}
            >
              Remove input {index + 1}
            </Button>
          </Stack>
          <Stack direction={{ xs: "column", md: "row" }} gap={1} sx={{ mt: 1 }}>
            {input.source === "eval" ? (
              <>
                <TextField
                  fullWidth
                  size="small"
                  label={`Input ${index + 1} evaluator ID`}
                  value={input.config_id || ""}
                  disabled={disabled}
                  onChange={(e) => update(index, "config_id", e.target.value)}
                />
                <TextField
                  fullWidth
                  size="small"
                  label={`Input ${index + 1} evaluator hash`}
                  value={input.config_hash}
                  disabled={disabled}
                  onChange={(e) => update(index, "config_hash", e.target.value)}
                  helperText="Exact persisted config_hash. Empty selects legacy unversioned results only."
                />
              </>
            ) : (
              <>
                <TextField
                  fullWidth
                  size="small"
                  select
                  label={`Input ${index + 1} field`}
                  value={input.field}
                  disabled={disabled}
                  onChange={(e) => update(index, "field", e.target.value)}
                >
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
                {input.field.startsWith("attribute_") && (
                  <TextField
                    fullWidth
                    size="small"
                    label={`Input ${index + 1} attribute key`}
                    value={input.attribute_key}
                    disabled={disabled}
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
      <Button
        sx={{ alignSelf: "flex-start" }}
        disabled={disabled || inputs.length >= 16}
        onClick={() =>
          onChange([...inputs, newInput(`value_${inputs.length + 1}`)])
        }
      >
        Add input
      </Button>
    </Stack>
  );
}

MetricInputs.propTypes = {
  inputs: PropTypes.array.isRequired,
  onChange: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
};
