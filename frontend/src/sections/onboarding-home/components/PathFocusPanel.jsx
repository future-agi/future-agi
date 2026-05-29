import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Chip from "@mui/material/Chip";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import Iconify from "src/components/iconify";
import { readableToken } from "../onboarding-home.constants";
import { ObservePanelActions, ObservePanelHeader } from "./observe-panel-utils";
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
  return index >= 0 ? index : 0;
};

const stepStatus = ({ index, activeIndex }) => {
  if (index < activeIndex) return "complete";
  if (index === activeIndex) return "current";
  return "queued";
};

export default function PathFocusPanel({
  action,
  fallbackAction,
  isChecking = false,
  onCheckAgain,
  onFallbackClick,
  onPrimaryClick,
  primaryPath,
  stage,
}) {
  const plan = PATH_FOCUS_PLANS[primaryPath];
  if (!plan) return null;

  const currentIndex = activeStepIndex(plan.steps, stage);
  const currentStep = plan.steps[currentIndex];

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
            const status = stepStatus({ index, activeIndex: currentIndex });
            const statusCopy = STATUS_COPY[status];

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

        <Box
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 1,
            p: 1.5,
            bgcolor: "background.neutral",
          }}
        >
          <Stack spacing={0.5}>
            <Typography variant="subtitle2">Current step</Typography>
            <Typography variant="body2" color="text.secondary">
              {currentStep.label}: {readableToken(stage)}
            </Typography>
          </Stack>
        </Box>

        <ObservePanelActions
          action={action}
          fallbackAction={fallbackAction}
          onPrimaryClick={onPrimaryClick}
          onFallbackClick={onFallbackClick}
          onCheckAgain={onCheckAgain}
          isChecking={isChecking}
        />
      </Stack>
    </Box>
  );
}

PathFocusPanel.propTypes = {
  action: PropTypes.object,
  fallbackAction: PropTypes.object,
  isChecking: PropTypes.bool,
  onCheckAgain: PropTypes.func,
  onFallbackClick: PropTypes.func,
  onPrimaryClick: PropTypes.func,
  primaryPath: PropTypes.string,
  stage: PropTypes.string,
};
