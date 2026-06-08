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

export default function WaitingForSignalPanel({
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
  canSendTestTrace = false,
  onSendTestTrace,
  isSendingTestTrace = false,
}) {
  const effectiveJourneyPlan = journeyPlan || observeFallbackJourneyPlan(stage);
  const currentStep = journeyCurrentStep(effectiveJourneyPlan, stage);
  const setupPackageLabel = getObserveSetupPackageLabel({
    setupLanguage,
    setupProvider,
  });
  const traceLabel = setupPackageLabel ? `${setupPackageLabel} trace` : "trace";
  const requestLabel = setupPackageLabel
    ? `${setupPackageLabel} request`
    : "request";
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
      canSendTestTrace={canSendTestTrace}
      onSendTestTrace={onSendTestTrace}
      isSendingTestTrace={isSendingTestTrace}
    />
  ) : null;

  return (
    <Box
      data-testid="waiting-for-signal-panel"
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
          eyebrow="Waiting for trace"
          title={`Send one ${traceLabel}`}
          description={`The Observe project exists. Keep this page open, run one ${requestLabel}, and Future AGI will open the trace when it appears. After review, the next step is the first quality check.`}
          chips={["observe", "waiting"]}
        />
        <ObserveJourneyProgress
          actionSlot={actionSlot}
          journeyPlan={effectiveJourneyPlan}
          singleActionFocus={singleActionFocus}
          stage={stage}
        />
        <Box
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 1,
            p: 1.5,
          }}
        >
          <Typography variant="subtitle2">
            {setupPackageLabel
              ? `${setupPackageLabel} trace status`
              : "Trace status"}
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
            Projects: {signals?.observeProjects || 0} · Traces:{" "}
            {signals?.traces || 0}
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.75 }}>
            Keep this page open after running the request. When the trace
            arrives, the next step is trace review, followed by the first
            quality check.
          </Typography>
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
            canSendTestTrace={canSendTestTrace}
            onSendTestTrace={onSendTestTrace}
            isSendingTestTrace={isSendingTestTrace}
          />
        )}
      </Stack>
    </Box>
  );
}

WaitingForSignalPanel.propTypes = {
  action: PropTypes.object,
  canSendTestTrace: PropTypes.bool,
  fallbackAction: PropTypes.object,
  isChecking: PropTypes.bool,
  isSendingTestTrace: PropTypes.bool,
  journeyPlan: PropTypes.object,
  onCheckAgain: PropTypes.func,
  onFallbackClick: PropTypes.func,
  onPrimaryClick: PropTypes.func,
  onSendTestTrace: PropTypes.func,
  singleActionFocus: PropTypes.bool,
  setupLanguage: PropTypes.string,
  setupProvider: PropTypes.string,
  signals: PropTypes.object,
  stage: PropTypes.string,
};
