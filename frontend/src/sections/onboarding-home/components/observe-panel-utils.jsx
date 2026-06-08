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
    label: "Current",
    icon: "mdi:progress-clock",
    color: "primary.main",
  },
  queued: {
    label: "Next",
    icon: "mdi:circle-outline",
    color: "text.disabled",
  },
};

const REFRESHABLE_FOCUS_STAGES = new Set([
  "waiting_for_first_trace",
  "waiting_for_first_trace_sample_available",
]);

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

export function CurrentStepGuide({
  actionSlot,
  label = "Current step",
  nextStep,
  step,
  stage,
  stepNumber,
  totalSteps,
}) {
  if (!step) return null;

  const progressLabel =
    stepNumber && totalSteps
      ? `Step ${stepNumber} of ${totalSteps}`
      : "Current step";
  const hasActionSlot = Boolean(actionSlot);

  return (
    <Box
      data-testid="current-step-guide"
      sx={{
        border: "1px solid",
        borderColor: "primary.main",
        borderRadius: 1,
        p: { xs: 1.5, md: hasActionSlot ? 2 : 1.5 },
        bgcolor: "action.hover",
      }}
    >
      <Stack
        direction={{ xs: "column", md: hasActionSlot ? "row" : "column" }}
        spacing={1.5}
        alignItems={{ xs: "stretch", md: hasActionSlot ? "center" : "stretch" }}
        justifyContent="space-between"
      >
        <Stack spacing={0.75} sx={{ maxWidth: 720 }}>
          <Stack direction="row" spacing={0.75} flexWrap="wrap">
            <Chip size="small" color="primary" label={label} />
            <Chip size="small" variant="outlined" label={progressLabel} />
          </Stack>
          <Typography variant="h6" color="text.primary">
            {step.label}
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {step.description || readableToken(stage)}
          </Typography>
          {nextStep?.label ? (
            <Stack direction="row" spacing={0.75} alignItems="center">
              <Iconify
                icon="mdi:arrow-right-circle-outline"
                width={18}
                sx={{ color: "primary.main", flexShrink: 0 }}
              />
              <Typography variant="body2" color="text.secondary">
                Next: {nextStep.label}
              </Typography>
            </Stack>
          ) : null}
        </Stack>
        {hasActionSlot ? (
          <Box
            sx={{
              display: "flex",
              justifyContent: { xs: "stretch", md: "flex-end" },
              minWidth: { md: 220 },
              "& .MuiStack-root": {
                width: { xs: "100%", md: "auto" },
              },
              "& .MuiButton-root": {
                minHeight: 44,
                width: { xs: "100%", sm: "auto" },
              },
            }}
          >
            {actionSlot}
          </Box>
        ) : null}
      </Stack>
    </Box>
  );
}

CurrentStepGuide.propTypes = {
  actionSlot: PropTypes.node,
  label: PropTypes.string,
  nextStep: PropTypes.object,
  stage: PropTypes.string,
  step: PropTypes.object,
  stepNumber: PropTypes.number,
  totalSteps: PropTypes.number,
};

const focusedStepLabel = ({ activeIndex, index, status }) => {
  if (status === "complete") return "Done";
  if (status === "current") return "Current";
  if (activeIndex !== null && index === activeIndex + 1) return "Next";
  return `Step ${index + 1}`;
};

const derivedStepStatus = ({ activeIndex, index, step }) => {
  if (step.status) return step.status;
  if (activeIndex === null) return "queued";
  return fallbackStepStatus({ index, activeIndex });
};

export function JourneyStepList({
  currentIndex,
  gridColumns = 4,
  singleActionFocus = false,
  startIndex = 0,
  steps,
  testIdPrefix,
}) {
  if (!steps?.length) return null;

  const activeIndex =
    currentIndex === null || typeof currentIndex !== "number"
      ? null
      : Math.min(Math.max(currentIndex, 0), steps.length - 1);

  if (singleActionFocus) {
    return (
      <Stack spacing={0.75}>
        {steps.map((step, index) => {
          const absoluteIndex = startIndex + index;
          const status = derivedStepStatus({
            activeIndex,
            index: absoluteIndex,
            step,
          });
          const statusLabel = focusedStepLabel({
            activeIndex,
            index: absoluteIndex,
            status,
          });
          const isCurrent = status === "current";

          return (
            <Box
              key={step.id || step.stage}
              data-testid={`${testIdPrefix}-${step.id || step.stage}`}
              sx={{
                border: "1px solid",
                borderColor: isCurrent
                  ? "primary.main"
                  : status === "complete"
                    ? "success.main"
                    : "divider",
                borderRadius: 1,
                p: 1,
                bgcolor: isCurrent ? "action.hover" : "inherit",
              }}
            >
              <Stack
                direction={{ xs: "column", sm: "row" }}
                spacing={1}
                alignItems={{ xs: "flex-start", sm: "center" }}
              >
                <Stack
                  direction="row"
                  spacing={1}
                  alignItems="center"
                  sx={{ flex: 1, minWidth: 0 }}
                >
                  <Box
                    sx={{
                      width: 28,
                      height: 28,
                      borderRadius: "50%",
                      border: "1px solid",
                      borderColor: isCurrent
                        ? "primary.main"
                        : status === "complete"
                          ? "success.main"
                          : "divider",
                      bgcolor: isCurrent ? "primary.main" : "transparent",
                      color: isCurrent
                        ? "primary.contrastText"
                        : "text.primary",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      flexShrink: 0,
                      typography: "caption",
                      fontWeight: 700,
                    }}
                  >
                    {status === "complete" ? (
                      <Iconify icon="mdi:check" width={16} />
                    ) : (
                      absoluteIndex + 1
                    )}
                  </Box>
                  <Box sx={{ minWidth: 0 }}>
                    <Typography variant="subtitle2">{step.label}</Typography>
                    <Typography variant="body2" color="text.secondary">
                      {step.description}
                    </Typography>
                  </Box>
                </Stack>
                <Chip
                  size="small"
                  label={statusLabel}
                  color={status === "complete" ? "success" : "default"}
                  variant={
                    status === "complete" || isCurrent ? "filled" : "outlined"
                  }
                  sx={{ flexShrink: 0 }}
                />
              </Stack>
            </Box>
          );
        })}
      </Stack>
    );
  }

  return (
    <Box
      sx={{
        display: "grid",
        gridTemplateColumns: {
          xs: "1fr",
          sm: "repeat(2, minmax(0, 1fr))",
          lg: `repeat(${Math.min(steps.length, gridColumns)}, minmax(0, 1fr))`,
        },
        gap: 1,
      }}
    >
      {steps.map((step, index) => {
        const absoluteIndex = startIndex + index;
        const status = derivedStepStatus({
          activeIndex,
          index: absoluteIndex,
          step,
        });
        const statusCopy = STATUS_COPY[status] || STATUS_COPY.queued;

        return (
          <Box
            key={step.id || step.stage}
            data-testid={`${testIdPrefix}-${step.id || step.stage}`}
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
  );
}

JourneyStepList.propTypes = {
  currentIndex: PropTypes.number,
  gridColumns: PropTypes.number,
  singleActionFocus: PropTypes.bool,
  startIndex: PropTypes.number,
  steps: PropTypes.array,
  testIdPrefix: PropTypes.string.isRequired,
};

export function ObserveJourneyProgress({
  actionSlot,
  journeyPlan,
  singleActionFocus = false,
  stage,
  showCurrentStepGuide = true,
}) {
  const steps = journeyPlan?.steps || [];

  if (!steps.length) return null;

  const currentStep = journeyCurrentStep(journeyPlan, stage);
  const currentIndex = Math.max(steps.indexOf(currentStep), 0);
  const visibleStepStartIndex = singleActionFocus ? currentIndex + 1 : 0;
  const visibleSteps = singleActionFocus
    ? steps.slice(visibleStepStartIndex)
    : steps;
  const focusedGuide = singleActionFocus || Boolean(actionSlot);

  if (singleActionFocus && !showCurrentStepGuide && visibleSteps.length === 0) {
    return null;
  }

  return (
    <Stack spacing={1.25} data-testid="observe-journey-progress">
      {showCurrentStepGuide ? (
        <CurrentStepGuide
          actionSlot={actionSlot}
          label={focusedGuide ? "Do this next" : "Current step"}
          nextStep={steps[currentIndex + 1]}
          step={currentStep}
          stage={stage}
          stepNumber={currentIndex + 1}
          totalSteps={steps.length}
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
          {focusedGuide && !singleActionFocus ? (
            <Chip
              size="small"
              variant="outlined"
              label={`Step ${currentIndex + 1} of ${steps.length}`}
            />
          ) : null}
        </Stack>
      ) : null}
      {visibleSteps.length ? (
        <JourneyStepList
          currentIndex={currentIndex}
          singleActionFocus={focusedGuide}
          startIndex={visibleStepStartIndex}
          steps={visibleSteps}
          testIdPrefix="observe-journey-step"
        />
      ) : null}
    </Stack>
  );
}

ObserveJourneyProgress.propTypes = {
  actionSlot: PropTypes.node,
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
  canSendTestTrace = false,
  onSendTestTrace,
  isSendingTestTrace = false,
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
  const showCheckAgain = Boolean(
    onCheckAgain &&
      (!singleActionFocus || REFRESHABLE_FOCUS_STAGES.has(journeyStep?.stage)),
  );
  const primaryLabel =
    action?.ctaLabel || (singleActionFocus && journeyStep?.label) || "Open";
  // Capability-gated: only render when the backend advertises support via
  // signals.test_trace_supported (threaded down as canSendTestTrace) AND a
  // handler is wired, so a real user never sees a button that 404s.
  const showSendTestTrace = Boolean(onSendTestTrace && canSendTestTrace);

  return (
    <Stack direction={{ xs: "column", sm: "row" }} spacing={1}>
      <Button
        variant={primaryVariant}
        component={primaryHref ? RouterLink : "button"}
        data-tour-anchor={journeyStep?.tourAnchor || undefined}
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
          {fallbackAction.ctaLabel || "Alternate setup"}
        </Button>
      ) : null}
      {showSendTestTrace ? (
        <Button
          variant="outlined"
          data-testid="send-test-trace-button"
          onClick={onSendTestTrace}
          disabled={isSendingTestTrace}
          startIcon={<Iconify icon="mdi:flask-outline" width={18} />}
        >
          {isSendingTestTrace ? "Sending test trace…" : "Send a test trace"}
        </Button>
      ) : null}
      {showCheckAgain ? (
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
  canSendTestTrace: PropTypes.bool,
  fallbackAction: PropTypes.object,
  isChecking: PropTypes.bool,
  isSendingTestTrace: PropTypes.bool,
  journeyStep: PropTypes.object,
  onCheckAgain: PropTypes.func,
  onFallbackClick: PropTypes.func,
  onPrimaryClick: PropTypes.func,
  onSendTestTrace: PropTypes.func,
  primaryVariant: PropTypes.oneOf(["contained", "outlined", "text"]),
  singleActionFocus: PropTypes.bool,
};
