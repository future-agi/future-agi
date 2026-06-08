import React from "react";
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

export default function TestOnboardingFocusPanel({
  blocker,
  currentStep,
  description,
  eyebrow = "Eval setup",
  hidden = false,
  primaryAction,
  secondaryAction,
  singleActionFocus = false,
  steps = [],
  sx,
  title,
  tourAnchor,
}) {
  if (hidden) {
    return null;
  }

  const stepPosition = currentStepPosition({ currentStep, steps });

  return (
    <Box
      data-testid="test-onboarding-focus"
      sx={{
        mb: 2,
        border: "1px solid",
        borderColor: "primary.main",
        borderRadius: 1,
        bgcolor: "background.paper",
        p: 1.5,
        ...sx,
      }}
    >
      <Stack
        direction={{ xs: "column", md: "row" }}
        spacing={1.5}
        justifyContent="space-between"
        alignItems={{ xs: "flex-start", md: "center" }}
      >
        <Stack spacing={0.75} sx={{ minWidth: 0 }}>
          <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap>
            <Chip size="small" label={eyebrow} />
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
            {blocker ? (
              <Chip size="small" color="warning" label={blocker} />
            ) : null}
          </Stack>
          <Box>
            <Typography variant="subtitle2">{title}</Typography>
            <Typography variant="body2" color="text.secondary">
              {description}
            </Typography>
          </Box>
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
        </Stack>
        <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
          {primaryAction ? (
            <Button
              data-tour-anchor={primaryAction.tourAnchor || tourAnchor}
              variant="contained"
              onClick={primaryAction.onClick}
              disabled={primaryAction.disabled}
              startIcon={<Iconify icon="mdi:arrow-right" width={18} />}
            >
              {primaryAction.label}
            </Button>
          ) : null}
          {secondaryAction && !singleActionFocus ? (
            <Button
              variant="outlined"
              onClick={secondaryAction.onClick}
              disabled={secondaryAction.disabled}
              startIcon={<Iconify icon="mdi:arrow-right" width={18} />}
            >
              {secondaryAction.label}
            </Button>
          ) : null}
        </Stack>
      </Stack>
    </Box>
  );
}

const actionShape = PropTypes.shape({
  disabled: PropTypes.bool,
  label: PropTypes.string.isRequired,
  onClick: PropTypes.func.isRequired,
  tourAnchor: PropTypes.string,
});

TestOnboardingFocusPanel.propTypes = {
  blocker: PropTypes.string,
  currentStep: PropTypes.string,
  description: PropTypes.string.isRequired,
  eyebrow: PropTypes.string,
  hidden: PropTypes.bool,
  primaryAction: actionShape,
  secondaryAction: actionShape,
  singleActionFocus: PropTypes.bool,
  steps: PropTypes.arrayOf(
    PropTypes.shape({
      complete: PropTypes.bool,
      label: PropTypes.string.isRequired,
    }),
  ),
  sx: PropTypes.object,
  title: PropTypes.string.isRequired,
  tourAnchor: PropTypes.string,
};
