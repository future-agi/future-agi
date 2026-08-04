import React, { useMemo } from "react";
import PropTypes from "prop-types";
import { Box, useTheme, CircularProgress } from "@mui/material";
import { Outlet } from "react-router";
import { useAuthContext } from "src/auth/hooks";
import { useDeploymentMode } from "src/hooks/useDeploymentMode";
import SvgColor from "src/components/svg-color";

const icon = (name) => (
  <SvgColor
    src={`/assets/icons/settings/${name}.svg`}
    sx={{ width: "20px", height: "20px" }}
  />
);

const ICONS = {
  Summary: icon("usageSummary"),
  Management: icon("userManagement"),
  Keys: icon("APIKeys"),
  Providers: icon("AIProviders"),
  Integrations: icon("Integrations"),
  MCPServer: icon("MCPServer"),
  Pricing: icon("plansPricing"),
  Billing: icon("Billing"),
  Profile: icon("Profile"),
  Security: icon("Security"),
};

// Loading component for tab content
const TabContentLoader = () => (
  <Box
    sx={{
      display: "flex",
      justifyContent: "center",
      alignItems: "center",
      height: "200px",
      backgroundColor: "background.paper",
    }}
  >
    <CircularProgress />
  </Box>
);

// Error boundary component
const TabErrorBoundary = ({ children }) => {
  return (
    <React.Suspense fallback={<TabContentLoader />}>{children}</React.Suspense>
  );
};

TabErrorBoundary.propTypes = {
  children: PropTypes.node.isRequired,
};

const SettingsLayout = React.memo(() => {
  const theme = useTheme();
  const { user } = useAuthContext();
  const { isOSS } = useDeploymentMode();
  const userOrgRole = user?.organization_role;
  const isOwner = userOrgRole === "Owner";
  const isAdmin = userOrgRole === "Admin";
  const isOwnerOrAdmin = isOwner || isAdmin;

  // Define tabs configuration - memoized to prevent recreation
  const _tabs = useMemo(() => {
    const allTabs = [
      // Administration items - only shown to owners
      ...(isOwner
        ? [
            ...(isOSS
              ? []
              : [
                  {
                    path: "/dashboard/settings/usage-summary",
                    title: "Usage Summary",
                    icon: ICONS.Summary,
                  },
                ]),
            {
              path: "/dashboard/settings/user-management",
              title: "User Management",
              icon: ICONS.Management,
            },
            {
              path: "/dashboard/settings/ai-providers",
              title: "AI providers",
              icon: ICONS.Providers,
            },
            // {
            //   path: "/dashboard/settings/api_keys",
            //   title: "API Keys",
            //   icon: ICONS.Keys,
            // },
            // {
            //   path: "/dashboard/settings/custom-model",
            //   title: "AI providers",
            //   icon: ICONS.Providers,
            // },
            {
              path: "/dashboard/settings/pricing",
              title: "Plans & Pricing",
              icon: ICONS.Pricing,
            },
            {
              path: "/dashboard/settings/billing",
              title: "Billing",
              icon: ICONS.Billing,
            },
            // {
            //   path: "/dashboard/settings/ee-licenses",
            //   title: "EE Licenses",
            //   icon: ICONS.Keys,
            // },
          ]
        : []),
      // Integrations - shown to owners and admins
      ...(isOwnerOrAdmin
        ? [
            {
              path: "/dashboard/settings/integrations",
              title: "Integrations",
              icon: ICONS.Integrations,
            },
            {
              path: "/dashboard/settings/mcp-server",
              title: "MCP Server",
              icon: ICONS.MCPServer,
            },
          ]
        : []),
      // Organization - Owner/Admin only
      ...(isOwnerOrAdmin
        ? [
            {
              path: "/dashboard/settings/org-settings",
              title: "Org Settings",
              icon: ICONS.Security,
            },
          ]
        : []),
      // User items - shown to all
      {
        path: "/dashboard/settings/profile-settings",
        title: "Profile",
        icon: ICONS.Profile,
      },
    ];
    return allTabs;
  }, [isOwner, isOwnerOrAdmin, isOSS]);

  // Memoized styles to prevent recreation
  const containerStyles = useMemo(
    () => ({
      display: "flex",
      flexDirection: "column",
      height: "100vh",
      backgroundColor: "background.paper",
    }),
    [],
  );

  const contentStyles = useMemo(
    () => ({
      flex: 1,
      overflow: "auto",
      backgroundColor: "background.paper",
      padding: theme.spacing(2),
    }),
    [theme],
  );

  return (
    <Box sx={containerStyles}>
      {/* <Paper sx={navPaperStyles}>
        <PricingNav
          tabs={tabs}
          currentTab={currentTab}
          onTabChange={handleTabChange}
        />
      </Paper> */}

      <Box sx={contentStyles}>
        <TabErrorBoundary>
          <Outlet />
        </TabErrorBoundary>
      </Box>
    </Box>
  );
});

SettingsLayout.displayName = "SettingsLayout";

export default SettingsLayout;
