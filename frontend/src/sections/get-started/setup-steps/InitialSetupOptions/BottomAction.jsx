import { Box, Button, Divider } from "@mui/material";
import PropTypes from "prop-types";
import React, { useMemo } from "react";
import { useNavigate } from "react-router";
import { useAuthContext } from "src/auth/hooks";
import {
  buildCurrentFlowContext,
  CurrentFlowEvents,
  isProductRoute,
  normalizeGetStartedStep,
  trackCurrentFlow,
} from "src/utils/analytics/currentFlow";

const BottomAction = ({ currentLabel, setCurrentLabel }) => {
  const navigate = useNavigate();
  const { user } = useAuthContext();
  const currentAction = useMemo(() => {
    const option = {};
    switch (currentLabel) {
      case "addKeys":
        option.buttonText = "Next";
        option.buttonAction = () => setCurrentLabel("createFirstDataset");
        option.targetStep = "createFirstDataset";
        break;
      case "SetupObsabilityInApplication":
        option.buttonText = "Go to observe";
        option.buttonAction = () => navigate("/dashboard/observe");
        option.targetRoute = "/dashboard/observe";
        break;
      case "RunFirstExperiment":
        option.buttonText = "Go to experiments";
        option.buttonAction = () => navigate("/dashboard/prototype");
        option.targetRoute = "/dashboard/prototype";
        break;
      case "inviteTeamMembers":
        option.buttonText = "Complete the setup";
        option.buttonAction = () => setCurrentLabel("completed");
        option.targetStep = "completed";
        break;
      default:
        option.buttonText = "";
        option.buttonAction = () => {};
    }
    return option;
  }, [currentLabel, navigate, setCurrentLabel]);

  const handlePrimaryClick = () => {
    const context = buildCurrentFlowContext({ user });
    trackCurrentFlow(CurrentFlowEvents.currentFlowGetStartedPrimaryClicked, {
      ...context,
      event_source: "get_started_bottom_action",
      selected_step: normalizeGetStartedStep(currentLabel),
      button_text: currentAction.buttonText,
      target_route: currentAction.targetRoute,
      target_step: normalizeGetStartedStep(currentAction.targetStep),
    });
    if (
      currentAction.targetRoute &&
      isProductRoute(currentAction.targetRoute)
    ) {
      trackCurrentFlow(
        CurrentFlowEvents.currentFlowFirstValueCandidate,
        {
          ...context,
          event_source: "get_started_bottom_action",
          selected_step: normalizeGetStartedStep(currentLabel),
          candidate_type: "get_started_primary_route",
          target_route: currentAction.targetRoute,
        },
        {
          onceKeyParts: [
            "firstValueCandidate",
            user?.default_workspace_id,
            user?.id,
          ],
        },
      );
    }
    currentAction.buttonAction();
  };

  return (
    <Box sx={{ bgcolor: "white.100" }}>
      <Divider orientation="horizontal"></Divider>
      <Box
        px={"16px"}
        py={"12px"}
        sx={{
          display: "flex",
          justifyContent: "flex-end",
          alignItems: "center",
          height: "100%",
        }}
      >
        <Button
          color="primary"
          variant="contained"
          onClick={handlePrimaryClick}
          sx={{ minWidth: "120px", height: "38px", padding: "8px 24px" }}
        >
          {currentAction?.buttonText}
        </Button>
      </Box>
    </Box>
  );
};

export default BottomAction;

BottomAction.propTypes = {
  setCurrentLabel: PropTypes.func,
  currentLabel: PropTypes.string,
};
