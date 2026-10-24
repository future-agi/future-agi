import React from "react";
import PropTypes from "prop-types";
import { Alert, AlertTitle, Button, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { useDeploymentMode } from "src/hooks/useDeploymentMode";

/**
 * Inline banner shown when an API action is blocked due to credit exhaustion.
 * Renders contextually where the action was attempted (not a global modal).
 *
 * Hidden entirely in OSS mode — there's no credit/billing system, and any
 * 402 in OSS is an EE-feature gate whose message is already surfaced by
 * the axios interceptor as a snackbar.
 *
 * Usage:
 *   <CreditExhaustionBanner
 *     error={exhaustionError}
 *     onUpgrade={handleUpgradeClick}
 *     onDismiss={handleDismiss}
 *   />
 */
export const CreditExhaustionBanner = ({
  error,
  onUpgrade,
  onDismiss,
  sx = {},
}) => {
  const { isCloud } = useDeploymentMode();
  if (!error || !isCloud) return null;

  const ctaText = error.upgradeCta?.text || "Upgrade your plan";

  return (
    <Alert
      severity="warning"
      icon={
        <Iconify
          icon="solar:wallet-money-bold"
          sx={{ width: 20, height: 20 }}
        />
      }
      onClose={onDismiss}
      action={
        <Button
          size="small"
          variant="outlined"
          color="warning"
          onClick={onUpgrade}
          sx={{ whiteSpace: "nowrap" }}
        >
          {ctaText}
        </Button>
      }
      sx={{
        "& .MuiAlert-action": {
          pt: 0,
          alignItems: "center",
        },
        ...sx,
      }}
    >
      <AlertTitle>AI Credits exhausted</AlertTitle>
      <Typography variant="body2" color="text.secondary">
        {error.result || "You've used all your free AI credits this month."}
      </Typography>
    </Alert>
  );
};

CreditExhaustionBanner.propTypes = {
  error: PropTypes.object,
  onUpgrade: PropTypes.func.isRequired,
  onDismiss: PropTypes.func.isRequired,
  sx: PropTypes.object,
};
