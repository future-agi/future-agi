import ExternalContentFrame from "src/components/external-content/ExternalContentFrame";
import { Box, Button, Typography } from "@mui/material";
import React, { useId, useState } from "react";
import NewExperiment from "src/sections/project/NewProject/NewExperiment";
import HeaderContent from "./HeaderContent";
import VideoContent from "./VideoContent";
import GetStartedDemoVideo from "../../GetStartedDemoVideo";
import PropTypes from "prop-types";
import { runFirstExperimentVideoContent } from "../../constant";
import { handleOnDocsClicked } from "src/utils/Mixpanel";
import ModelRoutes from "../../ModelRoutes";

// How an experiment is run, in the words the step's button uses
// (BottomAction: "Go to Datasets" / "Go to Experiments"); the bar below adds
// no second copy of these.
const RUN_EXPERIMENT_STEPS = [
  "Open a dataset in Datasets, or create one.",
  "Open its Experiments tab.",
  "Click New experiment, pick what to run on the dataset, and compare the results.",
];

const RunFirstExperiment = ({ setCurrentLabel }) => {
  const stepsHeadingId = useId();
  const sdkHeadingId = useId();
  const {
    title,
    description,
    srcThumbnail,
    buttonTitle,
    linkTitle,
    viewDocLink,
    iframeVideo,
  } = runFirstExperimentVideoContent;
  const [showDemoModal, setShowDemoModal] = useState(false);

  const handleClose = () => {
    setShowDemoModal(false);
  };

  return (
    <Box sx={{ width: "100%" }}>
      <Box sx={{ display: "flex", justifyContent: "space-between" }}>
        <HeaderContent
          title="Run your first experiment"
          description="Run an experiment on a dataset to compare prompts and models side by side and keep the one that works best."
        />
        <Button
          variant="text"
          size="small"
          sx={{ color: "text.secondary" }}
          onClick={() => setCurrentLabel("SetupObsabilityInApplication")}
        >
          Skip
        </Button>
      </Box>
      <ModelRoutes
        sentence="Experiments call a model, and your organization has no provider key of its own."
        onAddProviderKey={() => setCurrentLabel("addKeys")}
      />
      <Box sx={{ height: "330px", overflowY: "auto" }}>
        <VideoContent
          title={title}
          description={description}
          srcThumbnail={srcThumbnail}
          buttonTitle={buttonTitle}
          linkTitle={linkTitle}
          viewDocLink={viewDocLink}
          checkNowAction={() => setShowDemoModal(true)}
          onViewDocsLinkClick={() =>
            handleOnDocsClicked("get_started_run_experiment")
          }
        />
        <Typography
          id={stepsHeadingId}
          variant="s1"
          fontWeight={"fontWeightMedium"}
          color="text.primary"
        >
          Run an experiment
        </Typography>
        <Box
          component="ol"
          aria-labelledby={stepsHeadingId}
          sx={{
            m: 0,
            mt: "8px",
            pl: "24px",
            display: "flex",
            flexDirection: "column",
            gap: "8px",
          }}
        >
          {RUN_EXPERIMENT_STEPS.map((step) => (
            <Typography
              key={step}
              component="li"
              variant="s2"
              fontWeight={"fontWeightRegular"}
              color="text.secondary"
            >
              {step}
            </Typography>
          ))}
        </Box>
        <Box
          component="section"
          aria-labelledby={sdkHeadingId}
          sx={{
            mt: "24px",
            display: "flex",
            flexDirection: "column",
            gap: "12px",
          }}
        >
          <Typography
            id={sdkHeadingId}
            variant="s1"
            fontWeight={"fontWeightMedium"}
            color="text.primary"
          >
            Or run one from your code
          </Typography>
          <NewExperiment />
        </Box>
      </Box>
      <GetStartedDemoVideo
        showDemoModal={showDemoModal}
        handleClose={handleClose}
        headingTitle={`Video`}
        viewDockAction={() => {
          window.open(viewDocLink);
          handleOnDocsClicked("get_started_run_experiment_demo_modal");
        }}
        viewDocTitle="View docs"
        buttonTitle="Close"
        buttonAction={handleClose}
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
              src={iframeVideo}
              title="Prototype: Future AGI"
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
    </Box>
  );
};

export default RunFirstExperiment;

RunFirstExperiment.propTypes = {
  setCurrentLabel: PropTypes.func,
};
