import PropTypes from "prop-types";
import { useMemo } from "react";
import {
  Box,
  Checkbox,
  ListItemText,
  MenuItem,
  Select,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { personaTaskOptions } from "../_mock/personaScenarios";

/**
 * Which of the environment's tasks each library persona is written into.
 *
 *   Same tasks for everyone — N tasks every persona does, so results compare
 *                             persona to persona on one task (default)
 *   Different tasks each    — N tasks per persona, none shared, for breadth
 *   Every task              — full coverage: each persona does every task
 *   Pick tasks              — the tasks you choose, for each persona
 *
 * The N tasks are the least-covered ones — tasks no library persona has
 * played here come first. Shared by Add scenarios → From persona library
 * and the Personas tab's "Use in an environment" dialog.
 */
const MODES = [
  { value: "same", label: "Same tasks for everyone" },
  { value: "spread", label: "Different tasks each" },
  { value: "every", label: (n) => `Every task (${n})` },
  { value: "pick", label: "Pick tasks…" },
];

/* Why this many tasks — said for the plan and number actually chosen. Two
   is the default: the fewest where a caller's result is more than a single
   data point, and still cheap enough that adding personas stays a quick step. */
const countHint = (mode, per) => {
  if (mode === "spread") {
    return `Each persona gets ${per} task${per === 1 ? "" : "s"} of its own, none shared — wider coverage of this environment, but results don't compare persona to persona. Use Same tasks for everyone to compare callers.`;
  }
  if (per === 1) {
    return "Every persona plays the same 1 task. Quick, but a single result can't tell a caller problem from a task problem — 2 or more can.";
  }
  if (per === 2) {
    return "Every persona plays the same 2 tasks — the ones least covered here — so you can see whether one caller struggles where the others don't, at a small cost per run. For a firmer pattern, pick 3 or Every task.";
  }
  return `Every persona plays the same ${per} tasks — the ones least covered here. ${per} is enough to see a pattern: a caller who fails all ${per} where others pass points at the caller, not the task.`;
};

/* The select's own text matches the footer copy beside it (s2), rather than
   the input default, so the controls don't outweigh the sentence. */
const SELECT_SX = {
  typography: "s2",
  height: 32,
  "& .MuiSelect-select": { typography: "s2", py: 0.5 },
};

export default function PersonaTaskPlan({
  env,
  envState,
  plan,
  onChange,
  labels = false,
}) {
  const options = useMemo(
    () => personaTaskOptions(env, envState),
    [env, envState],
  );
  const set = (patch) => onChange({ ...plan, ...patch });
  const full = labels ? { width: "100%" } : {};
  const hasCount = plan.mode === "same" || plan.mode === "spread";

  const modeSelect = (
    <Select
      size="small"
      value={plan.mode}
      onChange={(e) => set({ mode: e.target.value })}
      sx={{ ...SELECT_SX, minWidth: 188, ...full }}
    >
      {MODES.map((m) => (
        <MenuItem key={m.value} value={m.value} sx={{ typography: "s2" }}>
          {typeof m.label === "function" ? m.label(options.length) : m.label}
        </MenuItem>
      ))}
    </Select>
  );

  const second = hasCount ? (
    <Select
      size="small"
      value={plan.per}
      onChange={(e) => set({ per: e.target.value })}
      sx={{ ...SELECT_SX, minWidth: 60, ...full }}
    >
      {[1, 2, 3].map((n) => (
        <MenuItem key={n} value={n} sx={{ typography: "s2" }}>
          {n}
        </MenuItem>
      ))}
    </Select>
  ) : plan.mode === "pick" ? (
    <Select
      size="small"
      multiple
      displayEmpty
      value={plan.taskKeys}
      onChange={(e) => set({ taskKeys: e.target.value })}
      renderValue={(v) =>
        v.length
          ? `${v.length} task${v.length === 1 ? "" : "s"}`
          : "Choose tasks"
      }
      MenuProps={{ PaperProps: { sx: { maxHeight: 360, maxWidth: 440 } } }}
      sx={{
        ...SELECT_SX,
        minWidth: 128,
        color: plan.taskKeys.length ? "text.primary" : "text.subtitle",
        ...full,
      }}
    >
      {options.map((o) => (
        <MenuItem
          key={o.key}
          value={o.key}
          sx={{ py: 0.5, alignItems: "flex-start" }}
        >
          <Checkbox
            size="small"
            checked={plan.taskKeys.includes(o.key)}
            sx={{ p: 0.5, mr: 1 }}
          />
          <ListItemText
            primary={o.title}
            secondary={o.kind}
            primaryTypographyProps={{
              typography: "s2",
              sx: { whiteSpace: "normal" },
            }}
            secondaryTypographyProps={{
              typography: "s3",
              color: "text.subtitle",
            }}
          />
        </MenuItem>
      ))}
    </Select>
  ) : null;

  if (labels) {
    return (
      <Stack direction="row" spacing={1.5}>
        <Box flex={2} minWidth={0}>
          <Label>Tasks</Label>
          {modeSelect}
        </Box>
        {second && (
          <Box flex={1} minWidth={0}>
            {hasCount ? (
              <Tooltip
                title={countHint(plan.mode, plan.per)}
                placement="top"
                arrow
              >
                <Stack
                  direction="row"
                  alignItems="center"
                  spacing={0.5}
                  sx={{ cursor: "help", mb: 0.75, width: "fit-content" }}
                >
                  <Label sx={{ mb: 0 }}>Tasks per persona</Label>
                  <Iconify
                    icon="solar:info-circle-linear"
                    width={14}
                    sx={{ color: "text.subtitle" }}
                  />
                </Stack>
              </Tooltip>
            ) : (
              <Label>Which tasks</Label>
            )}
            {second}
          </Box>
        )}
      </Stack>
    );
  }

  return (
    <Stack direction="row" alignItems="center" spacing={1}>
      <Typography
        sx={{ typography: "s3", color: "text.subtitle", whiteSpace: "nowrap" }}
      >
        Tasks
      </Typography>
      {modeSelect}
      {second}
      {hasCount && (
        <Tooltip title={countHint(plan.mode, plan.per)} placement="top" arrow>
          <Stack
            direction="row"
            alignItems="center"
            spacing={0.5}
            sx={{ cursor: "help" }}
          >
            <Typography
              sx={{
                typography: "s3",
                color: "text.subtitle",
                whiteSpace: "nowrap",
              }}
            >
              per persona
            </Typography>
            <Iconify
              icon="solar:info-circle-linear"
              width={14}
              sx={{ color: "text.subtitle" }}
            />
          </Stack>
        </Tooltip>
      )}
    </Stack>
  );
}

PersonaTaskPlan.propTypes = {
  env: PropTypes.object,
  envState: PropTypes.object,
  plan: PropTypes.shape({
    mode: PropTypes.string,
    per: PropTypes.number,
    taskKeys: PropTypes.array,
  }).isRequired,
  onChange: PropTypes.func.isRequired,
  labels: PropTypes.bool,
};

function Label({ children, sx }) {
  return (
    <Typography
      sx={{
        typography: "s3",
        fontWeight: 600,
        textTransform: "none",
        letterSpacing: 0.5,
        color: "text.subtitle",
        mb: 0.75,
        ...sx,
      }}
    >
      {children}
    </Typography>
  );
}
Label.propTypes = { children: PropTypes.node, sx: PropTypes.object };
