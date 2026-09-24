import { useEffect, useReducer, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { enqueueSnackbar } from "notistack";
import { useQueryClient } from "@tanstack/react-query";
import { Box } from "@mui/material";

import { paths } from "src/routes/paths";
import {
  usePreflight,
  preflightQueryKey,
} from "src/api/simulate-environments/preflight";
import { useBuildProgress } from "src/api/simulate-environments/buildProgress";
import {
  useBuildEnvironment,
  useRunSimulation,
} from "src/api/simulate-environments/environments";
import { environmentNameFor } from "src/api/simulate-environments/preflightPayload";

import { useEnvironmentsStoreShallow } from "../store/useEnvironmentsStore";
import { RUN_SIMULATION_COPY } from "../environmentOptions";
import { agentRefLabel } from "./helpers/agentRefLabel";
import { BUILD_STAGE, BUILD_HEADER_COPY, DERIVING_LABEL } from "./build.constants";
import {
  PIPELINE_PHASE,
  STEP_STATUS,
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
  const queryClient = useQueryClient();
  const {
    draft, buildStage, envId, buildProgress, retriedSections,
    startPreflight, acceptAudit,
  } = useEnvironmentsStoreShallow((s) => ({
    draft: s.draft,
    buildStage: s.buildStage,
    envId: s.envId,
    buildProgress: s.buildProgress,
    retriedSections: s.retriedSections,
    startPreflight: s.startPreflight,
    acceptAudit: s.acceptAudit,
  }));

  // The derived name, corrected in place from the header rather than a form.
  const [name, renameEnvironment] = useReducer(
    (_prev, value) => value,
    draft,
    environmentNameFor,
  );

  // One mount effect, guarded by a ref rather than an empty dependency list, so
  // every dependency can be declared honestly: a stale or hand-typed /build URL
  // bounces back to the matrix, and a real draft starts a fresh preflight. The
  // slice is never reset on unmount, so this is the only place it is armed.
  const started = useRef(false);
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    if (!draft) {
      navigate(BUILD_TAB, { replace: true });
      return;
    }
    // "Fresh preflight on mount" has to mean a fresh READ: the query is
    // staleTime:Infinity and keyed on the payload, so the same repo and branch
    // would otherwise replay the cached result from the previous visit.
    queryClient.removeQueries({ queryKey: preflightQueryKey.prefix });
    startPreflight();
  }, [draft, navigate, startPreflight, queryClient]);

  const { audit, refetch, isFetching } = usePreflight(draft, { retriedSections });
  const build = useBuildEnvironment();
  const run = useRunSimulation();
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

  const onRun = () =>
    run.mutate(envId, {
      onSuccess: () =>
        enqueueSnackbar(RUN_SIMULATION_COPY, { variant: "info" }),
    });

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
              isRereading={isFetching}
            />
          ))}

        {buildStage === BUILD_STAGE.BUILDING && (
          <BuildingStage progress={progress} />
        )}
      </Box>
    </Box>
  );
}
