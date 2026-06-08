import React, { useEffect } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import Iconify from "src/components/iconify";

const currentStepPosition = ({ currentStep, steps }) => {
  if (!steps.length) return null;

  const matchingIndex = steps.findIndex((step) => step.label === currentStep);
  if (matchingIndex >= 0) return matchingIndex + 1;

  const firstIncompleteIndex = steps.findIndex((step) => !step.complete);
  return (firstIncompleteIndex >= 0 ? firstIncompleteIndex : 0) + 1;
};

export default function EvalOnboardingFocusPanel({
  currentStep,
  description,
  hidden = false,
  onViewed,
  primaryAction = null,
  sourceSummary = null,
  steps = [],
  title,
  tourAnchor,
}) {
  useEffect(() => {
    if (!hidden) {
      onViewed?.();
    }
  }, [hidden, onViewed]);

  if (hidden) return null;

  const stepPosition = currentStepPosition({ currentStep, steps });

  return (
    <Box
      data-testid="eval-onboarding-focus"
      sx={{
        mb: 2,
        border: "1px solid",
        borderColor: "primary.main",
        borderRadius: 1,
        bgcolor: "background.paper",
        p: 1.5,
        flexShrink: 0,
      }}
    >
      <Stack spacing={0.75}>
        <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap>
          <Chip size="small" label="Eval setup" />
          {currentStep ? (
            <Chip size="small" variant="outlined" label={currentStep} />
          ) : null}
          {stepPosition ? (
            <Chip
              size="small"
              variant="outlined"
              label={`Step ${stepPosition} of ${steps.length}`}
            />
          ) : null}
        </Stack>
        <Box>
          <Typography variant="subtitle2">{title}</Typography>
          <Typography variant="body2" color="text.secondary">
            {description}
          </Typography>
        </Box>
        {sourceSummary?.label ? (
          <Box
            data-testid="eval-onboarding-source-summary"
            sx={{
              p: 1,
              borderRadius: "6px",
              bgcolor: "action.hover",
              border: "1px solid",
              borderColor: "divider",
            }}
          >
            <Stack direction="row" spacing={0.75} alignItems="center">
              <Iconify icon="mdi:check-circle" width={16} />
              <Typography variant="caption" fontWeight={600}>
                {sourceSummary.label}
              </Typography>
            </Stack>
            {sourceSummary.description ? (
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ display: "block", mt: 0.25 }}
              >
                {sourceSummary.description}
              </Typography>
            ) : null}
          </Box>
        ) : null}
        {steps.length ? (
          <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap>
            {steps.map((step) => (
              <Chip
                key={step.label}
                size="small"
                color={step.complete ? "success" : "default"}
                variant={step.complete ? "filled" : "outlined"}
                icon={
                  step.complete ? (
                    <Iconify icon="mdi:check" width={14} />
                  ) : undefined
                }
                label={step.label}
              />
            ))}
          </Stack>
        ) : null}
        {primaryAction?.label && primaryAction?.onClick ? (
          <Button
            data-tour-anchor={primaryAction.tourAnchor || tourAnchor}
            size="small"
            variant="contained"
            onClick={primaryAction.onClick}
            disabled={!!primaryAction.disabled}
            startIcon={<Iconify icon="mdi:arrow-right" width={16} />}
            sx={{ alignSelf: "flex-start" }}
          >
            {primaryAction.label}
          </Button>
        ) : null}
      </Stack>
    </Box>
  );
}

EvalOnboardingFocusPanel.propTypes = {
  currentStep: PropTypes.string,
  description: PropTypes.string.isRequired,
  hidden: PropTypes.bool,
  onViewed: PropTypes.func,
  primaryAction: PropTypes.shape({
    disabled: PropTypes.bool,
    label: PropTypes.string.isRequired,
    onClick: PropTypes.func.isRequired,
    tourAnchor: PropTypes.string,
  }),
  sourceSummary: PropTypes.shape({
    description: PropTypes.string,
    label: PropTypes.string.isRequired,
  }),
  steps: PropTypes.arrayOf(
    PropTypes.shape({
      complete: PropTypes.bool,
      label: PropTypes.string.isRequired,
    }),
  ),
  title: PropTypes.string.isRequired,
  tourAnchor: PropTypes.string,
};
