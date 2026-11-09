import React, { Suspense, lazy } from "react";
import { Helmet } from "react-helmet-async";
import { Navigate } from "react-router";
import { useExperimentSurface } from "src/sections/experiment-detail-classic/experimentsBasic";

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

const ExperimentWrapper = () => {
  const surface = useExperimentSurface();
  if (surface === "hidden") {
    return <Navigate to="/dashboard/develop" replace />;
  }
  return (
    <>
      <Helmet>
        <title>Experiment</title>
      </Helmet>
      <Suspense fallback={null}>
        {surface === "classic" ? (
          <ClassicExperimentWrapperView />
        ) : (
          <DsxExperimentWrapperView />
        )}
      </Suspense>
    </>
  );
};

export default ExperimentWrapper;
