import { useEffect, useReducer, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { Box } from "@mui/material";

import { paths } from "src/routes/paths";
import { usePreflight } from "src/api/simulate-environments/preflight";
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
import ReadAudit from "./read-audit/ReadAudit";
import BuildingStage from "./building/BuildingStage";
import DerivingAnimation from "./building/DerivingAnimation";

const BUILD_TAB = `${paths.dashboard.simulate.environments.root}?tab=build`;

/*
  The build orchestrator. Reads the draft the Build tab handed off, runs the
  real preflight, then walks the source through the read-audit → derivation
  chain. Ported from the designer's BuildFromAgent root (502–592): a flex-column
  with a fixed header over a single scrolling body that swaps by build stage. The
  stage machine lives in the store (preflight | building); this component owns
  none of it — it only reads the slice and fans the pieces out to the header and
  the body. Home resets the slice on mount, so the build page never resets on
  unmount (decision 5).
*/
export default function BuildEnvironment() {
  const navigate = useNavigate();
  const {
    draft, buildStage, envId, buildProgress, retriedSections,
    startPreflight, acceptAudit, adoptEnvironment,
  } = useEnvironmentsStoreShallow((s) => ({
    draft: s.draft,
    buildStage: s.buildStage,
    envId: s.envId,
    buildProgress: s.buildProgress,
    retriedSections: s.retriedSections,
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

  // A stale or hand-typed /build URL has no draft — bounce back to the matrix.
  useEffect(() => {
    if (!draft) navigate(BUILD_TAB, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Fire a fresh preflight once on mount; the slice is never reset on unmount.
  useEffect(() => {
    if (draft) startPreflight();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const { audit, refetch } = usePreflight(draft, { retriedSections });
  const build = useBuildEnvironment();
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
  // sees the building slice) from re-seeding over the reader's edits.
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
      { ...envFromDraft(draft, name, audit), id: envId, buildStatus: "ready" },
      now,
    );
    patch(seedAgentBuilt(draft, audit, now));
  }, [buildDone, envId, env, draft, name, audit, adoptEnvironment, patch]);

  const onRun = () => navigate(runSimulationTarget(env));

  const onBack = () => navigate(BUILD_TAB);

  // Mint the env id only when the audit is accepted; never before.
  const onBuild = (answers) =>
    build.mutate(undefined, {
      onSuccess: ({ envId: minted }) =>
        acceptAudit({ envId: minted, answers }),
    });

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
        {buildStage === BUILD_STAGE.PREFLIGHT &&
          (!audit ? (
            <DerivingAnimation label={DERIVING_LABEL.idle} />
          ) : (
            <ReadAudit
              audit={audit}
              onBuild={onBuild}
              onBack={onBack}
              onRetryRead={refetch}
            />
          ))}

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
