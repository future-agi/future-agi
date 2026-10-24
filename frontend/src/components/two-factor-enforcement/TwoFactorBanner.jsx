import React, { useState, useEffect, useCallback } from "react";
import { Alert, Box, Button, Typography } from "@mui/material";
import { useNavigate } from "react-router-dom";
import { paths } from "src/routes/paths";
import { useAuthContext } from "src/auth/hooks";
import Iconify from "src/components/iconify";

// ----------------------------------------------------------------------

const SESSION_STORAGE_KEY = "2fa-banner-dismissed";
const ENFORCEMENT_BLOCK_EVENT = "2fa-enforcement-block";

// ----------------------------------------------------------------------

export default function TwoFactorBanner() {
  const navigate = useNavigate();
  const { logout, user } = useAuthContext();

  const [graceEnds, setGraceEnds] = useState(null);
  const [blocked, setBlocked] = useState(false);
  const [dismissed, setDismissed] = useState(false);

  // Restore dismissed state from sessionStorage on mount
  useEffect(() => {
    if (sessionStorage.getItem(SESSION_STORAGE_KEY) === "true") {
      setDismissed(true);
    }
  }, []);

  // Check user-info for org 2FA requirement (grace period) and listen for
  // 403 enforcement-block events dispatched by the axios interceptor.
  useEffect(() => {
    const org2faRequired = user?.org_2fa_required ?? user?.org2faRequired;
    const org2faGraceEndsAt =
      user?.org_2fa_grace_ends_at ?? user?.org2faGraceEndsAt;
    if (org2faRequired && org2faGraceEndsAt) {
      const graceEnd = new Date(org2faGraceEndsAt);
      if (graceEnd > new Date()) {
        setGraceEnds(graceEnd);
      } else {
        // Grace period has already expired — clear it so the banner
        // doesn't flash before the blocking overlay appears.
        setGraceEnds(null);
      }
    }

    const handler = () => {
      setBlocked(true);
    };

    window.addEventListener(ENFORCEMENT_BLOCK_EVENT, handler);
    return () => window.removeEventListener(ENFORCEMENT_BLOCK_EVENT, handler);
  }, [user]);

  // ------------------------------------------------------------------
  // Handlers
  // ------------------------------------------------------------------

  const handleDismiss = useCallback(() => {
    setDismissed(true);
    sessionStorage.setItem(SESSION_STORAGE_KEY, "true");
  }, []);

  const handleNavigateToSecurity = useCallback(() => {
    setBlocked(false);
    navigate(paths.dashboard.settings.root);
  }, [navigate]);

  const handleLogout = useCallback(() => {
    logout();
  }, [logout]);

  // ------------------------------------------------------------------
  // Derived values
  // ------------------------------------------------------------------

  const daysRemaining = graceEnds
    ? Math.max(0, Math.ceil((graceEnds - new Date()) / (1000 * 60 * 60 * 24)))
    : null;

  // ------------------------------------------------------------------
  // Access restricted — full-screen blocking overlay
  // ------------------------------------------------------------------

  if (blocked) {
    return (
      <Box
        sx={{
          position: "fixed",
          top: 0,
          left: 0,
          right: 0,
          bottom: 0,
          bgcolor: "rgba(0, 0, 0, 0.7)",
          zIndex: 9999,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <Box
          sx={{
            bgcolor: "background.paper",
            borderRadius: "12px",
            p: 4,
            maxWidth: 480,
            textAlign: "center",
          }}
        >
          <Iconify
            icon="solar:shield-warning-bold"
            sx={{ width: 64, height: 64, color: "warning.main", mb: 2 }}
          />

          <Typography variant="h5" gutterBottom>
            Two-factor authentication required
          </Typography>

          <Typography color="text.secondary" sx={{ mb: 3 }}>
            Your organization requires all members to enable two-factor
            authentication. Please set up 2FA to continue accessing your
            organization.
          </Typography>

          <Button
            variant="contained"
            fullWidth
            onClick={handleNavigateToSecurity}
            sx={{ mb: 1 }}
          >
            Set up 2FA
          </Button>

          <Button
            variant="text"
            color="inherit"
            fullWidth
            onClick={handleLogout}
          >
            Log out
          </Button>
        </Box>
      </Box>
    );
  }

  // ------------------------------------------------------------------
  // Grace period banner — dismissible warning
  // ------------------------------------------------------------------

  if (
    graceEnds &&
    !dismissed &&
    !(user?.has_2fa_enabled ?? user?.has2faEnabled)
  ) {
    return (
      <Alert
        severity="warning"
        onClose={handleDismiss}
        sx={{
          "& .MuiAlert-message": {
            width: "100%",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
          },
        }}
      >
        <Typography variant="body2">
          Your organization requires two-factor authentication. You have{" "}
          <strong>
            {daysRemaining} day{daysRemaining !== 1 ? "s" : ""}
          </strong>{" "}
          to enable 2FA before your access is restricted.
        </Typography>

        <Button
          size="small"
          variant="outlined"
          color="warning"
          onClick={() => navigate(paths.dashboard.settings.root)}
          sx={{ ml: 2, whiteSpace: "nowrap" }}
        >
          Set up 2FA now
        </Button>
      </Alert>
    );
  }

  // ------------------------------------------------------------------
  // Nothing to show
  // ------------------------------------------------------------------

  return null;
}
