import ExternalContentFrame from "src/components/external-content/ExternalContentFrame";
import Box from "@mui/material/Box/Box";
import React, { Suspense, useEffect, useRef, useState } from "react";
import GetStartedHeaderView from "./GetStartedHeaderView";
import SetupStepsView from "./setup-steps/SetupStepsView";
import { useAuthContext } from "src/auth/hooks";
import { getStartedPage } from "./constant";
import axios, { endpoints } from "src/utils/axios";
import { useQuery } from "@tanstack/react-query";
import { countedSteps, listedSteps, menuesConstant } from "./constant";
import { useInviteAccess } from "./useInviteAccess";
import { useLocation } from "react-router-dom";
import { useSearchParams } from "src/routes/hooks";
import { ShowComponent } from "src/components/show";
import { useOrganization } from "src/contexts/OrganizationContext";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
const TryOutFeatures = React.lazy(
  () => import("./try-out-features/TryOutFeatures"),
);
const _GetStartedDemoVideo = React.lazy(() => import("./GetStartedDemoVideo"));

// The checks endpoint answers { result: { keys, dataset, ... } }.
const selectChecks = (response) => response?.data?.result;

// Shown while this visit's checks load, before the first unfinished step is
// known. Never remembered.
const PLACEHOLDER_STEP = "addKeys";

// Steps that show the ways to call a model themselves (ModelRoutes); the
// feature cards under them do not repeat the box.
const STEPS_WITH_MODEL_ROUTES = [
  "addKeys",
  "createFirstDataset",
  "createFirstEvaluation",
  "RunFirstExperiment",
];

// The step the user was last on, per person and organization, for this
// browser tab. The left-menu entry opens Get started with no step in the
// address, and an organization switch keeps this tab's storage.
const LAST_STEP_KEY = "getStarted.lastStep";
const lastStepKey = (identity) => `${LAST_STEP_KEY}:${identity}`;
// "completed" is the summary view after the last step.
const isKnownStep = (label) =>
  label === "completed" ||
  menuesConstant.menusList.some((item) => item.label === label);

const readLastStep = (identity) => {
  try {
    const label = window.sessionStorage.getItem(lastStepKey(identity));
    return isKnownStep(label) ? label : null;
  } catch {
    return null;
  }
};

const rememberStep = (identity, label) => {
  try {
    window.sessionStorage.setItem(lastStepKey(identity), label);
  } catch {
    // Storage can be unavailable (private mode); the page still works.
  }
};

const GetStartedView = () => {
  const { menusList, keySelector } = menuesConstant;
  const { user } = useAuthContext();
  const { countsInvite } = useInviteAccess();
  const { currentOrganizationId } = useOrganization() ?? {};
  const identity = `${user?.id ?? ""}:${currentOrganizationId ?? ""}`;
  const { search } = useLocation();
  const [currentLabel, setQueryParamState] = useSearchParams();
  const [_showDemoModal, setShowDemoModal] = useState(false);
  const { contactUs: _contactUs, getStartedVideo: _getStartedVideo } =
    getStartedPage;

  const { data, isFetching, dataUpdatedAt, refetch } = useQuery({
    queryKey: ["currentLabel"],
    queryFn: () => axios.get(endpoints.getStarted.getTabs),
    staleTime: 2 * 1000,
    select: selectChecks,
  });

  // The step in the address when the page opened (browser Back, or a link
  // to a step).
  const openedOn = useRef(undefined);
  if (openedOn.current === undefined) {
    const inAddress = new URLSearchParams(search).get("tab");
    openedOn.current = isKnownStep(inAddress) ? inAddress : null;
  }

  // Coming back reopens the step the user was on: the one in the address
  // when the page opened, else the one this person was on last in this
  // organization. Decided when the page opens, and again when the person or
  // the organization changes while it is open. Right after sign-in the
  // organization is known a moment after the page opens, so the address
  // still counts then; after a real change it belongs to the one before.
  const visit = useRef(null);
  if (visit.current?.identity !== identity) {
    const before = visit.current;
    const fromAddress =
      !before || !before.organizationId ? openedOn.current : null;
    const stored = readLastStep(identity);
    // The summary in the address (browser Back) is reopened only for
    // whoever finished: after an organization switch, Back can land on the
    // other organization's address.
    const trusted =
      fromAddress === "completed" && stored !== "completed"
        ? null
        : fromAddress;
    visit.current = {
      identity,
      organizationId: currentOrganizationId,
      returnStep: trusted ?? stored,
      // The step on screen once it is the user's, for the left-menu entry
      // while the page is open.
      lastStep: null,
      openedFirstStep: false,
      // After a change, wait for checks asked for this person and
      // organization.
      checksFrom: before ? Date.now() : 0,
      addressIsStale: Boolean(before) || fromAddress !== trusted,
    };
  }
  const thisVisit = visit.current;

  const _handleClose = () => {
    setShowDemoModal(false);
  };

  const setCurrentLabel = (label) => {
    setQueryParamState({ tab: label });
  };

  // The step on screen: the one in the address, else (the bare left-menu
  // entry, another query, an unknown step) the one the user was on, else
  // the placeholder until this visit's checks pick the first unfinished one.
  const inAddress = currentLabel.tab;
  const shownStep =
    isKnownStep(inAddress) && !thisVisit.addressIsStale
      ? inAddress
      : thisVisit.lastStep ?? thisVisit.returnStep ?? PLACEHOLDER_STEP;

  // Keep the address on the step on screen, so the page never falls back to
  // the summary for want of a step, and Back returns to it.
  useEffect(() => {
    if (inAddress === shownStep) {
      visit.current.addressIsStale = false;
      return;
    }
    setCurrentLabel(shownStep);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search, identity]);

  // A new person or organization while the page is open: ask the checks
  // again, for them.
  useEffect(() => {
    if (visit.current.checksFrom) refetch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [identity]);

  // Remember the step on screen once it is the user's (a returning step, the
  // first unfinished one, or one they picked), never the "Add keys"
  // placeholder shown while the checks load.
  useEffect(() => {
    const current = visit.current;
    if (!current.openedFirstStep && !current.returnStep) return;
    current.lastStep = shownStep;
    rememberStep(identity, shownStep);
  }, [shownStep, identity]);

  // Without a step to return to, open the first unfinished step once per
  // visit, from this visit's checks (not a cached answer that is still being
  // refreshed). Later refetches, such as the one when the user comes back to
  // the tab, must leave the user on the step they chose.
  useEffect(() => {
    const current = visit.current;
    if (!data || isFetching || current.openedFirstStep) return;
    if (dataUpdatedAt < current.checksFrom) return;
    current.openedFirstStep = true;

    // The first unfinished step among the ones that count for this reader,
    // the same steps their progress counts.
    const counted = countedSteps(listedSteps(menusList), countsInvite);
    const firstUnfinished = current.returnStep
      ? null
      : counted.find((item) => !data[keySelector[item.label]]);
    const opened = firstUnfinished?.label ?? shownStep;
    current.lastStep = opened;
    current.addressIsStale = false;
    if (opened !== inAddress) setCurrentLabel(opened);
    rememberStep(identity, opened);

    if (!Object.values(data).some(Boolean)) {
      if (sessionStorage.getItem("videoShows") !== "yes") {
        sessionStorage.setItem("videoShows", "yes");
        setShowDemoModal(true);
      }
    }
    trackEvent(Events.demoVideoPageLoaded, { [PropertyName.status]: "Loaded" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, isFetching, dataUpdatedAt]);

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
      <ShowComponent condition={Boolean(data)}>
        <SetupStepsView
          setShowDemoModal={setShowDemoModal}
          data={data}
          currentLabel={shownStep}
          setCurrentLabel={setCurrentLabel}
        />
        <Suspense>
          <TryOutFeatures
            onAddProviderKey={() => setCurrentLabel("addKeys")}
            showModelRoutes={!STEPS_WITH_MODEL_ROUTES.includes(shownStep)}
          />
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
              <ExternalContentFrame
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
