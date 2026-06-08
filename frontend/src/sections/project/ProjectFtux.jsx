import { Box, Typography } from "@mui/material";
import React, { useEffect, useState } from "react";
import { ShowComponent } from "src/components/show";
import { useLocation } from "react-router-dom";
import PropTypes from "prop-types";

import NewExperiment from "./NewProject/NewExperiment";
import NewObserve from "./NewProject/NewObserve";
import ObserveOnboardingFocusPanel from "src/sections/projects/ObserveOnboardingFocusPanel";

const ProjectFtux = ({
  observeSetupCopy,
  observeSetupPrimaryAction,
  observeSetupSecondaryAction,
  observeSetupTourAnchor,
  observeSetupVerification,
}) => {
  const location = useLocation();
  const currentPath = location.pathname;
  const [isObserve, setIsObserve] = useState(currentPath.includes("observe"));
  const showObserveOnboarding = isObserve && Boolean(observeSetupCopy);

  useEffect(() => {
    const isObserve = currentPath.includes("observe");
    setIsObserve(isObserve);
  }, [currentPath]);

  return (
    <Box
      sx={{
        backgroundColor: "background.paper",
        paddingX: "16px",
        paddingTop: "12px",
        paddingBottom: "12px",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
      }}
    >
      <Box
        sx={{ display: "flex", flexDirection: "column", alignItems: "center" }}
      >
        <Box
          component={"img"}
          sx={{ height: "51px", width: "51px" }}
          src="/favicon/logo.svg"
        />
        <Box sx={{ height: "10px" }} />
        <Typography fontSize="20px" fontWeight={700} color="text.primary">
          {showObserveOnboarding
            ? "Connect your app"
            : `Welcome to ${isObserve ? "Observe" : "Prototype"}`}
        </Typography>
        <Box sx={{ height: "5px" }} />
        <Typography
          fontSize="14px"
          color="text.disabled"
          textAlign="center"
          maxWidth={620}
        >
          {showObserveOnboarding
            ? "Choose the package your app uses, paste the matching setup, then run one request. Future AGI waits for the trace, opens review, then guides the first quality check."
            : "Create a project to experiment on your model"}
        </Typography>
        <Box sx={{ height: "20px" }} />
      </Box>
      {showObserveOnboarding ? (
        <ObserveOnboardingFocusPanel
          currentStep={observeSetupCopy.currentStep}
          description={observeSetupCopy.description}
          primaryAction={observeSetupPrimaryAction}
          secondaryAction={observeSetupSecondaryAction}
          singleActionFocus
          steps={observeSetupCopy.steps}
          sx={{ width: "100%", mb: 2 }}
          title={observeSetupCopy.title}
          tourAnchor={observeSetupTourAnchor}
        />
      ) : null}
      <Box sx={{ height: "27px" }} />
      <Box
        id={isObserve ? "observe-setup-instructions" : undefined}
        sx={{ width: "100%" }}
      >
        <ShowComponent condition={!isObserve}>
          <NewExperiment />
        </ShowComponent>
        <ShowComponent condition={isObserve}>
          <NewObserve
            setupVerification={observeSetupVerification}
            showFirstTraceGuide={showObserveOnboarding}
          />
        </ShowComponent>
      </Box>
    </Box>
  );
};

ProjectFtux.propTypes = {
  observeSetupCopy: PropTypes.shape({
    currentStep: PropTypes.string,
    description: PropTypes.string.isRequired,
    steps: PropTypes.arrayOf(
      PropTypes.shape({
        complete: PropTypes.bool,
        label: PropTypes.string.isRequired,
      }),
    ),
    title: PropTypes.string.isRequired,
  }),
  observeSetupPrimaryAction: PropTypes.shape({
    disabled: PropTypes.bool,
    label: PropTypes.string.isRequired,
    onClick: PropTypes.func.isRequired,
  }),
  observeSetupSecondaryAction: PropTypes.shape({
    disabled: PropTypes.bool,
    label: PropTypes.string.isRequired,
    onClick: PropTypes.func.isRequired,
  }),
  observeSetupTourAnchor: PropTypes.string,
  observeSetupVerification: PropTypes.shape({
    description: PropTypes.string.isRequired,
    primaryAction: PropTypes.shape({
      disabled: PropTypes.bool,
      label: PropTypes.string.isRequired,
      onClick: PropTypes.func.isRequired,
    }),
    status: PropTypes.oneOf(["ready", "waiting"]).isRequired,
    title: PropTypes.string.isRequired,
  }),
};

export default ProjectFtux;
