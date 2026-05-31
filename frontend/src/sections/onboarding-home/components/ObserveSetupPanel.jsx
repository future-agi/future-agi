import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Stack from "@mui/material/Stack";
import {
  CurrentStepGuide,
  ObserveJourneyProgress,
  ObservePanelActions,
  ObservePanelHeader,
} from "./observe-panel-utils";
import { observeFallbackJourneyPlan } from "./observe-fallback-journey-plan";
import { journeyCurrentStep } from "./journey-guide-utils";

export default function ObserveSetupPanel({
  action,
  fallbackAction,
  journeyPlan,
  onPrimaryClick,
  onFallbackClick,
  onCheckAgain,
  isChecking = false,
  singleActionFocus = false,
  stage = "connect_observability",
}) {
  const effectiveJourneyPlan = journeyPlan || observeFallbackJourneyPlan(stage);
  const currentStep = journeyCurrentStep(effectiveJourneyPlan, stage);
  const steps = effectiveJourneyPlan?.steps || [];
  const currentStepIndex = Math.max(steps.indexOf(currentStep), 0);
  const actionStep = currentStep || {
    stage,
    label: action?.title || "Connect observability",
    description:
      action?.description ||
      "Create the observe project and prepare the first trace.",
    tourAnchor: "observe_create_project_button",
  };

  return (
    <Box
      data-testid="observe-setup-panel"
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        p: 2,
        bgcolor: "background.paper",
      }}
    >
      <Stack spacing={2}>
        {!singleActionFocus ? (
          <ObservePanelHeader
            eyebrow={effectiveJourneyPlan.eyebrow || "Observe setup"}
            title={effectiveJourneyPlan.title || "Connect your agent"}
            description={
              effectiveJourneyPlan.description ||
              "Create the project, send one trace, then return here for the first review."
            }
            chips={effectiveJourneyPlan.chips || ["observe", "setup"]}
          />
        ) : null}
        {singleActionFocus ? (
          <CurrentStepGuide
            step={actionStep}
            stage={stage}
            stepNumber={currentStepIndex + 1}
            totalSteps={steps.length || 1}
          />
        ) : null}
        {singleActionFocus ? (
          <ObservePanelActions
            action={action}
            fallbackAction={fallbackAction}
            onPrimaryClick={onPrimaryClick}
            onFallbackClick={onFallbackClick}
            onCheckAgain={onCheckAgain}
            isChecking={isChecking}
            journeyStep={actionStep}
            singleActionFocus={singleActionFocus}
          />
        ) : null}
        <ObserveJourneyProgress
          journeyPlan={effectiveJourneyPlan}
          singleActionFocus={singleActionFocus}
          showCurrentStepGuide={!singleActionFocus}
          stage={stage}
        />
        {!singleActionFocus ? (
          <ObservePanelActions
            action={action}
            fallbackAction={fallbackAction}
            onPrimaryClick={onPrimaryClick}
            onFallbackClick={onFallbackClick}
            onCheckAgain={onCheckAgain}
            isChecking={isChecking}
            journeyStep={actionStep}
            singleActionFocus={singleActionFocus}
          />
        ) : null}
      </Stack>
    </Box>
  );
}

ObserveSetupPanel.propTypes = {
  action: PropTypes.object,
  fallbackAction: PropTypes.object,
  isChecking: PropTypes.bool,
  journeyPlan: PropTypes.object,
  onCheckAgain: PropTypes.func,
  onFallbackClick: PropTypes.func,
  onPrimaryClick: PropTypes.func,
  singleActionFocus: PropTypes.bool,
  stage: PropTypes.string,
};
