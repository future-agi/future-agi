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
  const currentStep = journeyCurrentStep(journeyPlan, stage);
  const fallbackCurrentStep = currentStep || {
    label: action?.title || "Connect observability",
    description:
      action?.description ||
      "Create the observe project and prepare the first trace.",
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
        <ObservePanelHeader
          eyebrow="Observe setup"
          title="Connect one observe project"
          description="Create the project, send one trace, then return here for the first review."
          chips={["observe", "setup"]}
        />
        {journeyPlan ? (
          <ObserveJourneyProgress
            journeyPlan={journeyPlan}
            singleActionFocus={singleActionFocus}
            stage={stage}
          />
        ) : singleActionFocus ? (
          <CurrentStepGuide step={fallbackCurrentStep} stage={stage} />
        ) : (
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: { xs: "1fr", md: "repeat(3, 1fr)" },
              gap: 1,
            }}
          >
            {[
              "Create observe project",
              "Send one trace",
              "Review the signal",
            ].map((item) => (
              <Box
                key={item}
                sx={{
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: 1,
                  p: 1.25,
                  minHeight: 76,
                }}
              >
                <Typography variant="body2">{item}</Typography>
              </Box>
            ))}
          </Box>
        )}
        <ObservePanelActions
          action={action}
          fallbackAction={fallbackAction}
          onPrimaryClick={onPrimaryClick}
          onFallbackClick={onFallbackClick}
          onCheckAgain={onCheckAgain}
          isChecking={isChecking}
          journeyStep={currentStep}
          singleActionFocus={singleActionFocus}
        />
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
