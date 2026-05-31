import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import Iconify from "src/components/iconify";
import {
  CurrentStepGuide,
  ObservePanelActions,
  ObservePanelHeader,
} from "./observe-panel-utils";
import { PATH_FOCUS_PLANS } from "./path-focus-plan";

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

const activeStepIndex = (steps, stage) => {
  const index = steps.findIndex((step) => step.stage === stage);
  return index >= 0 ? index : null;
};

const stepStatus = ({ index, activeIndex }) => {
  if (activeIndex === null) return "queued";
  if (index < activeIndex) return "complete";
  if (index === activeIndex) return "current";
  return "queued";
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
  const [showFullJourney, setShowFullJourney] =
    React.useState(!singleActionFocus);
  const plan = journeyPlan || PATH_FOCUS_PLANS[primaryPath];

  React.useEffect(() => {
    setShowFullJourney(!singleActionFocus);
  }, [singleActionFocus, primaryPath, stage]);

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
        <ObservePanelHeader
          eyebrow={plan.eyebrow}
          title={plan.title}
          description={plan.description}
          chips={plan.chips}
        />

        {singleActionFocus && currentIndex !== null ? (
          <Chip
            size="small"
            variant="outlined"
            label={`Step ${currentIndex + 1} of ${plan.steps.length}`}
            sx={{ alignSelf: "flex-start" }}
          />
        ) : null}

        {showFullJourney ? (
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: {
                xs: "1fr",
                sm: "repeat(2, minmax(0, 1fr))",
                lg: `repeat(${Math.min(plan.steps.length, 3)}, minmax(0, 1fr))`,
              },
              gap: 1,
            }}
          >
            {plan.steps.map((step, index) => {
              const status =
                step.status || stepStatus({ index, activeIndex: currentIndex });
              const statusCopy = STATUS_COPY[status] || STATUS_COPY.queued;

              return (
                <Box
                  key={step.stage}
                  data-testid={`path-focus-step-${step.stage}`}
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
                    minHeight: 112,
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
                        <Typography variant="subtitle2">
                          {step.label}
                        </Typography>
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

        <CurrentStepGuide step={currentStep} stage={stage} />

        <ObservePanelActions
          action={action}
          fallbackAction={
            singleActionFocus && action?.href ? null : fallbackAction
          }
          onPrimaryClick={onPrimaryClick}
          onFallbackClick={onFallbackClick}
          onCheckAgain={onCheckAgain}
          isChecking={isChecking}
          journeyStep={currentStep}
          singleActionFocus={singleActionFocus}
        />

        {singleActionFocus && !showFullJourney ? (
          <Button
            variant="text"
            size="small"
            onClick={() => setShowFullJourney(true)}
            sx={{ alignSelf: "flex-start" }}
          >
            Show full path
          </Button>
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
