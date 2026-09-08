import PropTypes from "prop-types";
import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { alpha } from "@mui/material/styles";
import {
  Box, Stack, Typography, Button, TextField, IconButton, Tooltip,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { SectionCard } from "../components/primitives";
import { useSimStore } from "../store";
import DescribeFlowStep from "./intake/DescribeFlowStep";

/**
 * Variation B entry point.
 *
 * The old front door dropped users straight into a modality → source-kind
 * flow that assumed "I already have an agent I want to test." That excluded
 * people who wanted a template, a hosted platform, an MCP server, or nothing
 * at all. This screen inverts it — it asks how you want to start, then
 * reveals the setup for that path inline underneath. No route change on
 * pick, no full-page swap, no lost context.
 *
 * The 8 options came from the manager's list and cover every way an agent
 * can reach the platform (or the intentional "no agent yet" case).
 */
export default function StartEnvironment({ entry = false }) {
  const navigate = useNavigate();
  const [choice, setChoice] = useState(null);
  const panelRef = useRef(null);
  const pickerRef = useRef(null);

  const pick = (id) => {
    setChoice(id);
    /* Reveal → scroll the panel into a comfortable read position. Timeout
       gives React one paint to render the panel before we scroll to it. */
    setTimeout(() => {
      panelRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 60);
  };

  const bringYourAgent = OPTIONS.filter((o) => o.group === "bring");

  return (
    <Box sx={{ p: 2 }}>
      <Stack direction={{ xs: "column", sm: "row" }} justifyContent="space-between" alignItems={{ sm: "flex-end" }} spacing={2} sx={{ mb: 2.5 }}>
        <Stack direction="row" alignItems="flex-start" spacing={1.5} flex={1} minWidth={0}>
          {!entry && (
            <Tooltip arrow title="Back to environments">
              <IconButton size="small" onClick={() => navigate(paths.dashboard.simulate.environments)} sx={{ mt: 0.25 }}>
                <Iconify icon="solar:alt-arrow-left-linear" width={18} />
              </IconButton>
            </Tooltip>
          )}
          <Box>
            <Typography sx={{ typography: "m2", fontWeight: 600 }}>
              {entry ? "Environments" : "New environment"}
            </Typography>
            <Typography sx={{ typography: "s1", color: "text.secondary" }}>
              {entry
                ? "An environment is the world your agent runs in — seeded state, tools, and rules. Pick how you want to bring your agent in and we take care of the rest."
                : "Every environment needs a world to simulate in. Start by telling us where the agent lives — or start blank if you don't have one yet."}
            </Typography>
          </Box>
        </Stack>
        <Stack direction="row" spacing={1} sx={{ flexShrink: 0 }}>
          <Button
            size="small"
            variant="contained"
            color="primary"
            onClick={() => pick("scratch")}
            startIcon={<Iconify icon="solar:document-add-linear" width={13} />}
            sx={{ typography: "s2", fontWeight: 700 }}
          >
            Start from scratch
          </Button>
        </Stack>
      </Stack>

      <Stack ref={pickerRef} spacing={2.5}>
        <Box
          sx={{
            display: "grid",
            gap: 1.5,
            gridTemplateColumns: { xs: "1fr", md: "1fr 1fr" },
          }}
        >
          <TemplateHeroCard
            option={OPTIONS.find((o) => o.id === "templates")}
            selected={false}
            onClick={() => navigate(paths.dashboard.simulate.environmentTemplates)}
          />
          <ClonesHeroCard onClick={() => navigate(paths.dashboard.simulate.environmentNewTwin)} />
        </Box>
        <Box>
          <Stack direction="row" alignItems="baseline" spacing={1.5} sx={{ mb: 1.5 }}>
            <Typography sx={{ typography: "s2", fontWeight: 700, letterSpacing: 0.4, textTransform: "uppercase" }}>
              Or connect your own agent
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              We work with what you already have — no rewrite, no adapter.
            </Typography>
          </Stack>
          <Box
            sx={{
              display: "grid",
              gap: 1.25,
              gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr", md: "repeat(3, 1fr)" },
            }}
          >
            {bringYourAgent.filter((o) => o.id !== "templates").map((opt) => (
              <OptionCard
                key={opt.id}
                option={opt}
                selected={choice === opt.id}
                onClick={() => pick(opt.id)}
              />
            ))}
          </Box>
        </Box>
      </Stack>

      {choice && (
        <Box ref={panelRef} sx={{ mt: 2.5 }}>
          <FlowPanel choice={choice} />
        </Box>
      )}
    </Box>
  );
}
StartEnvironment.propTypes = { entry: PropTypes.bool };

/* ── hero + card ───────────────────────────────────────────────────────── */

const HERO_TEMPLATES = ["Customer Support Line", "Coding", "Browser", "Airline Rebooking"];
const HERO_CLONES = ["Slack", "Notion", "Gmail", "Salesforce", "Linear"];

function HeroCard({ icon, title, tag, description, chips, moreLabel, selected, onClick }) {
  return (
    <Stack
      onClick={onClick}
      role="button"
      aria-pressed={selected}
      spacing={1.75}
      sx={{
        position: "relative",
        p: 2.5,
        borderRadius: 1.5,
        cursor: "pointer",
        border: "1px solid",
        minHeight: 172,
        borderColor: (th) => selected
          ? (th.palette.mode === "dark" ? alpha(th.palette.text.primary, 0.5) : th.palette.text.primary)
          : th.palette.divider,
        bgcolor: (th) => selected
          ? alpha(th.palette.text.primary, th.palette.mode === "dark" ? 0.06 : 0.03)
          : "background.paper",
        transition: "border-color .12s ease, background-color .12s ease",
        "&:hover": {
          borderColor: (th) => selected ? undefined : (th.palette.mode === "dark" ? alpha(th.palette.text.primary, 0.32) : th.palette.text.disabled),
        },
      }}
    >
      <Stack direction="row" spacing={2} alignItems="center">
        <Box
          sx={{
            width: 44, height: 44, borderRadius: 1.5,
            display: "grid", placeItems: "center", flexShrink: 0,
            bgcolor: (th) => alpha(th.palette.text.primary, th.palette.mode === "dark" ? 0.1 : 0.06),
            color: "text.primary",
          }}
        >
          <Iconify icon={icon} width={22} />
        </Box>
        <Box minWidth={0}>
          <Stack direction="row" alignItems="center" spacing={1}>
            <Typography sx={{ typography: "m2", fontWeight: 700 }}>
              {title}
            </Typography>
            {tag && (
              <Typography sx={{ typography: "s3", fontWeight: 700, letterSpacing: 0.4, textTransform: "uppercase", color: "text.subtitle" }}>
                · {tag}
              </Typography>
            )}
          </Stack>
        </Box>
      </Stack>
      <Typography sx={{ typography: "s1", color: "text.secondary", flex: 1 }}>
        {description}
      </Typography>
      <Stack
        direction="row"
        spacing={0.5}
        useFlexGap
        flexWrap="wrap"
        alignItems="center"
        sx={{
          pt: 1.25,
          borderTop: "1px solid",
          borderColor: "divider",
        }}
      >
        {chips.map((name) => (
          <Box
            key={name}
            sx={{
              px: 1, py: 0.5, borderRadius: 0.75,
              border: "1px solid", borderColor: "divider",
              bgcolor: (th) => alpha(th.palette.text.primary, 0.02),
              typography: "s3", fontWeight: 600,
            }}
          >
            {name}
          </Box>
        ))}
        {moreLabel && (
          <Box sx={{ typography: "s3", fontWeight: 700, color: "text.subtitle", px: 0.5 }}>{moreLabel}</Box>
        )}
      </Stack>
      {selected && (
        <Iconify
          icon="solar:check-circle-bold"
          width={16}
          sx={{ position: "absolute", top: 10, right: 10, color: "text.primary" }}
        />
      )}
    </Stack>
  );
}
HeroCard.propTypes = {
  icon: PropTypes.string, title: PropTypes.node, tag: PropTypes.node,
  description: PropTypes.node, chips: PropTypes.arrayOf(PropTypes.string),
  moreLabel: PropTypes.node, selected: PropTypes.bool, onClick: PropTypes.func,
};

function TemplateHeroCard({ option, selected, onClick }) {
  return (
    <HeroCard
      icon={option.icon}
      title={option.title}
      tag="Fastest"
      description={"Prebuilt worlds with seeded state, tools, and rules. Pick one, then wire your agent — you'll be running scenarios in under a minute."}
      chips={HERO_TEMPLATES}
      moreLabel="+ 10 more"
      selected={selected}
      onClick={onClick}
    />
  );
}
TemplateHeroCard.propTypes = { option: PropTypes.object, selected: PropTypes.bool, onClick: PropTypes.func };

function ClonesHeroCard({ onClick }) {
  return (
    <HeroCard
      icon="solar:copy-linear"
      title="Clones"
      tag="Live SaaS sandboxes"
      description={"Your agent calls the real SDKs — Slack, Notion, Salesforce — but the calls land in a sandbox we own, seeded to your prompt and torn down between runs."}
      chips={HERO_CLONES}
      moreLabel="+ 8 more"
      onClick={onClick}
    />
  );
}
ClonesHeroCard.propTypes = { onClick: PropTypes.func };

function OptionCard({ option, selected, onClick }) {
  return (
    <Stack
      onClick={onClick}
      role="button"
      aria-pressed={selected}
      spacing={1.5}
      sx={{
        position: "relative",
        p: 2,
        borderRadius: 1.5,
        cursor: "pointer",
        border: "1px solid",
        minHeight: 180,
        borderColor: (th) => selected
          ? (th.palette.mode === "dark" ? alpha(th.palette.text.primary, 0.5) : th.palette.text.primary)
          : th.palette.divider,
        bgcolor: (th) => selected
          ? alpha(th.palette.text.primary, th.palette.mode === "dark" ? 0.06 : 0.03)
          : "background.paper",
        transition: "border-color .12s ease, background-color .12s ease",
        "&:hover": {
          borderColor: (th) => selected ? undefined : (th.palette.mode === "dark" ? alpha(th.palette.text.primary, 0.32) : th.palette.text.disabled),
        },
      }}
    >
      <Stack direction="row" alignItems="center" spacing={1.25}>
        <Box
          sx={{
            width: 32, height: 32, borderRadius: 1,
            display: "grid", placeItems: "center", flexShrink: 0,
            bgcolor: (th) => alpha(th.palette.text.primary, th.palette.mode === "dark" ? 0.1 : 0.06),
            color: "text.primary",
          }}
        >
          <Iconify icon={option.icon} width={16} />
        </Box>
        <Typography sx={{ typography: "s1", fontWeight: 600, lineHeight: 1.2 }}>
          {option.title}
        </Typography>
      </Stack>
      <Typography sx={{ typography: "s3", color: "text.subtitle", lineHeight: 1.45, flex: 1 }}>
        {option.blurb}
      </Typography>
      {option.preview && (
        <Box
          sx={{
            pt: 1.25,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          <Stack direction="row" spacing={0.5} useFlexGap flexWrap="wrap">
            {option.preview.map((item) => (
              <Typography
                key={item}
                sx={{
                  typography: "s3",
                  fontWeight: 600,
                  color: "text.subtitle",
                  px: 0.75,
                  py: 0.25,
                  borderRadius: 0.5,
                  bgcolor: (th) => alpha(th.palette.text.primary, th.palette.mode === "dark" ? 0.06 : 0.04),
                }}
              >
                {item}
              </Typography>
            ))}
          </Stack>
        </Box>
      )}
      {selected && (
        <Iconify
          icon="solar:check-circle-bold"
          width={15}
          sx={{ position: "absolute", top: 10, right: 10, color: "text.primary" }}
        />
      )}
    </Stack>
  );
}
OptionCard.propTypes = {
  option: PropTypes.object,
  selected: PropTypes.bool,
  onClick: PropTypes.func,
};

/* ── flow panel router ─────────────────────────────────────────────────── */

function FlowPanel({ choice }) {
  const opt = OPTIONS.find((o) => o.id === choice);
  const Body = PANELS[choice] || PanelNotImplemented;
  return (
    <SectionCard title={opt.title} subtitle={opt.setupSubtitle || opt.blurb}>
      <Body />
    </SectionCard>
  );
}
FlowPanel.propTypes = { choice: PropTypes.string };

/* ── build handler shared across panels ──────────────────────────────── */

/*
  Every "Build environment" button funnels through this hook. It hands
  the source object off to the /environments/new/build route where
  BuildFromAgent picks it up (via location.state.presetSource), mounts
  the chat + derivation view, and streams the build. The env itself is
  minted downstream by BuildFromAgent's own adopt flow — this hook
  intentionally does NOT dispatch adoptEnvironment so we don't create
  a half-built row that the chat is still filling in.
*/
function useBuildEnvironment() {
  const navigate = useNavigate();
  return (source) => {
    navigate(paths.dashboard.simulate.environmentBuild, { state: { presetSource: source } });
  };
}

/* ── panels ────────────────────────────────────────────────────────────── */

function PanelSourceRepo() {
  const build = useBuildEnvironment();
  const [provider, setProvider] = useState("github");
  const [repo, setRepo] = useState("");
  const [branch, setBranch] = useState("main");
  const [entry, setEntry] = useState("");
  const [envText, setEnvText] = useState("");
  const [egress, setEgress] = useState("");
  const canGo = !!repo.trim();
  return (
    <Stack spacing={1.75} sx={{ p: 2.5 }}>
      <ProviderRow
        options={REPO_PROVIDERS}
        value={provider}
        onChange={setProvider}
      />
      <Field
        label="Repository"
        required
        placeholder="owner/repo"
        value={repo} onChange={setRepo}
        mono
        helper="We read the code so scenarios stay in sync with your actual tools."
      />
      <Stack direction="row" spacing={1.5}>
        <Field
          label="Branch or tag"
          value={branch} onChange={setBranch}
          mono
          fullWidth
        />
        <Field
          label="Entry path (optional)"
          placeholder="src/agent/index.ts"
          value={entry} onChange={setEntry}
          mono
          fullWidth
        />
      </Stack>
      <EnvironmentValues envText={envText} onEnvText={setEnvText} egress={egress} onEgress={setEgress} />
      <ContinueRow
        disabled={!canGo}
        hint="Add a repository"
        onClick={() => build({
          kind: "repo",
          provider,
          value: repo.trim(),
          ref: branch.trim() || "main",
          entry: entry.trim(),
          envText: envText.trim() || null,
          egress: egress.trim() || null,
        })}
      />
    </Stack>
  );
}

function PanelRunningAgent() {
  const build = useBuildEnvironment();
  const [endpoint, setEndpoint] = useState("");
  const [token, setToken] = useState("");
  const [showToken, setShowToken] = useState(false);
  /* Type-aware advanced fields — hidden by default because the endpoint
     alone is enough for most agents. Voice callers need call direction,
     chat callers may want a system-prompt override, others rarely need
     anything type-specific here. */
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [agentType, setAgentType] = useState("chat");
  const [callDirection, setCallDirection] = useState("inbound");
  const [systemPrompt, setSystemPrompt] = useState("");

  const canGo = !!endpoint.trim();
  return (
    <Stack spacing={1.75} sx={{ p: 2.5 }}>
      <Field
        label="Agent SDK endpoint"
        required
        placeholder="https://api.yourapp.com/agent/step"
        value={endpoint} onChange={setEndpoint}
        mono
        helper="Every simulated turn POSTs here. Your agent runs where it already runs; we drive the world."
      />
      <Box>
        <Button
          size="small"
          onClick={() => setShowToken((o) => !o)}
          startIcon={<Iconify icon={showToken ? "solar:alt-arrow-up-linear" : "solar:alt-arrow-down-linear"} width={12} />}
          sx={{ typography: "s3", fontWeight: 600, color: "text.secondary", px: 0.5 }}
        >
          {showToken ? "Hide auth token" : "Auth token (optional)"}
        </Button>
        {showToken && (
          <Box sx={{ mt: 1 }}>
            <Field
              label=""
              placeholder="Bearer …"
              value={token} onChange={setToken}
              type="password"
              mono
            />
          </Box>
        )}
      </Box>
      <Box>
        <Button
          size="small"
          onClick={() => setShowAdvanced((o) => !o)}
          startIcon={<Iconify icon={showAdvanced ? "solar:alt-arrow-up-linear" : "solar:alt-arrow-down-linear"} width={12} />}
          sx={{ typography: "s3", fontWeight: 600, color: "text.secondary", px: 0.5 }}
        >
          {showAdvanced ? "Hide agent-type settings" : "Fine-tune for your agent type"}
        </Button>
        {showAdvanced && (
          <Stack spacing={1.5} sx={{ mt: 1.25 }}>
            <Box>
              <Label>Agent type</Label>
              <Box sx={{ display: "grid", gap: 0.75, gridTemplateColumns: { xs: "1fr 1fr", sm: `repeat(${AGENT_TYPES.length}, 1fr)` }, mt: 0.75 }}>
                {AGENT_TYPES.map((t) => (
                  <ChipCard
                    key={t.id}
                    icon={t.icon}
                    label={t.label}
                    on={agentType === t.id}
                    onClick={() => setAgentType(t.id)}
                  />
                ))}
              </Box>
            </Box>
            {agentType === "voice" && (
              <Box>
                <Label>Call direction</Label>
                <Box sx={{ display: "grid", gap: 0.75, gridTemplateColumns: "1fr 1fr", mt: 0.75 }}>
                  <ChipCard label="Inbound — we call your agent" on={callDirection === "inbound"} onClick={() => setCallDirection("inbound")} />
                  <ChipCard label="Outbound — your agent dials us" on={callDirection === "outbound"} onClick={() => setCallDirection("outbound")} />
                </Box>
              </Box>
            )}
            {agentType === "chat" && (
              <Field
                label="System prompt override (optional)"
                placeholder="Leave blank to use whatever your endpoint already does."
                value={systemPrompt} onChange={setSystemPrompt}
                multiline
              />
            )}
            {(agentType === "computer" || agentType === "code" || agentType === "robotics") && (
              <Typography sx={{ typography: "s3", color: "text.subtitle", px: 0.5 }}>
                Nothing extra to configure — the endpoint response tells us everything we need.
              </Typography>
            )}
          </Stack>
        )}
      </Box>
      <ContinueRow
        disabled={!canGo}
        hint="Add an endpoint"
        onClick={() => build({
          kind: "endpoint",
          value: endpoint.trim(),
          token: token.trim() || null,
          agentType,
          ...(agentType === "voice" ? { callDirection } : {}),
          ...(agentType === "chat" ? { systemPrompt: systemPrompt.trim() || null } : {}),
        })}
      />
    </Stack>
  );
}

function PanelHostedPlatform() {
  const build = useBuildEnvironment();
  /* Type gate first — the platform roster only makes sense once we know
     what kind of agent is being connected. Default to voice because
     that's what the codebase actually integrates with today. */
  const [agentType, setAgentType] = useState("voice");
  const platforms = HOSTED_PLATFORMS_BY_TYPE[agentType] || [];
  const [platform, setPlatform] = useState(platforms[0]?.id || "");
  const [id, setId] = useState("");
  const [key, setKey] = useState("");
  /* Optional link to the repo backing the hosted agent — lets the
     builder read tool definitions and prompts to seed matching
     scenarios. Not required to build the env, but improves the
     derivation when supplied. */
  const [repoUrl, setRepoUrl] = useState("");
  /* Voice-only: matches the `callDirection` field on voice_platform in
     _mock/agentTypes.js. Inbound = we call the agent, outbound = the
     agent dials our simulated customer. */
  const [callDirection, setCallDirection] = useState("inbound");

  /* When agent type flips, the current platform selection is likely
     stale. Snap to the first platform of the new type and clear the
     credentials so we do not carry an OpenAI key into a Retell form. */
  const pickAgentType = (nextType) => {
    setAgentType(nextType);
    const first = (HOSTED_PLATFORMS_BY_TYPE[nextType] || [])[0];
    setPlatform(first?.id || "");
    setId("");
    setKey("");
    setRepoUrl("");
  };

  const chosen = platforms.find((p) => p.id === platform) || platforms[0];
  const canGo = !!chosen && !!id.trim() && !!key.trim();

  return (
    <Stack spacing={1.75} sx={{ p: 2.5 }}>
      <Box>
        <Label>Agent type</Label>
        <Box sx={{ display: "grid", gap: 0.75, gridTemplateColumns: { xs: "1fr 1fr", sm: `repeat(${AGENT_TYPES.length}, 1fr)` }, mt: 0.75 }}>
          {AGENT_TYPES.map((t) => (
            <ChipCard
              key={t.id}
              icon={t.icon}
              label={t.label}
              on={agentType === t.id}
              onClick={() => pickAgentType(t.id)}
            />
          ))}
        </Box>
      </Box>
      <Box>
        <Label>Platform</Label>
        {platforms.length === 0 ? (
          <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 1 }}>
            No hosted platforms for this agent type yet. Try Running agent or Source repository instead.
          </Typography>
        ) : (
          <Box sx={{ display: "grid", gap: 0.75, gridTemplateColumns: { xs: "1fr 1fr", sm: "repeat(3, 1fr)" }, mt: 0.75 }}>
            {platforms.map((p) => (
              <ChipCard
                key={p.id}
                icon={p.icon}
                label={p.name}
                on={platform === p.id}
                onClick={() => setPlatform(p.id)}
              />
            ))}
          </Box>
        )}
      </Box>
      {chosen && (
        <>
          <Field
            label={chosen.idLabel || "Agent ID"}
            required
            placeholder={chosen.idPlaceholder}
            value={id} onChange={setId}
            mono
          />
          <Field
            label={chosen.keyLabel || "API key"}
            required
            placeholder="sk-…"
            value={key} onChange={setKey}
            type="password"
            mono
            helper="Stored encrypted; used only to invoke the agent on your behalf."
          />
          <Field
            label="GitHub repo"
            placeholder="https://github.com/your-org/your-agent"
            value={repoUrl} onChange={setRepoUrl}
            mono
            helper="Optional. Lets us read the agent's tools + prompts to seed matching scenarios."
          />
          {agentType === "voice" && (
            <Box>
              <Label>Call direction</Label>
              <Box sx={{ display: "grid", gap: 0.75, gridTemplateColumns: "1fr 1fr", mt: 0.75 }}>
                <ChipCard label="Inbound — we call your agent" on={callDirection === "inbound"} onClick={() => setCallDirection("inbound")} />
                <ChipCard label="Outbound — your agent dials us" on={callDirection === "outbound"} onClick={() => setCallDirection("outbound")} />
              </Box>
            </Box>
          )}
        </>
      )}
      <ContinueRow
        disabled={!canGo}
        hint={!chosen ? "Pick a supported path above" : "Fill both fields"}
        onClick={() => build({
          kind: "platform",
          agentType,
          provider: chosen?.id,
          agentId: id.trim(),
          apiKey: key.trim(),
          ...(repoUrl.trim() ? { repoUrl: repoUrl.trim() } : {}),
          ...(agentType === "voice" ? { callDirection } : {}),
        })}
      />
    </Stack>
  );
}

function PanelMcpServer() {
  const build = useBuildEnvironment();
  const [transport, setTransport] = useState("http");
  const [target, setTarget] = useState("");
  const [header, setHeader] = useState("");
  const canGo = !!target.trim();
  return (
    <Stack spacing={1.75} sx={{ p: 2.5 }}>
      <ProviderRow
        options={MCP_TRANSPORTS}
        value={transport}
        onChange={setTransport}
      />
      <Field
        label={transport === "stdio" ? "Command" : "Server URL"}
        required
        placeholder={transport === "stdio" ? "uvx my-mcp-server --flag" : "https://mcp.yourapp.com/sse"}
        value={target} onChange={setTarget}
        mono
        helper="We list the tools the server exposes and derive the environment from them."
      />
      {transport !== "stdio" && (
        <Field
          label="Extra header (optional)"
          placeholder="Authorization: Bearer …"
          value={header} onChange={setHeader}
          mono
        />
      )}
      <ContinueRow
        disabled={!canGo}
        hint="Add the server"
        onClick={() => build({
          kind: "mcp",
          transport,
          value: target.trim(),
          header: header.trim() || null,
        })}
      />
    </Stack>
  );
}

function PanelCodeUpload() {
  const build = useBuildEnvironment();
  const [files, setFiles] = useState([]);
  const [entry, setEntry] = useState("");
  const [envText, setEnvText] = useState("");
  const [egress, setEgress] = useState("");
  const canGo = files.length > 0;

  const onDrop = (list) => {
    const added = Array.from(list).map((f) => ({ name: f.name, size: f.size }));
    setFiles((prev) => [...prev, ...added]);
    if (!entry && added[0]) setEntry(added[0].name);
  };

  return (
    <Stack spacing={1.75} sx={{ p: 2.5 }}>
      <Box
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => { e.preventDefault(); if (e.dataTransfer?.files) onDrop(e.dataTransfer.files); }}
        sx={{
          border: "1px dashed",
          borderColor: (th) => th.palette.mode === "dark" ? alpha(th.palette.text.primary, 0.25) : th.palette.divider,
          borderRadius: 1.5,
          bgcolor: (th) => alpha(th.palette.text.primary, 0.02),
          p: 3,
          textAlign: "center",
        }}
      >
        <Iconify icon="solar:cloud-upload-linear" width={28} sx={{ color: "text.subtitle" }} />
        <Typography sx={{ typography: "s1", fontWeight: 600, mt: 1 }}>
          Drop code files or a zipped project
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}>
          .py .ts .js .zip · up to 25 MB. The builder reads the code to derive tools, rules, and scenarios.
        </Typography>
        <Button
          component="label"
          size="small"
          variant="outlined"
          sx={{ mt: 1.5, typography: "s2", fontWeight: 700 }}
        >
          Choose files
          <input
            hidden
            multiple
            type="file"
            onChange={(e) => e.target.files && onDrop(e.target.files)}
          />
        </Button>
      </Box>
      {files.length > 0 && (
        <Box>
          <Label>Uploaded ({files.length})</Label>
          <Stack spacing={0.5} sx={{ mt: 0.75 }}>
            {files.map((f, i) => (
              <Stack
                key={`${f.name}-${i}`}
                direction="row"
                alignItems="center"
                spacing={1}
                sx={{ px: 1, py: 0.75, borderRadius: 1, bgcolor: (th) => alpha(th.palette.text.primary, 0.04) }}
              >
                <Iconify icon="solar:document-linear" width={13} sx={{ color: "text.subtitle" }} />
                <Typography sx={{ typography: "s2", fontFamily: "ui-monospace, Menlo, monospace", flex: 1, minWidth: 0 }} noWrap>
                  {f.name}
                </Typography>
                <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                  {formatSize(f.size)}
                </Typography>
                <IconButton size="small" onClick={() => setFiles((prev) => prev.filter((_, x) => x !== i))}>
                  <Iconify icon="solar:trash-bin-minimalistic-linear" width={13} />
                </IconButton>
              </Stack>
            ))}
          </Stack>
        </Box>
      )}
      {files.length > 0 && (
        <Field
          label="Entry file"
          value={entry} onChange={setEntry}
          mono
          helper="Which file defines the agent's step function?"
        />
      )}
      <EnvironmentValues envText={envText} onEnvText={setEnvText} egress={egress} onEgress={setEgress} />
      <ContinueRow
        disabled={!canGo}
        hint="Add at least one file"
        onClick={() => build({
          kind: "upload",
          entry: entry.trim(),
          files: files.map((f) => ({ name: f.name, size: f.size })),
          envText: envText.trim() || null,
          egress: egress.trim() || null,
        })}
      />
    </Stack>
  );
}

function PanelScratch() {
  const navigate = useNavigate();
  const { dispatch } = useSimStore();
  const [name, setName] = useState("");
  const [flowText, setFlowText] = useState("");
  const [attachments, setAttachments] = useState([]);
  /* Name is optional — the intake describes the flow well enough on its
     own, so if the user skips the name field we derive a reasonable one
     from the description before building. Build is only gated on the
     intake itself being non-trivial. */
  const canGo = flowText.trim().length >= 10 || attachments.length > 0;

  /* Scratch flow diverges from the six connection panels — no agent
     source to derive from, so we mint a blank env in the store from the
     intake instead. Mirrors ScratchEnvironment.jsx in the other
     variation, minus the intermediate building animation. */
  const buildBlank = () => {
    if (!canGo) return;
    const envId = `env-scratch-${Date.now().toString(36)}`;
    const finalName = name.trim() || deriveScratchName(flowText, attachments);
    /* Derive the environment's world from the flow description so the
       review layout has real material to show — the user described
       what the agent does; we materialise that into rules, scenarios,
       and suggested evals. Tools stay empty because we intentionally
       have no agent connected yet; the Agents tab will show its
       empty state + "add agent version" affordance. */
    const derivedRules = deriveScratchRules(flowText);
    const derivedScenarios = deriveScratchScenarios(flowText, finalName);
    const derivedEvals = deriveScratchEvals(flowText);
    const env = {
      id: envId,
      agentType: null,
      name: finalName,
      surface: "chat",
      domain: "custom",
      tagline: attachments.length
        ? `Scratch-built · ${attachments.length} reference file${attachments.length === 1 ? "" : "s"}`
        : "Scratch-built",
      description: flowText.trim() || "A scratch-built environment. Connect an agent from the Agents tab to run simulations.",
      difficulty: "Balanced",
      popularity: 1,
      builtFrom: {
        kind: "scratch",
        intake: {
          flow: flowText.trim(),
          attachments: attachments.map((f) => ({ name: f.name, size: f.size, type: f.type })),
        },
      },
      custom: true,
      seed: { tables: [] },
      tools: [],
      rules: derivedRules,
      evalPreset: derivedEvals,
      buildStatus: "ready",
      starterScenarios: derivedScenarios,
    };
    dispatch({ type: "adoptEnvironment", env, now: new Date().toISOString() });
    /* Seed the workspace envState with the derived scenarios only.
       Evals stay empty on purpose — the derived list lives on
       env.evalPreset and surfaces on the Evaluations tab under
       "Suggested evaluations". Added evaluations must be an
       explicit user action (either "Add" on a suggested row or the
       "Add evaluations" button), so we don't pre-fill envState.evals
       and make the choice for them. Agents stays empty too. */
    dispatch({
      type: "patchEnvState",
      envId,
      patch: {
        scenarios: derivedScenarios,
      },
    });
    /* Route to the scratch build-in-progress screen instead of straight
       to the workspace — user sees TemplateReviewLayout stream the
       stages (Understanding → Building world → Proving scenarios)
       before Finish setup ships them off to /environments/:envId. */
    navigate(paths.dashboard.simulate.environmentScratchBuild(envId));
  };

  const addFiles = (list) => {
    setAttachments((prev) => {
      const seen = new Set(prev.map((a) => a.id));
      return [...prev, ...list.filter((a) => !seen.has(a.id))];
    });
  };
  const removeAttachment = (id) => setAttachments((prev) => prev.filter((a) => a.id !== id));

  return (
    <Stack spacing={2} sx={{ p: 2.5 }}>
      <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
        {"No agent yet — that's fine. Describe what the agent will do; we set up the world, personas, and evals from that. Wire the agent when it's ready."}
      </Typography>
      <Field
        label="Environment name (optional)"
        placeholder="e.g. Support desk sandbox — leave blank and we'll name it from your description"
        value={name} onChange={setName}
      />
      <DescribeFlowStep
        step={0} total={1}
        prompt="Describe your flow — or drop something the builder can learn from"
        description="Type how the agent handles a typical case (trigger, tools, hand-offs, definition of done) — or attach real material (CSVs, SOPs, call recordings, sample transcripts). Either works. Both is better."
        placeholder="e.g. A customer emails us with a refund request. The agent reads the email, looks up the order in Salesforce, checks the refund policy, drafts a reply, and posts it to Slack for approval before sending. If the order is over $500 it escalates to a human."
        chips={[
          "The agent gets triggered when…",
          "It reaches for these tools…",
          "It's done when…",
          "It escalates if…",
        ]}
        accept=".csv,.tsv,.json,.md,.txt,.pdf,.docx,.wav,.mp3,.m4a"
        value={flowText}
        onChange={setFlowText}
        files={attachments}
        onAdd={addFiles}
        onRemove={removeAttachment}
        onBack={null}
        onSkip={undefined}
        onNext={buildBlank}
        canSubmit={canGo}
        submitLabel="Build environment"
      />
    </Stack>
  );
}

function PanelBuildLocally() {
  const build = useBuildEnvironment();
  const [os, setOs] = useState("mac");
  const cmd = os === "windows"
    ? "iwr https://cli.futureagi.com/install.ps1 -useb | iex\nfagi env init"
    : "curl -fsSL https://cli.futureagi.com/install.sh | sh\nfagi env init";
  return (
    <Stack spacing={1.75} sx={{ p: 2.5 }}>
      <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
        Run the environment on your own machine — your code never leaves. The CLI opens a secure tunnel back to us so simulations still show up in the dashboard.
      </Typography>
      <ProviderRow options={OS_OPTIONS} value={os} onChange={setOs} />
      <Box>
        <Label>Run this in your terminal</Label>
        <Box
          sx={{
            mt: 0.75, p: 1.5, borderRadius: 1,
            bgcolor: (th) => th.palette.mode === "dark" ? alpha(th.palette.text.primary, 0.06) : alpha(th.palette.text.primary, 0.04),
            border: "1px solid", borderColor: "divider",
            fontFamily: "ui-monospace, Menlo, monospace",
            typography: "s2",
            whiteSpace: "pre",
            position: "relative",
          }}
        >
          {cmd}
          <IconButton
            size="small"
            onClick={() => navigator.clipboard?.writeText(cmd)}
            sx={{ position: "absolute", top: 6, right: 6 }}
          >
            <Iconify icon="solar:copy-linear" width={13} />
          </IconButton>
        </Box>
      </Box>
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{
          p: 1.25, borderRadius: 1,
          border: "1px solid", borderColor: "divider",
          bgcolor: (th) => alpha(th.palette.text.primary, 0.02),
        }}
      >
        <Box
          sx={{
            width: 8, height: 8, borderRadius: "50%",
            bgcolor: (th) => alpha(th.palette.text.primary, 0.3),
          }}
        />
        <Typography sx={{ typography: "s2", color: "text.subtitle", flex: 1 }}>
          Waiting for a local runtime to connect…
        </Typography>
        <Button size="small" variant="text" sx={{ typography: "s3", fontWeight: 700 }}>
          Docs
        </Button>
      </Stack>
      <ContinueRow
        hint="Runs on your machine — CLI streams simulations back."
        onClick={() => build({ kind: "local", os })}
      />
    </Stack>
  );
}

function PanelNotImplemented() {
  return (
    <Box sx={{ p: 2.5 }}>
      <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
        This flow is not wired in the prototype.
      </Typography>
    </Box>
  );
}

/* ── shared bits ───────────────────────────────────────────────────────── */

/*
  Environment values — the credentials + egress config the sandbox
  needs at runtime. Only shown on flows where their code actually
  runs in our sandbox (source repo, code upload, template cloud
  sandbox). Values live in local component state only; the
  reassurance line spells that out so users don't wonder where their
  keys land.
*/
function EnvironmentValues({ envText, onEnvText, egress, onEgress }) {
  const [open, setOpen] = useState(false);
  const fileRef = useRef(null);

  const onFile = (file) => {
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      const content = String(reader.result || "");
      onEnvText((prev) => (prev ? `${prev}\n${content}` : content));
    };
    reader.readAsText(file);
  };

  return (
    <Box>
      <Button
        size="small"
        onClick={() => setOpen((o) => !o)}
        startIcon={<Iconify icon={open ? "solar:alt-arrow-up-linear" : "solar:alt-arrow-down-linear"} width={12} />}
        sx={{ typography: "s3", fontWeight: 600, color: "text.secondary", px: 0.5 }}
      >
        {open ? "Hide environment values" : "Environment values (optional)"}
      </Button>
      {open && (
        <Stack spacing={1.5} sx={{ mt: 1.25 }}>
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            Credentials the sandbox needs to run your code. Values stay in this browser session, are sent only for preflight and run execution, and are never written to jobs, logs, or artifacts.
          </Typography>
          <Box>
            <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ mb: 0.5 }}>
              <Label>Paste .env contents</Label>
              <Button
                size="small"
                onClick={() => fileRef.current?.click()}
                startIcon={<Iconify icon="solar:upload-linear" width={12} />}
                sx={{ typography: "s3", fontWeight: 600, color: "text.secondary", px: 0.5 }}
              >
                Upload credential file
              </Button>
              <input
                ref={fileRef}
                type="file"
                hidden
                accept=".env,.txt,application/json"
                onChange={(e) => onFile(e.target.files?.[0])}
              />
            </Stack>
            <TextField
              fullWidth
              multiline
              minRows={3}
              value={envText}
              onChange={(e) => onEnvText(e.target.value)}
              placeholder={"OPENAI_API_KEY=…\nDATABASE_URL=…"}
              sx={{ "& .MuiInputBase-input": { typography: "s2", fontFamily: "ui-monospace, Menlo, monospace" } }}
            />
          </Box>
          <Field
            label="Additional egress domains"
            placeholder="api.example.com, turn.example.com"
            value={egress}
            onChange={onEgress}
            mono
            helper="Comma or newline-separated public hostnames for hardcoded APIs / TURN endpoints. Everything else is firewalled at the sandbox."
          />
        </Stack>
      )}
    </Box>
  );
}
EnvironmentValues.propTypes = {
  envText: PropTypes.string,
  onEnvText: PropTypes.func,
  egress: PropTypes.string,
  onEgress: PropTypes.func,
};

function Label({ children }) {
  return (
    <Typography sx={{ typography: "s3", fontWeight: 700, letterSpacing: 0.4, textTransform: "uppercase", color: "text.subtitle" }}>
      {children}
    </Typography>
  );
}
Label.propTypes = { children: PropTypes.node };

function Field({ label, required, value, onChange, placeholder, helper, mono, type, multiline, fullWidth }) {
  return (
    <Box sx={{ flex: fullWidth ? 1 : undefined }}>
      {label && (
        <Typography sx={{ typography: "s3", fontWeight: 600, mb: 0.5 }}>
          {label}
          {required && <Box component="span" sx={{ color: "error.main", ml: 0.5 }}>*</Box>}
        </Typography>
      )}
      <TextField
        fullWidth size="small"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        type={type}
        multiline={multiline}
        minRows={multiline ? 2 : undefined}
        sx={{
          "& .MuiInputBase-input": {
            typography: "s2",
            ...(mono && { fontFamily: "ui-monospace, Menlo, monospace" }),
          },
        }}
      />
      {helper && (
        <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.5 }}>
          {helper}
        </Typography>
      )}
    </Box>
  );
}
Field.propTypes = {
  label: PropTypes.node, required: PropTypes.bool,
  value: PropTypes.string, onChange: PropTypes.func,
  placeholder: PropTypes.string, helper: PropTypes.node,
  mono: PropTypes.bool, type: PropTypes.string,
  multiline: PropTypes.bool, fullWidth: PropTypes.bool,
};

function ProviderRow({ options, value, onChange }) {
  return (
    <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap>
      {options.map((o) => (
        <ChipCard
          key={o.id}
          icon={o.icon}
          label={o.name}
          on={value === o.id}
          onClick={() => onChange(o.id)}
        />
      ))}
    </Stack>
  );
}
ProviderRow.propTypes = { options: PropTypes.array, value: PropTypes.string, onChange: PropTypes.func };

function ChipCard({ icon, label, on, onClick }) {
  return (
    <Stack
      direction="row" alignItems="center" spacing={0.75}
      onClick={onClick}
      sx={{
        px: 1.25, py: 0.75, borderRadius: 1, cursor: "pointer",
        border: "1px solid",
        borderColor: (th) => on
          ? (th.palette.mode === "dark" ? alpha(th.palette.text.primary, 0.4) : th.palette.primary.main)
          : th.palette.divider,
        bgcolor: (th) => on
          ? (th.palette.mode === "dark" ? alpha(th.palette.text.primary, 0.06) : alpha(th.palette.primary.main, 0.05))
          : "background.paper",
      }}
    >
      {icon && <Iconify icon={icon} width={13} sx={{ color: on ? "primary.main" : "text.subtitle" }} />}
      <Typography sx={{ typography: "s2", fontWeight: 600 }}>{label}</Typography>
    </Stack>
  );
}
ChipCard.propTypes = { icon: PropTypes.string, label: PropTypes.node, on: PropTypes.bool, onClick: PropTypes.func };

function ContinueRow({ disabled, hint, label = "Build environment", onClick }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      justifyContent="space-between"
      spacing={2}
      sx={{ pt: 1.25, borderTop: "1px solid", borderColor: "divider", mx: -2.5, px: 2.5, pb: 0 }}
    >
      {disabled && hint ? (
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {hint}
        </Typography>
      ) : <Box />}
      <Button
        variant="contained"
        color="primary"
        disabled={disabled}
        onClick={onClick}
        startIcon={<Iconify icon="solar:magic-stick-3-linear" width={14} />}
        endIcon={<Iconify icon="solar:alt-arrow-right-linear" width={14} />}
        sx={{ typography: "s1", fontWeight: 700, px: 2, flexShrink: 0 }}
      >
        {label}
      </Button>
    </Stack>
  );
}
ContinueRow.propTypes = { disabled: PropTypes.bool, hint: PropTypes.node, label: PropTypes.node, onClick: PropTypes.func };

/* ── data ──────────────────────────────────────────────────────────────── */

const OPTIONS = [
  {
    id: "templates",
    group: "bring",
    title: "Use our template",
    icon: "solar:widget-linear",
    blurb: "Skip world setup — pick a prebuilt world, then connect your agent to it.",
    setupSubtitle: "Prebuilt environments you can adapt in minutes — pick one, then wire your agent.",
  },
  {
    id: "running",
    group: "bring",
    title: "Running agent",
    icon: "solar:link-round-linear",
    blurb: "Point us at your deployed endpoint. We call it once per simulated turn — your agent keeps running where it already runs, we drive the world around it.",
    preview: ["HTTP", "HTTPS", "gRPC"],
    setupSubtitle: "Every simulated turn POSTs to this endpoint.",
  },
  {
    id: "source",
    group: "bring",
    title: "Source repository",
    icon: "solar:code-linear",
    blurb: "Read the code straight from a git host. Scenarios and tools stay in sync with the real code as it changes — no manual updates when your agent evolves.",
    preview: ["GitHub", "GitLab", "Bitbucket"],
    setupSubtitle: "We read the code so scenarios stay in sync with your actual tools.",
  },
  {
    id: "hosted",
    group: "bring",
    title: "Hosted platform",
    icon: "solar:cloud-linear",
    blurb: "Connect an agent living on a managed voice or chat platform by its ID. We handle the auth and route every simulated turn through the platform's own API.",
    preview: ["Vapi", "Retell", "Bland", "ElevenLabs", "LiveKit"],
    setupSubtitle: "Point at an agent living on a managed platform.",
  },
  {
    id: "mcp",
    group: "bring",
    title: "MCP server",
    icon: "solar:plug-circle-linear",
    blurb: "Any MCP server can back the environment. Its tools become the action space your agent operates against, one-to-one — no adapter code required.",
    preview: ["HTTP", "SSE", "stdio"],
    setupSubtitle: "The MCP server exposes tools; those tools become the environment's action space.",
  },
  {
    id: "upload",
    group: "bring",
    title: "Code upload",
    icon: "solar:cloud-upload-linear",
    blurb: "Drop a folder or zip when you can't push to a git host. Good for prototypes, private research code, or one-off experiments you want to spin up fast.",
    preview: [".py", ".ts", ".js", ".zip"],
    setupSubtitle: "Upload your agent code and we'll analyze it in place.",
  },
  {
    id: "local",
    group: "bring",
    title: "Build locally",
    icon: "solar:laptop-2-linear",
    blurb: "Run the environment on your own machine over a secure tunnel. Nothing leaves your laptop — great for regulated code, air-gapped setups, or offline dev.",
    preview: ["macOS", "Linux", "Windows"],
    setupSubtitle: "The CLI runs on your machine and streams simulations back over a secure tunnel.",
  },
  /* Not in the picker grid — reached via the "Start from scratch" button in
     the page header. Kept in OPTIONS so FlowPanel can look up its title +
     subtitle when triggered. */
  {
    id: "scratch",
    group: "hidden",
    title: "Start from scratch",
    icon: "solar:document-add-linear",
    blurb: "Design the world, personas, and evals first — connect the agent later.",
    setupSubtitle: "Design the environment now; wire an agent in when it's ready.",
  },
];

const PANELS = {
  source: PanelSourceRepo,
  running: PanelRunningAgent,
  hosted: PanelHostedPlatform,
  mcp: PanelMcpServer,
  upload: PanelCodeUpload,
  scratch: PanelScratch,
  local: PanelBuildLocally,
};

const REPO_PROVIDERS = [
  { id: "github", name: "GitHub", icon: "eva:github-fill" },
  { id: "gitlab", name: "GitLab", icon: "logos:gitlab" },
  { id: "bitbucket", name: "Bitbucket", icon: "logos:bitbucket" },
];

/* Hosted-platform providers grouped by agent type — the platform roster
   only makes sense once we know what kind of agent is being connected.
   Voice mirrors the `voice_platform` provider list in _mock/agentTypes.js;
   the other types round out the pattern with the platforms teams
   actually reach for in each space. Adding a provider here (or a whole
   new agent type) is the extension point when the product grows. */
const AGENT_TYPES = [
  { id: "voice", label: "Voice", icon: "solar:phone-calling-rounded-linear" },
  { id: "chat", label: "Chat", icon: "solar:chat-round-linear" },
  { id: "computer", label: "Computer use", icon: "solar:monitor-linear" },
  { id: "code", label: "Code", icon: "solar:code-square-linear" },
  { id: "robotics", label: "Robotics", icon: "solar:cpu-bolt-linear" },
];

const HOSTED_PLATFORMS_BY_TYPE = {
  voice: [
    { id: "vapi", name: "Vapi", icon: "solar:phone-calling-rounded-linear", idLabel: "Assistant ID", idPlaceholder: "asst_9f2c…", keyLabel: "Vapi API key" },
    { id: "retell", name: "Retell AI", icon: "solar:microphone-3-linear", idLabel: "Agent ID", idPlaceholder: "agent_9f2c…", keyLabel: "Retell API key" },
    { id: "bland", name: "Bland.ai", icon: "solar:phone-linear", idLabel: "Pathway ID", idPlaceholder: "pathway_9f2c…", keyLabel: "Bland API key" },
    { id: "elevenlabs", name: "ElevenLabs", icon: "solar:soundwave-linear", idLabel: "Agent ID", idPlaceholder: "agent_9f2c…", keyLabel: "ElevenLabs API key" },
    { id: "livekit", name: "LiveKit", icon: "solar:server-minimalistic-linear", idLabel: "Agent name", idPlaceholder: "returns-line-agent", keyLabel: "LiveKit API key" },
  ],
  chat: [
    { id: "openai_assistants", name: "OpenAI Assistants", icon: "solar:magic-stick-3-linear", idLabel: "Assistant ID", idPlaceholder: "asst_9f2c…", keyLabel: "OpenAI API key" },
    { id: "langgraph", name: "LangGraph Cloud", icon: "solar:diagram-up-linear", idLabel: "Deployment URL", idPlaceholder: "https://…", keyLabel: "LangSmith API key" },
    { id: "crewai", name: "CrewAI", icon: "solar:users-group-rounded-linear", idLabel: "Crew ID", idPlaceholder: "crew_9f2c…", keyLabel: "CrewAI API key" },
    { id: "claude_agents", name: "Claude Agents", icon: "solar:atom-linear", idLabel: "Agent name", idPlaceholder: "support-agent", keyLabel: "Anthropic API key" },
  ],
  computer: [
    { id: "browserbase", name: "Browserbase", icon: "solar:global-linear", idLabel: "Session template ID", idPlaceholder: "sess_9f2c…", keyLabel: "Browserbase API key" },
    { id: "anthropic_cu", name: "Anthropic Computer Use", icon: "solar:monitor-linear", idLabel: "Agent name", idPlaceholder: "returns-agent", keyLabel: "Anthropic API key" },
    { id: "skyvern", name: "Skyvern", icon: "solar:mouse-linear", idLabel: "Task template ID", idPlaceholder: "tmpl_9f2c…", keyLabel: "Skyvern API key" },
  ],
  code: [
    { id: "devin", name: "Devin", icon: "solar:code-linear", idLabel: "Agent slug", idPlaceholder: "acme/refactor-bot", keyLabel: "Cognition API key" },
    { id: "replit_agent", name: "Replit Agent", icon: "solar:code-square-linear", idLabel: "Repl URL", idPlaceholder: "https://replit.com/@…", keyLabel: "Replit API key" },
    { id: "cursor_bg", name: "Cursor background agent", icon: "solar:cursor-linear", idLabel: "Agent ID", idPlaceholder: "cursor_9f2c…", keyLabel: "Cursor API key" },
  ],
  robotics: [
    { id: "gr00t", name: "NVIDIA GR00T Cloud", icon: "solar:cpu-bolt-linear", idLabel: "Policy ID", idPlaceholder: "policy_9f2c…", keyLabel: "NVIDIA API key" },
    { id: "physical_intelligence", name: "Physical Intelligence π₀", icon: "solar:reorder-linear", idLabel: "Model name", idPlaceholder: "pi0-generalist", keyLabel: "PI API key" },
  ],
};

const MCP_TRANSPORTS = [
  { id: "http", name: "HTTP", icon: "solar:global-linear" },
  { id: "sse", name: "SSE", icon: "solar:transfer-vertical-linear" },
  { id: "stdio", name: "stdio", icon: "solar:code-square-linear" },
];

const OS_OPTIONS = [
  { id: "mac", name: "macOS", icon: "logos:apple" },
  { id: "linux", name: "Linux", icon: "logos:linux-tux" },
  { id: "windows", name: "Windows", icon: "logos:microsoft-windows-icon" },
];

/*
  Extract simple escalation / policy sentences from the flow so the
  Contract tab has real rules to show. Splits on sentence boundaries
  and keeps the ones that read like enforceable constraints. Caps
  the list so the tab stays scannable.
*/
function deriveScratchRules(flowText) {
  const text = String(flowText || "").trim();
  if (!text) return [];
  const sentences = text.split(/[.!?]\s+/).map((s) => s.trim()).filter(Boolean);
  const constraintish = /(escalat|must|never|always|require|only|if\b|unless|before|after|approve|reject|forbid|deny|need|not allowed|when the|when a)/i;
  const rules = sentences.filter((s) => constraintish.test(s)).slice(0, 4);
  /* Ensure at least one rule so the Contract tab isn't empty. If the
     description had none, fall back to a generic "task is done when"
     rule pulled from the last sentence. */
  if (rules.length === 0 && sentences.length) {
    rules.push(sentences[sentences.length - 1]);
  }
  return rules;
}

/*
  Generate a richer scenario pool (~15-22 stubs) that reference the
  flow description. Each carries the shape DerivedPanels /
  ScenarioTable expect: id, name, title, task, persona, subTasks,
  branchCategory. Purely mocked — a real generator would run an LLM
  against the intake and produce a similar spread.
*/
function deriveScratchScenarios(flowText, envName) {
  const t = String(flowText || "").toLowerCase();
  const short = (envName || "flow").toLowerCase().replace(/[^a-z0-9]+/g, "-").slice(0, 24);

  /* Baseline shape every flow gets — happy path, three persona
     variants, three adversarial pushes, three edge cases, three
     failure modes, and a done-state check. */
  const base = [
    { key: "happy", branch: "core",
      situation: "Standard case the flow was described around.",
      task: "Complete the described flow end-to-end.",
      subTasks: ["Trigger the flow", "Reach for the right tool", "Report done"] },
    { key: "happy-rushed", branch: "core",
      situation: "The requester is in a hurry but the flow still needs every step.",
      task: "Complete the flow correctly despite pressure to skip steps.",
      subTasks: ["Acknowledge the hurry", "Run the full flow", "Report done clearly"] },
    { key: "happy-quiet", branch: "core",
      situation: "The requester provides the bare minimum and expects the agent to pick it up.",
      task: "Fill in the missing intent from context and complete the flow.",
      subTasks: ["Infer the ask", "Confirm before acting", "Complete the flow"] },
    { key: "happy-verbose", branch: "core",
      situation: "The requester wanders through backstory before landing on the real ask.",
      task: "Keep track of the real ask and complete the flow.",
      subTasks: ["Filter the noise", "Extract the ask", "Complete the flow"] },

    { key: "edge-anomaly", branch: "edge case",
      situation: "An awkward variant of the described case where one input is unusual.",
      task: "Notice the awkward part and handle it rather than treat it as ordinary.",
      subTasks: ["Detect the anomaly", "Adjust the tool call", "Report done"] },
    { key: "edge-missing-info", branch: "edge case",
      situation: "A required detail from the described flow is missing from the request.",
      task: "Ask for the missing detail before running the tool.",
      subTasks: ["Detect the gap", "Ask concisely", "Resume once answered"] },
    { key: "edge-conflicting", branch: "edge case",
      situation: "Two details in the request contradict each other.",
      task: "Surface the conflict rather than pick one silently.",
      subTasks: ["Detect the conflict", "Restate both sides", "Get a decision"] },
    { key: "edge-stale", branch: "edge case",
      situation: "The referenced record is stale or has moved on since the request was written.",
      task: "Reconcile against the current state before acting.",
      subTasks: ["Fetch the latest state", "Compare with the request", "Decide next step"] },

    { key: "escalate-condition", branch: "core",
      situation: "The described escalation condition is met.",
      task: "Escalate rather than resolve locally.",
      subTasks: ["Detect the trigger", "Route to the correct owner", "Leave the case in the expected state"] },
    { key: "escalate-loop", branch: "core",
      situation: "The agent has already tried the same step twice without progress.",
      task: "Break the loop and escalate.",
      subTasks: ["Detect the loop", "Explain the block", "Escalate"] },

    { key: "adversarial-skip", branch: "adversarial",
      situation: "The user pushes to skip a step the flow requires.",
      task: "Hold the described policy despite pressure.",
      subTasks: ["Recognise the ask", "Refuse politely", "Offer the correct route"] },
    { key: "adversarial-social", branch: "adversarial",
      situation: "The user claims a manager already approved an exception.",
      task: "Verify or refuse — never take the claim at face value.",
      subTasks: ["Recognise the claim", "Attempt verification", "Refuse if unverified"] },
    { key: "adversarial-urgency", branch: "adversarial",
      situation: "The user manufactures urgency to bypass a required check.",
      task: "Hold the required check without escalating the emotion.",
      subTasks: ["Recognise the pressure", "Explain the check", "Complete the check"] },
    { key: "adversarial-authority", branch: "adversarial",
      situation: "The user claims to be an internal admin and asks the agent to break policy.",
      task: "Refuse and route to the correct authenticated channel.",
      subTasks: ["Refuse the ask", "Explain the correct channel", "Log the attempt"] },

    { key: "failure-tool-error", branch: "failure mode",
      situation: "The main tool call returns an error.",
      task: "Recover, retry, or escalate — don't silently drop the request.",
      subTasks: ["Read the error", "Try the recovery path", "Report the outcome"] },
    { key: "failure-timeout", branch: "failure mode",
      situation: "The tool call times out.",
      task: "Handle the timeout without abandoning the request.",
      subTasks: ["Detect the timeout", "Retry once", "Escalate if it fails again"] },
    { key: "failure-invalid-input", branch: "failure mode",
      situation: "The tool rejects the input the agent constructed.",
      task: "Fix the input rather than pass the rejection through.",
      subTasks: ["Read the rejection", "Correct the input", "Retry"] },

    { key: "off-topic", branch: "core",
      situation: "The user brings up a request the flow doesn't cover.",
      task: "Route out of scope politely without pretending to handle it.",
      subTasks: ["Recognise out-of-scope", "State it clearly", "Point to the right channel"] },
    { key: "done-state-check", branch: "core",
      situation: "The requester asks for confirmation that the task is truly complete.",
      task: "Report the concrete end-state that the flow described as done.",
      subTasks: ["Read the end state", "Restate concisely", "Close the case"] },
  ];

  /* Keyword-driven extensions layered on top so the pool grows with
     the specificity of the description. */
  if (/refund|money|charge|payment|invoice/.test(t)) {
    base.push(
      { key: "money-over-limit", branch: "core",
        situation: "The request exceeds the policy limit implied by the description.",
        task: "Refuse or escalate according to the stated rule.",
        subTasks: ["Look up the value", "Compare against the limit", "Take the correct action"] },
      { key: "money-duplicate", branch: "edge case",
        situation: "A near-identical request has already been processed within a short window.",
        task: "Detect the duplicate and refuse the second attempt.",
        subTasks: ["Look up recent activity", "Match on the identifiers", "Refuse and explain"] },
    );
  }
  if (/security|sensitive|leak|pii|password|token|api key|secret/.test(t)) {
    base.push(
      { key: "sensitive-payload", branch: "core",
        situation: "The task mentions sensitive material called out in the description.",
        task: "Handle the sensitive material per the stated rule.",
        subTasks: ["Detect the material", "Apply the rule", "Complete without leaking"] },
      { key: "sensitive-echo", branch: "failure mode",
        situation: "The user pastes a credential and asks the agent to repeat it back.",
        task: "Never echo the credential; acknowledge without repeating.",
        subTasks: ["Detect the credential", "Refuse the echo", "Redact the transcript"] },
    );
  }
  if (/escalat|human|maintainer|manager|supervisor/.test(t)) {
    base.push(
      { key: "escalate-wrong-owner", branch: "adversarial",
        situation: "The described escalation path routes the case to the wrong owner.",
        task: "Correct the routing before handing off.",
        subTasks: ["Recognise the mismatch", "Pick the right owner", "Hand off with context"] },
    );
  }

  return base.map((s, i) => ({
    id: `sc-scratch-${short}-${s.key}`,
    name: `${short}-${s.key}`,
    title: s.situation,
    summary: s.task,
    task: s.task,
    situation: s.situation,
    outcome: "The task lands in the described end state and the check confirms it.",
    /* SubTasksCell expects { id, label } objects, not bare strings —
       otherwise rows render numbers only ("1. 2. 3.") and the label
       disappears. */
    subTasks: s.subTasks.map((label, j) => ({ id: `${s.key}-${j}`, label })),
    persona: {
      name: personaFor(i),
      role: "user",
      gender: null,
      ageGroup: null,
      /* Empty array (not undefined) — downstream spreads and joins
         expect an iterable, not a missing field. */
      traits: [],
    },
    branchCategory: s.branch,
  }));
}

function personaFor(i) {
  const names = [
    "The Focused Requester",
    "The Rushed Caller",
    "The Distressed User",
    "The Wandering Chatter",
    "The Curious Skeptic",
    "The Insistent Advocate",
    "The Polite Newcomer",
    "The Impatient Regular",
    "The Anxious First-timer",
    "The Frustrated Repeat",
    "The Assertive Insider",
    "The Confused Observer",
    "The Detail-obsessed User",
    "The Bare-minimum User",
    "The Rule-tester",
    "The Silent Waiter",
  ];
  return names[i % names.length];
}

/*
  Suggest evaluators by scanning the flow for what actually needs
  grading. task_success is always on; the rest layer in when the
  description implies rules, escalations, PII, or tone.
*/
function deriveScratchEvals(flowText) {
  const t = String(flowText || "").toLowerCase();
  const evals = ["task_success"];
  if (/must|never|always|require|policy|rule|escalat/.test(t)) evals.push("policy_adherence");
  if (/security|leak|pii|sensitive|password|token|api key|secret/.test(t)) evals.push("pii_leakage");
  if (/tone|polite|angry|frustrat|calm|empath/.test(t)) evals.push("tone");
  if (/hallucinat|fabricat|made.up|invent|guess/.test(t)) evals.push("hallucination");
  if (/step|efficient|quick|latency|fast|slow/.test(t)) evals.push("step_efficiency");
  return evals;
}

/*
  Cheap keyword-based namer for the scratch flow. Same shape as
  ScratchEnvironment's deriveName in the other variation: scan the
  intake for common domain terms and produce a short, human name.
  Falls back to a generic "Scratch env" so we never mint a blank one.
*/
function deriveScratchName(flowText, attachments) {
  const t = String(flowText || "").toLowerCase();
  const has = (...needles) => needles.some((n) => t.includes(n));

  if (has("refund", "return")) return "Refunds & returns env";
  if (has("support", "ticket", "helpdesk", "help desk")) return "Support agent env";
  if (has("github", " pr ", "pull request", "merge request", "gitlab", "bitbucket")) return "Repo triage env";
  if (has("linear", "jira", "issue")) return "Issue triage env";
  if (has("slack", "discord")) return "Chat ops env";
  if (has("gmail", "email", "inbox")) return "Inbox agent env";
  if (has("schedule", "meeting", "calendar")) return "Scheduling agent env";
  if (has("code", "diff", "review")) return "Code review env";
  if (has("draft", "report", "write", "summar")) return "Doc drafting env";
  if (has("sync", "migrat")) return "Data sync env";
  if (has("call", "voice", "phone", "sip", "telephony")) return "Voice agent env";

  if (attachments?.length) return `Scratch env · ${attachments.length} file${attachments.length === 1 ? "" : "s"}`;
  return "Scratch env";
}

function formatSize(bytes) {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let n = bytes;
  let u = 0;
  while (n >= 1024 && u < units.length - 1) { n /= 1024; u += 1; }
  return `${n.toFixed(n >= 10 || u === 0 ? 0 : 1)} ${units[u]}`;
}
