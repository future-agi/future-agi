import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Button, Grid, Collapse, Tooltip } from "@mui/material";
import Iconify from "src/components/iconify";
import {
  AGENT_TYPES, AGENT_TYPE_GROUPS, agentTypesForSurface, getAgentType, issuedCredential,
  supportsMcp,
} from "../_mock/agentTypes";
import { getSurface } from "../_mock/surfaces";
import { SectionCard, CopyField, cardGrid } from "../components/primitives";
import { BootSequence } from "../components/loading";
import DynamicField from "./connect/DynamicField";
import McpConnect from "./connect/McpConnect";

/**
 * Connect your agent.
 *
 * Two halves: pick what kind of agent it is, then wire it up. The connect form
 * is generated from the type's own schema (see agentTypes.js), and the right
 * column flips between "here is the address we issued you" for inbound types
 * and "here is what we will send you" for outbound ones — because those are
 * genuinely different mental models and blurring them is why connect flows fail.
 */
export default function ConnectAgentStep({ env, envState, patch, onGo }) {
  const surface = getSurface(env.surface);
  const { recommended } = useMemo(
    () => agentTypesForSurface(env.surface),
    [env.surface],
  );

  // The environment already declares the agent type it needs, so the type
  // picker is a redundant second choice — go straight to that type's form and
  // keep the picker as an escape hatch behind "Change".
  const [typeId, setTypeId] = useState(envState.agent?.typeId || env.agentType);
  const [values, setValues] = useState(envState.agent?.values || {});
  const [phase, setPhase] = useState(envState.agent ? "connected" : "configure");
  const [showAll, setShowAll] = useState(false);

  // Two ways in, and they point in opposite directions. "endpoint" is the form
  // — we call the agent. "mcp" publishes the environment and the agent calls
  // us, which is the only route that works when the agent isn't reachable.
  const [mode, setMode] = useState(envState.agent?.via || "endpoint");

  const implied = typeId === env.agentType;
  const mcpAvailable = supportsMcp(getAgentType(typeId));

  const type = getAgentType(typeId);

  const missing = useMemo(() => {
    if (!type) return [];
    return type.fields.filter((f) => {
      if (!f.required) return false;
      if (f.dependsOn?.not != null && values[f.dependsOn.key] === f.dependsOn.not) return false;
      return !values[f.key];
    });
  }, [type, values]);

  const startTest = () => setPhase("testing");

  const finishTest = () => {
    setPhase("connected");
    patch({
      agent: { typeId, values, via: mode, connectedAt: new Date().toISOString() },
    });
  };

  /* ── connected summary ── */
  if (phase === "connected" && envState.agent) {
    return (
      <ConnectedSummary
        env={env}
        type={getAgentType(envState.agent.typeId)}
        values={envState.agent.values}
        via={envState.agent.via}
        onChange={() => { setPhase("configure"); setTypeId(envState.agent.typeId); setValues(envState.agent.values); }}
        onDisconnect={() => { patch({ agent: null }); setPhase("configure"); setTypeId(env.agentType); setValues({}); }}
        onGo={onGo}
      />
    );
  }

  /* ── type picker ── */
  if (phase === "pick") {
    const groups = showAll
      ? AGENT_TYPE_GROUPS.map((g) => ({ name: g, items: AGENT_TYPES.filter((t) => t.group === g) }))
      : [{ name: `Recommended for ${surface.label.toLowerCase()} environments`, items: recommended }];

    return (
      <Box sx={{ p: 2 }}>
        <Stack direction="row" alignItems="flex-start" spacing={2} sx={{ mb: 3 }}>
          <Box flex={1}>
            <Typography sx={{ typography: "m2", fontWeight: 600 }}>
              What kind of agent are you testing?
            </Typography>
            <Typography sx={{ typography: "s1", color: "text.secondary", maxWidth: 720 }}>
              {getAgentType(env.agentType)?.label} is what {env.name} expects. Pick something
              else only if your agent reaches this environment a different way.
            </Typography>
          </Box>
          <Button
            onClick={() => { setTypeId(env.agentType); setPhase("configure"); }}
            startIcon={<Iconify icon="solar:alt-arrow-left-linear" width={16} />}
            sx={{ typography: "s2", fontWeight: 600, color: "text.secondary", flexShrink: 0 }}
          >
            Back
          </Button>
        </Stack>

        {groups.map((g) => (
          <Box key={g.name} sx={{ mb: 3 }}>
            <Typography sx={{ typography: "s3", fontWeight: 700, color: "text.primary", textTransform: "uppercase", letterSpacing: .4, mb: 1.25 }}>
              {g.name}
            </Typography>
            <Box sx={cardGrid(300)}>
              {g.items.map((t) => (
                <AgentTypeCard
                  key={t.id}
                  type={t}
                  recommended={recommended.includes(t)}
                  onClick={() => { setTypeId(t.id); setValues({}); setPhase("configure"); }}
                />
              ))}
            </Box>
          </Box>
        ))}

        {!showAll && (
          <Button
            onClick={() => setShowAll(true)}
            startIcon={<Iconify icon="solar:widget-4-linear" width={16} />}
            sx={{ typography: "s2", fontWeight: 600, color: "text.secondary" }}
          >
            Show all {AGENT_TYPES.length} agent types
          </Button>
        )}
      </Box>
    );
  }

  /* ── configure + handshake ── */
  return (
    <Box sx={{ p: 2 }}>
      <Box sx={{ mb: 3 }}>
        <Typography sx={{ typography: "m2", fontWeight: 600 }}>Connect your agent</Typography>
        <Typography sx={{ typography: "s1", color: "text.secondary" }}>
          {surface.blurb} We handle the {surface.transports.join(", ")} side.
        </Typography>
      </Box>

      <Stack direction="row" alignItems="center" spacing={1.5} sx={{ mb: 2 }}>
        <Box
          sx={{
            width: 34, height: 34, borderRadius: 1, display: "grid", placeItems: "center", flexShrink: 0,
            bgcolor: (t) => alpha(type.color, t.palette.mode === "dark" ? 0.16 : 0.1),
            color: type.color,
          }}
        >
          <Iconify icon={type.icon} width={17} />
        </Box>
        <Box flex={1} minWidth={0}>
          <Stack direction="row" alignItems="center" spacing={0.75}>
            <Typography sx={{ typography: "s1", fontWeight: 700 }}>{type.label}</Typography>
            {implied && (
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                · required by this environment
              </Typography>
            )}
          </Stack>
          <Typography sx={{ typography: "s2", color: "text.subtitle" }}>{type.blurb}</Typography>
        </Box>
        <Button
          onClick={() => setPhase("pick")}
          sx={{ typography: "s2", fontWeight: 600, color: "text.secondary", flexShrink: 0 }}
        >
          Change
        </Button>
      </Stack>

      {/*
        Both routes reach the same place, but they point in opposite
        directions, so they are a visible choice rather than a hidden fallback.
        Only offered where the environment is a set of actions — a phone line
        has no tools to publish.
      */}
      {mcpAvailable && (
        <Box sx={{ ...cardGrid(300), gap: 1.5, mb: 2 }}>
          {ROUTES.map((r) => (
            <RouteCard
              key={r.id}
              route={r}
              selected={mode === r.id}
              onClick={() => setMode(r.id)}
            />
          ))}
        </Box>
      )}

      <Grid container spacing={2}>
        <Grid item xs={12} md={7}>
          {mode === "mcp" ? (
            <McpConnect
              env={env}
              type={type}
              testing={phase === "testing"}
              onConnect={startTest}
            />
          ) : (
          <SectionCard title="Connection details">
            <Stack spacing={2.25} sx={{ p: 2.5 }}>
              {type.fields.map((f) => (
                <DynamicField
                  key={f.key}
                  field={f}
                  value={values[f.key]}
                  values={values}
                  onChange={(v) => setValues((s) => ({ ...s, [f.key]: v }))}
                />
              ))}
            </Stack>

            <Stack
              direction="row"
              alignItems="center"
              spacing={1.5}
              sx={{ px: 2.5, py: 2, borderTop: "1px solid", borderColor: "divider" }}
            >
              <Tooltip
                arrow
                title={missing.length ? `Still needed: ${missing.map((m) => m.label).join(", ")}` : ""}
              >
                <span>
                  <Button
                    variant="contained"
                    color="primary"
                    disabled={missing.length > 0 || phase === "testing"}
                    onClick={startTest}
                    startIcon={
                      <Iconify
                        icon={phase === "testing" ? "solar:refresh-linear" : "solar:plug-circle-linear"}
                        width={16}
                      />
                    }
                    sx={{ typography: "s2", fontWeight: 700 }}
                  >
                    {phase === "testing" ? "Testing connection…" : "Test connection"}
                  </Button>
                </span>
              </Tooltip>
              {missing.length > 0 && (
                <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
                  {missing.length} required {missing.length === 1 ? "field" : "fields"} left
                </Typography>
              )}
            </Stack>

            {/*
              Live handshake — each step is a real thing we'd be doing.

              unmountOnExit matters: Collapse keeps its children mounted by
              default, so BootSequence would start ticking as soon as the form
              rendered and "connect" the agent with an empty form a few seconds
              later, without anyone pressing Test connection.
            */}
            <Collapse in={phase === "testing"} unmountOnExit>
              <Box sx={{ px: 2.5, py: 2.25, borderTop: "1px solid", borderColor: "divider", bgcolor: "background.neutral" }}>
                <BootSequence
                  steps={type.handshake}
                  accent={type.color}
                  stepMs={780}
                  onDone={finishTest}
                />
              </Box>
            </Collapse>
          </SectionCard>
          )}

          {/* The handshake is the same event either way, so it renders once. */}
          {mode === "mcp" && (
            <Collapse in={phase === "testing"} unmountOnExit>
              <Box sx={{ mt: 2, p: 2.25, borderRadius: 1.5, border: "1px solid", borderColor: "divider", bgcolor: "background.paper" }}>
                <BootSequence
                  steps={MCP_HANDSHAKE}
                  accent={type.color}
                  stepMs={780}
                  onDone={finishTest}
                />
              </Box>
            </Collapse>
          )}
        </Grid>

        <Grid item xs={12} md={5}>
          <DirectionPanel type={type} env={env} mode={mode} />
        </Grid>
      </Grid>
    </Box>
  );
}

ConnectAgentStep.propTypes = {
  env: PropTypes.object.isRequired,
  envState: PropTypes.object.isRequired,
  patch: PropTypes.func.isRequired,
  onGo: PropTypes.func,
};

/* ── pieces ──────────────────────────────────────────────────────────────── */

const ROUTES = [
  {
    id: "endpoint",
    label: "We connect to your agent",
    blurb: "Give us an address and credentials, and we drive it. Your agent has to be reachable from the internet.",
    icon: "solar:login-3-linear",
  },
  {
    id: "mcp",
    label: "Your agent connects to us",
    blurb: "We publish the environment as MCP tools. Works from a laptop or behind a VPN — nothing of yours is exposed.",
    icon: "solar:logout-3-linear",
  },
];

const MCP_HANDSHAKE = [
  "Publishing environment as MCP tools",
  "Waiting for your client",
  "Negotiating protocol version",
  "Scoping session to the first scenario",
];

function RouteCard({ route, selected, onClick }) {
  return (
    <Box
      onClick={onClick}
      sx={{
        p: 2, borderRadius: 1.5, cursor: "pointer", height: "100%",
        border: "1px solid",
        borderColor: selected ? "primary.main" : "divider",
        bgcolor: (t) => selected
          ? alpha(t.palette.primary.main, t.palette.mode === "dark" ? 0.12 : 0.05)
          : "background.paper",
        transition: "border-color .16s ease, background-color .16s ease",
        "&:hover": selected ? {} : { borderColor: "text.subtitle" },
      }}
    >
      <Stack direction="row" alignItems="flex-start" spacing={1.5}>
        <Box
          sx={{
            width: 30, height: 30, borderRadius: 0.875, display: "grid", placeItems: "center", flexShrink: 0,
            color: "text.secondary", bgcolor: "background.neutral",
          }}
        >
          <Iconify icon={route.icon} width={16} />
        </Box>
        <Box minWidth={0}>
          <Typography sx={{ typography: "s2", fontWeight: 700 }}>{route.label}</Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}>
            {route.blurb}
          </Typography>
        </Box>
      </Stack>
    </Box>
  );
}
RouteCard.propTypes = { route: PropTypes.object, selected: PropTypes.bool, onClick: PropTypes.func };

function Header({ title, subtitle }) {
  return (
    <Box sx={{ mb: 3 }}>
      <Typography sx={{ typography: "m2", fontWeight: 600 }}>{title}</Typography>
      <Typography sx={{ typography: "s1", color: "text.secondary", maxWidth: 720 }}>
        {subtitle}
      </Typography>
    </Box>
  );
}
Header.propTypes = { title: PropTypes.string, subtitle: PropTypes.string };

function AgentTypeCard({ type, recommended, onClick }) {
  return (
    <Box
      onClick={onClick}
      sx={{
        height: "100%", p: 2, borderRadius: 1.5, cursor: "pointer",
        border: "1px solid", borderColor: "divider", bgcolor: "background.paper",
        transition: "border-color .16s ease, transform .16s ease, box-shadow .16s ease",
        "&:hover": {
          borderColor: alpha(type.color, 0.5),
          transform: "translateY(-2px)",
          boxShadow: (t) => `0 6px 18px ${alpha(type.color, t.palette.mode === "dark" ? 0.16 : 0.1)}`,
        },
      }}
    >
      <Stack direction="row" alignItems="flex-start" spacing={1.25}>
        <Box
          sx={{
            width: 32, height: 32, borderRadius: 1, display: "grid", placeItems: "center", flexShrink: 0,
            bgcolor: (t) => alpha(type.color, t.palette.mode === "dark" ? 0.16 : 0.1),
            color: type.color,
          }}
        >
          <Iconify icon={type.icon} width={17} />
        </Box>
        <Box minWidth={0} flex={1}>
          <Stack direction="row" alignItems="center" spacing={0.5}>
            <Typography sx={{ typography: "s2", fontWeight: 700 }}>{type.label}</Typography>
            {recommended && (
              <Iconify icon="solar:star-bold" width={12} sx={{ color: "#CA8A04", flexShrink: 0 }} />
            )}
          </Stack>
          <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}>
            {type.blurb}
          </Typography>
        </Box>
      </Stack>
    </Box>
  );
}
AgentTypeCard.propTypes = { type: PropTypes.object, recommended: PropTypes.bool, onClick: PropTypes.func };

/**
 * Inbound vs outbound is the single most confusing thing about connecting an
 * agent, so it gets its own panel that says plainly which way traffic flows.
 */
function DirectionPanel({ type, env, mode }) {
  // Choosing MCP *is* choosing a direction, so the diagram follows the route
  // rather than the agent type's default.
  const inbound = mode === "mcp" || type.direction === "inbound" || type.direction === "both";
  const credKind = mode === "mcp" ? "mcp" :
    { voice_platform: "number", voice_sip: "sip", browser_agent: "token", computer_agent: "token",
      mcp_agent: "mcp", framework_agent: "token", multi_agent: "token", coding_agent: "token" }[type.id] || "webhook";

  return (
    <Stack spacing={2}>
      <SectionCard title="How traffic flows">
        <Box sx={{ p: 2.5 }}>
          <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 2 }}>
            <Node label="Your agent" icon={type.icon} color={type.color} />
            <Flow reverse={!inbound} />
            <Node label={env.name} icon="solar:server-square-linear" color="#7857FC" />
          </Stack>
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>
            {mode === "mcp"
              ? "Your agent opens the connection, so nothing of yours needs a public address. The server below is scoped to this environment and resets with it."
              : inbound
                ? "Your agent connects to us. Use the address below — it is scoped to this environment and resets with it."
                : "We connect to your agent. Give us an endpoint and credentials, and we'll drive the conversation."}
          </Typography>
        </Box>
      </SectionCard>

      {inbound && (
        <SectionCard title="Issued for this environment">
          <Stack spacing={1.75} sx={{ p: 2.5 }}>
            <CopyField label={credLabel(credKind)} value={issuedCredential(credKind)} />
            <CopyField label="Environment token" value={issuedCredential("token")} />
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              These rotate whenever you reset the environment.
            </Typography>
          </Stack>
        </SectionCard>
      )}
    </Stack>
  );
}
DirectionPanel.propTypes = { type: PropTypes.object, env: PropTypes.object, mode: PropTypes.string };

const credLabel = (kind) =>
  ({ number: "Test phone number", sip: "SIP address", webhook: "Inbound webhook URL",
     mcp: "MCP server URL", email: "Mailbox address", token: "Connection token" })[kind];

function Node({ label, icon, color }) {
  return (
    <Stack alignItems="center" spacing={0.75} sx={{ width: 92 }}>
      <Box
        sx={{
          width: 40, height: 40, borderRadius: 1.25, display: "grid", placeItems: "center",
          bgcolor: (t) => alpha(color, t.palette.mode === "dark" ? 0.16 : 0.1),
          color, border: () => `1px solid ${alpha(color, 0.26)}`,
        }}
      >
        <Iconify icon={icon} width={19} />
      </Box>
      <Typography noWrap sx={{ typography: "s3", fontWeight: 600, textAlign: "center", width: "100%" }}>
        {label}
      </Typography>
    </Stack>
  );
}
Node.propTypes = { label: PropTypes.string, icon: PropTypes.string, color: PropTypes.string };

function Flow({ reverse }) {
  return (
    <Stack alignItems="center" flex={1} spacing={0.5} sx={{ pb: 2.25 }}>
      <Box
        component="svg"
        viewBox="0 0 100 8"
        sx={{ width: "100%", height: 8, overflow: "visible" }}
      >
        <defs>
          <marker id={`arw-${reverse}`} markerWidth="6" markerHeight="6" refX="4" refY="3" orient="auto">
            <path d="M0,0 L6,3 L0,6 Z" fill="#7857FC" />
          </marker>
        </defs>
        <line
          x1={reverse ? 100 : 0} y1="4" x2={reverse ? 4 : 96} y2="4"
          stroke="#7857FC" strokeWidth="1.5" strokeDasharray="5 4"
          markerEnd={`url(#arw-${reverse})`}
          style={{ animation: "fagi-flow 1.1s linear infinite" }}
        />
      </Box>
      <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
        {reverse ? "we call your agent" : "your agent calls us"}
      </Typography>
      <Box component="style">
        {`@keyframes fagi-flow { to { stroke-dashoffset: ${reverse ? 18 : -18}; } }`}
      </Box>
    </Stack>
  );
}
Flow.propTypes = { reverse: PropTypes.bool };

function ConnectedSummary({ env, type, values, via, onChange, onDisconnect, onGo }) {
  const overMcp = via === "mcp";
  const shown = type.fields
    .filter((f) => values[f.key] != null && values[f.key] !== "" && f.type !== "secret")
    .slice(0, 5);

  return (
    <Box sx={{ p: 2 }}>
      <Header
        title="Agent connected"
        subtitle="Your agent is wired into this environment. Next, give it something to do."
      />

      <SectionCard>
        <Stack direction="row" alignItems="center" spacing={2} sx={{ p: 2.5 }}>
          <Box
            sx={{
              width: 44, height: 44, borderRadius: 1.25, display: "grid", placeItems: "center",
              bgcolor: (t) => alpha(type.color, t.palette.mode === "dark" ? 0.16 : 0.1),
              color: type.color,
            }}
          >
            <Iconify icon={type.icon} width={22} />
          </Box>
          <Box flex={1} minWidth={0}>
            <Stack direction="row" alignItems="center" spacing={0.75}>
              <Typography sx={{ typography: "s1", fontWeight: 700 }}>{type.label}</Typography>
              <Stack
                direction="row" alignItems="center" spacing={0.5}
                sx={{
                  px: 0.75, height: 20, borderRadius: 0.75, color: "#16A34A",
                  bgcolor: (t) => alpha("#16A34A", t.palette.mode === "dark" ? 0.16 : 0.1),
                }}
              >
                <Iconify icon="solar:check-circle-bold" width={12} />
                <Typography sx={{ typography: "s3", fontWeight: 700 }}>Connected</Typography>
              </Stack>
            </Stack>
            <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
              {overMcp
                ? `Connected to ${env.name} over MCP`
                : `Reachable from ${env.name}`}
            </Typography>
          </Box>
          <Button onClick={onChange} size="small" sx={{ typography: "s2", color: "text.secondary" }}>
            Edit
          </Button>
          <Button onClick={onDisconnect} size="small" color="error" sx={{ typography: "s2" }}>
            Disconnect
          </Button>
        </Stack>

        {overMcp && (
          <Stack sx={{ px: 2.5, py: 2, borderTop: "1px solid", borderColor: "divider" }} spacing={1.75}>
            <CopyField label="MCP server" value={issuedCredential("mcp")} />
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              Your client opened this connection. We hand it one scenario per session and
              restore the environment in between.
            </Typography>
          </Stack>
        )}

        {!overMcp && shown.length > 0 && (
          <Stack
            sx={{ px: 2.5, py: 2, borderTop: "1px solid", borderColor: "divider" }}
            spacing={1.25}
          >
            {shown.map((f) => (
              <Stack key={f.key} direction="row" spacing={2}>
                <Typography sx={{ typography: "s2", color: "text.subtitle", width: 150, flexShrink: 0 }}>
                  {f.label}
                </Typography>
                <Typography noWrap sx={{ typography: "s2", fontWeight: 600, fontFamily: "ui-monospace, Menlo, monospace" }}>
                  {String(values[f.key])}
                </Typography>
              </Stack>
            ))}
          </Stack>
        )}
      </SectionCard>

      <Button
        variant="contained"
        color="primary"
        onClick={() => onGo("scenarios")}
        endIcon={<Iconify icon="solar:arrow-right-linear" width={16} />}
        sx={{ mt: 2.5, typography: "s2", fontWeight: 700 }}
      >
        Add scenarios
      </Button>
    </Box>
  );
}
ConnectedSummary.propTypes = {
  env: PropTypes.object, type: PropTypes.object, values: PropTypes.object, via: PropTypes.string,
  onChange: PropTypes.func, onDisconnect: PropTypes.func, onGo: PropTypes.func,
};
