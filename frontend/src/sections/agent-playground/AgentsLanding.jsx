import React, { Suspense } from "react";
import { Helmet } from "react-helmet-async";
import { Outlet, useMatch, useNavigate } from "react-router-dom";
import { Box, Tab, Tabs } from "@mui/material";
import { LoadingScreen } from "src/components/loading-screen";
import PageHeader from "src/components/page-header";
import CreateAgentsButton from "./components/CreateAgentsButton";
import useCanEditAgent from "./hooks/useCanEditAgent";
import { agentsTabPath, resolveAgentsTab } from "./agentsTabs";

/**
 * Agents — the ones built here in the Playground, and the ones that live
 * elsewhere and were connected to run simulations against. Each tab is its
 * own route, rendered into the Outlet.
 */
export default function AgentsLanding() {
  const navigate = useNavigate();
  const match = useMatch(`${agentsTabPath(":tab")}/*`);
  const tab = resolveAgentsTab(match?.params.tab);
  const { canCreate } = useCanEditAgent();

  return (
    <>
      <Helmet>
        <title>Agents</title>
      </Helmet>
      <Box
        sx={{
          height: "100%",
          display: "flex",
          flexDirection: "column",
          minHeight: 0,
          overflow: "hidden",
        }}
      >
        <Box sx={{ px: 3, pt: 2, flexShrink: 0 }}>
          <PageHeader
            title="Agents"
            description="Build agents in the Playground or connect the ones you already run, then test them in simulations."
            actions={<CreateAgentsButton canCreatePlayground={canCreate} />}
            sx={{ mb: 0 }}
          />
        </Box>

        <Box sx={{ flexShrink: 0, px: 3, mt: 1.5 }}>
          <Tabs
            value={tab}
            onChange={(_, v) => navigate(agentsTabPath(v))}
            sx={{
              boxShadow: "none",
              borderBottom: "1px solid",
              borderColor: "divider",
            }}
          >
            <Tab value="connected" label="Connected agents" disableRipple />
            <Tab value="playground" label="Agent Playground" disableRipple />
          </Tabs>
        </Box>

        <Box
          sx={{
            flex: 1,
            minHeight: 0,
            display: "flex",
            flexDirection: "column",
            px: 3,
          }}
        >
          {/* Hover preload usually has the tab's code ready; a refresh, deep
              link or tab switch shows the one loader until it arrives. */}
          <Suspense fallback={<LoadingScreen compact />}>
            <Outlet />
          </Suspense>
        </Box>
      </Box>
    </>
  );
}
