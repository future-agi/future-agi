import PropTypes from "prop-types";
import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
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

// Only versions a run can take are offered: an active environment or scenario
// version, an agent version the server marks pickable (its check passed), and
// always the default.
const PICKABLE = VERSION_PIECES;
const offered = (piece, version) =>
  version.isDefault ||
  (piece === "agent" ? version.pickable : version.status === "active");

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
    ? shownError.message || shownError.detail || COPY.refused
    : null;

  const canSubmit =
    !submitting &&
    !versions.isLoading &&
    !plan.isLoading &&
    !refusedPlan &&
    !blocked &&
    !(!runAll && members.isLoading) &&
    (!needsMembers || memberKeys?.length > 0);

  let runLabel = warnings.length ? COPY.runAnyway : COPY.run;
  if (submitting) runLabel = COPY.starting;

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
    <TextField
      key={piece}
      select
      fullWidth
      size="small"
      label={COPY.pieces[piece]}
      value={selected[piece] || ""}
      onChange={(e) => pick(piece, e.target.value)}
      disabled={!options[piece].length || submitting}
      SelectProps={{
        displayEmpty: true,
        renderValue: (id) => {
          const version = options[piece].find((one) => one.id === id);
          return version ? optionLabel(version) : COPY.none;
        },
      }}
      InputLabelProps={{ shrink: true }}
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
    if (members.isLoading) return COPY.membersLoading;
    if (!memberKeys) return null;
    return `${COPY.allScenarios(memberKeys.length, named(suite))}${repeats}`;
  };

  const body = () => {
    if (versions.isLoading) {
      return (
        <Stack direction="row" spacing={1} alignItems="center">
          <CircularProgress size={16} />
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>
            {COPY.loading}
          </Typography>
        </Stack>
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
        {PICKABLE.map(pickerField)}
        {rerun === "fellBack" && (
          <Alert severity="info" data-testid="rerun-fallback">
            {agentFellBack ? COPY.rerunAgentFallback : COPY.rerunFallback}
          </Alert>
        )}
        {line && (
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>
            {line}
          </Typography>
        )}
        {drafts?.any && (
          <Alert severity="info" data-testid="run-drafts-note">
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
          <Alert severity="warning">
            {COPY.notInSuite(missing, named(suite))}
          </Alert>
        )}
        {/* Kept rendered while empty so the live region is announced. */}
        <Stack
          spacing={2}
          aria-live="polite"
          data-testid="run-plan"
          sx={{ "&:empty": { mt: 0 } }}
        >
          {plan.isLoading && (
            <Stack direction="row" spacing={1} alignItems="center">
              <CircularProgress size={14} />
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                {COPY.checking}
              </Typography>
            </Stack>
          )}
          {planFailed && (
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              {COPY.planError}
            </Typography>
          )}
          {warnings.map((warning) => (
            <Alert
              key={`${warning.code}:${warning.piece || ""}`}
              severity="warning"
              data-testid="run-warning"
            >
              {warning.message}
            </Alert>
          ))}
        </Stack>
      </>
    );
  };

  return (
    <Dialog
      open={open}
      onClose={submitting ? undefined : onClose}
      fullWidth
      maxWidth="xs"
    >
      <DialogTitle sx={{ typography: "m2", fontWeight: "fontWeightSemiBold" }}>
        {COPY.title}
      </DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ pt: 1 }}>
          {body()}
          {dialogError && (
            <Alert severity="error" data-testid="run-error">
              {errorText}
            </Alert>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button color="inherit" onClick={onClose} disabled={submitting}>
          {COPY.cancel}
        </Button>
        <Button variant="contained" onClick={submit} disabled={!canSubmit}>
          {runLabel}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

RunDialog.propTypes = {
  open: PropTypes.bool,
  env: PropTypes.shape({
    id: PropTypes.string,
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
