import PropTypes from "prop-types";
import { useEffect, useRef, useState } from "react";
import { useConnectedAgents } from "src/api/agents/connectedAgents";
import BuildEnvironmentDialog from "src/sections/agent-playground/connected/detail/BuildEnvironmentDialog";
import { agentIconOf, agentMetaOf } from "./starts/startUtils";
import Iconify from "src/components/iconify";
import { BTN_SX, PANEL_SX, T12, T13, chipSx, focusRing } from "./drawerStyles";
import { Box, Button, ButtonBase, Stack, Typography } from "@mui/material";
import ConnectSection from "./ConnectSection";
import { SectionHead } from "./starts/startBits";

const AGENTS_LIMIT = 100;
const AGENT_TYPES = [
  { id: "all", label: "All types" },
  { id: "voice", label: "Voice", icon: "solar:phone-calling-rounded-linear" },
  { id: "text", label: "Chat", icon: "solar:chat-round-linear" },
];
// Two rows of three before "Show all".
const FIRST_SHOWN = 6;

// One agent: the whole tile opens Build environment for it.
function AgentTile({ agent, onClick }) {
  return (
    <Box component="li" aria-label={agent.name} sx={{ minWidth: 0 }}>
      <ButtonBase
        onClick={onClick}
        aria-label={`Build environment for ${agent.name}`}
        sx={{
          width: "100%",
          minWidth: 0,
          px: 1.5,
          py: 1,
          borderRadius: "6px",
          display: "flex",
          alignItems: "center",
          gap: 1.25,
          textAlign: "left",
          "&:hover": { bgcolor: "action.hover" },
          "&:hover .agent-go, &.Mui-focusVisible .agent-go": { opacity: 1 },
          "&.Mui-focusVisible": focusRing,
        }}
      >
        <Iconify
          icon={agentIconOf(agent)}
          width={16}
          sx={{ color: "text.secondary", flexShrink: 0 }}
        />
        <Box flex={1} minWidth={0}>
          <Typography noWrap sx={{ ...T13, fontWeight: 500 }}>
            {agent.name}
          </Typography>
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
          className="agent-go"
          icon="solar:arrow-right-linear"
          width={14}
          sx={{
            color: "text.secondary",
            flexShrink: 0,
            opacity: 0,
            transition: "opacity .12s ease",
          }}
        />
      </ButtonBase>
    </Box>
  );
}
AgentTile.propTypes = {
  agent: PropTypes.object.isRequired,
  onClick: PropTypes.func.isRequired,
};

// The agents already connected. Each opens Build environment for itself.
function YourAgentsSection({ resumeAgentId, onAgentDialog }) {
  const { data, isLoading } = useConnectedAgents({
    page: 0,
    pageSize: AGENTS_LIMIT,
  });
  const agents = data?.items || [];
  const [type, setType] = useState("all");
  const [showAll, setShowAll] = useState(false);
  const [buildFor, setBuildFor] = useState(null);
  const resumed = useRef(false);
  const openFor = (agent) => {
    setBuildFor(agent);
    onAgentDialog?.(agent?.id || null);
  };
  useEffect(() => {
    if (resumed.current || !resumeAgentId || !agents.length) return;
    resumed.current = true;
    const agent = agents.find((a) => a.id === resumeAgentId);
    if (agent) openFor(agent);
  });
  const count = (id) =>
    id === "all"
      ? agents.length
      : agents.filter((a) => a.modality === id).length;
  const ofType =
    type === "all" ? agents : agents.filter((a) => a.modality === type);
  const shown = showAll ? ofType : ofType.slice(0, FIRST_SHOWN);
  const pick = (id) => {
    setType(id);
    setShowAll(false);
  };
  return (
    <Box>
      <SectionHead
        title="Your agents"
        subtitle="Pick one to build its environment. The version to test, how many scenarios to start with and how many worlds run in parallel come next."
      />
      <Box sx={PANEL_SX}>
        <Stack
          direction="row"
          alignItems="center"
          sx={{
            px: 2,
            py: 1.5,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Stack direction="row" spacing={0.75} sx={{ flex: 1, minWidth: 0 }}>
            {AGENT_TYPES.filter((t) => t.id === "all" || count(t.id)).map(
              (t) => (
                <ButtonBase
                  key={t.id}
                  aria-pressed={type === t.id}
                  onClick={() => pick(t.id)}
                  sx={chipSx(type === t.id)}
                >
                  {t.icon && <Iconify icon={t.icon} width={14} />}
                  {t.label}{" "}
                  <Box
                    component="span"
                    sx={{ color: "text.secondary", fontWeight: 400 }}
                  >
                    {count(t.id)}
                  </Box>
                </ButtonBase>
              ),
            )}
          </Stack>
          {ofType.length > FIRST_SHOWN && (
            <Button
              size="small"
              onClick={() => setShowAll((v) => !v)}
              sx={{ ...BTN_SX, color: "text.secondary", flexShrink: 0 }}
            >
              {showAll ? "Show fewer" : `Show all ${ofType.length}`}
            </Button>
          )}
        </Stack>
        {shown.length ? (
          <Box
            component="ul"
            sx={{
              listStyle: "none",
              m: 0,
              p: 1,
              display: "grid",
              gridTemplateColumns: "repeat(3, minmax(0, 1fr))",
              gap: 0.5,
            }}
          >
            {shown.map((a) => (
              <AgentTile key={a.id} agent={a} onClick={() => openFor(a)} />
            ))}
          </Box>
        ) : (
          <Typography sx={{ ...T13, color: "text.secondary", px: 2, py: 1.5 }}>
            {isLoading ? "Loading agents…" : "No agents connected yet."}
          </Typography>
        )}
      </Box>
      {buildFor && (
        <BuildEnvironmentDialog
          agent={buildFor}
          open
          onClose={() => openFor(null)}
        />
      )}
    </Box>
  );
}

export default function DrawerCatalog({
  onPick,
  onBrowseWorlds,
  resumeAgentId,
  onAgentDialog,
}) {
  return (
    <Stack spacing={4} sx={{ pb: 4 }}>
      <SectionHead
        title="Use a prebuilt world"
        subtitle="Choose a released environment and customize it with your requirements and files."
        action={<Button onClick={onBrowseWorlds}>Browse templates</Button>}
      />
      <ConnectSection onPick={onPick} />
      <YourAgentsSection
        resumeAgentId={resumeAgentId}
        onAgentDialog={onAgentDialog}
      />
    </Stack>
  );
}

YourAgentsSection.propTypes = {
  resumeAgentId: PropTypes.string,
  onAgentDialog: PropTypes.func,
};
DrawerCatalog.propTypes = {
  resumeAgentId: PropTypes.string,
  onAgentDialog: PropTypes.func,
  onPick: PropTypes.func.isRequired,
  onBrowseWorlds: PropTypes.func.isRequired,
};
