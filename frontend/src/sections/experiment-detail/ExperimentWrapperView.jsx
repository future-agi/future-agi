import { Box, Stack } from "@mui/material";
import React from "react";
import PropTypes from "prop-types";
import ExperimentRow from "./ExperimentRow";
import ExperimentBar from "./ExperimentBar";
import { Outlet, useParams } from "react-router";
import { useSearchParams } from "react-router-dom";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { readR2Params } from "src/sections/common/Analysis/analysisLinks";
import ExperimentDetailContextProvider from "./ExperimentContextProvider";
import { useDatasetRuns, useExperimentDetail } from "src/api/develop/runs";
import { experimentDefaultBaseline } from "./results/useResultsUrlState";
import { useRunConfigs } from "src/api/develop/r2";
import { useCandidateRunId } from "src/api/develop/analysis";
import ConfigDiffStrip from "src/sections/common/Runs/ConfigDiffStrip";
import { stripCandidate } from "src/sections/common/Runs/configDiff";
import { pageCandidate as pickCandidate } from "./results/pageCandidate";
import IterateDock from "./iterate/IterateDock";
import RunActionConfirmHost from "src/sections/develop-detail/Experiment/RunActionConfirmHost";
import IterateResultBanner from "./iterate/IterateResultBanner";
import LineageRibbon from "./iterate/LineageRibbon";
import ExternalRunWaitingBanner from "src/sections/develop-detail/Experiment/ExternalRunWaitingBanner"; // R3-G0 frozen line
import { statusOfError } from "src/sections/develop-detail/DataTab/DatasetNotFound";
import ExperimentNotFound from "./ExperimentNotFound";
import { experimentMissing } from "./experimentNotFoundModel";
import { DatasetDeniedPage } from "src/sections/common/Access/DatasetAccessGate";
import { viewDenialOf } from "src/sections/common/Access/accessModel";

// The experiment's runs for the R2 mounts (the detail read is shared with
// ExperimentRow, so this adds no request).
function useExperimentRuns() {
  const { experimentId } = useParams();
  const [searchParams] = useSearchParams();
  // RE-01: a 404 is an answer (no retries); other errors retry as before.
  const detail = useExperimentDetail(experimentId, {
    // W4 §9 (M6): a 403 is an answer too (the dataset is refused).
    retry: (count, error) =>
      ![403, 404].includes(statusOfError(error)) && count < 3,
    meta: { errorHandled: true },
  });
  const experiment = detail.data;
  const runIds = (experiment?.runs ?? []).map((r) => r.run_id);
  const { data: datasetRuns } = useDatasetRuns(experiment?.dataset_id);
  return {
    experimentId,
    denial: viewDenialOf(detail.error),
    missing: experimentMissing(detail, statusOfError),
    datasetId: experiment?.dataset_id,
    runIds,
    baselineId: experimentDefaultBaseline(
      runIds,
      searchParams.get("baseline"),
      datasetRuns?.pinned_run_id,
    ),
  };
}

const ExperimentWrapperView = () => {
  const { experimentId, datasetId, runIds, baselineId, missing, denial } =
    useExperimentRuns();
  // W4 §9 (M6): the experiment's dataset is refused ("No access"): the
  // same page as the dataset's, with Request view access.
  if (denial) return <DatasetDeniedPage denial={denial} />;
  // RE-01: an unknown or deleted experiment is terminal (no header spinner,
  // no tabs asking for runs that do not exist).
  if (missing) return <ExperimentNotFound experimentId={experimentId} />;
  return (
    <ExperimentWrapperBody
      experimentId={experimentId}
      datasetId={datasetId}
      runIds={runIds}
      baselineId={baselineId}
    />
  );
};

const ExperimentWrapperBody = ({
  experimentId,
  datasetId,
  runIds,
  baselineId,
}) => {
  // R4 lane X: also one-run experiments (the bar shows what the run used).
  const { data: runConfigs } = useRunConfigs(datasetId, {
    enabled: runIds.length > 0,
  });
  const ids = { datasetId, experimentId, runIds, baselineId };
  const [searchParams, setSearchParams] = useSearchParams();
  const cand = readR2Params(searchParams).cand;
  // R3: with no `cand=`, the page's candidate is the Summary's (the best
  // verdict). The Summary sends `baseline=` only when the URL has a valid
  // one, so this read shares its cache entry.
  const requested = searchParams.get("baseline");
  const pageCandidate = useCandidateRunId(datasetId, {
    runIds,
    baselineId,
    analysisBaselineId:
      requested === "none" || runIds.includes(requested)
        ? requested
        : undefined,
    cand,
  });
  // The strip pairs the baseline with `cand=`, else the drawer's focused
  // run (`run=`), else the page's candidate.
  // 8a: the same one-candidate rule as Compare (cand= › latest version ›
  // best verdict; a text-only voice run never by default).
  const theCandidate = pickCandidate({
    runs: runConfigs?.results ?? [],
    runIds,
    baselineId,
    cand,
    fallback: pageCandidate,
  }).id;
  const candidateId = stripCandidate({
    cand,
    focused: searchParams.get("run") ?? theCandidate ?? pageCandidate,
    baselineId,
    runIds,
  });
  const { role } = useAuthContext();
  const readOnly = !RolePermission.DATASETS[PERMISSIONS.UPDATE][role];
  return (
    <ExperimentDetailContextProvider>
      <Box
        sx={{
          backgroundColor: "background.paper",
          flex: 1,
          display: "flex",
          flexDirection: "column",
          gap: 1,
          // The tabs fill the window (grids scroll inside), as on Compare.
          minHeight: 0,
        }}
      >
        <ExperimentRow />
        <Stack
          direction="row"
          alignItems="center"
          data-testid="what-changed-line"
          sx={{ minWidth: 0, flexWrap: "wrap", columnGap: 1, rowGap: 0.5 }}
        >
          <ConfigDiffStrip
            baseline={baselineId}
            candidate={candidateId}
            runs={runConfigs?.results}
            candidates={runIds}
            onCandidate={(id) => {
              const next = new URLSearchParams(window.location.search);
              next.set("cand", id);
              setSearchParams(next, { replace: true });
            }}
            onBaseline={(id) => {
              const next = new URLSearchParams(window.location.search);
              next.set("baseline", id);
              next.delete("cand");
              setSearchParams(next, { replace: true });
            }}
            readOnly={readOnly}
          />
          <LineageRibbon compact {...ids} />
        </Stack>
        <ExperimentBar
          focusRun={
            (runConfigs?.results ?? []).find(
              (r) =>
                r.run_id ===
                // The drawer's run (`run=`, baseline included), else the
                // page's candidate, else the only run.
                (runIds.includes(searchParams.get("run"))
                  ? searchParams.get("run")
                  : candidateId ?? runIds[0]),
            ) ?? null
          }
        />
        <IterateResultBanner {...ids} />
        <ExternalRunWaitingBanner
          experimentId={experimentId}
          runs={runIds} // R3-G0 frozen line
        />
        <Outlet />
        <IterateDock {...ids} />
        {/* R3 MUST 9: the RunBar chip menu's Stop / Re-run confirm here. */}
        <RunActionConfirmHost />
      </Box>
    </ExperimentDetailContextProvider>
  );
};

ExperimentWrapperBody.propTypes = {
  experimentId: PropTypes.string,
  datasetId: PropTypes.string,
  runIds: PropTypes.arrayOf(PropTypes.string).isRequired,
  baselineId: PropTypes.string,
};

export default ExperimentWrapperView;
