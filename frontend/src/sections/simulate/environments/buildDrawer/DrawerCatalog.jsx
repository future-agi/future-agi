import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { Box, Button, ButtonBase, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { useConnectedAgents } from "src/api/agents/connectedAgents";
import { usePrebuiltEnvironments } from "src/api/simulate-environments/prebuilt";
import UiOnlyFlag from "../components/UiOnlyFlag";
import ConnectSection from "./ConnectSection";
import { featuredWorlds, worldsFrom } from "./worlds";
import {
  BTN_SX,
  OUTLINED_BTN_SX,
  PANEL_SX,
  ROW_BOX_SX,
  T12,
  T13,
  chipSx,
  focusRing,
} from "./drawerStyles";
import { SectionHead } from "./starts/startBits";
import { SaasSandboxRow, WorldCard } from "./starts/worldCards";
import BuildForAgentDialog from "./starts/BuildForAgentDialog";
import {
  FROM_CODE_ITEM,
  MCP_ITEM,
  SANDBOX_ITEM,
  worldItem,
} from "./starts/startItems";
import { agentIconOf, agentMetaOf } from "./starts/startUtils";

const AGENTS_LIMIT = 100;

function BuildFromCode({ onOpen }) {
  return (
    <Stack
      component="li"
      aria-label="Build from code"
      direction="row"
      alignItems="center"
      spacing={1.5}
      sx={{ ...ROW_BOX_SX, px: 2, py: 1.5 }}
    >
      <Iconify
        icon="solar:code-linear"
        width={16}
        sx={{ color: "text.secondary", flexShrink: 0 }}
      />
      <Box flex={1} minWidth={0}>
        <Typography sx={{ ...T13, fontWeight: 500 }}>
          Build from code
        </Typography>
        <Typography sx={{ ...T13, color: "text.secondary" }}>
          Use the CLI, Python or your coding agent. Environments you build this
          way show up here, managed in code.
        </Typography>
      </Box>
      <UiOnlyFlag />
      <Button
        variant="contained"
        color="primary"
        onClick={onOpen}
        sx={{ ...BTN_SX, flexShrink: 0 }}
      >
        Open setup
      </Button>
    </Stack>
  );
}
BuildFromCode.propTypes = { onOpen: PropTypes.func.isRequired };

function PrebuiltSection({ onPick, onBrowse }) {
  const { data } = usePrebuiltEnvironments();
  const worlds = useMemo(() => worldsFrom(data || []), [data]);
  const featured = featuredWorlds(worlds);
  return (
    <Box>
      <SectionHead
        title="Use a prebuilt world"
        subtitle="Tables, tools and rules, as designed. Try one now with its sample agent, or use it with one of your agents."
        action={
          <Stack direction="row" alignItems="center" spacing={1}>
            <UiOnlyFlag />
            <Button variant="outlined" onClick={onBrowse} sx={OUTLINED_BTN_SX}>
              {`Browse all ${worlds.length + 1}`}
            </Button>
          </Stack>
        }
      />
      <Box
        component="ul"
        sx={{
          listStyle: "none",
          m: 0,
          p: 0,
          display: "grid",
          gap: 1.5,
          gridTemplateColumns: "repeat(3, 1fr)",
        }}
      >
        {featured.map((w) => (
          <WorldCard key={w.id} world={w} onOpen={() => onPick(worldItem(w))} />
        ))}
      </Box>
      <Box sx={{ mt: 1.5 }}>
        <SaasSandboxRow onOpen={() => onPick(SANDBOX_ITEM)} />
      </Box>
    </Box>
  );
}
PrebuiltSection.propTypes = {
  onPick: PropTypes.func.isRequired,
  onBrowse: PropTypes.func.isRequired,
};

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
function YourAgentsSection() {
  const { data, isLoading } = useConnectedAgents({
    page: 0,
    pageSize: AGENTS_LIMIT,
  });
  const agents = data?.items || [];
  const [type, setType] = useState("all");
  const [showAll, setShowAll] = useState(false);
  const [buildFor, setBuildFor] = useState(null);
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
        subtitle="Pick one to build its environment. The version to test and how many scenarios to start with come next."
        action={<UiOnlyFlag />}
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
              <AgentTile key={a.id} agent={a} onClick={() => setBuildFor(a)} />
            ))}
          </Box>
        ) : (
          <Typography sx={{ ...T13, color: "text.secondary", px: 2, py: 1.5 }}>
            {isLoading ? "Loading agents…" : "No agents connected yet."}
          </Typography>
        )}
      </Box>
      <BuildForAgentDialog agent={buildFor} onClose={() => setBuildFor(null)} />
    </Box>
  );
}

/**
 * The Build environment drawer's first screen, as in Vel's design: every way
 * in. `onPick(item)` opens an item's setup; `onBrowseWorlds` every world.
 */
export default function DrawerCatalog({ onPick, onBrowseWorlds }) {
  return (
    <Stack spacing={4} sx={{ pb: 4 }}>
      <Box component="ul" sx={{ listStyle: "none", m: 0, p: 0 }}>
        <BuildFromCode onOpen={() => onPick(FROM_CODE_ITEM)} />
      </Box>
      <PrebuiltSection onPick={onPick} onBrowse={onBrowseWorlds} />
      <ConnectSection onPick={onPick} onPickMcp={() => onPick(MCP_ITEM)} />
      <YourAgentsSection />
    </Stack>
  );
}
DrawerCatalog.propTypes = {
  onPick: PropTypes.func.isRequired,
  onBrowseWorlds: PropTypes.func.isRequired,
};
