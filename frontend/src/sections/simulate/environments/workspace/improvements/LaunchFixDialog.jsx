import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Box, Button, Stack, Typography } from "@mui/material";
import { enqueueSnackbar } from "notistack";

import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import {
  fixErrorOf,
  useFixLaunchOptions,
  useLaunchFix,
} from "src/api/simulate-environments/fixRuns";

import SideDrawer from "../../components/SideDrawer";
import LaunchLimits, { ChipsInput, Row, SurfacesRow } from "./LaunchLimits";
import ScenarioPicker from "./ScenarioPicker";
import {
  NO_MODELS_NOTE,
  UNMEASURED_NOTE,
  UNTRACED_NOTE,
  blockedByDraft,
  blockedReason as blockedReasonOf,
  costLimitOf,
  countsLine,
  isTraced,
  callsUnmeasured,
  seesModels,
  tracesUnreadable,
  launchError,
  reviewSentence,
  variationsNote,
} from "./launchFormView";

// Screen 1 of Fix my agent v0: one read (launch-options) prefills it, one
// write (launch) starts it. The user decides the cases to fix and protect, the
// parts that may change and the limit; everything else is shown, not asked.
// ``initialTargets`` (Run Epoch) picks the cases to fix instead of the
// suggested ones.
export default function LaunchFixDialog({
  open,
  onClose,
  envId,
  sourceRunId,
  initialTargets,
  initialNote,
}) {
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
    if (!options || !open) return;
    setTargets(
      initialTargets?.length
        ? initialTargets
        : options.suggested.target_scenario_keys,
    );
    setProtectedKeys(options.suggested.protected_scenario_keys);
    setSurfaces(options.defaults.surfaces);
    setLocked([]);
    setBudget(options.defaults.budget_credits);
    setMinutes(options.defaults.max_minutes);
    setModels(options.defaults.allowed_models);
    setLatencyMargin(options.defaults.reply_latency_margin_pct);
    setCostMargin(options.defaults.cost_margin_pct);
    setCostLimit("");
  }, [options, open, initialTargets]);

  if (!options) {
    return (
      <SideDrawer open={open} onClose={onClose} width={640}>
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
      </SideDrawer>
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
    <SideDrawer open={open} onClose={onClose} width={640}>
      {/* Header and footer stay put; the form between them scrolls. */}
      <Stack
        role="dialog"
        aria-labelledby="launch-fix-title"
        sx={{ height: "100%", minHeight: 0 }}
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
              id="launch-fix-title"
              sx={{ typography: "s1", fontWeight: 600 }}
            >{`Run Epoch on ${agent.name}`}</Typography>
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              {`Searches for a better ${agent.name}, scored by this environment`}
            </Typography>
          </Box>
        </Stack>

        <Box sx={{ px: 2.5, py: 2, overflowY: "auto", flex: 1, minHeight: 0 }}>
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
              {initialNote ? (
                <Typography
                  sx={{ typography: "s2", color: "text.secondary" }}
                  data-testid="epoch-targets-note"
                >
                  {initialNote}
                </Typography>
              ) : null}
              {variations && (
                <Typography
                  sx={{ typography: "s3", color: "text.subtitle", mt: 0.5 }}
                >
                  {variations}
                </Typography>
              )}
              <Button
                size="small"
                variant="outlined"
                color="inherit"
                onClick={() => setPicking((v) => !v)}
                aria-expanded={picking}
                endIcon={
                  <Iconify
                    icon={
                      picking ? "eva:chevron-up-fill" : "eva:chevron-down-fill"
                    }
                    width={16}
                  />
                }
                sx={{ typography: "s2", alignSelf: "flex-start", mt: 1 }}
              >
                {picking ? "Hide cases" : "Choose cases"}
              </Button>
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
            <SurfacesRow
              surfaces={options.surfaces}
              value={surfaces}
              onChange={setSurfaces}
            />
            <Row k="Locked files" center>
              <ChipsInput
                values={locked}
                onChange={setLocked}
                label="Add a locked file"
                placeholder="Files or globs it must never edit, like prompts/*.md. Press Enter to add each."
                testId="locked-files"
              />
            </Row>
            <LaunchLimits
              limits={limits}
              modelsNote={
                lockable
                  ? null
                  : tracesUnreadable(options)
                    ? options.blocked.message
                    : callsUnmeasured(options)
                      ? UNMEASURED_NOTE
                      : isTraced(options)
                        ? NO_MODELS_NOTE
                        : UNTRACED_NOTE
              }
              models={models}
              onModels={setModels}
              latencyMargin={latencyMargin}
              onLatencyMargin={setLatencyMargin}
              costMargin={costMargin}
              onCostMargin={setCostMargin}
              costLimit={costLimit}
              onCostLimit={setCostLimit}
              budget={budget}
              onBudget={setBudget}
              minutes={minutes}
              onMinutes={setMinutes}
            />
          </Box>
        </Box>

        <Stack
          direction="row"
          alignItems="center"
          spacing={1.5}
          sx={{
            px: 2.5,
            py: 2,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
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
              variant="outlined"
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
          <Button
            size="small"
            onClick={onClose}
            sx={{ color: "text.secondary" }}
          >
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
      </Stack>
    </SideDrawer>
  );
}

LaunchFixDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
  envId: PropTypes.string.isRequired,
  sourceRunId: PropTypes.string,
  initialTargets: PropTypes.arrayOf(PropTypes.string),
  initialNote: PropTypes.string,
};
