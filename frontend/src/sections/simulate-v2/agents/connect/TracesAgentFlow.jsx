import PropTypes from "prop-types";
import { useEffect, useMemo, useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Button, CircularProgress, MenuItem, Select, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { useSimStore } from "../../store";
import { FRAMEWORK_META } from "../../_mock/agentRegistry";
import { OBSERVE_PROJECTS, observeSlugOf } from "../../_mock/connectAgent";
import { TRACE_AI_FRAMEWORKS, traceAiSetupFor } from "../../_mock/integrations";
import { agentReadFromTraces, firstTraceRead } from "../../_mock/tracesAgent";
import { Field, SegmentedToggle } from "../../environments/StartEnvironment";
import LocalSteps from "../../environments/LocalSteps";
import { LogoTile } from "../../environments/LandingBits";
import ConnectSteps from "./ConnectSteps";
import { TextLink, WhereOption } from "./connectBits";
import { detectedRequestOf } from "./detectedRequest";

const MONO = "ui-monospace, Menlo, monospace";
const STEPS = ["Connect"];
/* How long the prototype waits before the first trace "arrives". */
const FIRST_TRACE_MS = 4500;

/**
 * An agent from its traces. Two ways in, one screen:
 *   · Already tracing — pick the Observe project; we say what the traces show.
 *   · Send traces now — add traceAI, run the agent, and the first trace is read.
 * Either way the agent is saved with the prompt, model and tools its spans
 * report, linked to that project from the start. When the traces show it runs
 * on a platform or in code we can connect, that comes first — it tests more
 * and fixes go further.
 */
export default function TracesAgentFlow({ title, onSave, onOpen, initial = null, lockedType = null }) {
  const { state } = useSimStore();
  const [mode, setMode] = useState(initial?.mode === "send" ? "send" : "project");
  const [projectId, setProjectId] = useState(null);
  const [name, setName] = useState("");
  const [agentType, setAgentType] = useState(lockedType || null);

  /* Send now: what's being instrumented, and whether a trace has come in. */
  const [framework, setFramework] = useState("OpenAI SDK");
  const [language, setLanguage] = useState("Python");
  const [newProject, setNewProject] = useState("my-agent");
  const [listening, setListening] = useState(false);
  /* The steps by hand, folded under the one prompt a coding agent can follow. */
  const [byHand, setByHand] = useState(false);
  const [received, setReceived] = useState(false);
  useEffect(() => {
    if (!listening) return undefined;
    const t = setTimeout(() => {
      setReceived(true);
      setListening(false);
      /* Named after the project until someone names it. */
      setName((n) => n || newProject.replace(/[-_]+/g, " ").replace(/^\w/, (c) => c.toUpperCase()));
    }, FIRST_TRACE_MS);
    return () => clearTimeout(t);
  }, [listening]); // eslint-disable-line react-hooks/exhaustive-deps

  /* Projects already linked to an agent say so — one Observe project per agent. */
  const linkedName = (id) => {
    const key = state?.projectLinks?.[id];
    return key ? (state?.connectedAgents || []).find((a) => a.key === key)?.name || "an agent" : null;
  };
  const project = OBSERVE_PROJECTS.find((p) => p.id === projectId) || null;
  const projectSlug = observeSlugOf(newProject);
  const read = useMemo(() => {
    if (mode === "project") return project ? agentReadFromTraces(project) : null;
    return received ? firstTraceRead({ project: newProject, framework, channel: lockedType || "chat" }) : null;
  }, [mode, project, received, newProject, framework, lockedType]);

  /* The two ways in name different agents — a name typed or taken from one
     doesn't carry to the other. */
  const switchMode = (m) => {
    if (m === mode) return;
    setMode(m);
    setName(m === "project" ? (project?.traced?.agentName || project?.name || "") : received ? newProject.replace(/[-_]+/g, " ").replace(/^\w/, (c) => c.toUpperCase()) : "");
  };
  const pickProject = (p) => {
    setProjectId(p.id);
    setName(p.traced?.agentName || p.name);
    setAgentType(lockedType || null);
  };
  const setup = traceAiSetupFor({ framework, language, project: projectSlug });

  const channel = agentType || read?.channel || "chat";
  const source = read && {
    kind: "traces",
    agentType: channel,
    name: name.trim() || (mode === "project" ? project?.name : newProject),
    project: mode === "project" ? project?.id : projectSlug,
    observeProject: mode === "project" ? { mode: "existing", id: project?.id } : { mode: "create", id: projectSlug },
    traceCount: read.traceCount,
    prompt: read.prompt,
    model: read.model,
    tools: read.tools,
    ...(read.framework && { framework: read.framework }),
    ...(mode === "send" && { language }),
    versionNote: mode === "project"
      ? `Read from ${read.traceCount.toLocaleString()} traces in ${project?.name}.`
      : `Read from the first trace in ${projectSlug}.`,
  };

  const next = {
    label: "Save as agent",
    icon: "solar:pulse-2-linear",
    disabled: !source || !source.name,
    hint: mode === "project" ? "Pick the project its traces land in" : received ? "Name the agent" : "Waiting for the first trace",
    onClick: () => onSave(source),
  };

  return (
    <ConnectSteps
      title={title}
      subtitle="We read the agent from its traces — the prompt, model and tools it ran with — and link the project they land in."
      steps={STEPS}
      active={0}
      next={next}
    >
      <Stack spacing={2}>
        <Box sx={{ display: "grid", gap: 1, gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr" } }} role="radiogroup" aria-label="Where the traces are">
          <WhereOption
            icon="solar:pulse-2-linear" label="Already sending traces" short="They land in an Observe project"
            hint="Pick the project — we read the agent from its most recent traces."
            on={mode === "project"} onClick={() => switchMode("project")}
          />
          <WhereOption
            icon="solar:code-scan-linear" label="Send traces now" short="Add traceAI to the agent's code"
            hint="A few lines in your agent's code; we wait for the first trace."
            on={mode === "send"} onClick={() => switchMode("send")}
          />
        </Box>

        {mode === "project" ? (
          <ProjectList projects={OBSERVE_PROJECTS} picked={projectId} onPick={pickProject} linkedName={linkedName} />
        ) : (
          <Box>
            <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5} alignItems={{ sm: "flex-start" }} sx={{ mb: 2 }}>
              <Box sx={{ minWidth: 220 }}>
                <Typography sx={{ typography: "s3", fontWeight: 600, mb: 0.5 }}>Framework</Typography>
                <Select size="small" fullWidth value={framework} onChange={(e) => { setFramework(e.target.value); setReceived(false); }} sx={{ typography: "s2" }}>
                  {TRACE_AI_FRAMEWORKS.map((f) => <MenuItem key={f.id} value={f.id} sx={{ typography: "s2" }}>{f.label}</MenuItem>)}
                </Select>
              </Box>
              <Box>
                <Typography sx={{ typography: "s3", fontWeight: 600, mb: 0.5 }}>Language</Typography>
                <SegmentedToggle value={language} onChange={(v) => { setLanguage(v); setReceived(false); }} options={[{ value: "Python", label: "Python" }, { value: "TypeScript", label: "TypeScript" }]} />
              </Box>
              <Box flex={1} minWidth={0}>
                <Field label="Project its traces land in" fullWidth mono value={newProject} onChange={(v) => { setNewProject(v); setReceived(false); }} />
              </Box>
            </Stack>
            {language === "TypeScript" && !setup.ts && (
              <Typography sx={{ typography: "s3", color: "text.subtitle", mb: 1.5 }}>
                {`traceAI instruments ${framework} in Python only — these are the Python steps.`}
              </Typography>
            )}
            <CodingAgentPrompt framework={framework} language={setup.ts ? "TypeScript" : "Python"} project={projectSlug} setup={setup} />
            <TextLink onClick={() => setByHand((v) => !v)} sx={{ mt: 1.25 }}>{byHand ? "Hide the steps" : "Set it up by hand instead — three steps"}</TextLink>
            {byHand && (
              <Box sx={{ mt: 1.25 }}>
                <LocalSteps
                  steps={[
                    { title: "Install traceAI", body: "In the agent's environment.", cmd: setup.install },
                    { title: "Set your keys", body: "Where the agent runs. Copy them from Keys — they're read from the environment, never written into the code.", cmd: setup.env },
                    { title: "Add these lines before the agent starts", body: `Every call it makes is traced to ${projectSlug}.`, cmd: setup.code },
                  ]}
                />
              </Box>
            )}
            {listening ? (
              <ListeningPanel project={projectSlug} onStop={() => setListening(false)} />
            ) : (
            <Stack
              direction="row" alignItems="center" spacing={1.25}
              sx={{ mt: 2, px: 1.75, py: 1.25, border: "1px solid", borderColor: "divider", borderRadius: 1 }}
            >
              {received ? (
                <Iconify icon="solar:check-circle-bold" width={16} sx={{ color: "#16A34A", flexShrink: 0 }} />
              ) : listening ? (
                <CircularProgress size={14} thickness={5} sx={{ color: "text.subtitle", flexShrink: 0 }} />
              ) : (
                <Iconify icon="solar:play-circle-linear" width={16} sx={{ color: "text.subtitle", flexShrink: 0 }} />
              )}
              <Typography sx={{ typography: "s2", color: received ? "text.primary" : "text.secondary", flex: 1, minWidth: 0 }}>
                {received
                  ? `First trace received in ${projectSlug} · ${read?.spans || 1} spans`
                  : listening ? `Waiting for the first trace in ${projectSlug} — run your agent once…` : "Then run your agent once — we'll pick up its first trace."}
              </Typography>
              {!received && !listening && (
                <Button size="small" variant="outlined" onClick={() => setListening(true)} sx={{ typography: "s3", fontWeight: 700, color: "text.primary", borderColor: "divider", flexShrink: 0 }}>
                  Listen for traces
                </Button>
              )}
            </Stack>
            )}
          </Box>
        )}

        {read && <ReadResult read={read} name={name} onName={setName} agentType={channel} onAgentType={lockedType ? null : setAgentType} onOpen={onOpen} />}
      </Stack>
    </ConnectSteps>
  );
}

/*
  The fastest way in: one prompt for the coding agent already in the repo
  (Claude Code, Cursor, Codex) — it installs traceAI, wires the keys and
  registers the project. The three steps by hand stay one click away.
*/
const promptFor = ({ framework, language, project, setup }) => [
  `Add Future AGI tracing (traceAI) to this agent so every call it makes is traced to the Observe project "${project}".`,
  "",
  `- It's ${/^[aeiou]/i.test(framework) ? "an" : "a"} ${framework} agent in ${language}. Install: ${setup.install}`,
  "- Read FI_API_KEY and FI_SECRET_KEY from the environment where the agent runs. Never write them into the code.",
  "- Before the agent starts, register the project and instrument the framework:",
  "",
  setup.code,
  "",
  "Then run the agent once so its first trace is sent.",
].join("\n");

function CodingAgentPrompt({ framework, language, project, setup }) {
  const [copied, setCopied] = useState(false);
  const text = promptFor({ framework, language, project, setup });
  const copy = () => {
    navigator.clipboard?.writeText(text).catch(() => {});
    setCopied(true);
    setTimeout(() => setCopied(false), 1600);
  };
  return (
    <Box sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1, overflow: "hidden" }}>
      <Stack direction="row" alignItems="center" spacing={1} sx={{ px: 1.5, py: 1, borderBottom: "1px solid", borderColor: "divider" }}>
        <Iconify icon="solar:magic-stick-3-linear" width={15} sx={{ color: "text.secondary", flexShrink: 0 }} />
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>Ask your coding agent to add it</Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{"Paste this into Claude Code, Cursor or Codex, in the agent's repository."}</Typography>
        </Box>
        <Button
          size="small" variant="outlined" onClick={copy}
          startIcon={<Iconify icon={copied ? "solar:check-circle-linear" : "solar:copy-linear"} width={14} />}
          sx={{ typography: "s3", fontWeight: 700, height: 26, color: "text.primary", borderColor: "divider", flexShrink: 0 }}
        >
          {copied ? "Copied" : "Copy prompt"}
        </Button>
      </Stack>
      <Typography
        component="pre"
        sx={{ m: 0, px: 1.5, py: 1.25, maxHeight: 200, overflow: "auto", whiteSpace: "pre-wrap", fontFamily: "ui-monospace, Menlo, monospace", fontSize: 12, lineHeight: 1.55, color: "text.secondary", bgcolor: "background.neutral" }}
      >
        {text}
      </Typography>
    </Box>
  );
}
CodingAgentPrompt.propTypes = { framework: PropTypes.string, language: PropTypes.string, project: PropTypes.string, setup: PropTypes.object };

/*
  While we wait for the first trace: what we're waiting on, how long it's
  been, and — if nothing comes — what usually stops it. Other tracing tools
  show this as its own state; a single line read as nothing happening.
*/
const SLOW_MS = 15000;
function ListeningPanel({ project, onStop }) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const started = Date.now();
    const t = setInterval(() => setElapsed(Date.now() - started), 1000);
    return () => clearInterval(t);
  }, []);
  const secs = Math.floor(elapsed / 1000);
  const steps = [
    { label: `Project ${project} is ready for traces`, done: true },
    { label: "Your agent runs once — any request to it is enough", done: false, now: true },
    { label: "The first trace arrives, usually within a few seconds", done: false },
  ];
  return (
    <Box sx={{ mt: 2, border: "1px solid", borderColor: "divider", borderRadius: 1, overflow: "hidden" }}>
      <Stack direction="row" alignItems="center" spacing={1.25} sx={{ px: 1.75, py: 1.25, borderBottom: "1px solid", borderColor: "divider" }}>
        <CircularProgress size={14} thickness={5} sx={{ color: "text.subtitle", flexShrink: 0 }} />
        <Typography sx={{ typography: "s2", fontWeight: 600, flex: 1, minWidth: 0 }}>{`Listening for the first trace in ${project}`}</Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle", fontVariantNumeric: "tabular-nums" }}>{`${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, "0")}`}</Typography>
        <Button size="small" onClick={onStop} sx={{ typography: "s3", fontWeight: 700, color: "text.secondary", minWidth: 0 }}>Stop</Button>
      </Stack>
      <Stack spacing={0.75} sx={{ px: 1.75, py: 1.25 }}>
        {steps.map((st) => (
          <Stack key={st.label} direction="row" alignItems="center" spacing={1}>
            {st.done
              ? <Iconify icon="solar:check-circle-linear" width={15} sx={{ color: "text.secondary", flexShrink: 0 }} />
              : st.now
                ? <CircularProgress size={11} thickness={6} sx={{ color: "text.disabled", flexShrink: 0, mx: "2px" }} />
                : <Box sx={{ width: 11, height: 11, mx: "2px", borderRadius: "50%", border: "1px solid", borderColor: "divider", flexShrink: 0 }} />}
            <Typography sx={{ typography: "s2", color: st.done || st.now ? "text.primary" : "text.disabled" }}>{st.label}</Typography>
          </Stack>
        ))}
        {elapsed >= SLOW_MS && (
          <Typography sx={{ typography: "s3", color: "text.secondary", pt: 0.5 }}>
            Nothing yet? Check that FI_API_KEY and FI_SECRET_KEY are set where the agent runs, and that the lines above run before the agent starts.
          </Typography>
        )}
      </Stack>
    </Box>
  );
}
ListeningPanel.propTypes = { project: PropTypes.string.isRequired, onStop: PropTypes.func.isRequired };

TracesAgentFlow.propTypes = {
  title: PropTypes.string,
  onSave: PropTypes.func.isRequired,
  /* Opens another way in — the platform or code the traces point at. */
  onOpen: PropTypes.func,
  initial: PropTypes.object,
  lockedType: PropTypes.string,
};

/* The projects traces land in — trace count and recency, and whether one is already an agent's. */
function ProjectList({ projects, picked, onPick, linkedName }) {
  return (
    <Box sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1, overflow: "hidden", maxHeight: 320, overflowY: "auto" }} role="radiogroup" aria-label="Observe project">
      {projects.map((p, i) => {
        const linked = linkedName(p.id);
        const on = picked === p.id;
        return (
          <Stack
            key={p.id}
            direction="row" alignItems="center" spacing={1.5}
            role="radio" aria-checked={on} aria-disabled={!!linked} tabIndex={linked ? -1 : 0}
            onClick={() => !linked && onPick(p)}
            onKeyDown={(e) => { if (!linked && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); onPick(p); } }}
            sx={{
              px: 1.75, py: 1.1, borderTop: i ? "1px solid" : "none", borderColor: "divider",
              cursor: linked ? "default" : "pointer",
              bgcolor: (t) => (on ? alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.06 : 0.03) : "transparent"),
              "&:hover": linked ? undefined : { bgcolor: (t) => alpha(t.palette.text.primary, 0.04) },
            }}
          >
            <Iconify icon={on ? "solar:check-circle-bold" : "solar:circle-linear"} width={16} sx={{ color: on ? "text.primary" : "text.disabled", flexShrink: 0 }} />
            <Box flex={1} minWidth={0}>
              <Typography noWrap sx={{ typography: "s2", fontWeight: 600, color: linked ? "text.secondary" : "text.primary" }}>{p.name}</Typography>
              <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>{p.traces}</Typography>
            </Box>
            {linked && <Typography noWrap sx={{ typography: "s3", color: "text.subtitle", flexShrink: 0 }}>{`Linked to ${linked}`}</Typography>}
          </Stack>
        );
      })}
    </Box>
  );
}
ProjectList.propTypes = { projects: PropTypes.array, picked: PropTypes.string, onPick: PropTypes.func, linkedName: PropTypes.func };

/* What the traces said — the better way in first when they point at one, then the agent as read. */
function ReadResult({ read, name, onName, agentType, onAgentType, onOpen }) {
  const [promptOpen, setPromptOpen] = useState(false);
  const elsewhere = read.route === "platform"
    ? {
      logo: read.provider,
      title: `These are ${read.providerLabel} call logs${read.traced?.agentName ? ` from ${read.traced.agentName}` : ""}`,
      body: `Connect it as a ${read.providerLabel} agent and it's tested live in your account, with fixes made there — not just its prompt.`,
      action: `Connect as a ${read.providerLabel} agent`,
      go: () => onOpen?.(detectedRequestOf(read.traced)),
    }
    : read.route === "code"
      ? {
        icon: "mdi:github",
        title: `These traces come from ${FRAMEWORK_META[read.framework]?.label || read.framework} code${read.language ? ` in ${read.language}` : ""}`,
        body: "Connect its repository and it runs sealed in our sandbox, with fixes as pull requests — not just its prompt.",
        action: "Connect the repository",
        go: () => onOpen?.(detectedRequestOf(read.traced)),
      }
      : null;

  return (
    <Stack spacing={1.5}>
      {elsewhere && onOpen && (
        <Stack direction="row" alignItems="center" spacing={1.5} sx={{ px: 1.75, py: 1.5, border: "1px solid", borderColor: "divider", borderRadius: 1 }}>
          <LogoTile logo={elsewhere.logo} icon={elsewhere.icon} />
          <Box flex={1} minWidth={0}>
            <Typography sx={{ typography: "s2", fontWeight: 700 }}>{elsewhere.title}</Typography>
            <Typography sx={{ typography: "s3", color: "text.secondary" }}>{elsewhere.body}</Typography>
          </Box>
          <Button size="small" variant="contained" color="primary" onClick={elsewhere.go} sx={{ typography: "s2", fontWeight: 700, flexShrink: 0 }}>
            {elsewhere.action}
          </Button>
        </Stack>
      )}

      <Box>
        <Stack direction="row" alignItems="baseline" spacing={1} sx={{ mb: 0.75 }}>
          <Typography sx={{ typography: "s2", fontWeight: 700 }}>{elsewhere ? "Or save it from its traces" : "What the traces say"}</Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            {read.traceCount > 1 ? `from its last ${Math.min(read.traceCount, 200)} of ${read.traceCount.toLocaleString()} traces` : "from its first trace"}
          </Typography>
        </Stack>
        <Box sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1 }}>
          <Row label="Model"><Typography sx={{ typography: "s2", fontFamily: MONO }}>{read.model}</Typography></Row>
          <Row label="Tools">
            <Typography sx={{ typography: "s2", fontFamily: MONO, wordBreak: "break-word" }}>{read.tools.map((t) => t.name).join(", ")}</Typography>
          </Row>
          <Row label="System prompt">
            <Typography sx={{ typography: "s2", whiteSpace: "pre-wrap", color: "text.secondary", ...(!promptOpen && { display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }) }}>
              {read.prompt}
            </Typography>
            <Button size="small" onClick={() => setPromptOpen((o) => !o)} sx={{ typography: "s3", fontWeight: 700, color: "text.secondary", px: 0, minWidth: 0, mt: 0.25 }}>
              {promptOpen ? "Show less" : "Show all"}
            </Button>
          </Row>
          <Row label="Runs as">
            <Typography sx={{ typography: "s2" }}>Prompt only — its prompt and tools, on our model</Typography>
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>Until you connect where it runs. Fixes come back as a suggested change.</Typography>
          </Row>
        </Box>
      </Box>

      <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5} alignItems={{ sm: "flex-end" }}>
        <Box flex={1} minWidth={0}><Field label="Agent name" required fullWidth value={name} onChange={onName} placeholder="e.g. Returns assistant" /></Box>
        {onAgentType && (
          <Box sx={{ flexShrink: 0 }}>
            <Typography sx={{ typography: "s3", fontWeight: 600, mb: 0.5 }}>Talks over</Typography>
            <SegmentedToggle value={agentType} onChange={onAgentType} options={[{ value: "chat", label: "Chat" }, { value: "voice", label: "Voice" }]} />
          </Box>
        )}
      </Stack>
    </Stack>
  );
}
ReadResult.propTypes = {
  read: PropTypes.object.isRequired, name: PropTypes.string, onName: PropTypes.func,
  agentType: PropTypes.string, onAgentType: PropTypes.func, onOpen: PropTypes.func,
};

function Row({ label, children }) {
  return (
    <Stack direction="row" spacing={2} sx={{ px: 1.75, py: 1, "& + &": { borderTop: "1px solid", borderColor: "divider" } }}>
      <Typography sx={{ typography: "s2", color: "text.subtitle", width: 120, flexShrink: 0 }}>{label}</Typography>
      <Box flex={1} minWidth={0}>{children}</Box>
    </Stack>
  );
}
Row.propTypes = { label: PropTypes.string, children: PropTypes.node };
