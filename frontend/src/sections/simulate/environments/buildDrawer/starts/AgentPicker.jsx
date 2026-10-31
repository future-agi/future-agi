import PropTypes from "prop-types";
import { useEffect, useRef } from "react";
import { Box, Button, ButtonBase, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { useConnectedAgents } from "src/api/agents/connectedAgents";
import { PANEL_SX, T12, T13, focusRing } from "../drawerStyles";
import { useConnectNewAgent } from "../connectNewAgent";
import SampleTag from "../forms/shared/SampleTag";
import { agentIconOf, agentMetaOf, uiOnlyToast } from "./startUtils";

const AGENTS_LIMIT = 100;

function AgentRow({ agent, divider, selected, onClick }) {
  return (
    <ButtonBase
      role="radio"
      aria-checked={selected}
      aria-label={agent.name}
      onClick={onClick}
      sx={{
        width: "100%",
        px: 1.5,
        py: 1,
        display: "flex",
        alignItems: "center",
        gap: 1.5,
        textAlign: "left",
        borderTop: divider ? "1px solid" : "none",
        borderColor: "divider",
        bgcolor: selected ? "action.selected" : "transparent",
        "&:hover": { bgcolor: "action.hover" },
        "&.Mui-focusVisible": focusRing,
      }}
    >
      <Iconify
        icon={agentIconOf(agent)}
        width={16}
        sx={{ color: "text.secondary", flexShrink: 0 }}
      />
      <Box flex={1} minWidth={0}>
        <Stack direction="row" alignItems="center" spacing={0.75} minWidth={0}>
          <Typography noWrap sx={{ ...T13, fontWeight: 500, minWidth: 0 }}>
            {agent.name}
          </Typography>
          {agent.sample && <SampleTag />}
        </Stack>
        <Typography
          noWrap
          sx={{
            ...T12,
            color: "text.secondary",
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {agentMetaOf(agent)}
        </Typography>
      </Box>
      <Iconify
        icon={selected ? "solar:check-circle-bold" : "solar:circle-linear"}
        width={16}
        sx={{
          color: selected ? "text.primary" : "text.disabled",
          flexShrink: 0,
        }}
      />
    </ButtonBase>
  );
}
AgentRow.propTypes = {
  agent: PropTypes.object.isRequired,
  divider: PropTypes.bool,
  selected: PropTypes.bool,
  onClick: PropTypes.func,
};

/**
 * The connected agents a world source can build for, by modality. In the
 * Build environment drawer the sample agents connected there come first.
 */
export function useWorldAgents(modality = null) {
  const { data, isLoading } = useConnectedAgents({
    page: 0,
    pageSize: AGENTS_LIMIT,
  });
  const samples = useConnectNewAgent()?.samples;
  const all = [...(samples || []), ...(data?.items || [])];
  return {
    agents: modality ? all.filter((a) => a.modality === modality) : all,
    isLoading,
  };
}

/**
 * Pick the connected agent a world source becomes an environment for. A
 * world is for one type of agent, so `modality` ("voice" or "text") limits
 * the agents offered. `agents` comes from useWorldAgents. In the Build
 * environment drawer "Connect a new agent" opens the ways to connect one of
 * that type, and the agent connected is picked; elsewhere it only says it's
 * UI only.
 */
export default function AgentPicker({
  agents,
  isLoading,
  value,
  onChange,
  title = "Agent",
  subtitle = null,
  modality = null,
}) {
  const typeWord = modality === "voice" ? "voice" : modality ? "chat" : null;
  const connectNew = useConnectNewAgent();
  // The connect layer calls back after this picker may have re-rendered.
  const onChangeRef = useRef(onChange);
  useEffect(() => {
    onChangeRef.current = onChange;
  }, [onChange]);
  const connectAgent = () => {
    if (!connectNew) {
      uiOnlyToast("connecting a new agent from here");
      return;
    }
    connectNew.requestConnect({
      modality,
      onConnected: (agent) => onChangeRef.current(agent.id),
    });
  };
  return (
    <Box>
      <Stack
        direction="row"
        alignItems="baseline"
        spacing={1}
        sx={{ mb: 0.75 }}
      >
        <Typography sx={{ ...T13, fontWeight: 600 }}>{title}</Typography>
        <Typography
          sx={{ ...T12, color: "text.secondary", flex: 1, minWidth: 0 }}
        >
          {subtitle ||
            (typeWord
              ? `Your ${typeWord} agents. This world is for ${typeWord} agents.`
              : "The environment belongs to this agent in Agents.")}
        </Typography>
        <Button
          size="small"
          onClick={connectAgent}
          startIcon={<Iconify icon="solar:add-circle-linear" width={14} />}
          sx={{ ...T12, height: 28, color: "text.secondary", flexShrink: 0 }}
        >
          Connect a new agent
        </Button>
      </Stack>
      {agents.length ? (
        <Box
          role="radiogroup"
          aria-label={title}
          sx={{ ...PANEL_SX, maxHeight: 248, overflowY: "auto" }}
        >
          {agents.map((a, i) => (
            <AgentRow
              key={a.id}
              agent={a}
              divider={i > 0}
              selected={a.id === value}
              onClick={() => onChange(a.id)}
            />
          ))}
        </Box>
      ) : (
        <Typography
          sx={{
            ...T13,
            color: "text.secondary",
            px: 1.75,
            py: 1.25,
            border: "1px dashed",
            borderColor: "divider",
            borderRadius: "6px",
          }}
        >
          {isLoading
            ? "Loading agents…"
            : typeWord
              ? `No ${typeWord} agents in Agents yet. Connect one to build its environment from this world.`
              : "No agents in Agents yet. Connect one to build its environment from this world."}
        </Typography>
      )}
    </Box>
  );
}
AgentPicker.propTypes = {
  agents: PropTypes.array.isRequired,
  isLoading: PropTypes.bool,
  value: PropTypes.string,
  onChange: PropTypes.func.isRequired,
  title: PropTypes.string,
  subtitle: PropTypes.string,
  modality: PropTypes.oneOf(["voice", "text"]),
};
