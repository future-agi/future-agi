import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Chip from "@mui/material/Chip";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import {
  CurrentStepGuide,
  JourneyStepList,
  ObservePanelActions,
  ObservePanelHeader,
} from "./observe-panel-utils";
import { PATH_FOCUS_PLANS } from "./path-focus-plan";

const activeStepIndex = (steps, stage) => {
  const index = steps.findIndex((step) => step.stage === stage);
  return index >= 0 ? index : null;
};

export default function PathFocusPanel({
  action,
  fallbackAction,
  isChecking = false,
  journeyPlan,
  onCheckAgain,
  onFallbackClick,
  onPrimaryClick,
  primaryPath,
  singleActionFocus = false,
  stage,
}) {
  const plan = journeyPlan || PATH_FOCUS_PLANS[primaryPath];

  if (!plan || !plan.steps?.length) return null;

  const derivedCurrentIndex =
    typeof plan.currentStepIndex === "number"
      ? plan.currentStepIndex
      : activeStepIndex(plan.steps, stage);
  const currentIndex =
    derivedCurrentIndex === null
      ? null
      : Math.min(Math.max(derivedCurrentIndex, 0), plan.steps.length - 1);
  const currentStep = currentIndex === null ? null : plan.steps[currentIndex];
  const nextStep =
    currentIndex === null ? null : plan.steps[currentIndex + 1] || null;
  const visibleStepStartIndex =
    singleActionFocus && currentIndex !== null ? currentIndex + 1 : 0;
  const visibleSteps =
    singleActionFocus && currentIndex !== null
      ? plan.steps.slice(visibleStepStartIndex)
      : plan.steps;
  const currentActionSlot = currentStep ? (
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
  const focusedGuide = singleActionFocus || Boolean(currentActionSlot);

  return (
    <Box
      data-testid={`path-focus-panel-${primaryPath}`}
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
            eyebrow={plan.eyebrow}
            title={plan.title}
            description={plan.description}
            chips={plan.chips}
          />
        ) : null}

        {currentStep ? (
          <CurrentStepGuide
            actionSlot={currentActionSlot}
            label={singleActionFocus ? "Current step" : "Start here"}
            nextStep={nextStep}
            step={currentStep}
            stage={stage}
            stepNumber={currentIndex === null ? undefined : currentIndex + 1}
            totalSteps={plan.steps.length}
          />
        ) : null}

        {visibleSteps.length ? (
          <Stack
            direction={{ xs: "column", sm: "row" }}
            spacing={1}
            alignItems={{ xs: "flex-start", sm: "center" }}
            justifyContent="space-between"
          >
            <Typography variant="subtitle2">
              {singleActionFocus ? "Later steps" : "What happens next"}
            </Typography>
            {focusedGuide && !singleActionFocus && currentIndex !== null ? (
              <Chip
                size="small"
                variant="outlined"
                label={`Step ${currentIndex + 1} of ${plan.steps.length}`}
              />
            ) : null}
          </Stack>
        ) : null}

        {visibleSteps.length ? (
          <JourneyStepList
            currentIndex={currentIndex}
            gridColumns={3}
            singleActionFocus={focusedGuide}
            startIndex={visibleStepStartIndex}
            steps={visibleSteps}
            testIdPrefix="path-focus-step"
          />
        ) : null}

        {!currentStep ? (
          <>
            <CurrentStepGuide
              nextStep={nextStep}
              step={currentStep}
              stage={stage}
            />

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
          </>
        ) : null}
      </Stack>
    </Box>
  );
}

PathFocusPanel.propTypes = {
  action: PropTypes.object,
  fallbackAction: PropTypes.object,
  isChecking: PropTypes.bool,
  journeyPlan: PropTypes.object,
  onCheckAgain: PropTypes.func,
  onFallbackClick: PropTypes.func,
  onPrimaryClick: PropTypes.func,
  primaryPath: PropTypes.string,
  singleActionFocus: PropTypes.bool,
  stage: PropTypes.string,
};
