import React, { useEffect } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Chip from "@mui/material/Chip";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import Iconify from "src/components/iconify";

export default function EvalOnboardingFocusPanel({
  currentStep,
  description,
  hidden = false,
  onViewed,
  sourceSummary = null,
  steps = [],
  title,
}) {
  useEffect(() => {
    if (!hidden) {
      onViewed?.();
    }
  }, [hidden, onViewed]);

  if (hidden) return null;

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
          <Chip size="small" label="Eval onboarding" />
          {currentStep ? (
            <Chip size="small" variant="outlined" label={currentStep} />
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
      </Stack>
    </Box>
  );
}

EvalOnboardingFocusPanel.propTypes = {
  currentStep: PropTypes.string,
  description: PropTypes.string.isRequired,
  hidden: PropTypes.bool,
  onViewed: PropTypes.func,
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
};
