import PropTypes from "prop-types";
import { Suspense, lazy, useMemo, useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Button, ButtonBase, IconButton, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { useConnectedAgents } from "../store";
import { agentSourceOf } from "../_mock/agentRegistry";
import { agentChannelsOf } from "../_mock/agentLifecycle";
import { useSaveConnectedAgent } from "../agents/connect/useSaveConnectedAgent";
import { ConnectSubmitContext } from "../agents/connect/connectSubmit";
import BuildEnvironmentDialog from "../agents/BuildEnvironmentDialog";
import SideDrawer from "../components/SideDrawer";
import ChipCard from "./ChipCard";
import { SectionHead } from "./LandingBits";
import { PANEL_SX, focusRing } from "./landingStyles";
import { buildableAgentsOf, useBuildForAgent } from "./worldSource";
import { fitsEnvChannel } from "../_mock/agentSwitch";
import { isGitAgent, versionNameOf } from "../_mock/codeRefs";

/*
  Choosing the agent an environment belongs to (RSI PRD Flow 2, step 1:
  "Connect: pick the agent from Agents").

  · YourAgentsSection — the foot of the Environments landing: every agent in
    Agents, each opening the same Build environment dialog the agent page
    uses (name, version, 10–1,000 scenarios), then the builder.
  · WorldAgentPicker — where a world source (a prebuilt world, an MCP server,
    a SaaS sandbox) is set up: pick one of your agents, or connect a new one
    here, before anything is built.
*/

/* Loaded on demand: the picker reaches back into StartEnvironment (FlowPanel),
   which imports this file — a static import would close a cycle. */
const ConnectAgentPicker = lazy(() => import("../agents/connect/ConnectAgentPicker"));

const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/* "Vapi · v3 in production · 2 environments" */
const metaLine = (a) => {
  const src = agentSourceOf(a);
  const envs = a.usedIn?.length || 0;
  /* A code agent's production version reads as its commit. */
  const prod = a.production || a.latest || "v1";
  return [src.label, `${isGitAgent(a) ? versionNameOf(a, prod).main : prod} in production`, envs ? plural(envs, "environment") : "No environment yet"].join(" · ");
};

/* ── Your agents (landing) ──────────────────────────────────────────────── */

/* Two rows of three before "Show all". */
const FIRST_SHOWN = 6;

/* The agent's type, as its Definition names it — the same chips as
   Connect your agent above. "Voice and chat" only when an agent serves both. */
const TYPE_CHIPS = [
  { id: "voice", label: "Voice", icon: "solar:phone-calling-rounded-linear" },
  { id: "chat", label: "Chat", icon: "solar:chat-round-linear" },
  { id: "both", label: "Voice and chat", icon: "solar:chat-round-call-linear" },
];
const typeOf = (agent) => {
  const ch = agentChannelsOf(agent);
  if (ch.includes("voice") && ch.includes("chat")) return "both";
  return ch.includes("voice") ? "voice" : "chat";
};

export function YourAgentsSection({ sectionRef }) {
  const { agents: all } = useConnectedAgents();
  const agents = useMemo(() => buildableAgentsOf(all), [all]);
  const chips = useMemo(
    () => TYPE_CHIPS
      .map((t) => ({ ...t, count: agents.filter((a) => typeOf(a) === t.id).length }))
      .filter((t) => t.count),
    [agents],
  );
  const [type, setType] = useState(null);
  const [showAll, setShowAll] = useState(false);
  const [buildFor, setBuildFor] = useState(null);
  const build = useBuildForAgent();
  const ofType = type ? agents.filter((a) => typeOf(a) === type) : agents;
  const shown = showAll ? ofType : ofType.slice(0, FIRST_SHOWN);
  const pick = (t) => { setType(t); setShowAll(false); };

  return (
    <Box ref={sectionRef} sx={{ scrollMarginTop: 16 }}>
      <SectionHead
        title="Your agents"
        subtitle="Pick one to build its environment. The version to test and how many scenarios to start with come next."
      />
      {agents.length ? (
        <Box sx={{ ...PANEL_SX, overflow: "hidden" }}>
          <Stack direction="row" alignItems="center" sx={{ px: 2, py: 1.5, borderBottom: "1px solid", borderColor: "divider" }}>
            <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.75, flex: 1, minWidth: 0 }}>
              <ChipCard label={<CountLabel label="All types" count={agents.length} />} on={!type} onClick={() => pick(null)} />
              {chips.map((t) => (
                <ChipCard key={t.id} icon={t.icon} label={<CountLabel label={t.label} count={t.count} />} on={type === t.id} onClick={() => pick(t.id)} />
              ))}
            </Box>
            {ofType.length > FIRST_SHOWN && (
              <Button size="small" onClick={() => setShowAll((v) => !v)} sx={{ typography: "s2", fontWeight: 600, color: "text.secondary", flexShrink: 0 }}>
                {showAll ? "Show fewer" : `Show all ${ofType.length}`}
              </Button>
            )}
          </Stack>
          {/* Three across, like the prebuilt worlds above. */}
          <Box sx={{ p: 1, display: "grid", gap: 0.5, gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr", lg: "repeat(3, minmax(0, 1fr))" } }}>
            {shown.map((a) => (
              <AgentTile key={a.id} agent={a} onClick={() => setBuildFor(a)} />
            ))}
          </Box>
        </Box>
      ) : (
        <Typography sx={{ typography: "s2", color: "text.subtitle", px: 2, py: 1.5, border: "1px dashed", borderColor: "divider", borderRadius: 1 }}>
          No agents in Agents yet. Connect one above — its environment builds next.
        </Typography>
      )}
      {buildFor && (
        <BuildEnvironmentDialog
          open
          onClose={() => setBuildFor(null)}
          agent={buildFor}
          defaultVersion={buildFor.production || buildFor.latest}
          onBuild={(choice) => build(buildFor, choice)}
        />
      )}
    </Box>
  );
}

function CountLabel({ label, count }) {
  return (
    <>
      {`${label} `}
      <Box component="span" sx={{ fontWeight: 400, color: "text.secondary", fontVariantNumeric: "tabular-nums" }}>{count}</Box>
    </>
  );
}
CountLabel.propTypes = { label: PropTypes.string, count: PropTypes.number };

YourAgentsSection.propTypes = { sectionRef: PropTypes.object };

/* One agent on the landing: the whole tile opens Build environment. */
function AgentTile({ agent, onClick }) {
  const src = agentSourceOf(agent);
  return (
    <ButtonBase
      onClick={onClick}
      aria-label={`Build environment for ${agent.name}`}
      sx={{
        minWidth: 0, px: 1.25, py: 1, borderRadius: 1, display: "flex", alignItems: "center", gap: 1.25, textAlign: "left",
        "&:hover": { bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.05 : 0.03) },
        "&:hover .agent-go, &.Mui-focusVisible .agent-go": { opacity: 1 },
        "&.Mui-focusVisible": focusRing,
      }}
    >
      <Iconify icon={src.icon} width={16} sx={{ color: "text.secondary", flexShrink: 0 }} />
      <Box flex={1} minWidth={0}>
        <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>{agent.name}</Typography>
        <Typography noWrap sx={{ typography: "s3", color: "text.subtitle", fontVariantNumeric: "tabular-nums" }}>{metaLine(agent)}</Typography>
      </Box>
      <Iconify className="agent-go" icon="solar:arrow-right-linear" width={14} sx={{ color: "text.secondary", flexShrink: 0, opacity: 0, transition: "opacity .12s ease" }} />
    </ButtonBase>
  );
}
AgentTile.propTypes = { agent: PropTypes.object.isRequired, onClick: PropTypes.func };

function AgentRow({ agent, divider, selected = false, onClick }) {
  const src = agentSourceOf(agent);
  return (
    <ButtonBase
      onClick={onClick}
      aria-pressed={selected || undefined}
      sx={{
        width: "100%", px: 2, py: 1.25, display: "flex", alignItems: "center", gap: 1.5, textAlign: "left",
        borderTop: divider ? "1px solid" : "none", borderColor: "divider",
        bgcolor: (t) => (selected ? alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.06 : 0.03) : "transparent"),
        "&:hover": { bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.05 : 0.025) },
        "&.Mui-focusVisible": focusRing,
      }}
    >
      <Iconify icon={src.icon} width={16} sx={{ color: "text.secondary", flexShrink: 0 }} />
      <Box flex={1} minWidth={0}>
        <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>{agent.name}</Typography>
        <Typography noWrap sx={{ typography: "s3", color: "text.subtitle", fontVariantNumeric: "tabular-nums" }}>{metaLine(agent)}</Typography>
      </Box>
      <Iconify
        icon={selected ? "solar:check-circle-bold" : "solar:circle-linear"}
        width={16}
        sx={{ color: selected ? "text.primary" : "text.disabled", flexShrink: 0 }}
      />
    </ButtonBase>
  );
}
AgentRow.propTypes = {
  agent: PropTypes.object.isRequired, divider: PropTypes.bool,
  selected: PropTypes.bool, onClick: PropTypes.func,
};

/* ── the agent a world source builds for ───────────────────────────────── */

/**
 * Pick the agent this world becomes an environment for — one of yours, or a
 * new one connected here (the same flows as Agents → New agent). `value` is
 * the agent's id.
 */
export default function WorldAgentPicker({ value, onChange, title = "Agent", subtitle = null, surface = null }) {
  const { agents: all, state } = useConnectedAgents();
  /* A world is for one type of agent: only agents of that type are offered. */
  const agents = useMemo(
    () => buildableAgentsOf(all).filter((a) => !surface || fitsEnvChannel(a, { surface }, state)),
    [all, surface, state],
  );
  const typeWord = surface === "voice" ? "voice" : surface ? "chat" : null;
  const saveAgent = useSaveConnectedAgent();
  const [connecting, setConnecting] = useState(false);
  const onConnected = (source) => {
    const { id } = saveAgent(source);
    setConnecting(false);
    onChange(id);
  };
  return (
    <Box>
      <Stack direction="row" alignItems="baseline" spacing={1} sx={{ mb: 0.75 }}>
        <Typography sx={{ typography: "s2", fontWeight: 700 }}>{title}</Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle", flex: 1, minWidth: 0 }}>
          {subtitle || (typeWord ? `Your ${typeWord} agents — this world is for ${typeWord} agents.` : "The environment belongs to this agent in Agents.")}
        </Typography>
        <Button
          size="small"
          onClick={() => setConnecting(true)}
          startIcon={<Iconify icon="solar:add-circle-linear" width={14} />}
          sx={{ typography: "s3", fontWeight: 700, color: "text.secondary", flexShrink: 0 }}
        >
          Connect a new agent
        </Button>
      </Stack>
      {agents.length ? (
        <Box role="radiogroup" sx={{ ...PANEL_SX, maxHeight: 248, overflowY: "auto" }}>
          {agents.map((a, i) => (
            <AgentRow key={a.id} agent={a} divider={i > 0} selected={a.id === value} onClick={() => onChange(a.id)} />
          ))}
        </Box>
      ) : (
        <Typography sx={{ typography: "s2", color: "text.subtitle", px: 1.75, py: 1.25, border: "1px dashed", borderColor: "divider", borderRadius: 1 }}>
          {typeWord ? `No ${typeWord} agents in Agents yet — connect one to build its environment from this world.` : "No agents in Agents yet — connect one to build its environment from this world."}
        </Typography>
      )}

      <SideDrawer open={connecting} onClose={() => setConnecting(false)} width={760}>
        <Stack sx={{ height: "100%" }}>
          <Stack direction="row" alignItems="flex-start" spacing={2} sx={{ px: 2.5, py: 2, borderBottom: "1px solid", borderColor: "divider", flexShrink: 0 }}>
            <Box flex={1} minWidth={0}>
              <Typography sx={{ typography: "m2", fontWeight: 600 }}>{typeWord ? `Connect a ${typeWord} agent` : "Connect a new agent"}</Typography>
              <Typography sx={{ typography: "s2", color: "text.subtitle", mt: 0.25 }}>
                {typeWord
                  ? `This world is for ${typeWord} agents, so only ways to connect a ${typeWord} agent are shown. It joins Agents, and this environment is built for it.`
                  : "It joins Agents, and this environment is built for it."}
              </Typography>
            </Box>
            <IconButton size="small" onClick={() => setConnecting(false)} aria-label="Close">
              <Iconify icon="eva:close-fill" width={18} sx={{ color: "text.secondary" }} />
            </IconButton>
          </Stack>
          <Box sx={{ flex: 1, overflow: "auto", p: 2.5 }}>
            {connecting && (
              <Suspense fallback={null}>
                <ConnectSubmitContext.Provider value="Connect agent">
                  <ConnectAgentPicker onConnect={onConnected} flowsIn="inline" channel={surface} />
                </ConnectSubmitContext.Provider>
              </Suspense>
            )}
          </Box>
        </Stack>
      </SideDrawer>
    </Box>
  );
}
WorldAgentPicker.propTypes = {
  value: PropTypes.string,
  onChange: PropTypes.func.isRequired,
  title: PropTypes.string,
  subtitle: PropTypes.string,
  /* The world's channel ("voice" or "chat") — limits the agents offered. */
  surface: PropTypes.string,
};
