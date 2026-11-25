import PropTypes from "prop-types";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  Box,
  Button,
  MenuItem,
  Skeleton,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import ContentSkeleton from "src/components/content-skeleton";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import {
  VERSION_PIECES,
  isVersioningDisabled,
  useDrafts,
  useEnvironmentVersions,
  useRunPlan,
  useSuiteMembers,
} from "src/api/simulate-environments/versions";
import {
  useReadiness,
  useRereadWhenDefaultsMove,
} from "src/api/simulate-environments/readiness";
import {
  fixIsActive,
  useFixRun,
} from "src/api/simulate-environments/readinessFix";
import { DRAFTS_COPY, RUN_DIALOG_COPY as COPY } from "../workspace.constants";
import { inlineRunError, planRefusal, seedRefusal } from "./runErrors";
import RunBlockedLine from "./RunBlockedLine";
import RunPlanDrawer from "./RunPlanDrawer";
import RunPlanNote from "./RunPlanNote";
import RunSeedLine from "./RunSeedLine";
import { named } from "../agents/agentVersionStatus";
import HeldBackBox from "./HeldBackBox";
import ChangedSinceProvedBox from "./ChangedSinceProvedBox";
import ReadyToRunSection from "./readiness/ReadyToRunSection";
import SwitchPrompt from "./readiness/SwitchPrompt";
import {
  READY_TO_RUN_COPY,
  RUN_PLAN_COPY,
} from "./readiness/readiness.constants";
import { readinessView } from "./readiness/readinessView";

// The run plan's warning that saved changes moved what some scenarios are graded on since they were proved.
const CHANGED_SINCE_PROVED = "scenarios_need_revalidation";

// Only versions a run can take are offered: an active environment or scenario
// version, an agent version the server marks pickable (its check passed), and
// always the default.
const PICKABLE = VERSION_PIECES;
const offered = (piece, version) =>
  version.isDefault ||
  (piece === "agent" ? version.pickable : version.status === "active");

// The one red line, kept for what stops the run.
const STOP_SX = { typography: "s3", color: "error.main" };

const optionLabel = (version) =>
  version.isDefault ? `${named(version)} (${COPY.default})` : named(version);

/**
 * Pick the agent, environment and scenario versions a run uses, see what to
 * watch out for, and start it. Only the versions the user changed are sent;
 * the rest run the environment's defaults at submit time. While the server
 * offers Fix, what isn't ready can be sent to the builder chat from here.
 */
export default function RunDialog({
  open,
  env,
  scenarioIds,
  trials = 1,
  initialPicks,
  initialSeed = null,
  onClose,
  onOpenAgentVersions,
  onGoToChat,
  onReviewChanges,
  onSubmit,
  submitting = false,
  error = null,
  collapsed = false,
  onCollapsedChange,
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
  // Empty keeps the environment's seed; sent as typed so the server validates it.
  const [seed, setSeed] = useState(() => initialSeed ?? "");
  const seedValue = seed.trim() || null;
  // Per piece, the fix's saved version whose switch prompt was turned down.
  const [kept, setKept] = useState({});

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

  const suite = options.suite.find((one) => one.id === selected.suite);
  const runAll = scenarioIds === undefined;
  // Run-all names no scenarios: the server runs the picked suite whole. With versioning on that is a saved version,
  // whose scenarios say how many run.
  const needsMembers =
    runAll &&
    Boolean(picks.suite || (env?.versioning?.enabled && selected.suite));
  const membersWanted = Boolean(selected.suite) && (!runAll || needsMembers);
  const members = useSuiteMembers(env?.id, selected.suite, {
    enabled: open && membersWanted,
  });
  const memberKeys = members.data?.map((member) => member.scenario_key);
  const missing =
    !runAll && memberKeys
      ? scenarioIds.filter((key) => !memberKeys.includes(key))
      : [];
  // Selected scenarios the picked suite lacks are skipped.
  const runIds = runAll
    ? undefined
    : scenarioIds.filter((key) => !missing.includes(key));

  const plan = useRunPlan(env?.id, picks, {
    enabled: open && !versions.isLoading,
    scenarioKeys: runIds ?? null,
  });
  const allWarnings = plan.data?.warnings || [];
  const changed = allWarnings.filter(
    (one) => one.code === CHANGED_SINCE_PROVED,
  );
  const warnings = allWarnings.filter(
    (one) => one.code !== CHANGED_SINCE_PROVED,
  );
  const versioned = Boolean(env?.versioning?.enabled);
  // What readiness holds back for these picks and scenarios, and how many run.
  const selectionKeys = runIds ?? null;
  // A selection is asked about only once settled: the suite's scenarios loaded and at least one picked, else the
  // server refuses the keys.
  const readiness = useReadiness(env?.id, picks, {
    keys: selectionKeys,
    trials,
    enabled:
      open &&
      versioned &&
      Boolean(data) &&
      (runAll || (!(membersWanted && !memberKeys) && selectionKeys.length > 0)),
  });
  const selection = readiness.data?.selection;
  const readyView = useMemo(
    () => (readiness.data ? readinessView(readiness.data) : null),
    [readiness.data],
  );
  useRereadWhenDefaultsMove(env?.id, data?.defaults);
  const fixOffered = Boolean(readyView?.groups);
  const fixRun =
    useFixRun(env?.id, { enabled: open && fixOffered }).data ?? null;
  const fixActive = fixIsActive(fixRun);
  // From a Re-check click until the server says it is checking, Run waits too.
  const [sendingCheck, setSendingCheck] = useState(false);
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

  let blocked = null;
  if (needsMembers && memberKeys?.length === 0) {
    blocked = COPY.emptySuite(named(suite));
  } else if (!runAll && scenarioIds.length > 0 && runIds.length === 0) {
    blocked = COPY.noneInSuite(named(suite));
  }
  // Tools that reach production are blocked in this controlled test.
  const blockedTools = plan.data ? plan.data.blocked_tools : [];
  if (!blocked && plan.data && !plan.data.runnable)
    blocked = COPY.allBlocked(blockedTools.map((entry) => entry.tool));
  // With `next` set, a run of unproved cases starts their check or runs them unvalidated, so it isn't held.
  if (
    !blocked &&
    selection?.held_back?.count > 0 &&
    selection.running === 0 &&
    !selection.next
  )
    blocked = COPY.allHeld;

  // A check of the pair started meanwhile: once Ready to run reads it, it says so, and once the check ends its
  // refusal no longer applies.
  const pairChecking = readyView?.state === "checking";
  const wasChecking = useRef(false);
  useEffect(() => {
    if (
      wasChecking.current &&
      !pairChecking &&
      error?.error === "pair_validating"
    )
      setDismissedError(error);
    wasChecking.current = pairChecking;
  }, [pairChecking, error]);
  const submitError =
    error &&
    error !== dismissedError &&
    !(pairChecking && error.error === "pair_validating")
      ? inlineRunError(error)
      : null;
  const seedError = submitError ? seedRefusal(submitError) : null;
  // A re-run's refused versions fall back to the defaults instead.
  const shownError =
    (seedError ? null : submitError) ||
    (rerun === "checking" ? null : refusedPlan);
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

  const canSubmit =
    !submitting &&
    !versions.isLoading &&
    // A failed plan leaves blocking to the run's own refusal.
    (Boolean(plan.data) || planFailed) &&
    !refusedPlan &&
    !blocked &&
    !seedError &&
    // The server refuses a run while the pair is checked or after its check failed; the section says which.
    !readyView?.holdsRun &&
    !sendingCheck &&
    !(!runAll && members.isLoading) &&
    (!needsMembers || memberKeys?.length > 0);

  // With readiness, the button says how many run; held ones are never sent, so nothing reads "anyway".
  let runLabel = warnings.length ? COPY.runAnyway : COPY.run;
  // Nothing to run keeps the plain label; the blocked line says why.
  if (selection?.running > 0) runLabel = COPY.runCount(selection.running);
  if (submitting) runLabel = COPY.starting;

  const pick = (piece, id) => {
    // A pick on the default follows it when the default moves.
    setOverrides(({ [piece]: _, ...rest }) =>
      id === defaultId(piece) ? rest : { ...rest, [piece]: id },
    );
    setDismissedError(error);
    setRerun("done");
  };

  // The resolved versions ride along so a resubmit after the defaults moved is
  // a new request.
  const submit = () => {
    if (!canSubmit) return;
    onSubmit?.(picks, runIds, selected, seedValue);
  };

  // Older picks of the pieces a fix saved new versions of.
  const switchTargets = !fixOffered
    ? []
    : ["environment", "suite"].flatMap((piece) => {
        const savedId = fixRun?.saved?.[piece]?.id;
        const picked = options[piece].find((one) => one.id === picks[piece]);
        const saved = options[piece].find((one) => one.id === savedId);
        const older =
          picked &&
          saved &&
          new Date(picked.createdAt) < new Date(saved.createdAt);
        return older && kept[piece] !== saved.id ? [{ piece, saved }] : [];
      });
  // A draft the fix itself left is the fix; a later edit is someone else's.
  const draftsFromFix =
    fixRun?.state === "done" &&
    fixRun.draft &&
    drafts?.pieces?.every(
      (piece) =>
        new Date(drafts[piece].updatedAt || 0) <= new Date(fixRun.finished_at),
    );
  const draftsNote = () => {
    if (fixOffered && drafts.stale) return RUN_PLAN_COPY.draftStale;
    if (fixOffered && draftsFromFix) return RUN_PLAN_COPY.draftFix;
    return DRAFTS_COPY.notInRun;
  };

  const pickerField = (piece) => (
    <Stack key={piece} direction="row" spacing={1.5} alignItems="flex-start">
      <Typography
        component="label"
        id={`run-pick-${piece}-label`}
        htmlFor={`run-pick-${piece}`}
        sx={{
          typography: "s3",
          color: "text.secondary",
          width: 96,
          flexShrink: 0,
          pt: 0.75,
        }}
      >
        {RUN_PLAN_COPY.pickers[piece]}
      </Typography>
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
        sx={{ "& .MuiInputBase-root": { typography: "s2" } }}
      >
        {options[piece].map((version) => (
          <MenuItem key={version.id} value={version.id}>
            {optionLabel(version)}
          </MenuItem>
        ))}
      </TextField>
    </Stack>
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

  // The held-back box names every reason, the blocked tools' among them.
  const blockedToolLines = plan.data && !selection?.held_back?.count && (
    <RunBlockedLine
      blockedTools={blockedTools}
      heldBack={plan.data.held_back}
    />
  );

  const body = (close) => {
    if (versions.isLoading) {
      return <ContentSkeleton variant="panel" rows={2} label={COPY.loading} />;
    }
    if (!data) {
      return (
        <>
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>
            {isVersioningDisabled(versions.error)
              ? COPY.disabled
              : COPY.loadError}
          </Typography>
          {blockedToolLines}
        </>
      );
    }
    const line = scenarioLine();
    return (
      <>
        <Stack spacing={1}>{PICKABLE.map(pickerField)}</Stack>
        {switchTargets.map(({ piece, saved }) => (
          <SwitchPrompt
            key={piece}
            envId={env.id}
            piece={piece}
            picks={picks}
            saved={saved}
            keys={selectionKeys}
            trials={trials}
            onSwitch={() => pick(piece, saved.id)}
            onKeep={() => setKept((all) => ({ ...all, [piece]: saved.id }))}
          />
        ))}
        {rerun === "fellBack" && (
          <RunPlanNote data-testid="rerun-fallback">
            {agentFellBack ? COPY.rerunAgentFallback : COPY.rerunFallback}
          </RunPlanNote>
        )}
        {line && (
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>
            {line}
          </Typography>
        )}
        {selection && !blocked && Number.isInteger(selection.running) && (
          <Typography
            data-testid="run-will-run"
            sx={{ typography: "s1", fontWeight: "fontWeightMedium" }}
          >
            {COPY.willRun(selection.running, selection.requested)}
          </Typography>
        )}
        {versioned && (
          <ReadyToRunSection
            envId={env?.id}
            picks={picks}
            keys={selectionKeys}
            trials={trials}
            readiness={readiness}
            view={readyView}
            onSendingChange={setSendingCheck}
            onOpenAgentVersions={
              onOpenAgentVersions &&
              (() => {
                onOpenAgentVersions();
                close();
              })
            }
            onGoToChat={onGoToChat}
          />
        )}
        <HeldBackBox
          shownRows={readyView?.open.map((line) => line.rowId)}
          selection={selection}
        />
        <ChangedSinceProvedBox warnings={changed} />
        <RunSeedLine
          seed={seed}
          value={seedValue}
          error={seedError}
          onChange={(next) => {
            setSeed(next);
            setDismissedError(error);
          }}
        />
        {drafts?.any && !(fixOffered && fixActive) && (
          <RunPlanNote
            data-testid="run-drafts-note"
            action={
              fixOffered && onReviewChanges ? (
                <Button size="small" onClick={onReviewChanges}>
                  {RUN_PLAN_COPY.reviewChanges}
                </Button>
              ) : null
            }
          >
            {draftsNote()}
          </RunPlanNote>
        )}
        {blockedToolLines}
        {blocked && (
          <Typography sx={STOP_SX} data-testid="run-blocked">
            {blocked}
          </Typography>
        )}
        {members.isError && !members.data && (
          <Typography sx={STOP_SX}>{COPY.membersError}</Typography>
        )}
        {missing.length > 0 && !blocked && (
          <RunPlanNote severity="warning">
            {COPY.notInSuite(missing, named(suite))}
          </RunPlanNote>
        )}
        {/* Kept rendered while empty so the live region is announced. */}
        <Stack
          spacing={1}
          aria-live="polite"
          data-testid="run-plan"
          sx={{ "&:empty": { mt: 0 } }}
        >
          {plan.isLoading && (
            <Box role="progressbar" aria-busy="true" aria-label={COPY.checking}>
              <Skeleton variant="rounded" height={32} />
            </Box>
          )}
          {planFailed && (
            <RunPlanNote
              severity="warning"
              data-testid="run-plan-failed"
              action={
                <Button
                  size="small"
                  color="inherit"
                  onClick={() => plan.refetch()}
                >
                  {COPY.retryPlan}
                </Button>
              }
            >
              {COPY.planError}
            </RunPlanNote>
          )}
          {warnings.map((warning) => (
            <RunPlanNote
              key={`${warning.code}:${warning.piece || ""}`}
              severity="warning"
              data-testid="run-warning"
            >
              {warning.message}
            </RunPlanNote>
          ))}
        </Stack>
      </>
    );
  };

  const environment = options.environment.find(
    (one) => one.id === selected.environment,
  );

  return (
    <RunPlanDrawer
      open={open}
      subtitle={RUN_PLAN_COPY.subtitle(
        env?.name,
        environment && named(environment),
      )}
      busy={fixActive}
      locked={submitting}
      collapsed={collapsed}
      onCollapsedChange={onCollapsedChange}
      onClose={onClose}
      footer={(close) => (
        <>
          <Button color="inherit" onClick={close} disabled={submitting}>
            {COPY.cancel}
          </Button>
          <CustomTooltip
            show={pairChecking}
            size="small"
            arrow
            title={READY_TO_RUN_COPY.states.checking}
          >
            <span>
              <Button
                variant="contained"
                onClick={submit}
                disabled={!canSubmit}
                startIcon={<Iconify icon="solar:play-bold" width={16} />}
              >
                {runLabel}
              </Button>
            </span>
          </CustomTooltip>
        </>
      )}
    >
      {(close) => (
        <>
          {body(close)}
          {dialogError && (
            <Typography role="alert" sx={STOP_SX} data-testid="run-error">
              {errorText}
            </Typography>
          )}
        </>
      )}
    </RunPlanDrawer>
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
  initialSeed: PropTypes.string,
  onClose: PropTypes.func,
  // Opens the agent's versions for a fix made on the agent; absent where that can't open.
  onOpenAgentVersions: PropTypes.func,
  // Only where the page has no builder chat: takes the user to it.
  onGoToChat: PropTypes.func,
  // Opens the review of the unsaved changes; absent where that can't open.
  onReviewChanges: PropTypes.func,
  // (picks, scenarioIds, resolved version ids, seed or null)
  onSubmit: PropTypes.func,
  submitting: PropTypes.bool,
  error: PropTypes.object,
  // Only the edge tab shows; the body stays mounted.
  collapsed: PropTypes.bool,
  onCollapsedChange: PropTypes.func,
};
