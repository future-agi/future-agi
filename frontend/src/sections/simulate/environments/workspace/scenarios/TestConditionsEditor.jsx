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
  modesFor,
  testConditionLine,
  testConditionRoute,
} from "./testConditions.constants";

const EMPTY = {
  action: "",
  mode: "timeout_before_commit",
  on_call: 1,
  delay_seconds: 30,
  status: 500,
  message: "",
};

// A number as typed, an emptied one as null.
const asNumber = (typed) =>
  String(typed).trim() === "" ? null : Number(typed);

// The fault as the scenario file keeps it: only the fields its mode reads, the numbers as typed. The server checks
// their bounds and its refusal is shown on the test conditions.
function asFault(form) {
  const fault = {
    action: form.action,
    mode: form.mode,
    on_call: asNumber(form.on_call),
  };
  if (TIMEOUT_MODES.includes(form.mode)) {
    fault.delay_seconds = asNumber(form.delay_seconds);
  }
  if (form.mode === "error") {
    fault.status = asNumber(form.status);
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
  const targets = env?.faultTargets;
  const set = (key) => (event) =>
    setForm((current) => ({ ...current, [key]: event.target.value }));
  const modes = modesFor(
    form && targets?.find((one) => one.action === form.action),
  );
  // Another tool may not offer the mode picked for the last one: the first it offers is taken instead.
  const pickTool = (event) => {
    const target = targets.find((one) => one.action === event.target.value);
    const offered = modesFor(target);
    setForm((current) => ({
      ...current,
      action: event.target.value,
      mode: offered.includes(current.mode) ? current.mode : offered[0],
    }));
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
              {testConditionLine(fault, env)}
            </Typography>
            {testConditionRoute(fault, env) && (
              <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
                {testConditionRoute(fault, env)}
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
            {targets?.map((target) => (
              <MenuItem
                key={target.action}
                value={target.action}
                disabled={!target.reachable}
              >
                {target.reachable ? (
                  target.tool
                ) : (
                  <Tooltip title={TEST_CONDITIONS_COPY.inProcess} describeChild>
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
            {modes.map((mode) => (
              <MenuItem key={mode} value={mode}>
                {TEST_CONDITION_MODES[mode]}
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
    // The tools a test condition can name: {tool, action, reachable, writes}.
    faultTargets: PropTypes.arrayOf(
      PropTypes.shape({
        tool: PropTypes.string,
        action: PropTypes.string,
        reachable: PropTypes.bool,
        writes: PropTypes.bool,
      }),
    ),
  }),
  disabled: PropTypes.bool,
  error: PropTypes.string,
};
