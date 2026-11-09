import ExternalContentFrame from "src/components/external-content/ExternalContentFrame";
import { Box, Typography } from "@mui/material";
import React, { useState } from "react";
import HeaderContent from "./HeaderContent";
import VideoContent from "./VideoContent";
import GetStartedDemoVideo from "../../GetStartedDemoVideo";
import PropTypes from "prop-types";
import { setupObserveVideoContent } from "../../constant";
import NewObserve from "src/sections/project/NewProject/NewObserve";
import { handleOnDocsClicked } from "src/utils/Mixpanel";

const SetupObsabilityInApplication = ({ setCurrentLabel }) => {
  const [showDemoModal, setShowDemoModal] = useState(false);
  const {
    title,
    description,
    srcThumbnail,
    buttonTitle,
    linkTitle,
    viewDocLink,
    iframeVideo,
  } = setupObserveVideoContent;

  const handleClose = () => {
    setShowDemoModal(false);
  };

  return (
    <Box sx={{ width: "100%" }}>
      <Box sx={{ display: "flex", justifyContent: "space-between" }}>
        <HeaderContent
          title="Set observability in application"
          description="This tour will guide you through the key features and functionalities"
        />
        <Typography
          variant="s1"
          fontWeight={"fontWeightSemiBold"}
          color="text.disabled"
          sx={{ cursor: "pointer" }}
          onClick={() => setCurrentLabel("inviteTeamMembers")}
        >
          Skip
        </Typography>
      </Box>
      <Box sx={{ height: "330px", overflowY: "auto" }}>
        <VideoContent
          title={title}
          description={description}
          srcThumbnail={srcThumbnail}
          buttonTitle={buttonTitle}
          linkTitle={linkTitle}
          viewDocLink={viewDocLink}
          checkNowAction={() => setShowDemoModal(true)}
          onViewDocsLinkClick={() => handleOnDocsClicked("get_started_observe")}
        />
        <NewObserve />
      </Box>
      <GetStartedDemoVideo
        showDemoModal={showDemoModal}
        handleClose={handleClose}
        headingTitle={`Demo`}
        viewDockAction={() => {
          window.open(viewDocLink);
          handleOnDocsClicked("get_started_observe_demo_modal");
        }}
        viewDocTitle="View docs"
        buttonTitle="Start experimenting"
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
              title="Observe: Future AGI"
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

export default SetupObsabilityInApplication;

SetupObsabilityInApplication.propTypes = {
  setCurrentLabel: PropTypes.func,
};
