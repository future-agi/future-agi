import React, { useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Autocomplete,
  Box,
  Button,
  IconButton,
  LinearProgress,
  MenuItem,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import { keyframes } from "@mui/system";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { RouterLink } from "src/routes/components";
import { paths } from "src/routes/paths";
import Iconify from "src/components/iconify";
import PageHeader from "src/components/page-header";
import CodeBlock from "src/components/code-block";
import axios, { endpoints } from "src/utils/axios";
import {
  PYTHON_DEFINE,
  AGENT_PROMPT as SIM_AGENT_PROMPT,
  MCP_ADD_CMD,
} from "src/sections/simulate-v2/environments/fromCode";
import { LogoTile } from "src/sections/simulate-v2/environments/LandingBits";

/*
  Get started — simulation first.

  The main path is testing your agent in simulation: your coding agent
  (recommended), the CLI or Python, three numbered steps, and step 3 waits
  for the first run and links to it — with a way out (a sample agent) if
  nothing arrives. Tracing, evals and the gateway are one row below; Trace
  your app swaps the main path for the tracing setup (`?start=trace`), the
  same three steps waiting for the first trace.

  Real keys go into every snippet, shown masked and copied in full. A setup
  checklist on the right ticks itself off from the onboarding status (keys,
  first trace, first dataset, first evaluation) and the simulation history.
*/

const DOCS = "https://docs.futureagi.com";
const AGENT_DOCS = "https://docs.futureagi.com/llms.txt";

const TRACE_AGENT_PROMPT =
  "Add Future AGI tracing to this application. Install the traceAI SDK, register the tracer with project_type OBSERVE using my FI_API_KEY and FI_SECRET_KEY, and instrument every LLM and agent call following Future AGI's best practices. Refer to https://docs.futureagi.com for details.";

const TRACE_INSTALL_FALLBACK = "pip install fi-instrumentation-otel";
const TRACE_REGISTER_FALLBACK = `from fi_instrumentation import register
from fi_instrumentation.fi_types import ProjectType

trace_provider = register(
    project_type=ProjectType.OBSERVE,
    project_name="my-first-project",
)`;

const body = { fontSize: 13, lineHeight: "20px", color: "text.secondary" };
const clean = (code) =>
  typeof code === "string" ? code.replace(/^\n+/, "").replace(/\n+$/, "") : "";

/* How the agent is built → how `fai env init` points at it. */
const SIM_FRAMEWORKS = [
  {
    id: "openai-agents",
    label: "OpenAI Agents SDK",
    agent: "--entry agent.py:agent",
  },
  { id: "langgraph", label: "LangGraph", agent: "--entry graph.py:graph" },
  { id: "crewai", label: "CrewAI", agent: "--entry crew.py:crew" },
  { id: "pydantic-ai", label: "Pydantic AI", agent: "--entry agent.py:agent" },
  {
    id: "http",
    label: "Any language, over HTTP (TypeScript, Vercel AI SDK, Mastra…)",
    agent: "--agent-url http://localhost:3000/chat",
  },
  {
    id: "vapi",
    label: "Vapi (voice)",
    agent: "--vapi-assistant <assistant-id>",
  },
  { id: "retell", label: "Retell (voice)", agent: "--retell-agent <agent-id>" },
];

/* Prototype sessions get a representative framework list when the tracer
   API isn't reachable; a real backend always uses the live list. */
const isProtoSession = () =>
  import.meta.env.VITE_PROTOTYPE_AUTH_BYPASS === "true" &&
  localStorage.getItem("oss_proto_session") === "1";

const GH = "https://github.com/future-agi/traceAI";
const pyInstr = (pkg, cls) =>
  `from traceai_${pkg} import ${cls}\n\n# Trace every call automatically\n${cls}().instrument(tracer_provider=tracer)`;
const tsInstr = (mod, cls) =>
  `import { ${cls} } from "@traceai/${mod}";\n\nregisterInstrumentations({\n  instrumentations: [new ${cls}()],\n});`;

/* Shape mirrors getCodeBlockTracer().result.instruments. */
const MOCK_INSTRUMENTS = {
  langchain: {
    name: "LangChain",
    Python: { github: GH, code: pyInstr("langchain", "LangChainInstrumentor") },
    TypeScript: {
      github: GH,
      code: tsInstr("langchain", "LangChainInstrumentation"),
    },
  },
  langgraph: {
    name: "LangGraph",
    Python: { github: GH, code: pyInstr("langchain", "LangChainInstrumentor") },
  },
  openai: {
    name: "OpenAI",
    Python: { github: GH, code: pyInstr("openai", "OpenAIInstrumentor") },
    TypeScript: {
      github: GH,
      code: tsInstr("openai", "OpenAIInstrumentation"),
    },
  },
  openai_agents: {
    name: "OpenAI Agents",
    Python: {
      github: GH,
      code: pyInstr("openai_agents", "OpenAIAgentsInstrumentor"),
    },
  },
  anthropic: {
    name: "Anthropic",
    Python: { github: GH, code: pyInstr("anthropic", "AnthropicInstrumentor") },
  },
  bedrock: {
    name: "AWS Bedrock",
    Python: { github: GH, code: pyInstr("bedrock", "BedrockInstrumentor") },
  },
  gemini: {
    name: "Google Gemini",
    Python: {
      github: GH,
      code: pyInstr("google_genai", "GoogleGenAIInstrumentor"),
    },
  },
  vertexai: {
    name: "Vertex AI",
    Python: { github: GH, code: pyInstr("vertexai", "VertexAIInstrumentor") },
  },
  mistral: {
    name: "Mistral AI",
    Python: { github: GH, code: pyInstr("mistralai", "MistralAIInstrumentor") },
  },
  groq: {
    name: "Groq",
    Python: { github: GH, code: pyInstr("groq", "GroqInstrumentor") },
  },
  crewai: {
    name: "CrewAI",
    Python: { github: GH, code: pyInstr("crewai", "CrewAIInstrumentor") },
  },
  litellm: {
    name: "LiteLLM",
    Python: { github: GH, code: pyInstr("litellm", "LiteLLMInstrumentor") },
  },
  haystack: {
    name: "Haystack",
    Python: { github: GH, code: pyInstr("haystack", "HaystackInstrumentor") },
  },
  smolagents: {
    name: "Smolagents",
    Python: {
      github: GH,
      code: pyInstr("smolagents", "SmolagentsInstrumentor"),
    },
  },
  pydantic_ai: {
    name: "Pydantic AI",
    Python: {
      github: GH,
      code: pyInstr("pydantic_ai", "PydanticAIInstrumentor"),
    },
  },
  vercel_ai: {
    name: "Vercel AI SDK",
    TypeScript: {
      github: GH,
      code: tsInstr("vercel", "VercelAIInstrumentation"),
    },
  },
  mcp: {
    name: "MCP (Model Context Protocol)",
    Python: { github: GH, code: pyInstr("mcp", "MCPInstrumentor") },
  },
};

/*
  Brand marks, drawn the way the Build environment catalog draws them
  (LogoTile: Iconify brand sets, mono marks in the text colour, colour where
  the brand has one) — not the API's PNGs, whose baked-in padding and
  backgrounds don't line up. Keyed by the framework's name or id, squashed
  to letters; anything without a reliable mark gets a letter tile.
*/
const BRAND_ICON = {
  langchain: "simple-icons:langchain",
  langgraph: "simple-icons:langgraph",
  openai: "simple-icons:openai",
  openaiagents: "simple-icons:openai",
  anthropic: "simple-icons:anthropic",
  mcp: "simple-icons:modelcontextprotocol",
  mcpmodelcontextprotocol: "simple-icons:modelcontextprotocol",
  bedrock: "simple-icons:amazonwebservices",
  awsbedrock: "simple-icons:amazonwebservices",
  crewai: "simple-icons:crewai",
  dspy: "fagi:dspy",
  groq: "bxl:groq-ai",
  haystack: "simple-icons:haystack",
  litellm: "selfhst:litellm",
  llamaindex: "thesvg:llamaindex",
  mistral: "logos:mistral-ai-icon",
  mistralai: "logos:mistral-ai-icon",
  vertexai: "simple-icons:googlecloud",
  googleadk: "simple-icons:google",
  googlegenai: "simple-icons:googlegemini",
  gemini: "simple-icons:googlegemini",
  googlegemini: "simple-icons:googlegemini",
  smolagents: "simple-icons:huggingface",
  ollama: "simple-icons:ollama",
  pydanticai: "simple-icons:pydantic",
  vercelai: "simple-icons:vercel",
  vercelaisdk: "simple-icons:vercel",
};
const squash = (v) =>
  String(v || "")
    .toLowerCase()
    .replace(/[^a-z0-9]/g, "");
const brandIconOf = (key, item) =>
  BRAND_ICON[squash(key)] || BRAND_ICON[squash(item?.name)] || null;

function FrameworkMark({ id, item, size = 24 }) {
  const icon = brandIconOf(id, item);
  if (icon) return <LogoTile icon={icon} size={size} />;
  return (
    <Box
      aria-hidden
      sx={{
        width: size,
        height: size,
        flexShrink: 0,
        borderRadius: 0.75,
        display: "grid",
        placeItems: "center",
        border: "1px solid",
        borderColor: "divider",
        bgcolor: "action.hover",
        typography: "s3",
        fontWeight: 600,
        color: "text.secondary",
      }}
    >
      {(item?.name || id || "?").trim()[0]?.toUpperCase()}
    </Box>
  );
}
FrameworkMark.propTypes = {
  id: PropTypes.string,
  item: PropTypes.object,
  size: PropTypes.number,
};

/* The frameworks most people start from — shown first; the rest on "Show all". */
const POPULAR = [
  "openai",
  "openaiagents",
  "anthropic",
  "langchain",
  "langgraph",
  "crewai",
  "llamaindex",
  "bedrock",
];

// ---- keys: the workspace's own, masked on screen, copied in full -----------
function useFiKeys() {
  /* The workspace's newest enabled key — the list the Keys page shows. */
  const { data, isLoading } = useQuery({
    queryKey: ["get-started-keys"],
    queryFn: () =>
      axios.get(endpoints.keys.getKeys, {
        params: {
          current_page_index: 0,
          page_size: 10,
          sort_field: "created_at",
          sort_order: "desc",
        },
      }),
    select: (d) =>
      (d.data?.result?.table || []).find((r) => r.enabled !== false) || null,
  });
  const mask = (v) =>
    v ? `${String(v).slice(0, 4)}••••••••${String(v).slice(-4)}` : null;
  const api = data?.api_key || null;
  const secret = data?.secret_key || null;
  const shown = (masked) =>
    `export FI_API_KEY=${(masked ? mask(api) : api) || "your_api_key"}\nexport FI_SECRET_KEY=${(masked ? mask(secret) : secret) || "your_secret_key"}`;
  return {
    isLoading,
    name: data?.key_name || null,
    has: !!api,
    api,
    secret,
    mask,
    envShown: shown(true),
    envCopy: shown(false),
  };
}

function KeyValue({ name, value, masked }) {
  const [copied, setCopied] = useState(false);
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={0.75}
      sx={{ minWidth: 0 }}
    >
      <Typography
        sx={{ typography: "s3", color: "text.tertiary", flexShrink: 0 }}
      >
        {name}
      </Typography>
      <Typography
        noWrap
        sx={{ fontFamily: "monospace", fontSize: 12, color: "text.primary" }}
      >
        {masked}
      </Typography>
      <Tooltip title={copied ? "Copied" : `Copy ${name}`}>
        <IconButton
          size="small"
          aria-label={`Copy ${name}`}
          onClick={() => {
            navigator.clipboard?.writeText(value);
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
          }}
        >
          <Iconify
            icon={copied ? "solar:check-read-linear" : "solar:copy-linear"}
            width={14}
          />
        </IconButton>
      </Tooltip>
    </Stack>
  );
}
KeyValue.propTypes = {
  name: PropTypes.string,
  value: PropTypes.string,
  masked: PropTypes.string,
};

/* One line under the header: the keys every snippet below already uses. */
function KeyStrip({ keys }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={2}
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        px: 1.5,
        minHeight: 40,
        flexWrap: "wrap",
        rowGap: 0.5,
        mb: 3,
      }}
    >
      <Iconify
        icon="solar:key-linear"
        width={16}
        sx={{ color: "text.tertiary" }}
      />
      {keys.isLoading ? (
        <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
          Loading your keys…
        </Typography>
      ) : keys.has ? (
        <>
          <KeyValue
            name="FI_API_KEY"
            value={keys.api}
            masked={keys.mask(keys.api)}
          />
          <KeyValue
            name="FI_SECRET_KEY"
            value={keys.secret}
            masked={keys.mask(keys.secret)}
          />
          <Typography
            noWrap
            title="These keys are filled into every snippet below"
            sx={{
              typography: "s3",
              color: "text.tertiary",
              flex: 1,
              minWidth: 0,
            }}
          >
            {keys.name || "Used below"}
          </Typography>
        </>
      ) : (
        <Typography sx={{ typography: "s3", color: "text.tertiary", flex: 1 }}>
          No keys yet — create one and it fills in every snippet below.
        </Typography>
      )}
      <Tooltip
        arrow
        title="Self-hosting? Point the SDK and CLI at your deployment with FI_BASE_URL."
      >
        <Iconify
          icon="solar:info-circle-linear"
          width={14}
          aria-label="Self-hosting"
          sx={{ color: "text.disabled", flexShrink: 0 }}
        />
      </Tooltip>
      <Button
        component={RouterLink}
        href={paths.dashboard.keys}
        size="small"
        variant="text"
        sx={{ color: "text.secondary" }}
      >
        {keys.has ? "Manage keys" : "Create key"}
      </Button>
    </Stack>
  );
}
KeyStrip.propTypes = { keys: PropTypes.object };

// ---- what's done: onboarding status + simulation history --------------------
function useOnboardingStatus({ poll } = {}) {
  const { data } = useQuery({
    queryKey: ["get-started-status"],
    queryFn: () => axios.get(endpoints.getStarted.getTabs),
    select: (d) => d.data?.result || {},
    refetchInterval: poll ? 5000 : false,
  });
  return data || {};
}

/*
  The simulation history, read-only from the prototype's browser store, so
  this page can see a run land without mounting (and saving) a second copy
  of the store. Polls while asked to; never writes.
*/
const readSimRuns = () =>
  new Promise((resolve) => {
    if (typeof indexedDB === "undefined") return resolve([]);
    const req = indexedDB.open("fagi.simulate");
    req.onupgradeneeded = () => req.transaction?.abort();
    req.onerror = () => resolve([]);
    req.onsuccess = () => {
      const db = req.result;
      try {
        const st = db.transaction("state").objectStore("state");
        const keysReq = st.getAllKeys();
        keysReq.onsuccess = () => {
          const key = (keysReq.result || [])
            .map(String)
            .filter((k) => /^fagi\.simulate\.studio\.v\d+$/.test(k))
            .sort(
              (a, b) => Number(b.split(".v")[1]) - Number(a.split(".v")[1]),
            )[0];
          if (!key) return resolve([]);
          const getReq = st.get(key);
          getReq.onsuccess = () => {
            const v = getReq.result;
            const o = typeof v === "string" ? JSON.parse(v) : v;
            resolve(
              Object.entries(o?.byEnv || {}).flatMap(([envId, es]) =>
                (es?.runs || []).map((r) => ({ ...r, envId })),
              ),
            );
          };
          getReq.onerror = () => resolve([]);
        };
        keysReq.onerror = () => resolve([]);
      } catch {
        resolve([]);
      }
    };
  });

function useSimRuns({ poll } = {}) {
  const [runs, setRuns] = useState([]);
  useEffect(() => {
    let alive = true;
    const load = () => readSimRuns().then((r) => alive && setRuns(r));
    load();
    const t = poll ? setInterval(load, 5000) : null;
    return () => {
      alive = false;
      if (t) clearInterval(t);
    };
  }, [poll]);
  return runs;
}

// ---- numbered step on the shared rail ---------------------------------------
function Step({ n, title, action, last, children }) {
  return (
    <Stack direction="row" spacing={1.75}>
      <Stack alignItems="center" sx={{ flexShrink: 0 }}>
        <Box
          sx={{
            width: 24,
            height: 24,
            borderRadius: "50%",
            display: "grid",
            placeItems: "center",
            border: "1px solid",
            borderColor: "divider",
            typography: "s3",
            fontWeight: 600,
            color: "text.secondary",
          }}
        >
          {n}
        </Box>
        {!last && (
          <Box
            sx={{
              flex: 1,
              width: "1px",
              bgcolor: "divider",
              my: 0.75,
              minHeight: 24,
            }}
          />
        )}
      </Stack>
      <Box sx={{ flex: 1, minWidth: 0, pb: last ? 0 : 3 }}>
        <Stack
          direction="row"
          alignItems="center"
          spacing={1.5}
          sx={{ minHeight: 24, mb: 0.5 }}
        >
          <Typography sx={{ typography: "s2", fontWeight: 600, flex: 1 }}>
            {title}
          </Typography>
          {action}
        </Stack>
        {children}
      </Box>
    </Stack>
  );
}
Step.propTypes = {
  n: PropTypes.number,
  title: PropTypes.node,
  action: PropTypes.node,
  last: PropTypes.bool,
  children: PropTypes.node,
};

const Body = ({ children, mb = 1.25 }) => (
  <Typography sx={{ ...body, mb }}>{children}</Typography>
);
Body.propTypes = { children: PropTypes.node, mb: PropTypes.number };

// ---- the listening step: waiting → arrived, with a way out -----------------
const sonar = keyframes`
  0%   { transform: scale(0.6); opacity: 0.7; }
  100% { transform: scale(2.4); opacity: 0; }
`;

function Listening({
  arrived,
  waitingTitle,
  waitingBody,
  arrivedTitle,
  arrivedBody,
  action,
  wayOut,
}) {
  /* After a minute with nothing, say how to get unstuck. */
  const [slow, setSlow] = useState(false);
  useEffect(() => {
    if (arrived) return undefined;
    const t = setTimeout(() => setSlow(true), 60000);
    return () => clearTimeout(t);
  }, [arrived]);
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        px: 1.5,
        py: 1.25,
      }}
    >
      <Stack direction="row" alignItems="center" spacing={1.5}>
        {arrived ? (
          <Iconify
            icon="solar:check-circle-bold"
            width={20}
            sx={{ color: "status.success.fg", flexShrink: 0 }}
          />
        ) : (
          <Box
            aria-hidden
            sx={{
              position: "relative",
              width: 20,
              height: 20,
              display: "grid",
              placeItems: "center",
              flexShrink: 0,
            }}
          >
            {[0, 1].map((i) => (
              <Box
                key={i}
                sx={{
                  position: "absolute",
                  inset: 0,
                  borderRadius: "50%",
                  border: "1px solid",
                  borderColor: (t) => alpha(t.palette.text.primary, 0.45),
                  animation: `${sonar} 2s ease-out ${i}s infinite`,
                  "@media (prefers-reduced-motion: reduce)": {
                    animation: "none",
                  },
                }}
              />
            ))}
            <Box
              sx={{
                width: 6,
                height: 6,
                borderRadius: "50%",
                bgcolor: "text.primary",
              }}
            />
          </Box>
        )}
        <Box sx={{ flex: 1, minWidth: 0 }} role="status" aria-live="polite">
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>
            {arrived ? arrivedTitle : waitingTitle}
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            {arrived ? arrivedBody : waitingBody}
          </Typography>
        </Box>
        {arrived && action}
      </Stack>
      {!arrived && slow && wayOut && (
        <Typography
          sx={{ typography: "s3", color: "text.tertiary", mt: 1, pl: 4 }}
        >
          {wayOut}
        </Typography>
      )}
    </Box>
  );
}
Listening.propTypes = {
  arrived: PropTypes.bool,
  waitingTitle: PropTypes.node,
  waitingBody: PropTypes.node,
  arrivedTitle: PropTypes.node,
  arrivedBody: PropTypes.node,
  action: PropTypes.node,
  wayOut: PropTypes.node,
};

const InlineLink = ({ href, to, children }) => (
  <Box
    component={to ? RouterLink : "a"}
    {...(to
      ? { href: to }
      : { href, target: "_blank", rel: "noopener noreferrer" })}
    sx={{
      color: "text.primary",
      textDecoration: "underline",
      textUnderlineOffset: 2,
      "&:hover": { color: "text.secondary" },
    }}
  >
    {children}
  </Box>
);
InlineLink.propTypes = {
  href: PropTypes.string,
  to: PropTypes.string,
  children: PropTypes.node,
};

function PathTabs({ value, onChange, options, label }) {
  return (
    <ToggleButtonGroup
      exclusive
      size="small"
      value={value}
      onChange={(_, v) => v && onChange(v)}
      aria-label={label}
      sx={{ mb: 2 }}
    >
      {options.map((o) => (
        <ToggleButton key={o.value} value={o.value}>
          {o.label}
          {o.note && (
            <Box
              component="span"
              sx={{ ml: 0.75, color: "text.tertiary", fontWeight: 400 }}
            >
              {o.note}
            </Box>
          )}
        </ToggleButton>
      ))}
    </ToggleButtonGroup>
  );
}
PathTabs.propTypes = {
  value: PropTypes.string,
  onChange: PropTypes.func,
  options: PropTypes.array,
  label: PropTypes.string,
};

// ---- Test your agent in simulation (the main path) -------------------------
function SimulateSection({ keys }) {
  const [mode, setMode] = useState("agent"); // agent | cli | python
  const [fw, setFw] = useState(SIM_FRAMEWORKS[0].id);
  const framework =
    SIM_FRAMEWORKS.find((f) => f.id === fw) || SIM_FRAMEWORKS[0];
  /* A run counts as "yours" if it started after this page opened. */
  const since = useRef(Date.now()).current;
  const runs = useSimRuns({ poll: true });
  const latest = useMemo(
    () =>
      runs
        .filter((r) => Date.parse(r.startedAt || 0) > since)
        .sort((a, b) => Date.parse(b.startedAt) - Date.parse(a.startedAt))[0] ||
      null,
    [runs, since],
  );

  const listen = (n) => (
    <Step n={n} title="See your first run" last>
      <Listening
        arrived={!!latest}
        waitingTitle="Waiting for your first run"
        waitingBody="It shows up here the moment it starts — results stream into Environments."
        arrivedTitle={latest ? latest.label || "Your run started" : ""}
        arrivedBody={
          latest
            ? latest.status === "running"
              ? "Running now — open it to watch the conversations."
              : `${latest.passed ?? 0} of ${latest.total ?? 0} passed`
            : ""
        }
        action={
          latest && (
            <Button
              component={RouterLink}
              href={paths.dashboard.simulate.environmentStep(
                latest.envId,
                "runs",
              )}
              size="small"
              variant="contained"
              color="primary"
            >
              Open run
            </Button>
          )
        }
        wayOut={
          <>
            Not seeing it?{" "}
            <InlineLink to={paths.dashboard.simulate.environmentTemplates}>
              Try a sample agent
            </InlineLink>{" "}
            · <InlineLink href={DOCS}>Troubleshooting</InlineLink>
          </>
        }
      />
    </Step>
  );

  return (
    <Box>
      <PathTabs
        label="How to run it"
        value={mode}
        onChange={setMode}
        options={[
          { value: "agent", label: "Your coding agent", note: "Recommended" },
          { value: "cli", label: "CLI" },
          { value: "python", label: "Python" },
        ]}
      />

      {mode === "agent" && (
        <>
          <Step
            n={1}
            title="Add Future AGI to your coding agent"
            action={
              <Button
                size="small"
                variant="text"
                href={AGENT_DOCS}
                target="_blank"
                rel="noopener noreferrer"
                endIcon={
                  <Iconify icon="solar:arrow-right-up-linear" width={14} />
                }
                sx={{ color: "text.secondary" }}
              >
                Docs for coding agents
              </Button>
            }
          >
            <Body>
              Claude Code shown. Cursor, VS Code and Windsurf are in Settings →
              MCP Server.
            </Body>
            <CodeBlock code={MCP_ADD_CMD} />
          </Step>
          <Step n={2} title="Ask it to test your agent">
            <Body>
              In your agent&apos;s repo. It builds an environment from your
              code, runs it, and fixes what fails.
            </Body>
            <CodeBlock code={SIM_AGENT_PROMPT} wrap />
          </Step>
          {listen(3)}
        </>
      )}

      {mode === "cli" && (
        <>
          <Step n={1} title="Install and sign in">
            <Body>Once per machine — adds the Python SDK and the fai CLI.</Body>
            <CodeBlock code="pip install futureagi && fai login" />
            <Typography
              sx={{
                typography: "s3",
                color: "text.tertiary",
                mt: 1.25,
                mb: 0.75,
              }}
            >
              In CI, set your keys instead of signing in
            </Typography>
            <CodeBlock code={keys.envShown} copyText={keys.envCopy} />
          </Step>
          <Step n={2} title="Point an environment at your agent and run it">
            <Body>
              In your agent&apos;s project — the environment lives next to your
              code, versioned with it. --scenarios sets how many to generate.
            </Body>
            <TextField
              select
              size="small"
              label="Your agent is built with"
              value={fw}
              onChange={(e) => setFw(e.target.value)}
              sx={{ maxWidth: 420, width: "100%", mb: 1.25 }}
            >
              {SIM_FRAMEWORKS.map((f) => (
                <MenuItem key={f.id} value={f.id}>
                  {f.label}
                </MenuItem>
              ))}
            </TextField>
            <CodeBlock
              code={`fai env init my-first-env ${framework.agent} --scenarios 10\nfai env up my-first-env && fai sim run my-first-env --repeats 3`}
            />
          </Step>
          {listen(3)}
        </>
      )}

      {mode === "python" && (
        <>
          <Step n={1} title="Install and add your keys">
            <Body>Your keys are already in — copy and run.</Body>
            <CodeBlock
              code={`pip install futureagi\n${keys.envShown}`}
              copyText={`pip install futureagi\n${keys.envCopy}`}
            />
          </Step>
          <Step n={2} title="Define it and run it">
            <Body>Next to your agent. env.run() starts the run here.</Body>
            <CodeBlock code={PYTHON_DEFINE} />
          </Step>
          {listen(3)}
        </>
      )}

      {/* The way out when there's no agent to hand. */}
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.5}
        sx={{
          mt: 3,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
          px: 1.5,
          py: 1.25,
        }}
      >
        <Iconify
          icon="solar:play-circle-linear"
          width={20}
          sx={{ color: "text.secondary", flexShrink: 0 }}
        />
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>
            No agent handy?
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            Run a ready-made world with its sample agent — nothing to connect,
            results in a minute.
          </Typography>
        </Box>
        <Button
          component={RouterLink}
          href={paths.dashboard.simulate.environmentTemplates}
          size="small"
          variant="outlined"
        >
          Try a sample agent
        </Button>
      </Stack>
    </Box>
  );
}
SimulateSection.propTypes = { keys: PropTypes.object };

// ---- Trace your app ----------------------------------------------------------
function useObserveCode() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["get-started-observe-code"],
    queryFn: () =>
      axios.get(endpoints.project.getCodeBlockTracer, {
        params: { project_type: "observe" },
      }),
    select: (d) => d.data?.result,
  });
  const useMock = isProtoSession() && (!!error || !data?.instruments);
  return {
    isLoading: isLoading && !useMock,
    error,
    install: data?.installation_guide,
    register: data?.project_add_code,
    instruments: useMock ? MOCK_INSTRUMENTS : data?.instruments,
  };
}

function TraceSection({ keys, status }) {
  const [mode, setMode] = useState("agent"); // agent | sdk
  const [lang, setLang] = useState("python");
  const [selected, setSelected] = useState(null);
  const code = useObserveCode();

  /* Popular first, in that order; the rest as the API lists them. */
  const options = useMemo(() => {
    if (!code.instruments) return [];
    const rank = (key, item) => {
      const i = POPULAR.findIndex(
        (p) => p === squash(key) || p === squash(item?.name),
      );
      return i === -1 ? POPULAR.length : i;
    };
    return Object.entries(code.instruments)
      .map(([key, item], i) => ({ key, item, r: rank(key, item), i }))
      .sort((a, b) => a.r - b.r || a.i - b.i)
      .map((x) => ({
        ...x,
        group: x.r < POPULAR.length ? "Popular" : "All frameworks",
      }));
  }, [code.instruments]);
  useEffect(() => {
    if (!selected && options.length) setSelected(options[0].key);
  }, [options, selected]);

  const opt = options.find((o) => o.key === selected) || null;
  const sel = opt?.item;
  const langKey = lang === "python" ? "Python" : "TypeScript";
  const langData = sel ? sel[langKey] || sel.Python || sel.TypeScript : null;
  const hasBoth = !!(sel?.Python && sel?.TypeScript);
  /* The framework's own package, read off its import line (traceai_openai →
     traceAI-openai, @traceai/openai). */
  const frameworkPkg = (() => {
    const c = langData?.code || "";
    const py = c.match(/from (traceai_[a-z0-9_]+) import/);
    if (lang === "python" && py)
      return py[1].replace("traceai_", "traceAI-").replace(/_/g, "-");
    const ts = c.match(/from "(@traceai\/[a-z0-9-]+)"/);
    return lang !== "python" && ts ? ts[1] : null;
  })();
  const baseInstall = clean(code.install?.[langKey]) || TRACE_INSTALL_FALLBACK;
  const install =
    frameworkPkg && !baseInstall.includes(frameworkPkg)
      ? `${baseInstall} ${frameworkPkg}`
      : baseInstall;
  const register = clean(code.register?.[langKey]) || TRACE_REGISTER_FALLBACK;
  /* One complete snippet: register the tracer, then instrument the framework. */
  const snippet = [register, clean(langData?.code)]
    .filter(Boolean)
    .join("\n\n");

  const listen = (
    <Step n={3} title="See your first trace" last>
      <Listening
        arrived={!!status.observe}
        waitingTitle="Waiting for your first trace"
        waitingBody="Run your app — the first trace shows up here within seconds."
        arrivedTitle="Traces are arriving"
        arrivedBody="Your app is sending traces to Future AGI."
        action={
          <Button
            component={RouterLink}
            href={paths.dashboard.observe}
            size="small"
            variant="contained"
            color="primary"
          >
            Open Tracing
          </Button>
        }
        wayOut={
          <>
            Not seeing it? Check both keys are set, and FI_BASE_URL if you
            self-host · <InlineLink href={DOCS}>Troubleshooting</InlineLink>
          </>
        }
      />
    </Step>
  );

  return (
    <Box>
      <PathTabs
        label="How to set it up"
        value={mode}
        onChange={setMode}
        options={[
          { value: "agent", label: "Your coding agent", note: "Recommended" },
          { value: "sdk", label: "SDK" },
        ]}
      />
      {mode === "agent" ? (
        <>
          <Step n={1} title="Add your keys to the app's environment">
            <Body>Your keys are already in — copy and run.</Body>
            <CodeBlock code={keys.envShown} copyText={keys.envCopy} />
          </Step>
          <Step n={2} title="Ask your coding agent to add tracing">
            <Body>
              Claude Code, Cursor, Copilot or any coding agent — in your
              app&apos;s repo.
            </Body>
            <CodeBlock code={TRACE_AGENT_PROMPT} wrap />
          </Step>
          {listen}
        </>
      ) : (
        <>
          <Step n={1} title="Install and add your keys">
            <Body>Your keys are already in — copy and run.</Body>
            <CodeBlock
              code={`${install}\n${keys.envShown}`}
              copyText={`${install}\n${keys.envCopy}`}
            />
          </Step>
          <Step n={2} title="Instrument your app">
            <Stack
              direction="row"
              alignItems="center"
              spacing={1}
              sx={{ mb: 1.25, flexWrap: "wrap", rowGap: 1 }}
            >
              {code.isLoading ? (
                <LinearProgress sx={{ flex: 1 }} />
              ) : options.length ? (
                <Autocomplete
                  size="small"
                  disableClearable
                  options={options}
                  groupBy={(o) => o.group}
                  value={opt}
                  onChange={(_, o) => o && setSelected(o.key)}
                  getOptionLabel={(o) => o?.item?.name || ""}
                  isOptionEqualToValue={(a, b) => a.key === b.key}
                  renderOption={(props, o) => (
                    <Box component="li" {...props} key={o.key} sx={{ gap: 1 }}>
                      <FrameworkMark id={o.key} item={o.item} size={20} />
                      {o.item?.name}
                    </Box>
                  )}
                  renderInput={(params) => (
                    <TextField
                      {...params}
                      label="Your framework"
                      placeholder="Search frameworks"
                      InputProps={{
                        ...params.InputProps,
                        startAdornment: opt ? (
                          <Box sx={{ display: "flex", ml: 0.5 }}>
                            <FrameworkMark
                              id={opt.key}
                              item={opt.item}
                              size={20}
                            />
                          </Box>
                        ) : null,
                      }}
                    />
                  )}
                  sx={{ width: 320 }}
                />
              ) : (
                <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                  Couldn&apos;t load the framework list — use your coding agent
                  instead.
                </Typography>
              )}
              <Box sx={{ flex: 1 }} />
              {langData?.github && (
                <Tooltip title="View on GitHub">
                  <IconButton
                    size="small"
                    href={langData.github}
                    target="_blank"
                    rel="noopener noreferrer"
                    aria-label="View on GitHub"
                  >
                    <Iconify icon="mdi:github" width={16} />
                  </IconButton>
                </Tooltip>
              )}
              {hasBoth && (
                <ToggleButtonGroup
                  exclusive
                  size="small"
                  value={lang}
                  onChange={(_, v) => v && setLang(v)}
                  aria-label="Language"
                >
                  <ToggleButton value="python">Python</ToggleButton>
                  <ToggleButton value="typescript">TypeScript</ToggleButton>
                </ToggleButtonGroup>
              )}
            </Stack>
            <CodeBlock code={snippet} />
          </Step>
          {listen}
        </>
      )}
    </Box>
  );
}
TraceSection.propTypes = { keys: PropTypes.object, status: PropTypes.object };

// ---- Or start with ----------------------------------------------------------
function StartTile({ icon, title, desc, onClick, to }) {
  return (
    <Box
      component={to ? RouterLink : "button"}
      {...(to ? { href: to } : { type: "button", onClick })}
      sx={{
        all: "unset",
        boxSizing: "border-box",
        cursor: "pointer",
        display: "flex",
        alignItems: "center",
        gap: 1.25,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        px: 1.5,
        py: 1.25,
        color: "text.primary",
        "&:hover": { bgcolor: "action.hover" },
        "&:focus-visible": {
          outline: "2px solid",
          outlineColor: "primary.main",
          outlineOffset: 1,
        },
      }}
    >
      <Box
        sx={{
          width: 28,
          height: 28,
          borderRadius: 1,
          display: "grid",
          placeItems: "center",
          bgcolor: "background.muted",
          color: "text.secondary",
          flexShrink: 0,
        }}
      >
        <Iconify icon={icon} width={16} />
      </Box>
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Stack direction="row" alignItems="center" spacing={0.75}>
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>
            {title}
          </Typography>
        </Stack>
        <Typography noWrap sx={{ typography: "s3", color: "text.tertiary" }}>
          {desc}
        </Typography>
      </Box>
      <Iconify
        icon={
          to ? "solar:arrow-right-up-linear" : "solar:alt-arrow-right-linear"
        }
        width={14}
        sx={{ color: "text.disabled" }}
      />
    </Box>
  );
}
StartTile.propTypes = {
  icon: PropTypes.string,
  title: PropTypes.node,
  desc: PropTypes.node,
  onClick: PropTypes.func,
  to: PropTypes.string,
};

// -----------------------------------------------------------------------------
export default function GetStartedLaunchView() {
  const [params, setParams] = useSearchParams();
  const start = params.get("start") === "trace" ? "trace" : "simulate";
  const setStart = (v) => {
    const next = new URLSearchParams(params);
    if (v === "trace") next.set("start", "trace");
    else next.delete("start");
    setParams(next, { replace: true });
  };
  const keys = useFiKeys();
  const status = useOnboardingStatus({
    poll: start === "trace",
  });
  const trace = start === "trace";

  return (
    <Box sx={{ p: 3, width: "100%", bgcolor: "background.paper" }}>
      {/* One column, end to end. Each item says it's done where it lives:
          keys in the key strip, the first run at step 3, the rest on their
          tiles below. */}
      <Box>
        <PageHeader
          title={trace ? "Trace your app" : "Test your agent in simulation"}
          actions={
            <Button
              size="small"
              variant="outlined"
              href={DOCS}
              target="_blank"
              rel="noopener noreferrer"
              endIcon={
                <Iconify icon="solar:arrow-right-up-linear" width={14} />
              }
            >
              View docs
            </Button>
          }
          description={
            trace
              ? "Send every LLM and agent call to Future AGI — three steps, a couple of minutes."
              : "Run your agent against simulated users and a simulated world before it ships — from your coding agent, the CLI or Python."
          }
          back={
            trace ? (
              <IconButton
                size="small"
                aria-label="Back to simulation"
                onClick={() => setStart("simulate")}
                sx={{ mt: 0.25 }}
              >
                <Iconify icon="solar:alt-arrow-left-linear" width={16} />
              </IconButton>
            ) : null
          }
        />
        <KeyStrip keys={keys} />

        {trace ? (
          <TraceSection keys={keys} status={status} />
        ) : (
          <SimulateSection keys={keys} />
        )}

        <Typography sx={{ typography: "s2", fontWeight: 600, mt: 4, mb: 1.25 }}>
          Or start with
        </Typography>
        <Box
          sx={{
            display: "grid",
            gap: 1.25,
            gridTemplateColumns: {
              xs: "1fr",
              sm: "repeat(2, minmax(0, 1fr))",
            },
          }}
        >
          {trace ? (
            <StartTile
              icon="solar:play-circle-linear"
              title="Test your agent"
              desc="Simulated users, before it ships"
              onClick={() => setStart("simulate")}
            />
          ) : (
            <StartTile
              icon="solar:routing-2-linear"
              title="Trace your app"
              desc="See every LLM and agent call"
              onClick={() => setStart("trace")}
            />
          )}
          <StartTile
            icon="solar:test-tube-linear"
            title="Evaluate outputs"
            desc="Score quality, safety and more"
            to={paths.dashboard.evals}
          />
          <StartTile
            icon="solar:widget-5-linear"
            title="Run an experiment"
            desc="Compare prompts and models on a dataset"
            to={paths.dashboard.develop}
          />
          <StartTile
            icon="solar:server-square-linear"
            title="Route through the gateway"
            desc="One endpoint for every model"
            to={paths.dashboard.gateway.root}
          />
        </Box>
      </Box>
    </Box>
  );
}
