import { useEffect, useReducer, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { enqueueSnackbar } from "notistack";
import { Box } from "@mui/material";

import { paths } from "src/routes/paths";
import { errorMessage } from "src/pages/dashboard/harness/harnessShared";
import { useBuildProgress } from "src/api/simulate-environments/buildProgress";
import { useBuildEnvironment } from "src/api/simulate-environments/environments";
import { runSimulationTarget } from "src/api/simulate-environments/runs";
import { environmentNameFor } from "src/api/simulate-environments/preflightPayload";

import {
  useEnvironmentsStore,
  useEnvironmentsStoreShallow,
} from "../store/useEnvironmentsStore";
import { useEnvState } from "../store/envState";
import { envFromDraft, seedAgentBuilt } from "../workspace/helpers/seedEnvState";
import { agentRefLabel } from "./helpers/agentRefLabel";
import { BUILD_STAGE, BUILD_HEADER_COPY, DERIVING_LABEL } from "./build.constants";
import {
  PIPELINE_PHASE,
  STEP_STATUS,
  STAGE_ORDER,
  pipelineStatus,
  pipelineSummary,
} from "./buildPipeline.constants";
import BuildHeader from "./BuildHeader";
import BuildingStage from "./building/BuildingStage";
import DerivingAnimation from "./building/DerivingAnimation";

const BUILD_TAB = `${paths.dashboard.simulate.environments.root}?tab=build`;

/*
  The build orchestrator. Inline preflight already ran below the source form and
  passed, so the panel handed off a one-shot `pendingBuild` ticket and navigated
  here. This page consumes that ticket once on mount, creates the real job, then
  walks the derivation → building pane. There is no read-audit and no second
  preflight — the create call is the only network step it owns.

  Ported from the designer's BuildFromAgent root (502–592): a flex-column with a
  fixed header over a single scrolling body that swaps by build stage. The stage
  machine (preflight | building) lives in the store; this component only reads
  the slice and fans the pieces out. Home resets the slice on mount, so the build
  page never resets on unmount (decision 5).
*/
export default function BuildEnvironment() {
  const navigate = useNavigate();
  const {
    draft, buildStage, envId, buildProgress,
    consumePendingBuild, startPreflight, acceptAudit, adoptEnvironment,
  } = useEnvironmentsStoreShallow((s) => ({
    draft: s.draft,
    buildStage: s.buildStage,
    envId: s.envId,
    buildProgress: s.buildProgress,
    consumePendingBuild: s.consumePendingBuild,
    startPreflight: s.startPreflight,
    acceptAudit: s.acceptAudit,
    adoptEnvironment: s.adoptEnvironment,
  }));

  // The adopted env record, once the build hits 7/7. Selecting the single record
  // (not the whole map) keeps the build page from re-rendering on unrelated env
  // changes. Undefined until adoption, which is exactly the `primed` signal the
  // building pane keys its in-place swap off.
  const env = useEnvironmentsStore((s) =>
    s.envId ? s.workspaceEnvs[s.envId] : undefined,
  );
  const { envState, patch } = useEnvState(envId);

  // The derived name, corrected in place from the header rather than a form.
  const [name, renameEnvironment] = useReducer(
    (_prev, value) => value,
    draft,
    environmentNameFor,
  );

  const build = useBuildEnvironment();

  // Consume the one-shot ticket and fire create exactly once. A genuine handoff
  // leaves a ticket (non-persisted); a refresh / hand-typed URL / remount finds
  // none and bounces — so a reload can never mint a second job with a fresh
  // idempotency key. The ref guards React 18 StrictMode's double-invoke (which
  // preserves refs across the simulated remount) so we consume the ticket once.
  const startedRef = useRef(false);
  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;
    const ticket = consumePendingBuild();
    if (!ticket) {
      navigate(BUILD_TAB, { replace: true });
      return;
    }
    startPreflight();
    build.mutate(ticket.draft, {
      onSuccess: ({ envId: minted }) => acceptAudit({ envId: minted, answers: {} }),
      // A create failure (e.g. the sandbox is unavailable) must never silently
      // do nothing — surface it and return to the source form to retry.
      onError: (error) => {
        enqueueSnackbar(errorMessage(error), { variant: "error" });
        navigate(BUILD_TAB, { replace: true });
      },
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const agentRef = agentRefLabel(draft);
  const progress = useBuildProgress({
    envId,
    agentRef,
    enabled: buildStage === BUILD_STAGE.BUILDING,
  });

  // One source of truth for the pill and the timeline — the store's milestone set.
  const pipeline = pipelineStatus(
    buildProgress.done,
    buildProgress.running,
    PIPELINE_PHASE.SETUP,
    buildProgress.failure,
  );
  const summary = pipelineSummary(pipeline);
  const setupDone = pipeline
    .filter((step) => step.phase === PIPELINE_PHASE.SETUP)
    .every((step) => step.status === STEP_STATUS.DONE);
  const canRun = setupDone && !!name.trim();
  const runBlockedReason = !setupDone
    ? BUILD_HEADER_COPY.runBlocked
    : "Name this environment first";

  // Adopt the environment into the store the moment the derivation completes, so
  // the in-place workspace can render off the client slices and a later refresh
  // or My-Env open resolves the env by id. Ref-guarded against a same-mount
  // re-fire; the `env` guard stops a stale remount (whose first render still
  // sees the building slice) from re-seeding over the reader's edits. There is
  // no audit anymore — `worldFor(undefined)` falls back to the mock world.
  const primed = !!env;
  const buildDone = STAGE_ORDER.every((stage) => buildProgress.done.includes(stage));
  const adoptedRef = useRef(false);
  useEffect(() => {
    if (!buildDone || !envId || adoptedRef.current) return;
    adoptedRef.current = true;
    if (env) return;
    // ISO string (not Date.now()) so agent/version timestamps match the
    // template path and the `connectedAt: string` shape the cards expect.
    const now = new Date().toISOString();
    adoptEnvironment(
      { ...envFromDraft(draft, name, undefined), id: envId, buildStatus: "ready" },
      now,
    );
    patch(seedAgentBuilt(draft, undefined, now));
  }, [buildDone, envId, env, draft, name, adoptEnvironment, patch]);

  const onRun = () => navigate(runSimulationTarget(env));

  const onBack = () => navigate(BUILD_TAB);

  if (!draft) return null;

  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      <BuildHeader
        onBack={onBack}
        name={name}
        onRename={renameEnvironment}
        pipeline={pipeline}
        summary={summary}
        setupDone={setupDone}
        onRun={onRun}
        canRun={canRun}
        runBlockedReason={runBlockedReason}
      />

      <Box sx={{ flex: 1, minHeight: 0, overflow: "hidden", p: 2 }}>
        {buildStage === BUILD_STAGE.PREFLIGHT && (
          <DerivingAnimation label={DERIVING_LABEL.creating} />
        )}

        {buildStage === BUILD_STAGE.BUILDING && (
          <BuildingStage
            progress={progress}
            env={env}
            envState={envState}
            patch={patch}
            primed={primed}
          />
        )}
      </Box>
    </Box>
  );
}
