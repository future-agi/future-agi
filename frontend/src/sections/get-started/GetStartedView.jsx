import Box from "@mui/material/Box/Box";
import React, { Suspense, useState } from "react";
import GetStartedHeaderView from "./GetStartedHeaderView";
import SetupStepsView from "./setup-steps/SetupStepsView";
import { useAuthContext } from "src/auth/hooks";
import { getStartedPage } from "./constant";
import axios, { endpoints } from "src/utils/axios";
import { useQuery } from "@tanstack/react-query";
import { menuesConstant } from "./constant";
import { useSearchParams } from "src/routes/hooks";
import { ShowComponent } from "src/components/show";
import { Loader } from "src/components/loading-screen";
import ErrorState from "src/components/error-state";
import { getRequestErrorMessage } from "src/utils/errorUtils";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
const TryOutFeatures = React.lazy(
  () => import("./try-out-features/TryOutFeatures"),
);
const _GetStartedDemoVideo = React.lazy(() => import("./GetStartedDemoVideo"));

const GetStartedView = () => {
  const { menusList, keySelector } = menuesConstant;
  const [currentLabel, setQueryParamState] = useSearchParams({
    tab: "addKeys",
  });
  const [_showDemoModal, setShowDemoModal] = useState(false);
  const { user: _user } = useAuthContext();
  const { contactUs: _contactUs, getStartedVideo: _getStartedVideo } =
    getStartedPage;

  const labelData = (data) => {
    const label = data?.data?.result;
    const menuCopy = [...menusList]; // clone only if you plan to mutate, otherwise avoid
    for (const item of menuCopy) {
      if (!label?.[keySelector[item.label]]) {
        setCurrentLabel(item.label);
        break;
      }
    }

    if (label && !Object.values(label).some(Boolean)) {
      if (sessionStorage.getItem("videoShows") !== "yes") {
        sessionStorage.setItem("videoShows", "yes");
        setShowDemoModal(true);
      }
    }
    trackEvent(Events.demoVideoPageLoaded, { [PropertyName.status]: "Loaded" });
    return label;
  };

  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ["currentLabel"],
    queryFn: () => axios.get(endpoints.getStarted.getTabs),
    staleTime: 2 * 1000,
    select: labelData,
  });

  const _handleClose = () => {
    setShowDemoModal(false);
  };

  const setCurrentLabel = (label) => {
    setQueryParamState({ tab: label });
  };

  return (
    <Box
      sx={{
        backgroundColor: "background.paper",
        flexDirection: "column",
        // alignItems:'center',
        // justifyContent:'center',
        padding: "16px",
      }}
      overflow={"auto"}
      display={"flex"}
      gap={"24px"}
    >
      <GetStartedHeaderView setShowDemoModal={setShowDemoModal} />
      {/* The steps wait on the setup status; show the one loader meanwhile. */}
      <ShowComponent condition={isLoading}>
        <Loader />
      </ShowComponent>
      <ShowComponent condition={isError}>
        <ErrorState
          title="Could not load your setup steps"
          detail={getRequestErrorMessage(
            error,
            "Could not load your setup steps",
          )}
          onRetry={() => refetch()}
        />
      </ShowComponent>
      <ShowComponent condition={Boolean(data)}>
        <SetupStepsView
          setShowDemoModal={setShowDemoModal}
          data={data}
          currentLabel={currentLabel.tab}
          setCurrentLabel={setCurrentLabel}
        />
        <Suspense>
          <TryOutFeatures />
        </Suspense>
      </ShowComponent>
      {/* <Suspense>
        <GetStartedDemoVideo
          showDemoModal={showDemoModal}
          handleClose={handleClose}
          headingTitle={`Welcome Onboard, ${user?.name} 🎉`}
          headingDescription="Get ready to supercharge your AI development"
          viewDockAction={handleClose}
          viewDocTitle="Get started"
          buttonTitle="Book a demo"
          buttonAction={() => window.open(contactUs)}
          videoComponent={
            <div
              style={{
                position: "relative",
                paddingBottom: "calc(53.0625% + 41px)",
                height: 0,
                width: "100%",
              }}
            >
              <iframe
                src={getStartedVideo}
                title="Quick Product Overview | Future AGI"
                frameBorder="0"
                loading="lazy"
                allow="clipboard-write"
                allowFullScreen
                style={{
                  position: "absolute",
                  top: 0,
                  left: 0,
                  width: "100%",
                  height: "100%",
                  colorScheme: "light",
                }}
              />
            </div>
          }
        />
      </Suspense> */}
    </Box>
  );
};

export default GetStartedView;
