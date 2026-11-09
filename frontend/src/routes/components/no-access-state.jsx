import React from "react";
import PropTypes from "prop-types";
import Button from "@mui/material/Button";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import Iconify from "src/components/iconify";
import RouterLink from "./router-link";
import { describeNeededRoles, describeRole } from "./no-access-roles";

// One state for a settings URL the signed-in user's role may not open. It
// names the role the page needs and the user's own, and offers Profile, which
// every role can open. It replaces a silent redirect to an unrelated page.

export default function NoAccessState({
  allowedRoles,
  orgRole,
  workspaceRole,
}) {
  const needed = describeNeededRoles(allowedRoles);
  const yours = describeRole({ orgRole, workspaceRole });
  return (
    <Stack
      data-testid="settings-no-access"
      alignItems="center"
      justifyContent="center"
      spacing={2}
      sx={{ minHeight: "60vh", px: 3, textAlign: "center" }}
    >
      <Iconify
        icon="solar:lock-keyhole-minimalistic-linear"
        width={48}
        sx={{ color: "text.secondary" }}
      />
      <Typography variant="h5" component="h1">
        You don&apos;t have access to this page
      </Typography>
      {needed && (
        <Typography
          variant="body2"
          color="text.secondary"
          sx={{ maxWidth: 480 }}
        >
          This page needs {needed}.{yours ? ` You are ${yours}.` : ""} Ask an
          organization Owner or Admin if you need access.
        </Typography>
      )}
      <Button
        component={RouterLink}
        href="/dashboard/settings/profile-settings"
        variant="outlined"
        color="inherit"
      >
        Go to Profile
      </Button>
    </Stack>
  );
}

NoAccessState.propTypes = {
  allowedRoles: PropTypes.arrayOf(PropTypes.string),
  orgRole: PropTypes.string,
  workspaceRole: PropTypes.string,
};
