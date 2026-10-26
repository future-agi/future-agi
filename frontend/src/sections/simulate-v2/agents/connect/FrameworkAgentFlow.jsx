import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import { Box, Stack, Typography } from "@mui/material";
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
import {
  CodeSnippet,
  FrameworkTuning,
  TextLink,
  WhereOption,
} from "./connectBits";
import { HintLine } from "./replicationBits";
import { useConnectSubmitLabel } from "./connectSubmit";
import { hashSeed } from "../../_mock/runStream";

/* One screen: install, wrap, say where it runs — Connect asks the adapter to
   describe the agent and saves it with what it said. Review it, the Observe
   project and constraints on the agent's page, in Finish setting up. */
const STEPS = ["Connect"];
const validUrl = (u) => /^(https?|wss?):\/\/\S+$/i.test(String(u || "").trim());
/* How long the prototype's local agent takes to check in — a real one checks in when it starts. */
const CHECK_IN_MS = 2500;
const shortSha = (s) => hashSeed(s).toString(16).padStart(7, "0").slice(0, 7);

/* The adapter's own wrap, with serve() swapped for init(): the agent runs on
   this machine and checks in, instead of being served at a URL. */
const localInitOf = (adapter) =>
  adapter.code
    .replace(
      /^app = fagi\.serve\((.*)\)\s+#.*$/m,
      "fagi.init($1)  # checks in from this machine with its commit",
    )
    .replace(/import \{ serve \}/, "import { init }")
    .replace(
      /^serve\((.*), \{ port: 8000 \}\);.*$/m,
      "init($1); // checks in from this machine with its commit",
    );

/**
 * A framework agent through our SDK adapter — LangGraph, OpenAI Agents SDK,
 * CrewAI, Claude Agent SDK, Pipecat, Google ADK, Mastra, Vercel AI SDK, AWS
 * Strands, Pydantic AI, Agno, Haystack, LlamaIndex, smolagents, DSPy or
 * Microsoft Agent Framework: install it and wrap the graph, pipeline, entry
 * agent or crew in serve(); Connect has the adapter describe what it wraps
 * and saves that. Or run it on your own machine: init() instead of serve(),
 * and it checks in with its commit and uncommitted changes. The framework is picked on the directory and can be
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
  const [endpoint, setEndpoint] = useState(ADAPTER_ENDPOINT_PLACEHOLDER);
  const [reading, setReading] = useState(false);
  const [found, setFound] = useState(null);
  const [facts, setFacts] = useState({});
  /* Where it runs: served at a URL we call, or on your machine through our
     SDK — then it checks in with its commit and uncommitted changes. */
  const [where, setWhere] = useState("url");
  const [checkIn, setCheckIn] = useState(null);
  const local = where === "local";
  /* Set later on the agent's page: a linked repository, so we can build it. */
  const linkedRepo = "";

  const meta = FRAMEWORK_META[framework] || FRAMEWORK_META.langgraph;
  const adapter = ADAPTERS[framework] || ADAPTERS.langgraph;

  const switchFramework = (id) => {
    onFramework(id);
    setFound(null);
    setFacts({});
    setCheckIn(null);
  };

  /* The local agent checks in a moment after it starts. */
  useEffect(() => {
    if (!local || checkIn) return undefined;
    const t = setTimeout(
      () =>
        setCheckIn({
          commit: shortSha(`${framework}:local`),
          fingerprint: shortSha(`${framework}:local:dirty`),
          at: new Date().toISOString(),
        }),
      CHECK_IN_MS,
    );
    return () => clearTimeout(t);
  }, [local, checkIn, framework]);

  const describe = () => {
    setReading(true);
    setTimeout(() => {
      const r = describeAdapter({
        framework,
        endpoint: local ? ADAPTER_ENDPOINT_PLACEHOLDER : endpoint,
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
    /* serve() is how we reach it — we call it where it runs. On your machine
       there's nothing to call: the SDK checks in and runs scenarios there. */
    ...(local
      ? { runLocation: "sdk", localSdk: true }
      : { runtime: { endpoint: endpoint.trim(), tokenRef: "" } }),
    ...(members.length && { members }),
    ...(repoName && {
      linkedRepo: repoName,
      ...(viaGithub && { githubApp: true }),
      gitHost: "github",
    }),
    versionNote:
      local && checkIn
        ? `Checked in from your machine through the ${meta.label} adapter · commit ${checkIn.commit} + uncommitted changes ${checkIn.fingerprint}.`
        : `Described by the ${meta.label} adapter at ${endpoint.trim()}.`,
  };

  /* Connect: the adapter describes the agent, then it's saved as described. */
  const [saveWanted, setSaveWanted] = useState(false);
  useEffect(() => {
    if (!saveWanted || !found) return;
    setSaveWanted(false);
    onSave(source);
  }, [saveWanted, found]); // eslint-disable-line react-hooks/exhaustive-deps
  const next = {
    label: submitLabel,
    icon: "solar:link-circle-linear",
    disabled: local ? !checkIn : !validUrl(endpoint),
    hint: local
      ? "Waiting for your agent to check in"
      : "Add where serve() runs",
    busy: reading
      ? `Asking the ${meta.label} adapter to describe ${adapter.wraps}…`
      : null,
    onClick: () => {
      describe();
      setSaveWanted(true);
    },
  };

  return (
    <ConnectSteps
      title={title}
      subtitle="Wrap your graph, pipeline, agents or crew with our adapter. It serves the agent for simulations; you confirm what it found."
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
          <Label>Where it runs</Label>
          <Box
            sx={{
              display: "grid",
              gap: 1,
              mt: 0.75,
              gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr" },
            }}
          >
            <WhereOption
              icon="solar:plug-circle-linear"
              label="Served at a URL"
              on={!local}
              onClick={() => setWhere("url")}
              short="We call serve() where it runs · Deployed by you"
              hint="We can't build it ourselves, so your own coding agent makes changes and we prove them where it's served."
            />
            <WhereOption
              icon="solar:laptop-2-linear"
              label="Run on your machine"
              on={local}
              onClick={() => setWhere("local")}
              short="Local code through our SDK · identified by commit"
              hint="It reports its commit and uncommitted changes, so we can prove each change."
            />
          </Box>
        </Box>

        {local ? (
          <Box>
            <RailStep
              n={1}
              title="Install our SDK"
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
              title="Add one line where your agent starts"
              body={
                <HintLine
                  hint={`It runs our scenarios through ${meta.label}'s own loop on this machine and reports the structure.`}
                >
                  init() leaves your code as it is.
                </HintLine>
              }
            >
              <CodeSnippet code={localInitOf(adapter)} />
            </RailStep>
            <RailStep
              n={3}
              title="Run your agent as usual"
              body={
                <HintLine hint="init() dials out with your API key — no port to open.">
                  It checks in from your machine.
                </HintLine>
              }
              last
            >
              <Stack
                direction="row"
                alignItems="center"
                spacing={1}
                sx={{
                  maxWidth: 480,
                  px: 1.5,
                  py: 1,
                  borderRadius: 1,
                  border: "1px solid",
                  borderColor: "divider",
                }}
              >
                <Iconify
                  icon={
                    checkIn
                      ? "solar:check-circle-linear"
                      : "eos-icons:three-dots-loading"
                  }
                  width={16}
                  sx={{ color: "text.secondary", flexShrink: 0 }}
                />
                <Typography
                  component="div"
                  sx={{
                    typography: "s2",
                    color: checkIn ? "text.primary" : "text.secondary",
                  }}
                >
                  {checkIn ? (
                    <HintLine
                      hint={`Fingerprint of uncommitted changes ${checkIn.fingerprint}. Every run says which commit and changes it ran.`}
                    >
                      {"Connected · commit "}
                      <Box
                        component="span"
                        sx={{ fontFamily: "ui-monospace, Menlo, monospace" }}
                      >
                        {checkIn.commit}
                      </Box>
                      {" + uncommitted changes"}
                    </HintLine>
                  ) : (
                    "Waiting for your agent to check in…"
                  )}
                </Typography>
              </Stack>
            </RailStep>
          </Box>
        ) : (
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
              title="Run it and tell us where"
              body={
                <HintLine hint="serve() dials out with your API key, so local works while it's running.">
                  Local works — no port to open. Deployed? Use that URL.
                </HintLine>
              }
              last
            >
              <Box sx={{ maxWidth: 480 }}>
                <CodeSnippet code={adapter.run} />
                <Box sx={{ mt: 1.25 }}>
                  <Field
                    label="Adapter endpoint"
                    required
                    mono
                    placeholder={ADAPTER_ENDPOINT_PLACEHOLDER}
                    value={endpoint}
                    onChange={setEndpoint}
                    fullWidth
                  />
                </Box>
              </Box>
            </RailStep>
          </Box>
        )}

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

/* One way it can run — the same selectable card as the run-location choice. */

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
