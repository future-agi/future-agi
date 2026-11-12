import React, { Suspense, lazy, useState } from "react";
import { Helmet } from "react-helmet-async";
import { Navigate, useParams } from "react-router";
import { Box, Skeleton, Stack } from "@mui/material";
import { useExperimentSurface } from "src/sections/experiment-detail-classic/experimentsBasic";
import { useExperimentDetail } from "src/api/develop/runs";
import {
  experimentReadPolicy,
  isExperimentReadUnavailable,
} from "src/api/develop/experiment-read-policy";
import { statusOfError } from "src/sections/develop-detail/DataTab/DatasetNotFound";
import ExperimentNotFound from "src/sections/experiment-detail/ExperimentNotFound";
import { experimentMissing } from "src/sections/experiment-detail/experimentNotFoundModel";
import { isPrototypeMode } from "src/utils/prototype";

// L3 (the capability contract): prototype builds keep the DSX experiment page; a
// non-prototype build shows dev's own Data page when experimentsBasic is on
// and no experiment page at all when it is off. Both views load lazily, so a
// non-prototype build never loads the DSX results modules.
const DsxExperimentWrapperView = lazy(
  () => import("src/sections/experiment-detail/ExperimentWrapperView"),
);
const ClassicExperimentWrapperView = lazy(
  () => import("src/sections/experiment-detail-classic/ExperimentWrapperView"),
);

// The detail's own decision (opens_in_full_results, the same backend rule
// the experiments list marks its rows with): false when the new page could
// not show every variant (created before runs were recorded, or a variant
// with no counted run). Such an experiment opens on the original Data and
// Summary page, which reads its rows and stats. Read from the raw detail, so
// it works before the generated contracts list the field.
/** Which experiment page a loaded detail opens: "classic" or "dsx". */
const experimentPageOf = (detail) =>
  detail?.opens_in_full_results === false ? "classic" : "dsx";

const ExperimentPageSkeleton = () => (
  <Stack
    data-testid="experiment-page-loading"
    aria-busy="true"
    spacing={1.5}
    sx={{ flex: 1, p: 2, bgcolor: "background.paper" }}
  >
    <Stack direction="row" alignItems="center" gap={1.5}>
      <Skeleton variant="rounded" width={28} height={28} />
      <Skeleton width={240} height={28} />
    </Stack>
    <Stack direction="row" gap={2}>
      <Skeleton width={72} height={32} />
      <Skeleton width={96} height={32} />
    </Stack>
    <Box sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1 }}>
      <Skeleton variant="rectangular" height={40} />
      {[0, 1, 2, 3, 4, 5].map((i) => (
        <Skeleton
          key={i}
          variant="rectangular"
          height={44}
          sx={{ mt: "1px" }}
        />
      ))}
    </Box>
  </Stack>
);

// A build that shows the new experiment page to customers opens each
// experiment where its results can be read: the detail decides, read once
// under the key both pages share (["experiment", id]).
const ExperimentPageByRecord = () => {
  const { experimentId } = useParams();
  const detail = useExperimentDetail(experimentId, {
    ...experimentReadPolicy,
    meta: { errorHandled: true },
  });
  // A final answer decides, once per experiment: the detail, a missing
  // experiment, or a refused read. Any other failure (a server error, a
  // timeout, no network) opens the new page for now, which reports it and
  // reads the detail again when it mounts; a later detail then decides. The
  // page stays mounted while the detail is read again: it never goes back to
  // the skeleton.
  const [decision, setDecision] = useState({
    id: null,
    page: null,
    final: false,
  });
  let answer = null;
  let final = false;
  if (detail.isSuccess) {
    answer = experimentPageOf(detail.data);
    final = true;
  } else if (detail.isError) {
    const missing = experimentMissing(detail, statusOfError);
    answer = missing ? "missing" : "dsx";
    final = missing || isExperimentReadUnavailable(detail.error);
  }
  const current = decision.id === experimentId ? decision : null;
  const changes =
    answer !== null &&
    (current === null ||
      (!current.final && (final || current.page !== answer)));
  if (changes) {
    setDecision({ id: experimentId, page: answer, final });
  }
  const page = changes ? answer : current?.page ?? answer;

  if (page === null) return <ExperimentPageSkeleton />;
  if (page === "missing") {
    return <ExperimentNotFound experimentId={experimentId} />;
  }
  // Any other failure keeps the new page, which reports it as before.
  return (
    <Suspense fallback={<ExperimentPageSkeleton />}>
      {page === "classic" ? (
        <ClassicExperimentWrapperView />
      ) : (
        <DsxExperimentWrapperView />
      )}
    </Suspense>
  );
};

const ExperimentWrapper = () => {
  const surface = useExperimentSurface();
  if (surface === "hidden") {
    return <Navigate to="/dashboard/develop" replace />;
  }
  // Prototype builds read the mock backend, which records no experiment
  // before runs and serves no results marker: they keep the new page.
  const byRecord = surface === "dsx" && !isPrototypeMode();
  return (
    <>
      <Helmet>
        <title>Experiment</title>
      </Helmet>
      {byRecord ? (
        <ExperimentPageByRecord />
      ) : (
        <Suspense fallback={null}>
          {surface === "classic" ? (
            <ClassicExperimentWrapperView />
          ) : (
            <DsxExperimentWrapperView />
          )}
        </Suspense>
      )}
    </>
  );
};

export default ExperimentWrapper;
