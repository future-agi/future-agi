import React from "react";
import { Helmet } from "react-helmet-async";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Box, Tab, Tabs } from "@mui/material";
import { paths } from "src/routes/paths";
import ConnectedAgentsView from "src/sections/simulate-v2/agents/ConnectedAgentsView";
import AgentView from "./AgentView";
import CreateAgentsButton from "./components/CreateAgentsButton";
import useCanEditAgent from "./hooks/useCanEditAgent";
import { useAgentImprovementFlag } from "src/sections/simulate-v2/_mock/featureFlags";
import PageHeader from "src/components/page-header";

/**
 * Agents — the ones built here in the Playground, and the ones that live
 * elsewhere and were connected to run simulations against.
 */
export default function AgentsLanding() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  /* Connected agents is where improvements start, so it opens first when
     agent improvement is on; the Playground stays one click away. */
  const improving = useAgentImprovementFlag();
  const asked = params.get("tab");
  const tab =
    asked === "connected" || asked === "playground"
      ? asked
      : improving
        ? "connected"
        : "playground";
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
            info="Build agents in the Playground, or connect agents that already run somewhere else to test them in simulations."
            actions={<CreateAgentsButton canCreatePlayground={canCreate} />}
            sx={{ mb: 0 }}
          />
        </Box>

        <Box sx={{ flexShrink: 0, px: 3, mt: 1.5 }}>
          <Tabs
            value={tab}
            onChange={(_, v) => setParams({ tab: v }, { replace: true })}
            sx={{ borderBottom: "1px solid", borderColor: "divider" }}
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
          {tab === "playground" ? (
            <AgentView embedded />
          ) : (
            <ConnectedAgentsView
              onConnect={() => navigate(`${paths.dashboard.agents}/connect`)}
            />
          )}
        </Box>
      </Box>
    </>
  );
}
