import React from "react";
import { Helmet } from "react-helmet-async";
import { Box } from "@mui/material";
import IntegrationsList from "src/sections/settings/integrations/IntegrationsList";
import IntegrationsWorkspaceScope from "src/sections/settings/integrations/IntegrationsWorkspaceScope";

// The workspace's Integrations page is the Integrations page, with its own
// heading, for the workspace in its URL: it lists, adds and changes that
// workspace's connections, whichever workspace is current.
export default function WorkspaceIntegrations() {
  return (
    <>
      <Helmet>
        <title>Workspace Integrations</title>
      </Helmet>
      <Box sx={{ px: "2px" }}>
        <IntegrationsWorkspaceScope>
          <IntegrationsList />
        </IntegrationsWorkspaceScope>
      </Box>
    </>
  );
}
