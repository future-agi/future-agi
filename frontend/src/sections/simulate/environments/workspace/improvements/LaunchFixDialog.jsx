import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Box,
  Button,
  Checkbox,
  Chip,
  FormControlLabel,
  InputBase,
  MenuItem,
  Select,
  Stack,
  Typography,
} from "@mui/material";
import { enqueueSnackbar } from "notistack";

import { ClosableDialog } from "src/components/custom-dialog";
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
  NO_MODELS_NOTE,
  UNMEASURED_NOTE,
  UNTRACED_NOTE,
  blockedByDraft,
  blockedReason as blockedReasonOf,
  budgetSteps,
  costLimitOf,
  countsLine,
  isTraced,
  callsUnmeasured,
  seesModels,
  tracesUnreadable,
  launchError,
  marginSteps,
  minuteSteps,
  reviewSentence,
  variationsNote,
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

function Row({ k, center = false, children }) {
  return (
    <Stack
      direction="row"
      spacing={1.5}
      alignItems={center ? "center" : "flex-start"}
      sx={{
        py: 1,
        minHeight: 38,
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
Row.propTypes = {
  k: PropTypes.string.isRequired,
  center: PropTypes.bool,
  children: PropTypes.node,
};

// Values typed one at a time, each added by Enter, a comma or leaving the field.
function ChipsInput({ values, onChange, label, placeholder, testId }) {
  const add = (input) => {
    const v = input.value.trim().replace(/,$/, "").trim();
    if (!v) return;
    onChange([...new Set([...values, v])]);
    input.value = "";
  };
  return (
    <Stack
      direction="row"
      flexWrap="wrap"
      alignItems="center"
      gap={0.75}
      data-testid={testId}
    >
      {values.map((p) => (
        <Chip
          key={p}
          size="small"
          label={p}
          onDelete={() => onChange(values.filter((x) => x !== p))}
        />
      ))}
      <InputBase
        placeholder={
          values.length ? "Add another, then press Enter" : placeholder
        }
        inputProps={{ "aria-label": label }}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            add(e.target);
          }
        }}
        onChange={(e) => {
          if (e.target.value.endsWith(",")) add(e.target);
        }}
        onBlur={(e) => add(e.target)}
        sx={{
          flex: 1,
          minWidth: 180,
          "& .MuiInputBase-input": { typography: "s2", p: 0, height: 22 },
        }}
      />
    </Stack>
  );
}
ChipsInput.propTypes = {
  values: PropTypes.array.isRequired,
  onChange: PropTypes.func.isRequired,
  label: PropTypes.string.isRequired,
  placeholder: PropTypes.string.isRequired,
  testId: PropTypes.string,
};

// Screen 1 of Fix my agent v0: one read (launch-options) prefills it, one
// write (launch) starts it. The user decides the cases to fix and protect, the
// parts that may change and the limit; everything else is shown, not asked.
export default function LaunchFixDialog({ open, onClose, envId, sourceRunId }) {
  const navigate = useNavigate();
  const { options, error: optionsError } = useFixLaunchOptions(sourceRunId, {
    enabled: open,
  });
  const launch = useLaunchFix(envId);
  const [targets, setTargets] = useState([]);
  const [protectedKeys, setProtectedKeys] = useState([]);
  const [surfaces, setSurfaces] = useState([]);
  const [locked, setLocked] = useState([]);
  const [budget, setBudget] = useState(0);
  const [minutes, setMinutes] = useState(0);
  const [models, setModels] = useState([]);
  const [latencyMargin, setLatencyMargin] = useState(0);
  const [costMargin, setCostMargin] = useState(0);
  const [costLimit, setCostLimit] = useState("");
  const [picking, setPicking] = useState(false);

  useEffect(() => {
    if (!options) return;
    setTargets(options.suggested.target_scenario_keys);
    setProtectedKeys(options.suggested.protected_scenario_keys);
    setSurfaces(options.defaults.surfaces);
    setLocked([]);
    setBudget(options.defaults.budget_credits);
    setMinutes(options.defaults.max_minutes);
    setModels(options.defaults.allowed_models);
    setLatencyMargin(options.defaults.reply_latency_margin_pct);
    setCostMargin(options.defaults.cost_margin_pct);
    setCostLimit("");
  }, [options]);

  if (!options) {
    return (
      <ClosableDialog
        open={open}
        onClose={onClose}
        maxWidth={false}
        fullWidth
        PaperProps={{ sx: { maxWidth: 720 } }}
      >
        {optionsError ? (
          <Stack
            direction="row"
            alignItems="center"
            spacing={2}
            sx={{ p: 3, pr: 6 }}
          >
            <Typography sx={{ typography: "s2", flex: 1 }}>
              {fixErrorOf(optionsError).message ||
                "Could not load this run to improve it."}
            </Typography>
          </Stack>
        ) : (
          <Typography sx={{ p: 3, typography: "s2", color: "text.subtitle" }}>
            Loading the run…
          </Typography>
        )}
      </ClosableDialog>
    );
  }

  const { agent, environment, limits } = options;
  const lockable = seesModels(options);
  const error = launchError({
    targets,
    protectedKeys,
    surfaces,
    limits,
    models,
    seenModels: options.defaults.allowed_models,
    costLimit,
  });
  const blockedReason = blockedReasonOf(options, targets.length);
  const draftBlocks = blockedByDraft(options, targets.length);
  const variations = variationsNote(targets.length, limits, agent.kind);
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
        // A form that saw no model leaves the default to the platform.
        ...(lockable ? { allowed_models: models } : {}),
        reply_latency_margin_pct: latencyMargin,
        cost_margin_pct: costMargin,
        cost_limit_usd: costLimitOf(costLimit),
      });
      onClose();
      navigate(
        paths.dashboard.simulate.environments.improvement(envId, fix.id),
      );
    } catch (e) {
      enqueueSnackbar(fixErrorOf(e).message || "Could not start the epoch", {
        variant: "error",
      });
    }
  };

  return (
    <ClosableDialog
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
        <Box flex={1} minWidth={0} sx={{ pr: 4 }}>
          <Typography
            sx={{ typography: "s1", fontWeight: 600 }}
          >{`Run Epoch on ${agent.name}`}</Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            {`Searches for a better ${agent.name}, scored by this environment`}
          </Typography>
        </Box>
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
              {countsLine(targets.length, protectedKeys.length, limits)}
            </Typography>
            {variations && (
              <Typography
                sx={{ typography: "s3", color: "text.subtitle", mt: 0.5 }}
              >
                {variations}
              </Typography>
            )}
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
          <Row k="May change" center>
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
          <Row k="Locked files" center>
            <ChipsInput
              values={locked}
              onChange={setLocked}
              label="Add a locked file"
              placeholder="Files or globs it must never edit, like prompts/*.md. Press Enter to add each."
              testId="locked-files"
            />
          </Row>
          <Row k="Models" center>
            {lockable ? (
              <ChipsInput
                values={models}
                onChange={setModels}
                label="Add an allowed model"
                placeholder="The models it may call. Press Enter to add each."
                testId="allowed-models"
              />
            ) : (
              <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
                {tracesUnreadable(options)
                  ? options.blocked.message
                  : callsUnmeasured(options)
                    ? UNMEASURED_NOTE
                    : isTraced(options)
                      ? NO_MODELS_NOTE
                      : UNTRACED_NOTE}
              </Typography>
            )}
          </Row>
          <Row k="Reply time" center>
            <Select
              size="small"
              value={latencyMargin}
              onChange={(e) => setLatencyMargin(Number(e.target.value))}
              sx={{ ...selectSx, width: 220 }}
              inputProps={{ "aria-label": "Reply time margin" }}
            >
              {marginSteps(latencyMargin).map((n) => (
                <MenuItem key={n} value={n} sx={{ typography: "s2" }}>
                  {n === 0 ? "No increase" : `Up to ${n}% slower than now`}
                </MenuItem>
              ))}
            </Select>
          </Row>
          <Row k="Agent's cost" center>
            <Stack direction="row" spacing={1.5} alignItems="center">
              <Select
                size="small"
                value={costMargin}
                onChange={(e) => setCostMargin(Number(e.target.value))}
                sx={{ ...selectSx, width: 220 }}
                inputProps={{ "aria-label": "Agent's cost margin" }}
              >
                {marginSteps(costMargin).map((n) => (
                  <MenuItem key={n} value={n} sx={{ typography: "s2" }}>
                    {n === 0 ? "No increase" : `Up to ${n}% more than now`}
                  </MenuItem>
                ))}
              </Select>
              <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
                and at most $
              </Typography>
              <InputBase
                value={costLimit}
                onChange={(e) => setCostLimit(e.target.value)}
                placeholder="no limit"
                inputProps={{
                  "aria-label": "Agent's cost limit per conversation",
                  inputMode: "decimal",
                }}
                sx={{
                  width: 80,
                  "& .MuiInputBase-input": {
                    typography: "s2",
                    p: 0,
                    height: 22,
                  },
                }}
              />
              <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
                a conversation
              </Typography>
            </Stack>
          </Row>
          <Row k="Budget" center>
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
          <Row k="Stops at">
            <Typography sx={{ typography: "s2" }}>
              {`${formatCredits(budget)} or ${formatMinutes(minutes)}, whichever comes first.`}
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
            "A winner comes back for you to review. Nothing changes until you ship it."}
        </Typography>
        {draftBlocks && (
          <Button
            size="small"
            onClick={() => {
              onClose();
              // The Scenarios tab's draft bar saves or discards the changes.
              navigate(
                `${paths.dashboard.simulate.environments.detail(envId)}?tab=scenarios`,
              );
            }}
          >
            Review unsaved changes
          </Button>
        )}
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
          Run Epoch
        </Button>
      </Stack>
    </ClosableDialog>
  );
}

LaunchFixDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
  envId: PropTypes.string.isRequired,
  sourceRunId: PropTypes.string,
};
