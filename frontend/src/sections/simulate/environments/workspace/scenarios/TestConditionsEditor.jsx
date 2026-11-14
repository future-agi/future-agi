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
  TEST_CONDITION_MODES,
  TIMEOUT_MODES,
  faultTargets,
  testConditionLine,
} from "./testConditions.constants";

const EMPTY = {
  action: "",
  mode: "timeout_before_commit",
  on_call: 1,
  delay_seconds: 30,
  status: 500,
  message: "",
};

// The fault as the scenario file keeps it: only the fields its mode reads.
function asFault(form) {
  const fault = {
    action: form.action,
    mode: form.mode,
    on_call: Math.max(1, Number(form.on_call) || 1),
  };
  if (TIMEOUT_MODES.includes(form.mode)) {
    fault.delay_seconds = Math.min(
      30,
      Math.max(0, Number(form.delay_seconds) || 0),
    );
  }
  if (form.mode === "error") {
    fault.status = Math.min(599, Math.max(400, Number(form.status) || 500));
    if (form.message) fault.message = form.message;
  }
  return fault;
}

// A scenario's test conditions in the editor: the list with remove, and a form to add one. A tool the world's door
// cannot stand in front of is listed but cannot be chosen.
export default function TestConditionsEditor({
  faults,
  onChange,
  env,
  disabled = false,
  error = "",
}) {
  const [form, setForm] = useState(null);
  const targets = faultTargets(env);
  const set = (key) => (event) =>
    setForm((current) => ({ ...current, [key]: event.target.value }));

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
          <Typography sx={{ typography: "s1", flex: 1, minWidth: 0 }}>
            {testConditionLine(fault)}
          </Typography>
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
            startIcon={<Iconify icon="mingcute:add-line" width={14} />}
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
            onChange={set("action")}
            inputProps={{ "data-testid": "test-condition-tool" }}
          >
            {targets.map((target) => (
              <MenuItem
                key={target.action}
                value={target.action}
                disabled={!target.reachable}
              >
                {target.reachable ? (
                  target.tool
                ) : (
                  <Tooltip title={TEST_CONDITIONS_COPY.inProcess}>
                    <span>{target.tool}</span>
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
            helperText={TEST_CONDITION_HINTS[form.mode]}
          >
            {Object.entries(TEST_CONDITION_MODES).map(([mode, label]) => (
              <MenuItem key={mode} value={mode}>
                {label}
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
                inputProps={{ min: 0, max: 30 }}
              />
            )}
            {form.mode === "error" && (
              <TextField
                size="small"
                type="number"
                label={TEST_CONDITIONS_COPY.status}
                value={form.status}
                onChange={set("status")}
                inputProps={{ min: 400, max: 599 }}
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
              disabled={!form.action}
              onClick={() => {
                onChange([...faults, asFault(form)]);
                setForm(null);
              }}
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
    doorReach: PropTypes.object,
    toolEntrypoints: PropTypes.array,
    tools: PropTypes.array,
  }),
  disabled: PropTypes.bool,
  error: PropTypes.string,
};
