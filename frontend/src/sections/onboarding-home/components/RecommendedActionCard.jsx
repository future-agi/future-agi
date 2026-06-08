import React from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import Iconify from "src/components/iconify";
import Link from "@mui/material/Link";
import { RouterLink } from "src/routes/components";
import { readableToken } from "../onboarding-home.constants";
import { hasPathSampleFixture } from "./path-sample-fixtures";

// When the recommended step is blocked or no action is available, the only
// honest forward move is one that never depends on the same activation state
// that just dead-ended. The goal picker is not a route (it only renders while
// the backend reports the "choose_goal" stage), and the legacy Get Started page
// is deliberately off-limits, so reloading Home would strand the user.
//
// For a non-Observe path that ships an in-app sample fixture, the honest
// primary recovery is that sample: it lives on Home, never depends on the
// dead-ended activation state, and shows the value before any real setup (doc
// 08 empty-state model - Primary: connect / explore sample; Secondary: docs).
// When there is no in-app sample, the product documentation remains the
// always-valid forward surface: it works for every path, explains the setup the
// user is stuck on, and opens in a new tab so the onboarding context stays put.
const RECOVERY_DESTINATION = {
  href: "https://docs.futureagi.com/docs",
  label: "View setup guide",
};

const actionHref = (action) => {
  if (!action || action.blocked || !action.routeAvailable || !action.href) {
    return null;
  }
  return action.href;
};

// `demoted` renders the docs as a tertiary text link so it sits below a higher
// priority recovery (the in-app sample) without competing for the eye.
const RecoveryAction = ({ demoted = false }) =>
  demoted ? (
    <Link
      component="a"
      href={RECOVERY_DESTINATION.href}
      target="_blank"
      rel="noopener noreferrer"
      variant="body2"
      color="text.secondary"
      sx={{
        alignSelf: "flex-start",
        display: "inline-flex",
        alignItems: "center",
        gap: 0.5,
      }}
    >
      <Iconify icon="mdi:open-in-new" width={16} />
      {RECOVERY_DESTINATION.label}
    </Link>
  ) : (
    <Button
      variant="outlined"
      component="a"
      href={RECOVERY_DESTINATION.href}
      target="_blank"
      rel="noopener noreferrer"
      startIcon={<Iconify icon="mdi:open-in-new" width={18} />}
      sx={{ alignSelf: "flex-start" }}
    >
      {RECOVERY_DESTINATION.label}
    </Button>
  );

RecoveryAction.propTypes = {
  demoted: PropTypes.bool,
};

// A blocked non-Observe path with an in-app sample fixture: make the sample the
// primary recovery. This only reveals/scrolls to the already-rendered Home
// preview - it never completes or advances setup.
const SampleRecoveryAction = ({ onShowSample }) => (
  <Button
    variant="contained"
    onClick={onShowSample}
    startIcon={<Iconify icon="mdi:eye-outline" width={18} />}
    sx={{ alignSelf: "flex-start" }}
  >
    See a sample instead
  </Button>
);

SampleRecoveryAction.propTypes = {
  onShowSample: PropTypes.func,
};

const blockedReasonLabel = (reason) => {
  if (reason === "route_not_implemented")
    return "This setup step is not ready yet.";
  if (reason === "feature_disabled") return "This setup step is disabled.";
  if (reason === "permission_limited")
    return "You need workspace write access.";
  return readableToken(reason);
};

export default function RecommendedActionCard({
  action,
  label,
  variant = "primary",
  onActionClick,
  primaryPath,
  onShowSample,
}) {
  const href = actionHref(action);
  // A blocked action plus an in-app sample for this path means the sample is
  // the honest primary recovery and the external docs link gets demoted.
  const sampleRecoveryAvailable = Boolean(
    action?.blocked &&
      onShowSample &&
      primaryPath &&
      hasPathSampleFixture(primaryPath),
  );

  if (!action) {
    return (
      <Box
        data-testid={`onboarding-${variant}-action-empty`}
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
          p: 2,
          minHeight: 148,
        }}
      >
        <Stack spacing={1.25}>
          <Stack spacing={0.5}>
            <Typography variant="subtitle2">{label}</Typography>
            <Typography variant="body2" color="text.secondary">
              No action is available right now. The setup guide walks you
              through every path so you can keep going.
            </Typography>
          </Stack>
          <RecoveryAction />
        </Stack>
      </Box>
    );
  }

  return (
    <Box
      data-testid={`onboarding-${variant}-action`}
      sx={{
        border: "1px solid",
        borderColor: variant === "primary" ? "primary.main" : "divider",
        borderRadius: 1,
        p: 2,
        minHeight: 188,
        bgcolor: "background.paper",
      }}
    >
      <Stack spacing={1.25}>
        <Stack direction="row" justifyContent="space-between" gap={1}>
          <Typography variant="subtitle2">{label}</Typography>
          <Stack direction="row" spacing={0.75} flexWrap="wrap">
            <Chip
              size="small"
              label={readableToken(action.kind)}
              sx={{ textTransform: "capitalize" }}
            />
            {action.estimatedMinutes ? (
              <Chip
                size="small"
                variant="outlined"
                label={`${action.estimatedMinutes} min`}
              />
            ) : null}
            {action.isSample ? (
              <Chip size="small" variant="outlined" label="Sample" />
            ) : null}
          </Stack>
        </Stack>
        <Stack spacing={0.5}>
          <Typography variant="h6">{action.title}</Typography>
          <Typography variant="body2" color="text.secondary">
            {action.description}
          </Typography>
        </Stack>
        {action.blocked ? (
          <Alert severity="info" sx={{ borderRadius: 1 }}>
            {blockedReasonLabel(action.blockedReason)}
          </Alert>
        ) : null}
        <Button
          variant={variant === "primary" ? "contained" : "outlined"}
          component={href ? RouterLink : "button"}
          href={href || undefined}
          disabled={!href}
          onClick={() => onActionClick?.(action)}
          startIcon={<Iconify icon="mdi:arrow-right" width={18} />}
          sx={{ alignSelf: "flex-start" }}
        >
          {action.ctaLabel || "Open"}
        </Button>
        {sampleRecoveryAvailable ? (
          <Stack spacing={0.75} sx={{ alignItems: "flex-start" }}>
            <SampleRecoveryAction onShowSample={onShowSample} />
            <RecoveryAction demoted />
          </Stack>
        ) : action.blocked ? (
          <RecoveryAction />
        ) : null}
      </Stack>
    </Box>
  );
}

RecommendedActionCard.propTypes = {
  action: PropTypes.object,
  label: PropTypes.string.isRequired,
  onActionClick: PropTypes.func,
  onShowSample: PropTypes.func,
  primaryPath: PropTypes.string,
  variant: PropTypes.oneOf(["primary", "fallback"]),
};
