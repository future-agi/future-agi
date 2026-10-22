import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { paths } from "src/routes/paths";
import { alpha } from "@mui/material/styles";
import { Box, Button, CircularProgress, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { TRACE_AI_INSTRUMENTORS, traceAiInstrumentor, traceAiSnippets } from "../../_mock/traceAiCatalog";
import { SegmentedToggle } from "../../environments/StartEnvironment";
import LocalSteps from "../../environments/LocalSteps";
import { TextLink } from "./connectBits";

const selectedBg = (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.06 : 0.03);
const SHOWN = 8;

/**
 * Sending an agent's traces to an Observe project — the same four sections,
 * in the same order, as Observe's New project drawer: install dependencies,
 * load the API keys, set up telemetry (register the project), set up
 * instrumentation (one instrumentor per framework). The agent's own framework
 * is picked already; any other is a click away.
 *
 * Above them, one row for the fastest way in on a code agent: a prompt the
 * coding agent in its repository (Claude Code, Cursor, Codex) follows to do
 * the same four steps.
 *
 * Used wherever traces are connected — the agent's Next steps and New agent →
 * Traces — so it reads the same everywhere.
 */
export default function SendTracesSetup({ project, instrumentorId, onInstrumentor, matchedId = null, fallbackFor = null, defaultLanguage = "python", onLanguage = null, agentLabel = "agent", pullRequest = null }) {
  const navigate = useNavigate();
  const [language, setLanguageState] = useState(defaultLanguage);
  const setLanguage = (l) => { setLanguageState(l); onLanguage?.(l); };
  const [allShown, setAllShown] = useState(false);
  const ins = traceAiInstrumentor(instrumentorId) || traceAiInstrumentor("openai");
  const s = traceAiSnippets({ instrumentorId: ins.id, language, project });

  /* Observe's order, the agent's own first — fixed, so a click never moves a
     tile. Folded, a pick past the first few takes the last place. */
  const matched = traceAiInstrumentor(matchedId);
  const ordered = matched ? [matched, ...TRACE_AI_INSTRUMENTORS.filter((i) => i.id !== matched.id)] : TRACE_AI_INSTRUMENTORS;
  const folded = ordered.slice(0, SHOWN);
  const tiles = allShown ? ordered : folded.some((i) => i.id === ins.id) ? folded : [...folded.slice(0, SHOWN - 1), ins];

  const steps = [
    {
      title: "Install dependencies",
      body: "Configure the application to send traces to Future AGI.",
      cmd: s.install,
    },
    {
      title: "Load API keys",
      body: "Add them where the agent is deployed — its secrets or environment variables. traceAI reads them from the environment, so they never go in the code.",
      extra: <TextLink onClick={() => navigate(paths.dashboard.keys)} sx={{ mb: 1 }}>Copy them from Keys</TextLink>,
      cmd: s.keys,
    },
    {
      title: "Set up telemetry",
      body: `Registers the application to send traces to ${project}. Add it before any other code runs.`,
      cmd: s.register,
    },
    {
      title: "Set up instrumentation",
      body: fallbackFor && ins.id === matchedId
        ? `traceAI has no instrumentor for ${fallbackFor} yet — ${ins.name} traces its model calls, with the prompt, model and tools.`
        : ins.id === matchedId
          ? `${ins.name} is what this ${agentLabel} is built with — it traces every call. Using something else? Pick it.`
          : "Pick what the agent is built with — each has its own instrumentor.",
      extra: (
        <Box sx={{ mb: 1 }}>
          <Box sx={{ display: "grid", gap: 0.75, gridTemplateColumns: { xs: "1fr 1fr", sm: "repeat(4, 1fr)" } }} role="radiogroup" aria-label="Instrumentor">
            {tiles.map((i) => (
              <InstrumentorTile
                key={i.id}
                name={i.name}
                on={i.id === ins.id}
                note={i.id === matchedId ? (fallbackFor ? `For ${fallbackFor}` : "This agent") : language === "typescript" && !i.typescript ? "Python only" : null}
                onClick={() => onInstrumentor(i.id)}
              />
            ))}
          </Box>
          {TRACE_AI_INSTRUMENTORS.length > SHOWN && (
            <TextLink onClick={() => setAllShown((v) => !v)} sx={{ mt: 0.75 }}>
              {allShown ? "Show fewer" : `Show all ${TRACE_AI_INSTRUMENTORS.length} instrumentors`}
            </TextLink>
          )}
        </Box>
      ),
      cmd: s.instrument,
    },
  ];

  return (
    <Stack spacing={2}>
      {pullRequest && <PullRequestRow {...pullRequest} />}
      <CodingAgentRow project={project} ins={ins} snippets={s} language={s.ts ? "TypeScript" : "Python"} alternative={!!pullRequest} />
      <Box>
        <Stack direction="row" alignItems="center" spacing={1.5} sx={{ mb: 1.5 }}>
          <Typography sx={{ typography: "s2", fontWeight: 700, flex: 1, minWidth: 0 }}>Or set it up by hand</Typography>
          <SegmentedToggle value={language} onChange={setLanguage} options={[{ value: "python", label: "Python" }, { value: "typescript", label: "TypeScript" }]} />
        </Stack>
        {language === "typescript" && !s.ts && (
          <Typography sx={{ typography: "s3", color: "text.subtitle", mb: 1.5 }}>
            {`${ins.name} is instrumented in Python only — these are the Python steps.`}
          </Typography>
        )}
        <LocalSteps steps={steps} />
      </Box>
    </Stack>
  );
}
SendTracesSetup.propTypes = {
  /* The Observe project the traces land in — named in the register() call. */
  project: PropTypes.string.isRequired,
  instrumentorId: PropTypes.string.isRequired,
  onInstrumentor: PropTypes.func.isRequired,
  /* The instrumentor that fits the agent, when the agent is known — tagged "This agent". */
  matchedId: PropTypes.string,
  /* The agent's framework when traceAI has no instrumentor for it. */
  fallbackFor: PropTypes.string,
  defaultLanguage: PropTypes.oneOf(["python", "typescript"]),
  onLanguage: PropTypes.func,
  agentLabel: PropTypes.string,
  /* A GitHub agent: we can add it with a pull request — { repo, draft, onOpen }. */
  pullRequest: PropTypes.shape({ repo: PropTypes.string, draft: PropTypes.object, onOpen: PropTypes.func }),
};

/*
  A GitHub agent: we have the repository, so the first way in is a pull
  request that adds the same lines — the person reviews and merges it; we
  never merge.
*/
function PullRequestRow({ repo, draft, onOpen }) {
  const [preview, setPreview] = useState(false);
  return (
    <Box sx={{ border: "1px solid", borderColor: "text.primary", borderRadius: 1, overflow: "hidden" }}>
      <Stack direction="row" alignItems="center" spacing={1.25} sx={{ px: 1.5, py: 1 }}>
        <Iconify icon="solar:branching-paths-up-linear" width={16} sx={{ color: "text.primary", flexShrink: 0 }} />
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "s2", fontWeight: 700 }}>Add it with a pull request</Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            {`We open a draft PR on ${repo} with the four steps below — ${draft.files.length} files. You review and merge it; we never merge. `}
            <TextLink onClick={() => setPreview((v) => !v)} sx={{ display: "inline", typography: "s3" }}>{preview ? "Hide" : "Preview"}</TextLink>
          </Typography>
        </Box>
        <Button
          size="small" variant="contained" color="primary" onClick={onOpen}
          startIcon={<Iconify icon="solar:branching-paths-up-linear" width={14} />}
          sx={{ typography: "s3", fontWeight: 700, height: 28, flexShrink: 0 }}
        >
          Open draft PR
        </Button>
      </Stack>
      {preview && (
        <Box sx={{ borderTop: "1px solid", borderColor: "divider" }}>
          <Stack sx={{ px: 1.5, py: 1 }} spacing={0.5}>
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              <Box component="span" sx={{ fontFamily: "ui-monospace, Menlo, monospace", color: "text.primary" }}>{draft.branch}</Box>
              {` · ${draft.title}`}
            </Typography>
            {draft.files.map((f) => (
              <Stack key={f.path} direction="row" spacing={1} alignItems="baseline">
                <Typography sx={{ typography: "s3", fontFamily: "ui-monospace, Menlo, monospace", flexShrink: 0 }}>{f.path}</Typography>
                <Typography noWrap sx={{ typography: "s3", color: "text.subtitle", flex: 1, minWidth: 0 }}>{f.title}</Typography>
                <Typography sx={{ typography: "s3", color: "#16A34A", fontFamily: "ui-monospace, Menlo, monospace", flexShrink: 0 }}>{`+${f.adds}`}</Typography>
              </Stack>
            ))}
          </Stack>
          <Typography sx={{ px: 1.5, pt: 0.75, typography: "s3", color: "text.subtitle", borderTop: "1px solid", borderColor: "divider" }}>{draft.preview.label}</Typography>
          <Typography
            component="pre"
            sx={{ m: 0, px: 1.5, pb: 1.25, pt: 0.5, maxHeight: 220, overflow: "auto", whiteSpace: "pre-wrap", fontFamily: "ui-monospace, Menlo, monospace", fontSize: 12, lineHeight: 1.55, color: "text.secondary" }}
          >
            {draft.preview.code}
          </Typography>
        </Box>
      )}
    </Box>
  );
}
PullRequestRow.propTypes = { repo: PropTypes.string, draft: PropTypes.object, onOpen: PropTypes.func };

function InstrumentorTile({ name, on, note, onClick }) {
  return (
    <Box
      role="radio" aria-checked={on} tabIndex={0}
      onClick={onClick}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onClick(); } }}
      sx={{
        px: 1.25, py: 0.75, borderRadius: 1, cursor: "pointer", minWidth: 0,
        border: "1px solid", borderColor: on ? "text.primary" : "divider",
        bgcolor: (t) => (on ? selectedBg(t) : "transparent"),
        "&:hover": { borderColor: on ? "text.primary" : "text.disabled" },
        "&:focus-visible": { outline: "2px solid", outlineColor: "text.primary", outlineOffset: 1 },
      }}
    >
      <Typography noWrap title={name} sx={{ typography: "s2", fontWeight: on ? 700 : 500 }}>{name}</Typography>
      {note && <Typography noWrap sx={{ typography: "s3", fontSize: 11, color: "text.subtitle" }}>{note}</Typography>}
    </Box>
  );
}
InstrumentorTile.propTypes = { name: PropTypes.string, on: PropTypes.bool, note: PropTypes.string, onClick: PropTypes.func.isRequired };

/* The four steps as one prompt for the coding agent already in the repository. */
const promptFor = ({ project, ins, snippets, language }) => [
  `Add Future AGI tracing (traceAI) to this agent so every call it makes is traced to the Observe project "${project}".`,
  "",
  `1. Install: ${snippets.install}`,
  "2. Read FI_API_KEY and FI_SECRET_KEY from the environment where the agent runs. Never write them into the code.",
  `3. Before any other code runs, register the project (${language}):`,
  "",
  snippets.register,
  "",
  `4. Instrument it with ${ins.name}:`,
  "",
  snippets.instrument,
  "",
  "Then run the agent once so its first trace is sent.",
].join("\n");

function CodingAgentRow({ project, ins, snippets, language, alternative = false }) {
  const [copied, setCopied] = useState(false);
  const [preview, setPreview] = useState(false);
  const text = promptFor({ project, ins, snippets, language });
  const copy = () => {
    navigator.clipboard?.writeText(text).catch(() => {});
    setCopied(true);
    setTimeout(() => setCopied(false), 1600);
  };
  return (
    <Box sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1, overflow: "hidden" }}>
      <Stack direction="row" alignItems="center" spacing={1.25} sx={{ px: 1.5, py: 1 }}>
        <Iconify icon="solar:magic-stick-3-linear" width={15} sx={{ color: "text.secondary", flexShrink: 0 }} />
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>{alternative ? "Or let your coding agent add it" : "Let your coding agent add it"}</Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            {"One prompt for Claude Code, Cursor or Codex in the agent's repository — it does the four steps below. "}
            <TextLink onClick={() => setPreview((v) => !v)} sx={{ display: "inline", typography: "s3" }}>{preview ? "Hide" : "Preview"}</TextLink>
          </Typography>
        </Box>
        <Button
          size="small" variant="outlined" onClick={copy}
          startIcon={<Iconify icon={copied ? "solar:check-circle-linear" : "solar:copy-linear"} width={14} />}
          sx={{ typography: "s3", fontWeight: 700, height: 26, color: "text.primary", borderColor: "divider", flexShrink: 0 }}
        >
          {copied ? "Copied" : "Copy prompt"}
        </Button>
      </Stack>
      {preview && (
        <Typography
          component="pre"
          sx={{ m: 0, px: 1.5, py: 1.25, maxHeight: 220, overflow: "auto", whiteSpace: "pre-wrap", fontFamily: "ui-monospace, Menlo, monospace", fontSize: 12, lineHeight: 1.55, color: "text.secondary", bgcolor: "background.neutral", borderTop: "1px solid", borderColor: "divider" }}
        >
          {text}
        </Typography>
      )}
    </Box>
  );
}
CodingAgentRow.propTypes = { project: PropTypes.string, ins: PropTypes.object, snippets: PropTypes.object, language: PropTypes.string, alternative: PropTypes.bool };

/**
 * After the steps: run the agent once and wait for its first trace — what
 * we're waiting on, how long it's been, and, if nothing comes, what usually
 * stops it. Observe's drawer stops at the code; this says it worked.
 *   status: "idle" | "listening" | "received"
 */
const SLOW_MS = 15000;
export function FirstTraceWait({ project, status, spans = 1, onListen, onStop }) {
  if (status === "listening") return <ListeningPanel project={project} onStop={onStop} />;
  const received = status === "received";
  return (
    <Stack direction="row" alignItems="center" spacing={1.25} sx={{ px: 1.75, py: 1.25, border: "1px solid", borderColor: "divider", borderRadius: 1 }}>
      <Iconify icon={received ? "solar:check-circle-bold" : "solar:play-circle-linear"} width={16} sx={{ color: received ? "#16A34A" : "text.subtitle", flexShrink: 0 }} />
      <Typography sx={{ typography: "s2", color: received ? "text.primary" : "text.secondary", flex: 1, minWidth: 0 }}>
        {received ? `First trace received in ${project} · ${spans} spans` : "Then run your agent once — we'll pick up its first trace."}
      </Typography>
      {!received && (
        <Button size="small" variant="outlined" onClick={onListen} sx={{ typography: "s3", fontWeight: 700, color: "text.primary", borderColor: "divider", flexShrink: 0 }}>
          Listen for traces
        </Button>
      )}
    </Stack>
  );
}
FirstTraceWait.propTypes = {
  project: PropTypes.string.isRequired,
  status: PropTypes.oneOf(["idle", "listening", "received"]).isRequired,
  spans: PropTypes.number,
  onListen: PropTypes.func.isRequired,
  onStop: PropTypes.func.isRequired,
};

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
    <Box sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1, overflow: "hidden" }}>
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
            Nothing yet? Check that FI_API_KEY and FI_SECRET_KEY are set where the agent runs, and that the telemetry lines run before the agent starts.
          </Typography>
        )}
      </Stack>
    </Box>
  );
}
ListeningPanel.propTypes = { project: PropTypes.string.isRequired, onStop: PropTypes.func.isRequired };
