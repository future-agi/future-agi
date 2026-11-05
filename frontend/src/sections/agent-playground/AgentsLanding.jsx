import React, { Suspense } from "react";
import { Helmet } from "react-helmet-async";
import { Outlet, useMatch, useNavigate } from "react-router-dom";
import { Box, CircularProgress, Stack, Tab, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { SegmentedTabs } from "src/components/tabs/tabs";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
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
          p: 2,
          gap: 1.5,
          minHeight: 0,
          overflow: "hidden",
        }}
      >
        <Stack direction="row" alignItems="center" spacing={0.75}>
          <Typography
            typography="m2"
            fontWeight="fontWeightMedium"
            color="text.primary"
          >
            Agents
          </Typography>
          <CustomTooltip
            show
            arrow
            size="small"
            title="Build agents in the Playground, or connect agents that already run somewhere else to test them in simulations."
          >
            <Box sx={{ display: "flex", color: "text.subtitle" }}>
              <Iconify icon="solar:info-circle-linear" width={16} />
            </Box>
          </CustomTooltip>
        </Stack>

        <Stack
          direction="row"
          alignItems="center"
          justifyContent="space-between"
        >
          <SegmentedTabs
            value={tab}
            onChange={(_, v) => navigate(agentsTabPath(v))}
          >
            <Tab value="connected" label="Connected agents" disableRipple />
            <Tab value="playground" label="Agent Playground" disableRipple />
          </SegmentedTabs>
          <CreateAgentsButton canCreatePlayground={canCreate} />
        </Stack>

        <Box
          sx={{
            flex: 1,
            minHeight: 0,
            display: "flex",
            flexDirection: "column",
          }}
        >
          <Suspense
            fallback={
              <Box sx={{ display: "flex", justifyContent: "center", p: 4 }}>
                <CircularProgress size={24} />
              </Box>
            }
          >
            <Outlet />
          </Suspense>
        </Box>
      </Box>
    </>
  );
}
