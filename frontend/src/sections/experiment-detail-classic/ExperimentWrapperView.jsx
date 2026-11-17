import { Box } from "@mui/material";
import React, { useEffect } from "react";
import { Helmet } from "react-helmet-async";
import { Navigate, useLocation, useNavigate, useParams } from "react-router";
import { AudioPlaybackProvider } from "src/components/custom-audio/context-provider/AudioPlaybackContext";
import { useGetExperimentDetails } from "src/hooks/useGetExperimentDetails";
import { statusOfError } from "src/sections/develop-detail/DataTab/DatasetNotFound";
import ExperimentNotFound from "src/sections/experiment-detail/ExperimentNotFound";
import ExperimentRow from "./ExperimentRow";
import ExperimentBar from "./ExperimentBar";
import ExperimentDetailContextProvider from "./ExperimentContextProvider";
import ExperimentDataView from "./ExperimentData/ExperimentDataView";
import { classicDataPath, classicLandingSearch } from "./classicRoutes";
import ClassicExperimentSummary from "./ClassicExperimentSummary";
import { CLASSIC_EXPERIMENT_COPY } from "src/launch/classicExperimentCopy";

// Dev's ExperimentWrapperView rendered <Outlet /> under the route's children
// (data, summary). In the launch build the children are the DSX ones
// (ExperimentIndexRedirect, RunResultsView, matrix, paths, summary, traces),
// so the classic wrapper renders the legacy Data and Summary hosts itself.
// Census §A restores the working stats summary, not the DSX analysis host.
// Other sub-paths (and the index) land on /data. No DSX child route mounts.
const ExperimentWrapperView = () => {
  const { experimentId } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const onData = location.pathname.replace(/\/+$/, "").endsWith("/data");
  const onSummary = location.pathname.replace(/\/+$/, "").endsWith("/summary");
  const detail = useGetExperimentDetails(
    onData || onSummary ? experimentId : null,
  );
  const experiment = detail.data;
  // An experiment the API answers 404 for (deleted, or another
  // organization's) is terminal: dev's grid would show placeholder ERR
  // columns and a live Rerun button. Any other failure keeps dev's page and
  // its own retry banner.
  const missing =
    Boolean(detail.isError) && statusOfError(detail.error) === 404;

  // The DSX create lands on /experiment/{id}/data with no datasetId or
  // payload. Dev's experiment selector reads both from the URL, so fill them
  // in from the experiment itself once it loads.
  const landingSearch = classicLandingSearch(location.search, experiment);
  useEffect(() => {
    if ((onData || onSummary) && landingSearch !== null) {
      navigate(
        { pathname: location.pathname, search: landingSearch },
        {
          replace: true,
        },
      );
    }
  }, [onData, onSummary, landingSearch, location.pathname, navigate]);

  if (!onData && !onSummary) {
    return (
      <Navigate
        to={{
          pathname: classicDataPath(experimentId),
          search: location.search,
        }}
        replace
      />
    );
  }

  if (missing) return <ExperimentNotFound experimentId={experimentId} />;

  return (
    <ExperimentDetailContextProvider>
      <Helmet>
        <title>
          {onSummary ? CLASSIC_EXPERIMENT_COPY.summaryTitle : "Experiment Data"}
        </title>
      </Helmet>
      <Box
        data-testid="classic-experiment-page"
        sx={{
          backgroundColor: "background.paper",
          flex: 1,
          display: "flex",
          flexDirection: "column",
          gap: 1,
        }}
      >
        <ExperimentRow />
        <ExperimentBar />
        {onSummary ? (
          <ClassicExperimentSummary />
        ) : (
          <AudioPlaybackProvider>
            <ExperimentDataView />
          </AudioPlaybackProvider>
        )}
      </Box>
    </ExperimentDetailContextProvider>
  );
};

export default ExperimentWrapperView;
