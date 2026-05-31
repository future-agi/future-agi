import React from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import Iconify from "src/components/iconify";
import { RouterLink } from "src/routes/components";
import { paths } from "src/routes/paths";

const defaultFallbackAction = {
  description: "The existing setup checklist is still available.",
  href: paths.dashboard.getstarted,
  label: "Get Started",
  title: "Open Get Started instead",
};

export default function OnboardingHomeError({
  error,
  fallbackAction,
  onRetry,
}) {
  const message =
    error?.result?.message ||
    error?.message ||
    "Home could not load right now.";
  const action = fallbackAction || defaultFallbackAction;

  return (
    <Box
      data-testid="onboarding-home-error"
      sx={{
        width: "100%",
        minHeight: "calc(100vh - 120px)",
        bgcolor: "background.paper",
        p: { xs: 2, md: 3 },
      }}
    >
      <Stack spacing={2} sx={{ maxWidth: 720, mx: "auto" }}>
        <Alert severity="warning" sx={{ borderRadius: 1 }}>
          {message}
        </Alert>
        <Stack spacing={0.75}>
          <Typography variant="h4">{action.title}</Typography>
          <Typography variant="body2" color="text.secondary">
            {action.description}
          </Typography>
        </Stack>
        <Stack direction={{ xs: "column", sm: "row" }} spacing={1.25}>
          <Button
            variant="contained"
            onClick={onRetry}
            startIcon={<Iconify icon="mdi:refresh" width={18} />}
          >
            Retry
          </Button>
          <Button
            variant="outlined"
            component={RouterLink}
            href={action.href}
            startIcon={<Iconify icon="mdi:arrow-right" width={18} />}
          >
            {action.label}
          </Button>
        </Stack>
      </Stack>
    </Box>
  );
}

OnboardingHomeError.propTypes = {
  error: PropTypes.object,
  fallbackAction: PropTypes.shape({
    description: PropTypes.string.isRequired,
    href: PropTypes.string.isRequired,
    label: PropTypes.string.isRequired,
    title: PropTypes.string.isRequired,
  }),
  onRetry: PropTypes.func,
};
