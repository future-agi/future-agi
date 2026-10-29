import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Box,
  Button,
  Checkbox,
  Chip,
  Dialog,
  FormControlLabel,
  IconButton,
  InputBase,
  MenuItem,
  Select,
  Stack,
  Typography,
} from "@mui/material";
import { enqueueSnackbar } from "notistack";

import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { paths } from "src/routes/paths";
import {
  fixErrorOf,
  useFixLaunchOptions,
  useLaunchFix,
} from "src/api/simulate-environments/fixRuns";

import ScenarioPicker from "./ScenarioPicker";
import { formatCredits, formatMinutes } from "./fixRunView";
import {
  budgetSteps,
  countsLine,
  heldBackLine,
  launchError,
  minuteSteps,
  reviewSentence,
} from "./launchFormView";

const selectSx = {
  height: 34,
  typography: "s2",
  "& .MuiSelect-select": { typography: "s2", py: 0.75 },
};
const linkSx = {
  typography: "s2",
  color: "text.secondary",
  cursor: "pointer",
  border: 0,
  p: 0,
  bgcolor: "transparent",
  "&:hover": { color: "text.primary" },
};

function Row({ k, children }) {
  return (
    <Stack
      direction="row"
      spacing={1.5}
      sx={{
        py: 1,
        borderTop: "1px solid",
        borderColor: "divider",
        "&:first-of-type": { borderTop: 0, pt: 0 },
      }}
    >
      <Typography
        sx={{
          typography: "s2",
          color: "text.subtitle",
          width: 110,
          flexShrink: 0,
        }}
      >
        {k}
      </Typography>
      <Box sx={{ flex: 1, minWidth: 0 }}>{children}</Box>
    </Stack>
  );
}
Row.propTypes = { k: PropTypes.string.isRequired, children: PropTypes.node };

function LockedFiles({ paths: locked, onChange }) {
  return (
    <Stack direction="row" flexWrap="wrap" alignItems="center" gap={0.75}>
      {locked.map((p) => (
        <Chip
          key={p}
          size="small"
          label={p}
          onDelete={() => onChange(locked.filter((x) => x !== p))}
        />
      ))}
      <InputBase
        placeholder={
          locked.length ? "Add a path" : "None. Add a path it must never edit"
        }
        inputProps={{ "aria-label": "Add a locked file" }}
        onKeyDown={(e) => {
          const v = e.target.value.trim();
          if (e.key !== "Enter" || !v) return;
          onChange([...new Set([...locked, v])]);
          e.target.value = "";
        }}
        sx={{ typography: "s2", flex: 1, minWidth: 180 }}
      />
    </Stack>
  );
}
LockedFiles.propTypes = {
  paths: PropTypes.array.isRequired,
  onChange: PropTypes.func.isRequired,
};

// Screen 1 of Fix my agent v0: one read (launch-options) prefills it, one
// write (launch) starts it. The user decides the cases to fix and protect, the
// parts that may change and the limit; everything else is shown, not asked.
export default function LaunchFixDialog({ open, onClose, envId, sourceRunId }) {
  const navigate = useNavigate();
  const { options } = useFixLaunchOptions(sourceRunId, { enabled: open });
  const launch = useLaunchFix(envId);
  const [targets, setTargets] = useState([]);
  const [protectedKeys, setProtectedKeys] = useState([]);
  const [surfaces, setSurfaces] = useState([]);
  const [locked, setLocked] = useState([]);
  const [budget, setBudget] = useState(0);
  const [minutes, setMinutes] = useState(0);
  const [picking, setPicking] = useState(false);

  useEffect(() => {
    if (!options) return;
    setTargets(options.suggested.target_scenario_keys);
    setProtectedKeys(options.suggested.protected_scenario_keys);
    setSurfaces(options.defaults.surfaces);
    setLocked([]);
    setBudget(options.defaults.budget_credits);
    setMinutes(options.defaults.max_minutes);
  }, [options]);

  if (!options) {
    return (
      <Dialog
        open={open}
        onClose={onClose}
        maxWidth={false}
        fullWidth
        PaperProps={{ sx: { maxWidth: 720 } }}
      >
        <Typography sx={{ p: 3, typography: "s2", color: "text.subtitle" }}>
          Loading the run…
        </Typography>
      </Dialog>
    );
  }

  const { agent, environment, limits } = options;
  const error = launchError({ targets, protectedKeys, surfaces, limits });
  const blockedReason = options.blocked?.message;
  const startDisabled = !!blockedReason || !!error || launch.isPending;
  const agentLine = [
    `${agent.name} · agent v${agent.version_number}`,
    agent.ref,
  ]
    .filter(Boolean)
    .join(" · ");
  const identity =
    environment.version == null
      ? agentLine
      : `${agentLine} × environment v${environment.version}`;

  const start = async () => {
    try {
      const fix = await launch.mutateAsync({
        source_run_id: sourceRunId,
        target_scenario_keys: targets,
        protected_scenario_keys: protectedKeys,
        surfaces,
        locked_paths: locked,
        budget_credits: budget,
        max_minutes: minutes,
      });
      onClose();
      navigate(
        paths.dashboard.simulate.environments.improvement(envId, fix.id),
      );
    } catch (e) {
      enqueueSnackbar(
        fixErrorOf(e).message || "Could not start the improvement",
        { variant: "error" },
      );
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth={false}
      fullWidth
      PaperProps={{
        sx: { maxWidth: 720, borderRadius: "8px", backgroundImage: "none" },
      }}
    >
      <Stack
        direction="row"
        alignItems="flex-start"
        sx={{
          px: 2.5,
          py: 2,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <Box flex={1} minWidth={0}>
          <Typography
            sx={{ typography: "s1", fontWeight: 600 }}
          >{`Improve ${agent.name}`}</Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            {`Searches for a better ${agent.name}, scored by this environment`}
          </Typography>
        </Box>
        <IconButton size="small" onClick={onClose} aria-label="Close">
          <Iconify icon="mingcute:close-line" width={18} />
        </IconButton>
      </Stack>

      <Box sx={{ px: 2.5, py: 2, overflowY: "auto" }}>
        <Typography sx={{ typography: "s2", mb: 1.5 }}>
          {reviewSentence({
            targets: targets.length,
            protectedCount: protectedKeys.length,
            surfaces,
            all: options.surfaces,
            budget,
            minutes,
          })}
        </Typography>
        <Box>
          <Row k="Agent">
            <Typography sx={{ typography: "s2" }}>{identity}</Typography>
          </Row>
          <Row k="Runs in">
            <Typography sx={{ typography: "s2" }}>
              {environment.name}
            </Typography>
          </Row>
          <Row k="Scenarios">
            <Typography sx={{ typography: "s2" }}>
              {countsLine(targets.length, protectedKeys.length)}
            </Typography>
            <Typography
              sx={{ typography: "s3", color: "text.tertiary", mt: 0.25 }}
            >
              {heldBackLine(targets.length)}
            </Typography>
            <Typography
              component="button"
              type="button"
              onClick={() => setPicking((v) => !v)}
              sx={{ ...linkSx, mt: 0.75 }}
            >
              {picking ? "Hide cases" : "Choose cases"}
            </Typography>
            {picking && (
              <ScenarioPicker
                options={options}
                targets={targets}
                protectedKeys={protectedKeys}
                onTargets={setTargets}
                onProtected={setProtectedKeys}
              />
            )}
          </Row>
          <Row k="May change">
            <Stack direction="row" flexWrap="wrap" columnGap={2}>
              {options.surfaces.map((s) => (
                <CustomTooltip
                  key={s.id}
                  show={!s.allowed && !!s.reason}
                  title={s.reason || ""}
                  size="small"
                  arrow
                >
                  <Box component="span">
                    <FormControlLabel
                      label={s.label}
                      disabled={!s.allowed}
                      control={
                        <Checkbox
                          size="small"
                          checked={surfaces.includes(s.id)}
                          onChange={() =>
                            setSurfaces((cur) =>
                              cur.includes(s.id)
                                ? cur.filter((x) => x !== s.id)
                                : [...cur, s.id],
                            )
                          }
                        />
                      }
                      sx={{
                        mr: 0,
                        "& .MuiFormControlLabel-label": { typography: "s2" },
                      }}
                    />
                  </Box>
                </CustomTooltip>
              ))}
            </Stack>
          </Row>
          <Row k="Locked files">
            <LockedFiles paths={locked} onChange={setLocked} />
          </Row>
          <Row k="Budget">
            <Stack direction="row" spacing={1.5}>
              <Select
                size="small"
                value={budget}
                onChange={(e) => setBudget(Number(e.target.value))}
                sx={{ ...selectSx, width: 220 }}
                inputProps={{ "aria-label": "Budget" }}
              >
                {budgetSteps(budget, limits).map((n) => (
                  <MenuItem key={n} value={n} sx={{ typography: "s2" }}>
                    {formatCredits(n)}
                  </MenuItem>
                ))}
              </Select>
              <Select
                size="small"
                value={minutes}
                onChange={(e) => setMinutes(Number(e.target.value))}
                sx={{ ...selectSx, width: 220 }}
                inputProps={{ "aria-label": "Time limit" }}
              >
                {minuteSteps(minutes, limits).map((n) => (
                  <MenuItem key={n} value={n} sx={{ typography: "s2" }}>
                    {formatMinutes(n)}
                  </MenuItem>
                ))}
              </Select>
            </Stack>
          </Row>
          <Row k="Cost">
            <Typography sx={{ typography: "s2" }}>
              Stops at the limit with the best it found.
            </Typography>
          </Row>
        </Box>
      </Box>

      <Stack
        direction="row"
        alignItems="center"
        spacing={1.5}
        sx={{ px: 2.5, py: 2, borderTop: "1px solid", borderColor: "divider" }}
      >
        <Typography
          sx={{
            typography: "s3",
            color: blockedReason || error ? "text.primary" : "text.tertiary",
            flex: 1,
          }}
        >
          {blockedReason ||
            error ||
            "A winner comes back as a new version. You decide whether to make it the default."}
        </Typography>
        <Button size="small" onClick={onClose} sx={{ color: "text.secondary" }}>
          Cancel
        </Button>
        <Button
          size="small"
          variant="contained"
          color="primary"
          disabled={startDisabled}
          onClick={start}
          startIcon={<Iconify icon="solar:play-bold" width={16} />}
        >
          Start improvement
        </Button>
      </Stack>
    </Dialog>
  );
}

LaunchFixDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
  envId: PropTypes.string.isRequired,
  sourceRunId: PropTypes.string,
};
