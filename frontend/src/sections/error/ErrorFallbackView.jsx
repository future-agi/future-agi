import { Box, Button, Link, Stack, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import Iconify from "src/components/iconify";

// The app-wide error fallback: what happened, in plain words, and two ways
// out. Retry resets the error boundary (the app's boundary also reloads the
// page); rendered without one, Retry reloads. Go home is a full page load,
// which clears whatever state broke the page.
const ErrorFallbackView = ({ resetErrorBoundary }) => {
  const handleRetry = () => {
    if (resetErrorBoundary) {
      resetErrorBoundary();
    } else {
      window.location.reload();
    }
  };

  return (
    <Box
      display="flex"
      justifyContent="center"
      alignItems="center"
      minHeight="100vh"
      px={3}
      bgcolor="background.default"
    >
      <Stack
        alignItems="center"
        spacing={2}
        sx={{ maxWidth: 480, textAlign: "center" }}
      >
        <Iconify
          icon="solar:danger-triangle-linear"
          width={48}
          sx={{ color: "text.secondary" }}
        />
        <Typography variant="h5" component="h1" color="text.primary">
          Something went wrong
        </Typography>
        <Typography variant="body2" color="text.secondary">
          This page hit an unexpected error and could not be shown. Retry, or go
          back to the home page. If it keeps happening, contact{" "}
          <Link href="mailto:support@futureagi.com" underline="hover">
            support@futureagi.com
          </Link>
          .
        </Typography>
        <Stack
          direction="row"
          spacing={1.5}
          justifyContent="center"
          flexWrap="wrap"
          useFlexGap
        >
          <Button variant="contained" color="primary" onClick={handleRetry}>
            Retry
          </Button>
          <Button variant="outlined" color="inherit" href="/dashboard/develop">
            Go home
          </Button>
        </Stack>
      </Stack>
    </Box>
  );
};

export default ErrorFallbackView;

ErrorFallbackView.propTypes = {
  error: PropTypes.any,
  resetErrorBoundary: PropTypes.func,
};
