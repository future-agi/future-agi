import { useState } from "react";
import { Box, Stack } from "@mui/material";
import {
  DEFAULT_SCENARIOS,
  isValidScenarioCount,
} from "../../panels/scenarioCountRules";
import {
  Field,
  HintLine,
  ScenarioRow,
  SegmentedToggle,
} from "../forms/formBits";
import AgentPicker, { useWorldAgents } from "./AgentPicker";
import { StartFooter, TagChip } from "./startBits";
import { uiOnlyToast } from "./startUtils";

// Sample Gateway servers: the Gateway's MCP list isn't read here yet.
const SAMPLE_GATEWAY = [
  { id: "shopify", label: "Shopify", status: "connected", toolCount: 6 },
  { id: "stripe", label: "Stripe", status: "connected", toolCount: 5 },
  { id: "zendesk", label: "Zendesk", status: "connected", toolCount: 4 },
  { id: "linear", label: "Linear", status: "error", toolCount: 0 },
];

const TRANSPORTS = [
  { id: "http", name: "HTTP", icon: "solar:global-linear" },
  { id: "sse", name: "SSE", icon: "solar:transfer-vertical-linear" },
  {
    id: "stdio",
    name: "stdio",
    icon: "solar:code-square-linear",
    disabled: true,
    tag: "On your machine only",
  },
];

/**
 * An MCP server's tools as an agent's world: the agent is picked first, then
 * the server, from the Gateway or by URL. UI only: the Gateway servers are
 * samples and building from a server isn't wired yet.
 */
export default function McpForm() {
  const { agents, isLoading } = useWorldAgents();
  const [agentId, setAgentId] = useState(null);
  const agent = agents.find((a) => a.id === agentId) || null;
  const [source, setSource] = useState("gateway");
  const [gwPick, setGwPick] = useState(null);
  const [transport, setTransport] = useState("http");
  const [target, setTarget] = useState("");
  const [token, setToken] = useState("");
  const [scenarioCount, setScenarioCount] = useState(DEFAULT_SCENARIOS);
  const countOk = isValidScenarioCount(scenarioCount);
  const canGo =
    !!agent && countOk && (source === "gateway" ? !!gwPick : !!target.trim());

  return (
    <Box>
      <Stack spacing={1.75} sx={{ p: 2.5 }}>
        <AgentPicker
          agents={agents}
          isLoading={isLoading}
          value={agentId}
          onChange={setAgentId}
          subtitle="The server's tools become the world of this agent's environment."
        />
        <SegmentedToggle
          label="Where the server is"
          value={source}
          onChange={setSource}
          options={[
            {
              value: "gateway",
              label: `From your Gateway · ${SAMPLE_GATEWAY.length}`,
            },
            { value: "url", label: "Another server" },
          ]}
        />
        {source === "gateway" ? (
          <Stack
            direction="row"
            spacing={0.75}
            flexWrap="wrap"
            useFlexGap
            aria-label="Gateway servers"
          >
            {SAMPLE_GATEWAY.map((g) => (
              <TagChip
                key={g.id}
                icon="solar:plug-circle-linear"
                label={g.label}
                on={gwPick === g.id}
                onClick={() => setGwPick(g.id)}
                tag={
                  g.status === "connected"
                    ? `${g.toolCount} tools · Sample`
                    : "Unreachable"
                }
                disabled={g.status !== "connected"}
              />
            ))}
          </Stack>
        ) : (
          <>
            <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap>
              {TRANSPORTS.map((t) => (
                <TagChip
                  key={t.id}
                  icon={t.icon}
                  label={t.name}
                  on={transport === t.id}
                  onClick={() => setTransport(t.id)}
                  tag={t.tag}
                  disabled={t.disabled}
                />
              ))}
            </Stack>
            <Field
              label="Server URL"
              required
              mono
              placeholder="https://mcp.yourapp.com/sse"
              value={target}
              onChange={setTarget}
              helper={
                <HintLine
                  text="Its tools become the world the agent acts on."
                  hint="We build the world from the server's tools. A stdio server only works on your machine."
                />
              }
            />
            <Field
              label="Auth token (optional)"
              mono
              type="password"
              placeholder="Paste the token, or vault://acme/sim/mcp_server_token"
              value={token}
              onChange={setToken}
              helper="Stored encrypted and used only by Future AGI. Never shown again, never sent to the improver."
            />
          </>
        )}
        <ScenarioRow value={scenarioCount} onChange={setScenarioCount} />
      </Stack>
      <StartFooter
        disabled={!canGo}
        hint={
          !agent
            ? "Pick the agent this environment is for"
            : source === "gateway" && !gwPick
              ? "Pick a server from your Gateway"
              : source === "url" && !target.trim()
                ? "Add the server"
                : "Scenarios to generate: 1 to 1,000"
        }
        onClick={() => uiOnlyToast("building from an MCP server")}
      />
    </Box>
  );
}
