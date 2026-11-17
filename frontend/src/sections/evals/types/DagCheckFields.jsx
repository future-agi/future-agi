// W7 A2 (W7-3): the fields of a decision tree's check node — its type
// (contains, not contains, regex, equals expected, JSON path, tool called,
// length) and the inputs that type reads.
import React from "react";
import PropTypes from "prop-types";
import {
  FormControlLabel,
  MenuItem,
  Stack,
  Switch,
  TextField,
  Typography,
} from "@mui/material";
import { CHECK_TYPES } from "src/sections/common/EvalTypes/dagModel";

const fieldSx = { "& .MuiInputBase-root": { fontSize: "13px" } };

export default function DagCheckFields({ node, onCheck, disabled }) {
  const c = node.check ?? {};
  const t = c.type;
  return (
    <Stack direction="row" gap={1} flexWrap="wrap">
      <TextField
        select
        size="small"
        disabled={disabled}
        label="Check"
        value={t ?? ""}
        sx={{ ...fieldSx, minWidth: 170 }}
        onChange={(e) =>
          onCheck({
            type: e.target.value,
            on: e.target.value === "tool_called" ? "tool_calls" : "output",
          })
        }
      >
        {CHECK_TYPES.map((o) => (
          <MenuItem key={o.value} value={o.value}>
            {o.label}
          </MenuItem>
        ))}
      </TextField>
      <TextField
        size="small"
        disabled={disabled}
        label="On"
        value={c.on ?? (t === "tool_called" ? "tool_calls" : "output")}
        sx={{ ...fieldSx, width: t === "tool_called" ? 240 : 120 }}
        helperText={
          t === "tool_called"
            ? 'JSON array, e.g. [{"name":"lookup"}]'
            : "output or a variable"
        }
        onChange={(e) =>
          onCheck({
            on:
              e.target.value.trim() ||
              (t === "tool_called" ? "tool_calls" : "output"),
          })
        }
      />
      {t === "regex" ? (
        <TextField
          size="small"
          disabled={disabled}
          label="Pattern"
          value={c.pattern ?? ""}
          sx={fieldSx}
          onChange={(e) => onCheck({ pattern: e.target.value })}
        />
      ) : null}
      {t === "contains" || t === "not_contains" || t === "tool_called" ? (
        <TextField
          size="small"
          disabled={disabled}
          label={t === "tool_called" ? "Tool name" : "Text"}
          value={c.value ?? ""}
          sx={fieldSx}
          onChange={(e) => onCheck({ value: e.target.value })}
        />
      ) : null}
      {t === "contains" || t === "not_contains" || t === "regex" ? (
        <FormControlLabel
          label={<Typography typography="s3">Match case</Typography>}
          control={
            <Switch
              size="small"
              disabled={disabled}
              checked={Boolean(c.case_sensitive)}
              onChange={(e) => onCheck({ case_sensitive: e.target.checked })}
            />
          }
        />
      ) : null}
      {t === "json_path" ? (
        <>
          <TextField
            size="small"
            disabled={disabled}
            label="Path"
            value={c.path ?? ""}
            sx={fieldSx}
            placeholder="$.order.id"
            onChange={(e) => onCheck({ path: e.target.value })}
          />
          <TextField
            select
            size="small"
            disabled={disabled}
            label="Test"
            value={c.op ?? "exists"}
            sx={{ ...fieldSx, width: 110 }}
            onChange={(e) => onCheck({ op: e.target.value })}
          >
            <MenuItem value="exists">exists</MenuItem>
            <MenuItem value="equals">equals</MenuItem>
          </TextField>
          {c.op === "equals" ? (
            <TextField
              size="small"
              disabled={disabled}
              label="Value"
              value={c.value ?? ""}
              sx={fieldSx}
              onChange={(e) => onCheck({ value: e.target.value })}
            />
          ) : null}
        </>
      ) : null}
      {t === "length" ? (
        <>
          <TextField
            select
            size="small"
            disabled={disabled}
            label="Length"
            value={c.op ?? "lte"}
            sx={{ ...fieldSx, width: 90 }}
            onChange={(e) => onCheck({ op: e.target.value })}
          >
            <MenuItem value="lte">≤</MenuItem>
            <MenuItem value="gte">≥</MenuItem>
          </TextField>
          <TextField
            size="small"
            disabled={disabled}
            type="number"
            label="Characters"
            value={c.n ?? ""}
            sx={{ ...fieldSx, width: 120 }}
            onChange={(e) =>
              onCheck({
                n: e.target.value === "" ? "" : Number(e.target.value),
              })
            }
          />
        </>
      ) : null}
    </Stack>
  );
}

DagCheckFields.propTypes = {
  node: PropTypes.object.isRequired,
  onCheck: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
};
