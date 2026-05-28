import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import Iconify from "src/components/iconify";

export default function GatewayOnboardingFocusPanel({
  blocker,
  currentStep,
  description,
  hidden = false,
  primaryAction,
  secondaryAction,
  steps = [],
  title,
}) {
  if (hidden) {
    return null;
  }

  return (
    <Box
      data-testid="gateway-onboarding-focus"
      sx={{
        mb: 3,
        border: "1px solid",
        borderColor: "primary.main",
        borderRadius: 1,
        bgcolor: "background.paper",
        p: 1.5,
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
            <Chip size="small" label="Gateway onboarding" />
            {currentStep ? (
              <Chip size="small" variant="outlined" label={currentStep} />
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
          {secondaryAction ? (
            <Button
              variant="outlined"
              onClick={secondaryAction.onClick}
              disabled={secondaryAction.disabled}
              startIcon={<Iconify icon="mdi:arrow-right" width={18} />}
            >
              {secondaryAction.label}
            </Button>
          ) : null}
          {primaryAction ? (
            <Button
              variant="contained"
              onClick={primaryAction.onClick}
              disabled={primaryAction.disabled}
              startIcon={<Iconify icon="mdi:arrow-right" width={18} />}
            >
              {primaryAction.label}
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
});

GatewayOnboardingFocusPanel.propTypes = {
  blocker: PropTypes.string,
  currentStep: PropTypes.string,
  description: PropTypes.string.isRequired,
  hidden: PropTypes.bool,
  primaryAction: actionShape,
  secondaryAction: actionShape,
  steps: PropTypes.arrayOf(
    PropTypes.shape({
      complete: PropTypes.bool,
      label: PropTypes.string.isRequired,
    }),
  ),
  title: PropTypes.string.isRequired,
};
