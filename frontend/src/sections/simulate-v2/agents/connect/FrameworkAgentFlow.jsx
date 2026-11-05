import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import {
  Box,
  Button,
  CircularProgress,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { FRAMEWORK_META } from "../../_mock/agentRegistry";
import { installedRepos, useIntegrations } from "../../_mock/integrations";
import {
  ADAPTERS,
  ADAPTER_ENDPOINT_PLACEHOLDER,
  BUILTIN_TOOL_LIBRARY,
  adapterShapeOf,
  describeAdapter,
  frameworkMembersOf,
  frameworkTuningOf,
} from "../../_mock/connectAgent";
import {
  OPENAI_AGENT_BUILDER_NOTE,
  TRACEAI_FRAMEWORK_NOTE,
} from "../../environments/connectOptions";
import { ChipCard, Field, Label } from "../../environments/StartEnvironment";
import ConnectSteps from "./ConnectSteps";
import { CodeSnippet, FrameworkTuning, TextLink } from "./connectBits";
import { HintLine } from "./replicationBits";
import { CONNECT_LABEL, useConnectSubmitLabel } from "./connectSubmit";

/* One screen: install, wrap, run. serve() calls in with the API key, the
   adapter describes the agent, and the person confirms what it found before
   it's saved. A deployed agent can be called at its URL instead. Review it,
   the Observe project and constraints on the agent's page, in Finish
   setting up. */
const STEPS = ["Connect"];
/* Prototype: how long until a running serve() calls in. */
const CALL_IN_MS = 6000;
const validUrl = (u) => /^(https?|wss?):\/\/\S+$/i.test(String(u || "").trim());

/**
 * A framework agent through our SDK adapter — LangGraph, OpenAI Agents SDK,
 * CrewAI, Claude Agent SDK, Pipecat, Google ADK, Mastra, Vercel AI SDK, AWS
 * Strands, Pydantic AI, Agno, Haystack, LlamaIndex, smolagents, DSPy or
 * Microsoft Agent Framework: install it and wrap the graph, pipeline, entry
 * agent or crew in serve(); Connect has the adapter describe what it wraps
 * and saves that. Runs happen in Future AGI's cloud, calling serve() where
 * it's deployed. The framework is picked on the directory and can be
 * switched at the top, which says what improvements can change in it.
 */
export default function FrameworkAgentFlow({
  title,
  framework,
  onFramework,
  onSave,
  onSwitch,
  initial = null,
}) {
  const submitLabel = useConnectSubmitLabel();
  const integrations = useIntegrations();
  /* "listen": serve() calls in with the API key · "url": we call it there. */
  const [mode, setMode] = useState("listen");
  const [endpoint, setEndpoint] = useState("");
  const [reading, setReading] = useState(false);
  const [found, setFound] = useState(null);
  const [facts, setFacts] = useState({});
  /* Waiting: the agent shows up once serve() is running. */
  useEffect(() => {
    if (mode !== "listen" || found || reading) return undefined;
    const t = setTimeout(() => describe(""), CALL_IN_MS);
    return () => clearTimeout(t);
  }, [mode, found, reading, framework]); // eslint-disable-line react-hooks/exhaustive-deps

  /* Set later on the agent's page: a linked repository, so we can build it. */
  const linkedRepo = "";

  const meta = FRAMEWORK_META[framework] || FRAMEWORK_META.langgraph;
  const adapter = ADAPTERS[framework] || ADAPTERS.langgraph;

  const switchFramework = (id) => {
    onFramework(id);
    setFound(null);
    setFacts({});
  };
  const waitAgain = (next = "listen") => {
    setFound(null);
    setFacts({});
    setMode(next);
  };

  const describe = (at) => {
    setReading(true);
    setTimeout(() => {
      const r = describeAdapter({
        framework,
        endpoint: at,
      });
      setFound(r);
      setFacts(
        Object.fromEntries(
          Object.entries(r.facts).map(([k, f]) => [k, f.value]),
        ),
      );
      setReading(false);
    }, 900);
  };

  /* Prompt files, wherever this framework keeps them. */
  const shape = adapterShapeOf(framework);
  const fileOf = (ref) => String(ref || "").split(" · ")[0];
  const prompts = !found
    ? []
    : shape === "graph"
      ? [
          ...new Set(
            (found.structure.graph?.nodes || [])
              .filter((n) => n.prompt)
              .map((n) => fileOf(n.prompt)),
          ),
        ]
      : shape === "agents"
        ? [
            ...new Set(
              (found.structure.agents || []).map((a) => fileOf(a.instructions)),
            ),
          ]
        : ["config/agents.yaml", "config/tasks.yaml"];
  const tuning = frameworkTuningOf(framework);
  const repoName = linkedRepo
    .trim()
    .replace(/^https?:\/\/[^/]+\//, "")
    .replace(/\.git$/, "");
  const viaGithub =
    !!repoName &&
    installedRepos(integrations).some(
      (r) => r.name.toLowerCase() === repoName.toLowerCase(),
    );

  const members = found
    ? frameworkMembersOf(framework, found.structure, facts.model)
    : [];
  const source = {
    kind: "framework",
    framework,
    /* A voice pipeline (Pipecat) takes calls; everything else chats. */
    ...(meta.voice && { agentType: "voice" }),
    name: (facts.name || "").trim(),
    entry: facts.entry || adapter.entry,
    ...(found?.structure || {}),
    ...(facts.guardrails && { guardrails: facts.guardrails }),
    prompts,
    tools: (facts.tools || []).map((name) => {
      const known = BUILTIN_TOOL_LIBRARY.find((t) => t.name === name);
      return { name, desc: known?.desc || "", args: known?.args || [] };
    }),
    model: facts.model,
    /* serve() is how we reach it — we call it where it runs. */
    runtime:
      mode === "url"
        ? { endpoint: endpoint.trim(), tokenRef: "" }
        : { dialOut: true },
    ...(members.length && { members }),
    ...(repoName && {
      linkedRepo: repoName,
      ...(viaGithub && { githubApp: true }),
      gitHost: "github",
    }),
    versionNote:
      mode === "url"
        ? `Described by the ${meta.label} adapter at ${endpoint.trim()}.`
        : `Described by the ${meta.label} adapter when it called in.`,
  };

  /* Confirm: saved as the adapter described it — only once it has. */
  const next = {
    label:
      submitLabel === CONNECT_LABEL ? "Confirm and add agent" : submitLabel,
    icon: "solar:check-circle-linear",
    disabled: !found,
    hint:
      mode === "url"
        ? "Check the URL first"
        : "Turns on when your agent calls in",
    busy: reading ? `Reading ${adapter.wraps}…` : null,
    onClick: () => onSave(source),
  };
  const sizeOf = () => {
    const st = found?.structure || {};
    if (st.graph?.nodes) return `${st.graph.nodes.length} steps`;
    if (st.agents) return `${st.agents.length} agents`;
    if (st.crew) return `${st.crew.agents.length} agents`;
    return null;
  };

  return (
    <ConnectSteps
      title={title}
      subtitle="Wrap your graph, pipeline or crew with our adapter, then confirm what it found."
      steps={STEPS}
      active={0}
      next={next}
    >
      <Stack spacing={2}>
        {/* Picked on the directory: the drawer is that framework's. */}
        {!initial?.framework && (
          <Box>
            <Label>Framework</Label>
            <Box
              sx={{ display: "flex", flexWrap: "wrap", gap: 0.75, mt: 0.75 }}
            >
              {Object.entries(FRAMEWORK_META).map(([id, f]) => (
                <ChipCard
                  key={id}
                  icon={f.icon}
                  label={f.label}
                  on={framework === id}
                  onClick={() => switchFramework(id)}
                />
              ))}
            </Box>
          </Box>
        )}
        <FrameworkTuning tuning={tuning} structure={meta.structure} />

        <Box>
          <RailStep
            n={1}
            title="Install the adapter"
            body={
              meta.lang === "typescript"
                ? "One package — it pulls in nothing else."
                : "One extra on futureagi — it pulls in nothing else."
            }
          >
            <CodeSnippet code={adapter.install} />
          </RailStep>
          <RailStep
            n={2}
            title={`Wrap ${adapter.wraps}`}
            body={
              <HintLine
                hint={`It runs conversations through ${meta.label}'s own loop and reports the structure.`}
              >
                serve() leaves your code as it is.
              </HintLine>
            }
          >
            <CodeSnippet code={adapter.code} />
          </RailStep>
          <RailStep
            n={3}
            title="Run it"
            body={
              <HintLine hint="serve() dials out with your API key, so local works while it's running.">
                Local works — no port to open.
              </HintLine>
            }
            last
          >
            <Box sx={{ maxWidth: 560 }}>
              <CodeSnippet code={adapter.run} />
              <Box sx={{ mt: 1.25 }}>
                {found ? (
                  <StatusRow
                    icon={
                      <Iconify
                        icon="solar:check-circle-bold"
                        width={16}
                        sx={{ color: "status.success.fg", flexShrink: 0 }}
                      />
                    }
                    text={[
                      `Found ${facts.name || adapter.entry}`,
                      sizeOf(),
                      `${(facts.tools || []).length} tools`,
                      facts.model,
                    ]
                      .filter(Boolean)
                      .join(" · ")}
                    action={
                      <Button
                        variant="outlined"
                        size="small"
                        onClick={() => waitAgain(mode)}
                        sx={{ minWidth: 0 }}
                      >
                        Not it? Try again
                      </Button>
                    }
                  />
                ) : mode === "listen" ? (
                  <StatusRow
                    icon={
                      <CircularProgress
                        size={14}
                        thickness={5}
                        sx={{ color: "text.secondary", flexShrink: 0 }}
                      />
                    }
                    text={
                      reading
                        ? "Your agent called in — reading it…"
                        : "Waiting for your agent… run the command above."
                    }
                  />
                ) : (
                  <Stack direction="row" spacing={1} alignItems="flex-end">
                    <Box flex={1} minWidth={0}>
                      <Field
                        label="Where serve() runs"
                        required
                        mono
                        placeholder={ADAPTER_ENDPOINT_PLACEHOLDER.replace(
                          "localhost:8000",
                          "agent.example.com",
                        )}
                        value={endpoint}
                        onChange={setEndpoint}
                        fullWidth
                      />
                    </Box>
                    <Button
                      size="small"
                      variant="outlined"
                      disabled={!validUrl(endpoint) || reading}
                      onClick={() => describe(endpoint)}
                      sx={{ color: "text.primary", borderColor: "divider" }}
                    >
                      {reading ? "Checking…" : "Check"}
                    </Button>
                  </Stack>
                )}
              </Box>
              {!found && (
                <Typography
                  sx={{ typography: "s3", color: "text.subtitle", mt: 0.75 }}
                >
                  {mode === "listen" ? (
                    <>
                      Deployed somewhere we should call instead?{" "}
                      <TextLink onClick={() => waitAgain("url")}>
                        Paste its URL
                      </TextLink>
                    </>
                  ) : (
                    <TextLink onClick={() => waitAgain("listen")}>
                      Wait for it to call in instead
                    </TextLink>
                  )}
                </Typography>
              )}
            </Box>
          </RailStep>
        </Box>

        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          Code already in GitHub, GitLab or Bitbucket?{" "}
          <TextLink onClick={() => onSwitch("repo")}>
            Point us at the repository instead
          </TextLink>
        </Typography>
        <Box
          sx={{
            px: 1.5,
            py: 1,
            borderRadius: 1,
            bgcolor: "background.neutral",
          }}
        >
          <Typography
            component="div"
            sx={{ typography: "s3", color: "text.secondary" }}
          >
            <HintLine
              hint={`${OPENAI_AGENT_BUILDER_NOTE} ${TRACEAI_FRAMEWORK_NOTE}`}
            >
              OpenAI Agent Builder is shutting down.
            </HintLine>
          </Typography>
        </Box>
      </Stack>
    </ConnectSteps>
  );
}

FrameworkAgentFlow.propTypes = {
  title: PropTypes.string,
  framework: PropTypes.oneOf(Object.keys(FRAMEWORK_META)).isRequired,
  initial: PropTypes.object,
  onFramework: PropTypes.func.isRequired,
  limits: PropTypes.object.isRequired,
  onLimits: PropTypes.func.isRequired,
  onSave: PropTypes.func.isRequired,
  onSwitch: PropTypes.func,
  profile: PropTypes.object.isRequired,
  onProfile: PropTypes.func.isRequired,
  observe: PropTypes.object,
  onObserve: PropTypes.func.isRequired,
  prefill: PropTypes.object,
};

/* The live state under Run it: waiting, reading, or what was found. */
function StatusRow({ icon, text, action = null }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1.25}
      sx={{
        px: 1.5,
        py: 1,
        minHeight: 40,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
      }}
    >
      {icon}
      <Typography sx={{ typography: "s2", flex: 1, minWidth: 0 }}>
        {text}
      </Typography>
      {action}
    </Stack>
  );
}
StatusRow.propTypes = {
  icon: PropTypes.node,
  text: PropTypes.string,
  action: PropTypes.node,
};

/* One numbered step of the install, on a thin rail — like Develop locally. */
function RailStep({ n, title, body, last, children }) {
  return (
    <Stack direction="row" spacing={1.75}>
      <Stack alignItems="center" sx={{ flexShrink: 0 }}>
        <Box
          sx={{
            width: 22,
            height: 22,
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
              minHeight: 20,
            }}
          />
        )}
      </Stack>
      <Box sx={{ flex: 1, minWidth: 0, pb: last ? 0 : 2 }}>
        <Typography
          sx={{ typography: "s2", fontWeight: 600, lineHeight: "22px" }}
        >
          {title}
        </Typography>
        <Typography
          component="div"
          sx={{ typography: "s3", color: "text.subtitle", mb: 1 }}
        >
          {body}
        </Typography>
        {children}
      </Box>
    </Stack>
  );
}
RailStep.propTypes = {
  n: PropTypes.number,
  title: PropTypes.string,
  body: PropTypes.node,
  last: PropTypes.bool,
  children: PropTypes.node,
};
