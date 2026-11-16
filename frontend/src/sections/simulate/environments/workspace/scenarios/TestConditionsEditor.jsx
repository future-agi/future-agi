import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
  Button,
  IconButton,
  MenuItem,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import TestConditionField from "./TestConditionField";
import {
  NUMBER_FIELDS,
  OBJECT_FIELDS,
  TEST_CONDITIONS_COPY,
  asTyped,
  objectProblem,
  testConditionRoute,
} from "./testConditions.constants";

const EMPTY = { action: "", mode: "" };

// An in-process tool is listed but cannot be chosen; the server validates the rest.
export default function TestConditionsEditor({
  faults,
  onChange,
  env,
  disabled = false,
  error = "",
  problems = [],
}) {
  const [form, setForm] = useState(null);
  const targets = env?.faultTargets;
  const set = (key) => (event) =>
    setForm((current) => ({ ...current, [key]: event.target.value }));
  // A field not typed yet is empty.
  const typedIn = (key) => form[key] ?? "";
  const target = form && targets?.find((one) => one.action === form.action);
  const modes = target ? target.modes : [];
  // The picked mode, with the fields it takes and what it means for the agent.
  const mode = modes.find((one) => one.mode === form?.mode);
  // Conditions that are not yet a JSON object say why under their field, and hold Add back.
  const unreadable =
    Boolean(mode) &&
    mode.fields.some(
      (key) => OBJECT_FIELDS.includes(key) && objectProblem(typedIn(key)),
    );
  // Falls back to the new tool's first mode when it lacks the picked one.
  const pickTool = (event) => {
    const picked = targets.find((one) => one.action === event.target.value);
    const offered = picked.modes.map((one) => one.mode);
    setForm((current) => ({
      ...current,
      ...Object.fromEntries(
        NUMBER_FIELDS.filter((key) => !current[key] && current[key] !== 0).map(
          (key) => [key, picked.defaults[key] ?? ""],
        ),
      ),
      action: picked.action,
      mode: offered.includes(current.mode) ? current.mode : offered[0] || "",
    }));
  };
  // Every field the mode takes, as typed, and no other; shown by its tool and mode until the server resolves it.
  const add = () => {
    const fault = {
      action: form.action,
      mode: form.mode,
      ...Object.fromEntries(
        mode.fields.map((key) => [key, asTyped(key, typedIn(key))]),
      ),
    };
    onChange([
      ...faults,
      { ...fault, tool: target.tool, line: `${target.tool}: ${mode.label}` },
    ]);
    setForm(null);
  };

  return (
    <Stack spacing={1.5} data-testid="test-conditions">
      {faults.length === 0 && (
        <Typography sx={{ typography: "s1", color: "text.subtitle" }}>
          {TEST_CONDITIONS_COPY.empty}
        </Typography>
      )}
      {faults.map((fault, index) => (
        <Stack
          key={`${fault.action}-${index}`}
          direction="row"
          alignItems="center"
          spacing={1}
        >
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Typography sx={{ typography: "s1" }}>{fault.line}</Typography>
            {testConditionRoute(fault) && (
              <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
                {testConditionRoute(fault)}
              </Typography>
            )}
            {/* The server's problems with this fault, each by the field it names. */}
            {problems
              .filter((one) => one.index === index)
              .map((one) => (
                <Typography
                  key={`${one.field}-${one.message}`}
                  role="alert"
                  sx={{ typography: "s2", color: "error.main" }}
                >
                  {one.field
                    ? `${TEST_CONDITIONS_COPY.fields[one.field] || one.field}: ${one.message}`
                    : one.message}
                </Typography>
              ))}
          </Box>
          {!disabled && (
            <IconButton
              size="small"
              aria-label={TEST_CONDITIONS_COPY.remove}
              onClick={() => onChange(faults.filter((_, at) => at !== index))}
            >
              <Iconify icon="solar:trash-bin-minimalistic-linear" width={16} />
            </IconButton>
          )}
        </Stack>
      ))}
      {error && (
        <Typography role="alert" sx={{ typography: "s1", color: "error.main" }}>
          {error}
        </Typography>
      )}
      {!disabled && !form && (
        <Box>
          <Button
            size="small"
            variant="outlined"
            color="inherit"
            onClick={() => setForm(EMPTY)}
            startIcon={<Iconify icon="solar:add-circle-linear" width={14} />}
          >
            {TEST_CONDITIONS_COPY.add}
          </Button>
        </Box>
      )}
      {!disabled && form && (
        <Stack
          spacing={1.5}
          sx={{
            p: 1.5,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 1,
          }}
        >
          <TextField
            select
            size="small"
            label={TEST_CONDITIONS_COPY.tool}
            value={form.action}
            onChange={pickTool}
            inputProps={{ "data-testid": "test-condition-tool" }}
          >
            {targets?.map((one) => (
              <MenuItem
                key={one.action}
                value={one.action}
                disabled={!one.reachable}
              >
                {one.reachable ? (
                  one.tool
                ) : (
                  <Tooltip title={TEST_CONDITIONS_COPY.inProcess} describeChild>
                    <span>{one.tool}</span>
                  </Tooltip>
                )}
              </MenuItem>
            ))}
          </TextField>
          <TextField
            select
            size="small"
            label={TEST_CONDITIONS_COPY.whatHappens}
            value={form.mode}
            onChange={set("mode")}
            disabled={!target}
            helperText={mode?.hint}
          >
            {modes.map((one) => (
              <MenuItem key={one.mode} value={one.mode}>
                {one.label}
              </MenuItem>
            ))}
          </TextField>
          {/* The fields the picked mode takes, as the tool lists them. */}
          <Stack direction="row" flexWrap="wrap" gap={1.5}>
            {(mode ? mode.fields : []).map((key) => (
              <TestConditionField
                key={key}
                name={key}
                mode={mode}
                value={typedIn(key)}
                bounds={target.bounds[key]}
                onChange={set(key)}
              />
            ))}
          </Stack>
          <Stack direction="row" spacing={1} justifyContent="flex-end">
            <Button size="small" color="inherit" onClick={() => setForm(null)}>
              Cancel
            </Button>
            <Button
              size="small"
              variant="contained"
              disabled={!form.action || !form.mode || unreadable}
              onClick={add}
            >
              {TEST_CONDITIONS_COPY.add}
            </Button>
          </Stack>
        </Stack>
      )}
    </Stack>
  );
}

TestConditionsEditor.propTypes = {
  faults: PropTypes.arrayOf(PropTypes.object).isRequired,
  onChange: PropTypes.func.isRequired,
  env: PropTypes.shape({
    faultTargets: PropTypes.arrayOf(
      PropTypes.shape({
        tool: PropTypes.string,
        action: PropTypes.string,
        reachable: PropTypes.bool,
        // Each mode the world can apply, with the fields it takes and what it means for the agent.
        modes: PropTypes.arrayOf(
          PropTypes.shape({
            mode: PropTypes.string,
            label: PropTypes.string,
            fields: PropTypes.arrayOf(PropTypes.string),
            hint: PropTypes.string,
          }),
        ),
        defaults: PropTypes.object,
        bounds: PropTypes.object,
      }),
    ),
  }),
  disabled: PropTypes.bool,
  error: PropTypes.string,
  // The server's problems: `{index, field, message}` each, `index` the fault in `faults`.
  problems: PropTypes.arrayOf(
    PropTypes.shape({
      index: PropTypes.number,
      field: PropTypes.string,
      message: PropTypes.string,
    }),
  ),
};
