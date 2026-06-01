import { Box, Drawer, IconButton, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React, { useState, useEffect } from "react";
import Iconify from "src/components/iconify";
import { ShowComponent } from "src/components/show";
import { useLocation } from "react-router-dom";

import NewExperiment from "./NewExperiment";
import NewObserve from "./NewObserve";
import {
  getObserveSetupOnboardingParams,
  OBSERVE_ONBOARDING_MODES,
} from "src/sections/projects/observeOnboardingRoute";

const NewProjectDrawer = ({ observeSetupVerification, open, onClose }) => {
  const location = useLocation();
  const currentPath = location.pathname;
  const observeSetupOnboardingParams = getObserveSetupOnboardingParams(
    location.search,
  );
  const [isObserve, setIsObserve] = useState(currentPath.includes("observe"));
  const showObserveFirstTraceGuide =
    isObserve &&
    observeSetupOnboardingParams.mode ===
      OBSERVE_ONBOARDING_MODES.SETUP_OBSERVE;
  useEffect(() => {
    const isObserve = currentPath.includes("observe");
    setIsObserve(isObserve);
  }, [currentPath]);

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onClose}
      PaperProps={{
        sx: {
          height: "100vh",
          position: "fixed",
          zIndex: 9999,
          borderRadius: "10px",
          backgroundColor: "background.paper",
        },
      }}
      ModalProps={{
        BackdropProps: {
          style: { backgroundColor: "transparent" },
        },
      }}
    >
      <Box sx={{ width: "80vw", padding: 2 }}>
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
          }}
        >
          <Typography
            veriant="m3"
            fontWeight={"fontWeightMedium"}
            color="text.primary"
          >
            New Projects
          </Typography>
          <IconButton onClick={onClose}>
            <Iconify icon="mingcute:close-line" />
          </IconButton>
        </Box>
        {/* <Box sx={{ marginTop: "24px" }} /> */}
        {/* <ToggleButtonGroup sx={{ padding: 0.5, gap: 0.5 }}>
          <Button
            size="small"
            variant={selected === "experiment" ? "soft" : "text"}
            color={selected === "experiment" ? "primary" : "inherit"}
            onClick={() => setSelected("experiment")}
          >
            Experiment
          </Button>
          <Button
            size="small"
            variant={selected === "observe" ? "soft" : "text"}
            color={selected === "observe" ? "primary" : "inherit"}
            onClick={() => setSelected("observe")}
          >
            Observe
          </Button>
        </ToggleButtonGroup> */}
        <Box sx={{ marginTop: "12px" }} />
        <ShowComponent condition={!isObserve}>
          <NewExperiment />
        </ShowComponent>
        <ShowComponent condition={isObserve}>
          <NewObserve
            setupVerification={observeSetupVerification}
            showFirstTraceGuide={showObserveFirstTraceGuide}
          />
        </ShowComponent>
      </Box>
    </Drawer>
  );
};

NewProjectDrawer.propTypes = {
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
  open: PropTypes.bool,
  onClose: PropTypes.func,
  isObserve: PropTypes.bool,
  isPrototype: PropTypes.bool,
};

export default NewProjectDrawer;
