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
import {
  TEST_CONDITIONS_COPY,
  TEST_CONDITION_HINTS,
  TIMEOUT_MODES,
  fileFault,
  testConditionLine,
  testConditionRoute,
} from "./testConditions.constants";

const EMPTY = {
  action: "",
  mode: "",
  on_call: "",
  delay_seconds: "",
  status: "",
  message: "",
};
const NUMBERS = ["on_call", "delay_seconds", "status"];

const asNumber = (typed) =>
  String(typed).trim() === "" ? null : Number(typed);

// An in-process tool is listed but cannot be chosen; the server validates the rest.
export default function TestConditionsEditor({
  faults,
  onChange,
  env,
  disabled = false,
  error = "",
}) {
  const [form, setForm] = useState(null);
  const targets = env?.faultTargets;
  const set = (key) => (event) =>
    setForm((current) => ({ ...current, [key]: event.target.value }));
  const target = form && targets?.find((one) => one.action === form.action);
  const modes = target ? target.modes : [];
  const bound = (key) => target?.bounds[key] || {};
  // Falls back to the new tool's first mode when it lacks the picked one.
  const pickTool = (event) => {
    const picked = targets.find((one) => one.action === event.target.value);
    const offered = picked.modes.map((one) => one.mode);
    setForm((current) => ({
      ...current,
      ...Object.fromEntries(
        NUMBERS.filter((key) => current[key] === "").map((key) => [
          key,
          picked.defaults[key] ?? "",
        ]),
      ),
      action: picked.action,
      mode: offered.includes(current.mode) ? current.mode : offered[0] || "",
    }));
  };
  const add = () => {
    const fault = fileFault({
      ...form,
      ...Object.fromEntries(NUMBERS.map((key) => [key, asNumber(form[key])])),
    });
    const label = modes.find((one) => one.mode === form.mode)?.label;
    onChange([...faults, { ...fault, tool: target.tool, mode_label: label }]);
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
            <Typography sx={{ typography: "s1" }}>
              {testConditionLine(fault)}
            </Typography>
            {testConditionRoute(fault) && (
              <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
                {testConditionRoute(fault)}
              </Typography>
            )}
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
            helperText={TEST_CONDITION_HINTS[form.mode]}
          >
            {modes.map((one) => (
              <MenuItem key={one.mode} value={one.mode}>
                {one.label}
              </MenuItem>
            ))}
          </TextField>
          <Stack direction="row" spacing={1.5}>
            <TextField
              size="small"
              type="number"
              label={TEST_CONDITIONS_COPY.onCall}
              value={form.on_call}
              onChange={set("on_call")}
              inputProps={{ min: 1 }}
            />
            {TIMEOUT_MODES.includes(form.mode) && (
              <TextField
                size="small"
                type="number"
                label={TEST_CONDITIONS_COPY.delay}
                value={form.delay_seconds}
                onChange={set("delay_seconds")}
                inputProps={bound("delay_seconds")}
              />
            )}
            {form.mode === "error" && (
              <TextField
                size="small"
                type="number"
                label={TEST_CONDITIONS_COPY.status}
                value={form.status}
                onChange={set("status")}
                inputProps={bound("status")}
              />
            )}
          </Stack>
          {form.mode === "error" && (
            <TextField
              size="small"
              label={TEST_CONDITIONS_COPY.message}
              value={form.message}
              onChange={set("message")}
              inputProps={{ maxLength: 500 }}
            />
          )}
          <Stack direction="row" spacing={1} justifyContent="flex-end">
            <Button size="small" color="inherit" onClick={() => setForm(null)}>
              Cancel
            </Button>
            <Button
              size="small"
              variant="contained"
              disabled={!form.action || !form.mode}
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
        modes: PropTypes.arrayOf(
          PropTypes.shape({ mode: PropTypes.string, label: PropTypes.string }),
        ),
        defaults: PropTypes.object,
        bounds: PropTypes.object,
      }),
    ),
  }),
  disabled: PropTypes.bool,
  error: PropTypes.string,
};
