import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import {
  ObserveJourneyProgress,
  ObservePanelActions,
  ObservePanelHeader,
} from "./observe-panel-utils";
import { observeFallbackJourneyPlan } from "./observe-fallback-journey-plan";
import { journeyCurrentStep } from "./journey-guide-utils";
import { getObserveSetupPackageLabel } from "src/sections/projects/observeOnboardingRoute";

export default function FirstSignalPanel({
  action,
  fallbackAction,
  journeyPlan,
  signals,
  stage,
  onPrimaryClick,
  onFallbackClick,
  onCheckAgain,
  isChecking = false,
  singleActionFocus = false,
  setupLanguage,
  setupProvider,
}) {
  const isImprovement = stage === "create_trace_evaluator";
  const effectiveJourneyPlan = journeyPlan || observeFallbackJourneyPlan(stage);
  const currentStep = journeyCurrentStep(effectiveJourneyPlan, stage);
  const setupPackageLabel = getObserveSetupPackageLabel({
    setupLanguage,
    setupProvider,
  });
  const traceLabel = setupPackageLabel ? `${setupPackageLabel} trace` : "trace";
  const actionSlot = currentStep ? (
    <ObservePanelActions
      action={action}
      fallbackAction={fallbackAction}
      onPrimaryClick={onPrimaryClick}
      onFallbackClick={onFallbackClick}
      onCheckAgain={onCheckAgain}
      isChecking={isChecking}
      journeyStep={currentStep}
      singleActionFocus={singleActionFocus || Boolean(currentStep)}
    />
  ) : null;

  return (
    <Box
      data-testid="first-signal-panel"
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
          eyebrow={isImprovement ? "First improvement" : "First trace"}
          title={
            isImprovement
              ? `Create a quality check from this ${traceLabel}`
              : setupPackageLabel
                ? `${setupPackageLabel} trace received`
                : "First trace received"
          }
          description={
            isImprovement
              ? setupPackageLabel
                ? `The ${setupPackageLabel} trace has been reviewed. Create a repeatable quality check next.`
                : "The first trace has been reviewed. Create a repeatable quality check next."
              : setupPackageLabel
                ? `Review the ${setupPackageLabel} trace to inspect inputs, outputs, latency, cost, and errors.`
                : "Review the trace to inspect inputs, outputs, latency, cost, and errors."
          }
          chips={["observe", isImprovement ? "improve" : "review"]}
        />
        <ObserveJourneyProgress
          actionSlot={actionSlot}
          journeyPlan={effectiveJourneyPlan}
          singleActionFocus={singleActionFocus}
          stage={stage}
        />
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: { xs: "1fr", sm: "repeat(2, 1fr)" },
            gap: 1,
          }}
        >
          <Box
            sx={{
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 1,
              p: 1.5,
            }}
          >
            <Typography variant="subtitle2">
              {setupPackageLabel ? `${setupPackageLabel} trace` : "Trace"}
            </Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
              {signals?.firstTraceId || "Waiting for trace id"}
            </Typography>
          </Box>
          <Box
            sx={{
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 1,
              p: 1.5,
            }}
          >
            <Typography variant="subtitle2">Review status</Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
              {signals?.traceReviews ? "Reviewed" : "Not reviewed"}
            </Typography>
          </Box>
        </Box>
        {currentStep ? null : (
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
        )}
      </Stack>
    </Box>
  );
}

FirstSignalPanel.propTypes = {
  action: PropTypes.object,
  fallbackAction: PropTypes.object,
  isChecking: PropTypes.bool,
  journeyPlan: PropTypes.object,
  onCheckAgain: PropTypes.func,
  onFallbackClick: PropTypes.func,
  onPrimaryClick: PropTypes.func,
  singleActionFocus: PropTypes.bool,
  setupLanguage: PropTypes.string,
  setupProvider: PropTypes.string,
  signals: PropTypes.object,
  stage: PropTypes.string,
};
