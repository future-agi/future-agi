import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import Iconify from "src/components/iconify";
import { RouterLink } from "src/routes/components";
import { isDestinationTourAnchorDismissed } from "src/sections/onboarding-tour/destinationTourDismissal";
import { readableToken } from "../onboarding-home.constants";
import {
  hrefWithJourneyGuide,
  journeyCurrentStep,
} from "./journey-guide-utils";

const observeActionHref = (action) => {
  if (!action || action.blocked || !action.routeAvailable || !action.href) {
    return null;
  }
  return action.href;
};

const STATUS_COPY = {
  complete: {
    label: "Done",
    icon: "mdi:check-circle",
    color: "success.main",
  },
  current: {
    label: "Now",
    icon: "mdi:progress-clock",
    color: "primary.main",
  },
  queued: {
    label: "Next",
    icon: "mdi:circle-outline",
    color: "text.disabled",
  },
};

const fallbackStepStatus = ({ index, activeIndex }) => {
  if (index < activeIndex) return "complete";
  if (index === activeIndex) return "current";
  return "queued";
};

export function ObservePanelHeader({
  eyebrow,
  title,
  description,
  chips = [],
}) {
  return (
    <Stack spacing={1}>
      <Stack direction="row" spacing={0.75} flexWrap="wrap">
        <Chip size="small" label={eyebrow} />
        {chips.map((chip) => (
          <Chip
            key={chip}
            size="small"
            variant="outlined"
            label={readableToken(chip)}
            sx={{ textTransform: "capitalize" }}
          />
        ))}
      </Stack>
      <Stack spacing={0.5}>
        <Typography variant="h6">{title}</Typography>
        <Typography variant="body2" color="text.secondary">
          {description}
        </Typography>
      </Stack>
    </Stack>
  );
}

ObservePanelHeader.propTypes = {
  chips: PropTypes.arrayOf(PropTypes.string),
  description: PropTypes.string.isRequired,
  eyebrow: PropTypes.string.isRequired,
  title: PropTypes.string.isRequired,
};

export function CurrentStepGuide({ step, stage, stepNumber, totalSteps }) {
  if (!step) return null;

  const progressLabel =
    stepNumber && totalSteps
      ? `Step ${stepNumber} of ${totalSteps}`
      : "Current step";

  return (
    <Box
      data-testid="current-step-guide"
      sx={{
        border: "1px solid",
        borderColor: "primary.main",
        borderRadius: 1,
        p: 1.5,
        bgcolor: "action.hover",
      }}
    >
      <Stack spacing={0.75} sx={{ maxWidth: 720 }}>
        <Chip
          size="small"
          variant="outlined"
          label={progressLabel}
          sx={{ alignSelf: "flex-start" }}
        />
        <Typography variant="h6" color="text.primary">
          {step.label}
        </Typography>
        <Typography variant="body2" color="text.secondary">
          {step.description || readableToken(stage)}
        </Typography>
      </Stack>
    </Box>
  );
}

CurrentStepGuide.propTypes = {
  stage: PropTypes.string,
  step: PropTypes.object,
  stepNumber: PropTypes.number,
  totalSteps: PropTypes.number,
};

export function ObserveJourneyProgress({
  journeyPlan,
  singleActionFocus = false,
  stage,
  showCurrentStepGuide = true,
}) {
  const steps = journeyPlan?.steps || [];

  if (!steps.length) return null;

  const currentStep = journeyCurrentStep(journeyPlan, stage);
  const currentIndex = Math.max(steps.indexOf(currentStep), 0);
  const visibleSteps =
    singleActionFocus && currentStep
      ? steps.filter((_, index) => index !== currentIndex)
      : steps;

  if (singleActionFocus && !showCurrentStepGuide && visibleSteps.length === 0) {
    return null;
  }

  return (
    <Stack spacing={1.25} data-testid="observe-journey-progress">
      {showCurrentStepGuide ? (
        <CurrentStepGuide
          step={currentStep}
          stage={stage}
          stepNumber={currentIndex + 1}
          totalSteps={steps.length}
        />
      ) : null}
      <Stack
        direction={{ xs: "column", sm: "row" }}
        spacing={1}
        alignItems={{ xs: "flex-start", sm: "center" }}
        justifyContent="space-between"
      >
        <Typography variant="subtitle2">
          {singleActionFocus ? "What happens next" : "Setup checklist"}
        </Typography>
        {singleActionFocus ? (
          <Chip
            size="small"
            variant="outlined"
            label={`${visibleSteps.length} remaining`}
          />
        ) : null}
      </Stack>
      {visibleSteps.length ? (
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: {
              xs: "1fr",
              sm: "repeat(2, minmax(0, 1fr))",
              lg: `repeat(${Math.min(visibleSteps.length, 4)}, minmax(0, 1fr))`,
            },
            gap: 1,
          }}
        >
          {visibleSteps.map((step) => {
            const originalIndex = steps.indexOf(step);
            const status =
              step.status ||
              fallbackStepStatus({
                index: originalIndex,
                activeIndex: currentIndex,
              });
            const statusCopy = STATUS_COPY[status] || STATUS_COPY.queued;

            return (
              <Box
                key={step.id || step.stage}
                data-testid={`observe-journey-step-${step.id || step.stage}`}
                sx={{
                  border: "1px solid",
                  borderColor:
                    status === "current"
                      ? "primary.main"
                      : status === "complete"
                        ? "success.main"
                        : "divider",
                  borderRadius: 1,
                  p: 1.25,
                  minHeight: 104,
                  bgcolor: status === "current" ? "action.hover" : "inherit",
                }}
              >
                <Stack spacing={0.75}>
                  <Stack
                    direction="row"
                    alignItems="center"
                    justifyContent="space-between"
                    spacing={1}
                  >
                    <Stack direction="row" alignItems="center" spacing={0.75}>
                      <Iconify
                        icon={statusCopy.icon}
                        width={18}
                        sx={{ color: statusCopy.color, flexShrink: 0 }}
                      />
                      <Typography variant="subtitle2">{step.label}</Typography>
                    </Stack>
                    <Chip
                      size="small"
                      label={statusCopy.label}
                      color={status === "complete" ? "success" : "default"}
                      variant={status === "complete" ? "filled" : "outlined"}
                    />
                  </Stack>
                  <Typography variant="body2" color="text.secondary">
                    {step.description}
                  </Typography>
                </Stack>
              </Box>
            );
          })}
        </Box>
      ) : null}
    </Stack>
  );
}

ObserveJourneyProgress.propTypes = {
  journeyPlan: PropTypes.object,
  singleActionFocus: PropTypes.bool,
  showCurrentStepGuide: PropTypes.bool,
  stage: PropTypes.string,
};

export function ObservePanelActions({
  action,
  fallbackAction,
  onPrimaryClick,
  onFallbackClick,
  onCheckAgain,
  isChecking = false,
  journeyStep,
  primaryVariant = "contained",
  singleActionFocus = false,
}) {
  const primaryHref = hrefWithJourneyGuide(
    observeActionHref(action),
    journeyStep,
  );
  const replayHref =
    !singleActionFocus &&
    primaryHref &&
    journeyStep?.tourAnchor &&
    isDestinationTourAnchorDismissed({ anchor: journeyStep.tourAnchor })
      ? hrefWithJourneyGuide(primaryHref, journeyStep, { replay: true })
      : null;
  const fallbackHref = observeActionHref(fallbackAction);
  const showSecondaryRoutes = !singleActionFocus || !primaryHref;
  const primaryLabel =
    singleActionFocus && journeyStep?.label
      ? journeyStep.label
      : action?.ctaLabel || "Open";

  return (
    <Stack direction={{ xs: "column", sm: "row" }} spacing={1}>
      <Button
        variant={primaryVariant}
        component={primaryHref ? RouterLink : "button"}
        href={primaryHref || undefined}
        disabled={!primaryHref}
        onClick={() => onPrimaryClick?.(action)}
        startIcon={<Iconify icon="mdi:arrow-right" width={18} />}
      >
        {primaryLabel}
      </Button>
      {replayHref ? (
        <Button
          variant="text"
          component={RouterLink}
          href={replayHref}
          startIcon={<Iconify icon="mdi:target" width={18} />}
        >
          Show tip
        </Button>
      ) : null}
      {fallbackAction && showSecondaryRoutes ? (
        <Button
          variant="outlined"
          component={fallbackHref ? RouterLink : "button"}
          href={fallbackHref || undefined}
          disabled={!fallbackHref}
          onClick={() => onFallbackClick?.(fallbackAction)}
        >
          {fallbackAction.ctaLabel || "Fallback"}
        </Button>
      ) : null}
      {onCheckAgain ? (
        <Button
          variant="text"
          onClick={onCheckAgain}
          disabled={isChecking}
          startIcon={<Iconify icon="mdi:refresh" width={18} />}
        >
          Check again
        </Button>
      ) : null}
    </Stack>
  );
}

ObservePanelActions.propTypes = {
  action: PropTypes.object,
  fallbackAction: PropTypes.object,
  isChecking: PropTypes.bool,
  journeyStep: PropTypes.object,
  onCheckAgain: PropTypes.func,
  onFallbackClick: PropTypes.func,
  onPrimaryClick: PropTypes.func,
  primaryVariant: PropTypes.oneOf(["contained", "outlined", "text"]),
  singleActionFocus: PropTypes.bool,
};
