import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Button, Grid, Collapse, Tooltip, TextField } from "@mui/material";
import Iconify from "src/components/iconify";
import {
  AGENT_TYPES, AGENT_TYPE_GROUPS, agentTypesForSurface, getAgentType, issuedCredential,
  supportsMcp,
} from "../_mock/agentTypes";
import { getSurface } from "../_mock/surfaces";
import { runtimeGap, runtimeValuesFrom, detectedStack } from "../_mock/builder";
import { currentAgentVersion, agentVersionsWithRuns, nextAgentVersion } from "../_mock/versions";
import { contractFor } from "../_mock/contract";
import { getAdapter } from "../_mock/rlContract";
import { MODALITY_FOR } from "../_mock/fidelity";
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
export default function ConnectAgentStep({ env, envState, patch, addAgentVersion, onGo, buildMode, chromeless }) {
  const surface = getSurface(env.surface);
  const { recommended } = useMemo(
    () => agentTypesForSurface(env.surface),
    [env.surface],
  );

  // The environment already declares the agent type it needs, so the type
  // picker is a redundant second choice — go straight to that type's form and
  // keep the picker as an escape hatch behind "Change".
  const [typeId, setTypeId] = useState(envState.agent?.typeId || env.agentType);
  const gap = runtimeGap(env.builtFrom);
  const [values, setValues] = useState(
    envState.agent?.values || runtimeValuesFrom(env.builtFrom),
  );
  const [phase, setPhase] = useState(envState.agent ? "connected" : "configure");
  const [showAll, setShowAll] = useState(false);
  /*
    "edit"        — a save patches the current connection in place, for
                    typos and moved endpoints.
    "new-version" — a save mints agent v_next and swaps the connection to
                    the new one. The old version stays in the history so
                    runs against it remain comparable.
  */
  const [intent, setIntent] = useState("edit");
  const [versionNote, setVersionNote] = useState("");

  // Two ways in, and they point in opposite directions. "endpoint" is the form
  // — we call the agent. "mcp" publishes the environment and the agent calls
  // us, which is the only route that works when the agent isn't reachable.
  const [mode, setMode] = useState(envState.agent?.via || "endpoint");

  const implied = typeId === env.agentType;
  const mcpAvailable = supportsMcp(getAgentType(typeId));

  const type = getAgentType(typeId);
  /*
    The modality is not stored anywhere separately — the RL contract, the
    fidelity controls and the runtime connection all resolve it from the
    connected agent. So picking a type from another surface is not a cosmetic
    relabel: it rewrites the contract. Said here, where the choice is made,
    rather than discovered later on the contract page.
  */
  const movesModality = !!type && !type.surfaces.includes(env.surface);
  const movedTo = movesModality
    ? getAdapter(MODALITY_FOR[type.surfaces[0]] || "chat")
    : null;

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
    const nextAgent = { typeId, values, via: mode, connectedAt: new Date().toISOString() };
    /*
      Two save paths. "edit" quietly patches — same agent, better URL. A
      "new-version" save mints a new agent version (nextAgentVersion) and
      swaps the connection to it; the old version stays in the history so
      any runs stamped against it remain comparable and re-runnable.
      Runs pin the version they started against, so post-mint runs land
      as v_next automatically.
    */
    if (intent === "new-version") {
      const version = nextAgentVersion(envState, {
        note: versionNote.trim() || "Connected a different agent implementation",
        reach: mode,
      });
      addAgentVersion?.(version);
      setVersionNote("");
      setIntent("edit");
    }
    patch({ agent: nextAgent });
  };

  /* ── connected summary ── */
  if (phase === "connected" && envState.agent) {
    return (
      <ConnectedSummary
        env={env}
        envState={envState}
        chromeless={chromeless}
        type={getAgentType(envState.agent.typeId)}
        values={envState.agent.values}
        via={envState.agent.via}
        connectedAt={envState.agent.connectedAt}
        onChange={() => { setPhase("configure"); setIntent("edit"); setTypeId(envState.agent.typeId); setValues(envState.agent.values); }}
        onConnectDifferent={() => {
          /*
            A different implementation of the same job (competing
            codebase, different vendor) is a new AGENT VERSION on the
            same environment — runs stay comparable because the world
            didn't move. Clear the current values so the form reads as
            a fresh connect, and flip intent so the save mints v_next.
          */
          setPhase("configure");
          setIntent("new-version");
          setTypeId(envState.agent.typeId);
          setValues({});
        }}
        versionNote={versionNote}
        onGo={onGo}
        buildMode={buildMode}
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
        <Typography sx={{ typography: "m2", fontWeight: 600 }}>
          {intent === "new-version" ? `Connect agent ${nextAgentVersion(envState).label}` : "Connect your agent"}
        </Typography>
        <Typography sx={{ typography: "s1", color: "text.secondary" }}>
          {intent === "new-version"
            ? `Forking a new agent version on this environment. Save mints ${nextAgentVersion(envState).label} and swaps the connection to it; the previous version stays in the history so any runs against it remain comparable.`
            : `${surface.blurb} We handle the ${surface.transports.join(", ")} side.`}
        </Typography>
      </Box>

      {/*
        A note field for new-version saves. Small, optional, kept where
        the intent banner is so it reads as "why this version" not "why
        this connection". Persists onto the version record so six weeks
        from now the runs list can still answer "what was different
        about v3".
      */}
      {intent === "new-version" && (
        <Box sx={{ mb: 2.5, p: 2, borderRadius: 1.25, border: "1px solid", borderColor: (t) => alpha("#7857FC", 0.3), bgcolor: (t) => alpha("#7857FC", t.palette.mode === "dark" ? 0.08 : 0.04) }}>
          <Typography sx={{ typography: "s3", fontWeight: 700, color: "#7857FC", textTransform: "uppercase", letterSpacing: 0.4, mb: 0.75 }}>
            Version note (optional)
          </Typography>
          <TextField
            fullWidth size="small"
            value={versionNote}
            onChange={(e) => setVersionNote(e.target.value)}
            placeholder="What is different about this version — e.g. Vendor Y implementation"
            InputProps={{ sx: { typography: "s2" } }}
          />
        </Box>
      )}

      {/*
        Being asked for an agent a second time reads as duplicated work unless
        the difference is stated: stage 1 READ the agent, a run has to DRIVE it.
      */}
      {gap && (
        <Box
          sx={{
            p: 2, mb: 2.5, borderRadius: 1.25, border: "1px solid",
            borderColor: gap.reusable ? alpha("#16A34A", 0.3) : "divider",
            bgcolor: (t) => gap.reusable
              ? alpha("#16A34A", t.palette.mode === "dark" ? 0.09 : 0.045)
              : t.palette.background.neutral,
          }}
        >
          <Stack direction="row" spacing={1.5} alignItems="flex-start">
            <Iconify
              icon={gap.reusable ? "solar:check-circle-bold" : "solar:lightbulb-linear"}
              width={17}
              sx={{ color: gap.reusable ? "#16A34A" : "primary.main", flexShrink: 0, mt: "1px" }}
            />
            <Box flex={1} minWidth={0}>
              <Typography sx={{ typography: "s2", fontWeight: 700 }}>{gap.title}</Typography>
              <Typography sx={{ typography: "s2", color: "text.secondary", mt: 0.25 }}>{gap.note}</Typography>
              {env.builtFrom?.value && (
                <Typography noWrap sx={{ typography: "s3", color: "text.subtitle", mt: 0.75, fontFamily: "ui-monospace, Menlo, monospace" }}>
                  {env.builtFrom.value}{env.builtFrom.ref ? ` @ ${env.builtFrom.ref.value}` : ""}
                </Typography>
              )}
            </Box>
          </Stack>
        </Box>
      )}

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
        Only when it actually matters. A voice agent on a voice environment
        says nothing; a coding agent on a phone line says everything.
      */}
      {movesModality && (
        <Stack
          direction="row"
          spacing={1.25}
          alignItems="flex-start"
          sx={{
            p: 2, mb: 2, borderRadius: 1,
            border: "1px solid", borderColor: "divider",
            bgcolor: "background.neutral",
          }}
        >
          <Iconify
            icon="solar:danger-triangle-linear"
            width={16}
            sx={{ color: "text.subtitle", flexShrink: 0, mt: "1px" }}
          />
          <Box minWidth={0}>
            <Typography sx={{ typography: "s2", fontWeight: 700 }}>
              This changes the environment, not just the connection
            </Typography>
            <Typography sx={{ typography: "s2", color: "text.secondary", mt: 0.25 }}>
              {surface?.label || env.surface} is what this environment was built for.
              Connecting {type.label} moves the contract to the{" "}
              {movedTo?.label.toLowerCase()} adapter — the observation and action spaces,
              the fidelity controls and the runtime connection all follow it. The scenarios,
              personas and evals were written for {(surface?.label || env.surface).toLowerCase()}{" "}
              and stay as they are, so review them before you run.
            </Typography>
            <Button
              size="small"
              onClick={() => onGo("contract")}
              sx={{ typography: "s2", fontWeight: 700, color: "text.secondary", px: 0, mt: 0.5 }}
            >
              See the contract this writes
            </Button>
          </Box>
        </Stack>
      )}

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
  buildMode: PropTypes.bool,
  chromeless: PropTypes.bool,
  env: PropTypes.object.isRequired,
  envState: PropTypes.object.isRequired,
  patch: PropTypes.func.isRequired,
  addAgentVersion: PropTypes.func,
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

/**
 * The connected agent, in full.
 *
 * This environment was read *from* this agent — its contract, its world and
 * its scenarios all derive from it — so the page is a profile of the agent
 * rather than a receipt for a form. And there is no disconnect: removing the
 * agent would orphan everything downstream of it. Pointing at a newer build is
 * "Add version"; correcting how we reach it is "Edit".
 */
function ConnectedSummary({ env, envState, chromeless, type, values, via, connectedAt, onChange, onConnectDifferent, onGo, buildMode }) {
  const overMcp = via === "mcp";
  const stack = detectedStack(env.builtFrom);
  const version = currentAgentVersion(envState);
  const versions = agentVersionsWithRuns(envState);
  const connSurface = getSurface(env.surface);
  const contract = contractFor(env);
  const [showTools, setShowTools] = useState(false);

  /* Provider-aware labels, so the row matches the dashboard the id came from. */
  const labelFor = (f) => (f.labelFrom ? f.labelFrom.map[values[f.labelFrom.key]]?.label : null) || f.label;
  const shown = type.fields.filter(
    (f) => values[f.key] != null && values[f.key] !== "" &&
      !(f.dependsOn?.not != null && values[f.dependsOn.key] === f.dependsOn.not),
  );
  const promptOnly = Math.max(1, Math.round((env.rules?.length || 0) * 0.6));

  return (
    <Box sx={{ p: chromeless ? 0 : 2 }}>
      {/*
        Chromeless mode drops the "Agent connected" hero + description
        — the caller (AgentsPanel) already labels this section, so the
        redundant heading was creating heading-soup on that screen.
      */}
      {!chromeless && (
        <Header
          title="Agent connected"
          subtitle="Everything in this environment was read from this agent. Next, give it something to do."
        />
      )}

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
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                · {version.label}{version.current ? " · current" : ""}
              </Typography>
            </Stack>
            <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
              {overMcp ? `Connected to ${env.name} over MCP` : `Reachable from ${env.name}`}
            </Typography>
          </Box>
          <Stack direction="row" spacing={1} sx={{ flexShrink: 0 }}>
            <Button
              onClick={onChange}
              size="small"
              sx={{ typography: "s2", fontWeight: 700, color: "text.secondary" }}
            >
              Edit connection
            </Button>
            {/*
              A competing implementation of the same job (different
              codebase, different vendor, but same tools and rules) is a
              new agent version on the same environment. The world stays
              put; only the endpoint moves. Runs across versions stay
              comparable on the same suite.
            */}
            <Tooltip arrow title="Fork a new agent version pointed at a different endpoint. Runs stay comparable on this environment.">
              <Button
                onClick={onConnectDifferent}
                size="small"
                startIcon={<Iconify icon="solar:branching-paths-up-linear" width={14} />}
                sx={{ typography: "s2", fontWeight: 700, color: "text.primary" }}
              >
                Connect a different agent
              </Button>
            </Tooltip>
          </Stack>
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
          <Stack sx={{ px: 2.5, py: 2, borderTop: "1px solid", borderColor: "divider" }} spacing={1.25}>
            {shown.map((f) => (
              <Stack key={f.key} direction="row" spacing={2}>
                <Typography sx={{ typography: "s2", color: "text.subtitle", width: 150, flexShrink: 0 }}>
                  {labelFor(f)}
                </Typography>
                <Typography noWrap sx={{ typography: "s2", fontWeight: 600, fontFamily: "ui-monospace, Menlo, monospace" }}>
                  {f.type === "secret" ? "••••••••" : String(values[f.key])}
                </Typography>
              </Stack>
            ))}
          </Stack>
        )}
      </SectionCard>

      {/* ── what it can do, read from the source ── */}
      <SectionCard
        title="What this agent can do"
        subtitle={`${env.tools?.length || 0} tools and ${env.rules?.length || 0} rules, read from the source — not typed in`}
        sx={{ mt: 2 }}
        action={
          <Button
            size="small"
            onClick={() => setShowTools((o) => !o)}
            sx={{ typography: "s2", fontWeight: 700, color: "primary.main" }}
          >
            {showTools ? "Hide tools" : "Show all tools"}
          </Button>
        }
      >
        <Box sx={{ px: 2.5, py: 2 }}>
          <Stack direction="row" spacing={0.75} flexWrap="wrap" rowGap={0.75}>
            {(env.tools || []).slice(0, showTools ? 99 : 6).map((t) => (
              <Tooltip key={t.name} arrow title={t.desc}>
                <Box
                  sx={{
                    px: 1, py: 0.375, borderRadius: 0.75, border: "1px solid", borderColor: "divider",
                    typography: "s3", fontFamily: "ui-monospace, Menlo, monospace", color: "text.secondary",
                  }}
                >
                  {t.name}
                </Box>
              </Tooltip>
            ))}
            {!showTools && (env.tools?.length || 0) > 6 && (
              <Typography sx={{ typography: "s3", color: "text.subtitle", alignSelf: "center" }}>
                +{env.tools.length - 6} more
              </Typography>
            )}
          </Stack>
        </Box>

        <Stack sx={{ px: 2.5, py: 2, borderTop: "1px solid", borderColor: "divider" }} spacing={1.25}>
          {(env.rules || []).map((r) => (
            <Stack key={r} direction="row" spacing={1.25} alignItems="flex-start">
              <Iconify icon="solar:shield-check-linear" width={15} sx={{ color: "primary.main", flexShrink: 0, mt: "2px" }} />
              <Typography sx={{ typography: "s2", color: "text.secondary" }}>{r}</Typography>
            </Stack>
          ))}
          <Typography sx={{ typography: "s3", color: "text.subtitle", pt: 0.5 }}>
            {promptOnly} of {env.rules?.length || 0} are prompt-only — the code will not stop the
            agent breaking them, so they are graded rather than guaranteed.
          </Typography>
        </Stack>
      </SectionCard>

      {/* ── the three facts panels ── */}
      <Box sx={{ ...cardGrid(300), gap: 2, mt: 2 }}>
        <SectionCard title="What we drive" subtitle="The side of the conversation we handle">
          <Stack sx={{ px: 2.5, py: 2 }} spacing={1.25}>
            <SummaryRow label="Channel" value={connSurface.label} />
            <SummaryRow label="Transports" value={connSurface.transports.join(" · ")} />
            <SummaryRow label="Detected" value={`${stack.modality} · ${stack.stack}`} cap />
            <SummaryRow label="How we read it" value={stack.how} />
          </Stack>
        </SectionCard>

        <SectionCard title="Where it came from" subtitle="The source this environment was read from">
          <Stack sx={{ px: 2.5, py: 2 }} spacing={1.25}>
            {env.builtFrom ? (
              <>
                <SummaryRow label="Source" value={env.builtFrom.kind} cap />
                <SummaryRow label="Location" value={env.builtFrom.value} mono />
                {env.builtFrom.ref && (
                  <SummaryRow label="Pinned to" value={`${env.builtFrom.ref.kind} · ${env.builtFrom.ref.value}`} mono />
                )}
              </>
            ) : (
              <SummaryRow label="Source" value="Adopted from a template" />
            )}
            <SummaryRow label="Connected" value={connectedAt ? new Date(connectedAt).toLocaleString() : "—"} />
          </Stack>
        </SectionCard>

        {!overMcp && (
          <SectionCard title="Issued for this environment" subtitle="Rotates whenever you reset the environment">
            <Stack sx={{ px: 2.5, py: 2 }} spacing={1.75}>
              {env.surface === "voice" && (
                <CopyField label="Test phone number" value={issuedCredential("number")} />
              )}
              <CopyField label="Environment token" value={issuedCredential("token")} />
            </Stack>
          </SectionCard>
        )}
      </Box>

      {/* ── version history — post-run only ── */}
      {!buildMode && (
      <SectionCard
        title="Agent versions"
        subtitle="Scenarios belong to the environment, so the same suite runs against any of these"
        sx={{ mt: 2 }}
      >
        <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
          {versions.map((v) => (
            <Stack key={v.id} direction="row" alignItems="center" spacing={2} sx={{ px: 2.5, py: 1.5 }}>
              <Typography sx={{ typography: "s2", fontWeight: 700, width: 34, flexShrink: 0 }}>{v.label}</Typography>
              <Box flex={1} minWidth={0}>
                <Stack direction="row" alignItems="center" spacing={0.75}>
                  <Typography noWrap sx={{ typography: "s2", color: "text.secondary" }}>{v.note}</Typography>
                  {v.current && (
                    <Typography sx={{ typography: "s3", fontWeight: 700, color: "#16A34A", flexShrink: 0 }}>current</Typography>
                  )}
                </Stack>
              </Box>
              <Typography sx={{ typography: "s3", color: "text.subtitle", flexShrink: 0 }}>
                {v.runs ? `${v.runs} run${v.runs === 1 ? "" : "s"}` : "never run"}
              </Typography>
            </Stack>
          ))}
        </Stack>
      </SectionCard>
      )}

      {/* Amendments — post-run only. */}
      {!buildMode && contract.amendments?.length > 0 && (
        <SectionCard
          title="Amendments"
          subtitle="Where the source did not settle it and we made a call — visibly authored, not derived"
          sx={{ mt: 2 }}
        >
          <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
            {contract.amendments.map((a) => (
              <Box key={a.name} sx={{ px: 2.5, py: 1.75 }}>
                <Typography sx={{ typography: "s2", fontWeight: 700, fontFamily: "ui-monospace, Menlo, monospace" }}>
                  {a.name}
                </Typography>
                <Typography sx={{ typography: "s2", color: "text.secondary" }}>{a.why}</Typography>
              </Box>
            ))}
          </Stack>
        </SectionCard>
      )}

    </Box>
  );
}

ConnectedSummary.propTypes = {
  buildMode: PropTypes.bool,
  chromeless: PropTypes.bool,
  env: PropTypes.object, envState: PropTypes.object, type: PropTypes.object, values: PropTypes.object, via: PropTypes.string,
  connectedAt: PropTypes.string,
  onChange: PropTypes.func, onConnectDifferent: PropTypes.func, onGo: PropTypes.func,
  versionNote: PropTypes.string,
};

function SummaryRow({ label, value, mono, cap }) {
  return (
    <Stack direction="row" spacing={2} alignItems="flex-start">
      <Typography sx={{ typography: "s2", color: "text.subtitle", width: 110, flexShrink: 0 }}>
        {label}
      </Typography>
      {/* No blanket capitalize: it title-cased whole sentences and broke words
          mid-token. Only `cap` — used for short enum values like a source
          kind — gets the treatment, and only monospace values may break. */}
      <Typography
        sx={{
          typography: "s2", fontWeight: 600, minWidth: 0,
          wordBreak: mono ? "break-all" : "normal",
          textTransform: cap ? "capitalize" : "none",
          fontFamily: mono ? "ui-monospace, Menlo, monospace" : "inherit",
        }}
      >
        {value}
      </Typography>
    </Stack>
  );
}
SummaryRow.propTypes = { label: PropTypes.string, value: PropTypes.node, mono: PropTypes.bool, cap: PropTypes.bool };
