import React from "react";
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

export function CurrentStepGuide({ step, stage }) {
  if (!step) return null;

  return (
    <Box
      data-testid="current-step-guide"
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        p: 1.25,
        bgcolor: "background.neutral",
      }}
    >
      <Stack spacing={0.75}>
        <Typography variant="subtitle2">Current step</Typography>
        <Typography variant="body2" color="text.primary">
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
};

export function ObserveJourneyProgress({
  journeyPlan,
  singleActionFocus = false,
  stage,
}) {
  const steps = journeyPlan?.steps || [];
  if (!steps.length) return null;

  const currentStep = journeyCurrentStep(journeyPlan, stage);
  const currentIndex = Math.max(steps.indexOf(currentStep), 0);

  if (singleActionFocus) {
    return (
      <Stack spacing={1.25} data-testid="observe-journey-progress">
        <Chip
          size="small"
          variant="outlined"
          label={`Step ${currentIndex + 1} of ${steps.length}`}
          sx={{ alignSelf: "flex-start" }}
        />
        <CurrentStepGuide step={currentStep} stage={stage} />
      </Stack>
    );
  }

  return (
    <Stack spacing={1.25} data-testid="observe-journey-progress">
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: {
            xs: "1fr",
            sm: "repeat(2, minmax(0, 1fr))",
            lg: `repeat(${Math.min(steps.length, 4)}, minmax(0, 1fr))`,
          },
          gap: 1,
        }}
      >
        {steps.map((step, index) => {
          const status =
            step.status ||
            fallbackStepStatus({ index, activeIndex: currentIndex });
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
      <CurrentStepGuide step={currentStep} stage={stage} />
    </Stack>
  );
}

ObserveJourneyProgress.propTypes = {
  journeyPlan: PropTypes.object,
  singleActionFocus: PropTypes.bool,
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
        {action?.ctaLabel || "Open"}
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
