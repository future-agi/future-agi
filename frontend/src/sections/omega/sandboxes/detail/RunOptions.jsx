import React, { useId } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import IconButton from "@mui/material/IconButton";
import InputAdornment from "@mui/material/InputAdornment";
import MenuItem from "@mui/material/MenuItem";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import SandboxIcon from "../components/SandboxIcon";
import {
  ENV_MAX_ENTRIES,
  WORKSPACE_ROOT,
  filledEnvRows,
  formatSeconds,
  newEnvRow,
  timeLimits,
  timeoutChoices,
} from "../lib/runOptions";

const monoInput = {
  "& input": {
    fontFamily:
      '"JetBrains Mono", "SFMono-Regular", Menlo, Consolas, monospace',
    fontSize: 13,
  },
};

function EnvRow({ row, error, disabled, onChange, onRemove }) {
  const label = row.key.trim() || "this variable";
  return (
    <Box
      data-testid="run-env-row"
      sx={{
        display: "grid",
        gridTemplateColumns: {
          xs: "1fr auto",
          sm: "minmax(0, 2fr) minmax(0, 3fr) auto",
        },
        gap: 1,
        alignItems: "flex-start",
      }}
    >
      <TextField
        size="small"
        placeholder="NAME"
        value={row.key}
        disabled={disabled}
        onChange={(event) => onChange({ key: event.target.value })}
        error={Boolean(error?.key)}
        helperText={error?.key}
        sx={monoInput}
        inputProps={{
          "aria-label": "Variable name",
          "data-testid": "run-env-key",
          spellCheck: false,
          autoCapitalize: "off",
          autoComplete: "off",
        }}
      />
      <TextField
        size="small"
        placeholder="value"
        value={row.value}
        disabled={disabled}
        onChange={(event) => onChange({ value: event.target.value })}
        error={Boolean(error?.value)}
        helperText={error?.value}
        sx={{
          ...monoInput,
          gridColumn: { xs: "1 / 2", sm: "auto" },
          gridRow: { xs: 2, sm: "auto" },
        }}
        inputProps={{
          "aria-label": `Value of ${label}`,
          "data-testid": "run-env-value",
          spellCheck: false,
          autoCapitalize: "off",
          autoComplete: "off",
        }}
      />
      <IconButton
        size="small"
        disabled={disabled}
        onClick={onRemove}
        aria-label={`Remove ${label}`}
        data-testid="run-env-remove"
        sx={{
          mt: 0.5,
          gridRow: { xs: "1 / 3", sm: "auto" },
          gridColumn: { xs: 2, sm: "auto" },
        }}
      >
        <SandboxIcon name="close" size={16} />
      </IconButton>
    </Box>
  );
}

EnvRow.propTypes = {
  row: PropTypes.shape({
    id: PropTypes.string.isRequired,
    key: PropTypes.string.isRequired,
    value: PropTypes.string.isRequired,
  }).isRequired,
  error: PropTypes.object,
  disabled: PropTypes.bool,
  onChange: PropTypes.func.isRequired,
  onRemove: PropTypes.func.isRequired,
};

/**
 * The Run tab's options panel: working folder, time limit and environment
 * variables for the next command. Every field is validated as the user types
 * (lib/runOptions.js mirrors EE's rules); the time limit is offered only when
 * EE states its limits.
 */
export default function RunOptions({
  id,
  caps,
  options,
  errors,
  disabled,
  onChange,
}) {
  const fieldId = useId();
  const limits = timeLimits(caps);
  const rows = options.env;
  const setRow = (rowId, patch) =>
    onChange({
      env: rows.map((row) => (row.id === rowId ? { ...row, ...patch } : row)),
    });
  const removeRow = (rowId) =>
    onChange({ env: rows.filter((row) => row.id !== rowId) });
  const full = filledEnvRows(rows).length >= ENV_MAX_ENTRIES;

  return (
    <Box
      id={id}
      data-testid="run-options"
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        p: 1.5,
        display: "flex",
        flexDirection: "column",
        gap: 2,
        minWidth: 0,
      }}
    >
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: { xs: "1fr", sm: limits ? "3fr 2fr" : "1fr" },
          gap: 1.5,
        }}
      >
        <TextField
          size="small"
          label="Working folder"
          placeholder="src/app"
          value={options.cwd}
          disabled={disabled}
          onChange={(event) => onChange({ cwd: event.target.value })}
          error={Boolean(errors.cwd)}
          helperText={
            errors.cwd ||
            "Inside /workspace. The folder must exist; mkdir -p makes one."
          }
          sx={monoInput}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <Box
                  component="span"
                  sx={{ fontFamily: "monospace", fontSize: 13 }}
                >
                  {WORKSPACE_ROOT}/
                </Box>
              </InputAdornment>
            ),
          }}
          inputProps={{
            "data-testid": "run-cwd",
            spellCheck: false,
            autoCapitalize: "off",
            autoComplete: "off",
          }}
        />
        {limits && (
          <TextField
            select
            size="small"
            label="Time limit"
            value={options.timeoutSeconds}
            disabled={disabled}
            onChange={(event) =>
              onChange({ timeoutSeconds: Number(event.target.value) })
            }
            error={Boolean(errors.timeoutSeconds)}
            helperText={
              errors.timeoutSeconds ||
              `A stop is requested if it runs longer. Up to ${formatSeconds(limits.max)}.`
            }
            inputProps={{ "data-testid": "run-timeout" }}
          >
            {timeoutChoices(caps).map((seconds) => (
              <MenuItem key={seconds} value={seconds}>
                {formatSeconds(seconds)}
                {seconds === limits.default ? " (default)" : ""}
              </MenuItem>
            ))}
          </TextField>
        )}
      </Box>

      <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
        <Box>
          <Typography
            id={`${fieldId}-env`}
            typography="s1"
            fontWeight="fontWeightMedium"
            color="text.primary"
          >
            Environment variables
          </Typography>
          <Typography typography="s2" color="text.secondary">
            Set for this command only. Activity records the command, never these
            values.
          </Typography>
        </Box>
        <Box
          role="group"
          aria-labelledby={`${fieldId}-env`}
          sx={{ display: "flex", flexDirection: "column", gap: 1 }}
        >
          {rows.map((row) => (
            <EnvRow
              key={row.id}
              row={row}
              error={errors.env?.[row.id]}
              disabled={disabled}
              onChange={(patch) => setRow(row.id, patch)}
              onRemove={() => removeRow(row.id)}
            />
          ))}
        </Box>
        {errors.envCount && (
          <Typography typography="s2" color="error.main">
            {errors.envCount}
          </Typography>
        )}
        <Box>
          <Button
            size="small"
            disabled={disabled || full}
            onClick={() => onChange({ env: [...rows, newEnvRow()] })}
            startIcon={<SandboxIcon name="plus" size={14} />}
            sx={{ typography: "s2" }}
            data-testid="run-env-add"
          >
            {full ? `${ENV_MAX_ENTRIES} variables is the most` : "Add variable"}
          </Button>
        </Box>
      </Box>
    </Box>
  );
}

RunOptions.propTypes = {
  id: PropTypes.string.isRequired,
  caps: PropTypes.object,
  options: PropTypes.shape({
    timeoutSeconds: PropTypes.number,
    cwd: PropTypes.string.isRequired,
    env: PropTypes.array.isRequired,
  }).isRequired,
  errors: PropTypes.object.isRequired,
  disabled: PropTypes.bool,
  onChange: PropTypes.func.isRequired,
};
