import { Box, Button, Typography } from "@mui/material";
import React from "react";
import HeaderContent from "./HeaderContent";
import SetupOrganization from "src/sections/auth/jwt/setup-org";
import { useDeploymentMode } from "src/hooks/useDeploymentMode";
import { RouterLink } from "src/routes/components";
import { paths } from "src/routes/paths";
import {
  useInviteAccess,
  useWorkspaceMembersPage,
} from "../../useInviteAccess";

const INVITE_ON_MEMBERS = "Invite teammates from Settings > Members.";
const ASK_TO_INVITE =
  "Ask an owner or admin of your organization to invite teammates from Settings > Members.";

const InviteTeamMembers = () => {
  // The same reading as the invite form (setup-org.jsx): only a confirmed oss
  // edition leaves it with no invite fields, so there this step points to
  // the Members page instead of showing a lone "Continue".
  const { isOSS, isSuccess } = useDeploymentMode();
  const invitesHere = !(isSuccess && isOSS);
  // The form sends a request only the organization Owner may send, so it is
  // the Owner's only. Anyone else who can invite is sent where they can, and
  // never told to ask someone else: the organization Members page when its
  // route lets them in, else their workspace's Members page when that route
  // does, each with its link, else the switcher's own "Invite members".
  // Anyone who cannot invite is told who can, with the link when the
  // Members route lets them in.
  const { isOwner, canInvite, opensMembers } = useInviteAccess();
  const showsForm = isOwner && invitesHere;
  const toMembers = isOwner || (canInvite && opensMembers);
  const workspaceMembers = useWorkspaceMembersPage({
    enabled: canInvite && !toMembers,
  });
  let line = ASK_TO_INVITE;
  let link = opensMembers ? paths.dashboard.settings.manageteam : null;
  if (toMembers) {
    line = INVITE_ON_MEMBERS;
  } else if (canInvite && workspaceMembers.isLoading) {
    // Until the workspace list answers, no line rather than a wrong one.
    line = null;
    link = null;
  } else if (canInvite && workspaceMembers.admits) {
    line = `Invite teammates from Settings > ${workspaceMembers.name} > Members.`;
    link = workspaceMembers.path;
  } else if (canInvite) {
    line = "Invite teammates from the workspace switcher > Invite members.";
    link = null;
  }

  return (
    <Box>
      <HeaderContent
        title="Invite team members"
        description="Invite teammates to your organization so they can sign in and work with you."
      />
      {showsForm ? (
        <Box sx={{ height: "355px" }}>
          <SetupOrganization getStarted={true} />
        </Box>
      ) : (
        <Box
          sx={{
            marginTop: "20px",
            display: "flex",
            flexDirection: "column",
            alignItems: "flex-start",
            gap: "12px",
          }}
        >
          <Typography
            variant="s1"
            fontWeight={"fontWeightRegular"}
            color="text.primary"
          >
            {line}
          </Typography>
          {link && (
            <Button
              component={RouterLink}
              href={link}
              variant="outlined"
              color="primary"
              size="small"
            >
              Go to Members
            </Button>
          )}
        </Box>
      )}
    </Box>
  );
};

export default InviteTeamMembers;
