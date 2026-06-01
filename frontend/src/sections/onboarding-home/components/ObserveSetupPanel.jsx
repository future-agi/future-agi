import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
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
  const nextStep = steps[currentStepIndex + 1] || null;
  const actionStep = currentStep || {
    stage,
    label: action?.title || "Connect observability",
    description:
      action?.description ||
      "Create the observe project and prepare the first trace.",
    tourAnchor: "observe_create_project_button",
  };
  const actionSlot = (
    <ObservePanelActions
      action={action}
      fallbackAction={fallbackAction}
      onPrimaryClick={onPrimaryClick}
      onFallbackClick={onFallbackClick}
      onCheckAgain={onCheckAgain}
      isChecking={isChecking}
      journeyStep={actionStep}
      singleActionFocus={singleActionFocus || Boolean(actionStep)}
    />
  );

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
        ) : (
          <Stack spacing={0.25}>
            <Typography variant="subtitle2">First action</Typography>
            <Typography variant="body2" color="text.secondary">
              Complete this action, then continue with the next steps below.
            </Typography>
          </Stack>
        )}
        {actionStep ? (
          <CurrentStepGuide
            actionSlot={actionSlot}
            label={singleActionFocus ? "First action" : "Start here"}
            nextStep={nextStep}
            step={actionStep}
            stage={stage}
            stepNumber={currentStepIndex + 1}
            totalSteps={steps.length || 1}
          />
        ) : null}
        <ObserveJourneyProgress
          journeyPlan={effectiveJourneyPlan}
          singleActionFocus={singleActionFocus}
          showCurrentStepGuide={false}
          stage={stage}
        />
        {!actionStep ? (
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
