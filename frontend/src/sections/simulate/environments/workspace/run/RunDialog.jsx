import PropTypes from "prop-types";
import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  Button,
  DialogActions,
  DialogContent,
  DialogTitle,
  MenuItem,
  Skeleton,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { ClosableDialog } from "src/components/custom-dialog";
import Iconify from "src/components/iconify";
import ContentSkeleton from "src/components/content-skeleton";
import {
  VERSION_PIECES,
  isVersioningDisabled,
  useDrafts,
  useEnvironmentVersions,
  useRunPlan,
  useSuiteMembers,
} from "src/api/simulate-environments/versions";
import { DRAFTS_COPY, RUN_DIALOG_COPY as COPY } from "../workspace.constants";
import { inlineRunError, planRefusal } from "./runErrors";
import ReadyToRunSection from "./readiness/ReadyToRunSection";
import {
  READY_TO_RUN_COPY,
  RUN_PLAN_COPY,
} from "./readiness/readiness.constants";

// Only versions a run can take are offered: an active environment or scenario
// version, an agent version the server marks pickable (its check passed), and
// always the default.
const PICKABLE = VERSION_PIECES;
const offered = (piece, version) =>
  version.isDefault ||
  (piece === "agent" ? version.pickable : version.status === "active");

const BOX_SX = { border: "1px solid", borderColor: "divider", borderRadius: 1 };

// Notes sit compact in the plan, like its Ready to run lines: a 16px icon
// level with one line of small text.
const NOTE_SX = {
  py: 0.5,
  px: 1.5,
  alignItems: "center",
  typography: "s3",
  "& .MuiAlert-icon": { py: 0, mr: 1, fontSize: 16 },
  "& .MuiAlert-icon svg": { width: 16, height: 16 },
  "& .MuiAlert-message": { py: 0.5 },
};

const named = (version) => version?.label || COPY.unlabelled;
const optionLabel = (version) =>
  version.isDefault ? `${named(version)} (${COPY.default})` : named(version);

/**
 * Pick the agent, environment and scenario versions a run uses, see what to
 * watch out for, and start it. Only the versions the user changed are sent;
 * the rest run the environment's defaults at submit time.
 */
export default function RunDialog({
  open,
  env,
  scenarioIds,
  trials = 1,
  initialPicks,
  onClose,
  onSubmit,
  submitting = false,
  error = null,
}) {
  const versions = useEnvironmentVersions(env?.id, { enabled: open });
  const { data } = versions;
  const { data: drafts } = useDrafts(env?.id, {
    enabled: open && Boolean(env?.versioning?.enabled),
  });
  const [overrides, setOverrides] = useState(() => ({
    ...(initialPicks || {}),
  }));
  // A re-run's versions stay picked once their plan resolves: "checking" until
  // then, "fellBack" to the defaults when they can't run now.
  const [rerun, setRerun] = useState(() =>
    PICKABLE.some((piece) => initialPicks?.[piece]) ? "checking" : "done",
  );
  // Only the re-run's agent version fell back; its other versions are kept.
  const [agentFellBack, setAgentFellBack] = useState(false);
  // A refusal stays shown until the user changes a pick.
  const [dismissedError, setDismissedError] = useState(null);
  // What Ready to run read for these picks: counts and whether Run is allowed.
  const [readiness, setReadiness] = useState(null);

  const options = useMemo(
    () =>
      Object.fromEntries(
        VERSION_PIECES.map((piece) => [
          piece,
          (data?.[piece] || []).filter((version) => offered(piece, version)),
        ]),
      ),
    [data],
  );
  const defaultId = (piece) => data?.defaults?.[piece]?.id || null;
  // A pick that is no longer an active version falls back to the default.
  const selected = Object.fromEntries(
    VERSION_PIECES.map((piece) => {
      const pick = PICKABLE.includes(piece) ? overrides[piece] : null;
      const valid = pick && options[piece].some((one) => one.id === pick);
      return [piece, valid ? pick : defaultId(piece)];
    }),
  );
  const picks = Object.fromEntries(
    PICKABLE.filter(
      (piece) => selected[piece] && selected[piece] !== defaultId(piece),
    ).map((piece) => [piece, selected[piece]]),
  );

  const plan = useRunPlan(env?.id, picks, { enabled: open && Boolean(data) });
  const warnings = plan.data?.warnings || [];
  const refusedPlan = planRefusal(plan.error);
  const planFailed =
    plan.isError &&
    !plan.data &&
    !refusedPlan &&
    !isVersioningDisabled(plan.error);

  useEffect(() => {
    if (rerun !== "checking" || !data) return;
    const stale = PICKABLE.filter(
      (piece) =>
        overrides[piece] &&
        !options[piece].some((one) => one.id === overrides[piece]),
    );
    // The server refusing only the agent pick is a stale agent too.
    const agentRefused =
      refusedPlan?.error === "version_not_pickable" &&
      refusedPlan?.piece === "agent" &&
      Boolean(overrides.agent);
    if (agentRefused && !stale.includes("agent")) stale.push("agent");
    if (stale.length === 1 && stale[0] === "agent") {
      // The environment and scenario versions are still checked together.
      setOverrides(({ agent, ...kept }) => kept);
      setAgentFellBack(true);
    } else if (stale.length || refusedPlan) {
      setOverrides({});
      setAgentFellBack(false);
      setRerun("fellBack");
    } else if (plan.data || plan.isError) {
      // A plan that failed on the server keeps the re-run's versions.
      setRerun(agentFellBack ? "fellBack" : "done");
    }
  }, [
    rerun,
    data,
    options,
    overrides,
    agentFellBack,
    refusedPlan,
    plan.data,
    plan.isError,
  ]);

  const suite = options.suite.find((one) => one.id === selected.suite);
  const runAll = scenarioIds === undefined;
  // Run-all of the default suite runs the server's current list; of another
  // suite, that suite's scenarios, so it needs them. With versioning on the
  // list may show unsaved changes, so the default's are named instead.
  const needsMembers =
    runAll &&
    Boolean(picks.suite || (env?.versioning?.enabled && selected.suite));
  const members = useSuiteMembers(env?.id, selected.suite, {
    enabled: open && Boolean(selected.suite) && (!runAll || needsMembers),
  });
  const memberKeys = members.data?.map((member) => member.scenario_key);
  const missing =
    !runAll && memberKeys
      ? scenarioIds.filter((key) => !memberKeys.includes(key))
      : [];
  // Selected scenarios the picked suite lacks are skipped.
  let runIds = runAll
    ? undefined
    : scenarioIds.filter((key) => !missing.includes(key));
  if (needsMembers) runIds = memberKeys;
  let blocked = null;
  if (needsMembers && memberKeys?.length === 0) {
    blocked = COPY.emptySuite(named(suite));
  } else if (!runAll && scenarioIds.length > 0 && runIds.length === 0) {
    blocked = COPY.noneInSuite(named(suite));
  }

  const submitError =
    error && error !== dismissedError ? inlineRunError(error) : null;
  // A re-run's refused versions fall back to the defaults instead.
  const shownError = submitError || (rerun === "checking" ? null : refusedPlan);
  const pickerError =
    shownError?.error === "version_not_pickable" &&
    VERSION_PIECES.includes(shownError.piece)
      ? shownError.piece
      : null;
  const dialogError = shownError && !pickerError ? shownError : null;
  const errorText = shownError
    ? shownError.message ||
      shownError.detail ||
      COPY.errors[shownError.error] ||
      COPY.refused
    : null;

  // The server holds back blocked scenarios itself; the popup shows the
  // count and refuses only a run with nothing left to send.
  const held = readiness?.summary;
  const heldBack = held?.blocked > 0;
  const allHeld = heldBack && held.blocked >= held.requested;
  const blockingArea = readiness?.open.find(
    (line) => line.status === "blocked",
  )?.area;
  const blockingLabel = READY_TO_RUN_COPY.areas[blockingArea];

  const canSubmit =
    !submitting &&
    !versions.isLoading &&
    !plan.isLoading &&
    !refusedPlan &&
    !blocked &&
    !(!runAll && members.isLoading) &&
    (!needsMembers || memberKeys?.length > 0) &&
    (!readiness || readiness.canRun) &&
    !allHeld;

  let runLabel = warnings.length ? COPY.runAnyway : COPY.run;
  if (heldBack && !allHeld) {
    runLabel = RUN_PLAN_COPY.runOf(
      held.requested - held.blocked,
      held.requested,
    );
  }
  if (submitting) runLabel = COPY.starting;
  // Ready to run reads the scenarios this run sends. Run-all asks for the
  // picked scenario version as a whole, so it never waits on its members.
  const readinessKeys = runAll ? null : runIds;

  const pick = (piece, id) => {
    setOverrides((current) => ({ ...current, [piece]: id }));
    setDismissedError(error);
    setRerun("done");
  };

  // The resolved versions ride along so a resubmit after the defaults moved is
  // a new request.
  const submit = () => {
    if (!canSubmit) return;
    onSubmit?.(picks, runIds, selected);
  };

  const pickerField = (piece) => (
    <Stack key={piece} direction="row" spacing={2} alignItems="flex-start">
      <Typography
        component="label"
        id={`run-pick-${piece}-label`}
        htmlFor={`run-pick-${piece}`}
        sx={{
          typography: "s2",
          color: "text.secondary",
          width: 140,
          flexShrink: 0,
          pt: 0.75,
        }}
      >
        {COPY.pieces[piece]}
      </Typography>
      {versionPicker(piece)}
    </Stack>
  );

  const versionPicker = (piece) => (
    <TextField
      id={`run-pick-${piece}`}
      select
      fullWidth
      size="small"
      value={selected[piece] || ""}
      onChange={(e) => pick(piece, e.target.value)}
      disabled={!options[piece].length || submitting}
      SelectProps={{
        labelId: `run-pick-${piece}-label`,
        displayEmpty: true,
        renderValue: (id) => {
          const version = options[piece].find((one) => one.id === id);
          return version ? optionLabel(version) : COPY.none;
        },
      }}
      error={pickerError === piece}
      helperText={pickerError === piece ? errorText : undefined}
    >
      {options[piece].map((version) => (
        <MenuItem key={version.id} value={version.id}>
          {optionLabel(version)}
        </MenuItem>
      ))}
    </TextField>
  );

  const scenarioLine = () => {
    const repeats = trials > 1 ? ` ${COPY.trials(trials)}` : "";
    if (blocked) return null;
    if (!runAll) return `${COPY.selectedScenarios(runIds.length)}${repeats}`;
    if (!selected.suite) return null;
    if (!needsMembers)
      return `${COPY.allCurrentScenarios(named(suite))}${repeats}`;
    if (members.isLoading) return null;
    if (!memberKeys) return null;
    return `${COPY.allScenarios(memberKeys.length, named(suite))}${repeats}`;
  };

  const body = () => {
    if (versions.isLoading) {
      // The plan's own boxes, greyed, so nothing jumps when they load.
      return (
        <>
          <Box sx={BOX_SX}>
            <ContentSkeleton variant="panel" rows={2} label={COPY.loading} />
          </Box>
          <Box sx={BOX_SX} aria-hidden>
            <ContentSkeleton variant="panel" rows={3} role="presentation" />
          </Box>
        </>
      );
    }
    if (!data) {
      return (
        <Typography sx={{ typography: "s2", color: "text.secondary" }}>
          {isVersioningDisabled(versions.error)
            ? COPY.disabled
            : COPY.loadError}
        </Typography>
      );
    }
    const line = scenarioLine();
    return (
      <>
        {/* Versions first: Ready to run answers for whatever is picked. */}
        <Stack spacing={1.5} sx={{ ...BOX_SX, p: 1.5 }}>
          {PICKABLE.map(pickerField)}
        </Stack>
        {env?.versioning?.enabled && !blocked && (
          <ReadyToRunSection
            envId={env.id}
            picks={picks}
            keys={readinessKeys}
            trials={trials}
            onView={setReadiness}
          />
        )}
        {/* The run line and its notes read as one group, tighter than the
            sections above. */}
        <Stack spacing={1} sx={{ "&:empty": { display: "none" } }}>
          {rerun === "fellBack" && (
            <Alert severity="info" sx={NOTE_SX} data-testid="rerun-fallback">
              {agentFellBack ? COPY.rerunAgentFallback : COPY.rerunFallback}
            </Alert>
          )}
          {needsMembers && members.isLoading && !blocked && (
            <Box
              role="progressbar"
              aria-busy="true"
              aria-label={COPY.membersLoading}
            >
              <Skeleton variant="rounded" width="45%" height={14} />
            </Box>
          )}
          {(line || (heldBack && !allHeld)) && (
            <Stack spacing={0.5}>
              {line && (
                <Typography
                  sx={{ typography: "s2", fontWeight: "fontWeightMedium" }}
                >
                  {line}
                </Typography>
              )}
              {heldBack && !allHeld && (
                <Typography sx={{ typography: "s3", color: "text.secondary" }}>
                  {RUN_PLAN_COPY.heldBack(held, blockingLabel)}
                </Typography>
              )}
            </Stack>
          )}
          {allHeld && (
            <Alert severity="error" sx={NOTE_SX} data-testid="run-all-held">
              {RUN_PLAN_COPY.allHeld(blockingLabel)}
            </Alert>
          )}
          {drafts?.any && (
            <Alert severity="info" sx={NOTE_SX} data-testid="run-drafts-note">
              {DRAFTS_COPY.notInRun}
            </Alert>
          )}
          {blocked && (
            <Typography
              sx={{ typography: "s2", color: "error.main" }}
              data-testid="run-blocked"
            >
              {blocked}
            </Typography>
          )}
          {members.isError && !members.data && (
            <Typography sx={{ typography: "s2", color: "error.main" }}>
              {COPY.membersError}
            </Typography>
          )}
          {missing.length > 0 && !blocked && (
            <Alert severity="warning" sx={NOTE_SX}>
              {COPY.notInSuite(missing, named(suite))}
            </Alert>
          )}
          {/* Kept rendered while empty so the live region is announced. */}
          <Stack
            spacing={1}
            aria-live="polite"
            data-testid="run-plan"
            sx={{ "&:empty": { mt: 0 } }}
          >
            {plan.isLoading && (
              <Box
                role="progressbar"
                aria-busy="true"
                aria-label={COPY.checking}
              >
                <Skeleton variant="rounded" height={32} />
              </Box>
            )}
            {planFailed && (
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                {COPY.planError}
              </Typography>
            )}
            {warnings.map((warning) => (
              <Alert
                key={`${warning.code}:${warning.piece || ""}`}
                sx={NOTE_SX}
                severity="warning"
                data-testid="run-warning"
              >
                {warning.message}
              </Alert>
            ))}
          </Stack>
        </Stack>
      </>
    );
  };

  return (
    <ClosableDialog
      open={open}
      onClose={submitting ? undefined : onClose}
      fullWidth
      maxWidth="sm"
    >
      <DialogTitle sx={{ typography: "m2", fontWeight: "fontWeightSemiBold" }}>
        {RUN_PLAN_COPY.title}
        <Box
          component="span"
          sx={{
            display: "block",
            typography: "s3",
            fontWeight: "fontWeightRegular",
            color: "text.secondary",
          }}
        >
          {RUN_PLAN_COPY.subtitle(
            env?.name,
            options.environment.find((one) => one.id === selected.environment)
              ?.label,
          )}
        </Box>
      </DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ pt: 1 }}>
          {body()}
          {dialogError && (
            <Alert severity="error" sx={NOTE_SX} data-testid="run-error">
              {errorText}
            </Alert>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button color="inherit" onClick={onClose} disabled={submitting}>
          {COPY.cancel}
        </Button>
        <Button
          variant="contained"
          onClick={submit}
          disabled={!canSubmit}
          startIcon={<Iconify icon="solar:play-bold" width={16} />}
        >
          {runLabel}
        </Button>
      </DialogActions>
    </ClosableDialog>
  );
}

RunDialog.propTypes = {
  open: PropTypes.bool,
  env: PropTypes.shape({
    id: PropTypes.string,
    name: PropTypes.string,
    versioning: PropTypes.shape({ enabled: PropTypes.bool }),
  }),
  // Undefined runs every scenario.
  scenarioIds: PropTypes.arrayOf(PropTypes.string),
  trials: PropTypes.number,
  // {agent, environment, suite} → version id, pre-selected (a re-run's versions).
  initialPicks: PropTypes.objectOf(PropTypes.string),
  onClose: PropTypes.func,
  // (picks, scenarioIds, resolved {agent, environment, suite} version ids).
  onSubmit: PropTypes.func,
  submitting: PropTypes.bool,
  error: PropTypes.object,
};
