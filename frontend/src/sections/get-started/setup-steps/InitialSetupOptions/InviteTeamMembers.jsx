import { Box } from "@mui/material";
import React from "react";
import HeaderContent from "./HeaderContent";
import SetupOrganization from "src/sections/auth/jwt/setup-org";

const InviteTeamMembers = () => {
  return (
    <Box>
      <HeaderContent
        title="Invite team members"
        description="This tour will guide you through the key features and functionalities"
      />
      <Box sx={{ height: "355px" }}>
        <SetupOrganization getStarted={true} />
      </Box>
    </Box>
  );
};

export default InviteTeamMembers;
