import PropTypes from "prop-types";
import { Stack, Typography } from "@mui/material";
import { AGENT_TYPES } from "src/sections/agents/constants";
import usePanelBuild from "../../hooks/usePanelBuild";
import { DEFAULT_SCENARIOS } from "../../panels/scenarioCountRules";
import { T12 } from "../drawerStyles";
import useDrawerForm from "./useDrawerForm";
import { Field, FormCard, HintLine, ToggleRow } from "./formBits";
import LiveKitRoomConfig from "./extras/LiveKitRoomConfig";

/** A LiveKit agent, by its project: we join its rooms as the caller. */
export default function LiveKitForm({ onBusyChange }) {
  const build = usePanelBuild();
  const [form, set] = useDrawerForm(
    {
      livekitUrl: "",
      key: "",
      apiSecret: "",
      agentName: "",
      systemPrompt: "",
      repoUrl: "",
      agentSpeaksFirst: false,
      scenarioCount: DEFAULT_SCENARIOS,
    },
    build,
  );
  const {
    livekitUrl,
    key,
    apiSecret,
    agentName,
    systemPrompt,
    repoUrl,
    agentSpeaksFirst,
    scenarioCount,
  } = form;
  const missing = !livekitUrl.trim()
    ? "Add the server URL"
    : !key.trim() || !apiSecret.trim()
      ? "Add the API key and secret"
      : !agentName.trim()
        ? "Add the agent name"
        : !systemPrompt.trim()
          ? "Paste the agent's system prompt"
          : null;

  const buildSource = () => ({
    kind: "platform",
    agentType: AGENT_TYPES.VOICE,
    provider: "livekit",
    scenarioCount: Number(scenarioCount) || undefined,
    agentId: agentName.trim(),
    apiKey: key.trim(),
    apiSecret: apiSecret.trim(),
    livekitUrl: livekitUrl.trim(),
    prompt: systemPrompt.trim(),
    ...(repoUrl.trim() ? { repoUrl: repoUrl.trim() } : {}),
    callDirection: "inbound",
    contact: { mode: "web", inboundCalls: true, agentSpeaksFirst },
  });

  return (
    <FormCard
      intro="A LiveKit agent, by its project. Add the project's URL and key, and the name the agent's worker registers with. We join its rooms as the caller."
      build={build}
      scenarioCount={scenarioCount}
      onScenarioCount={set("scenarioCount")}
      canRun={!missing}
      blockedReason="Add the server, key, secret, agent name and prompt, then preflight checks them."
      onRun={() => build.runPreflight(buildSource())}
      hint={missing}
      onBusyChange={onBusyChange}
    >
      <Typography component="div" sx={{ ...T12, color: "text.secondary" }}>
        <HintLine
          text="Test only. Connected by its LiveKit project, it gets a suggested fix."
          hint="LiveKit Agents is a code framework, so proven changes come through its code."
        />
      </Typography>
      <Field
        label="LiveKit server URL"
        required
        mono
        value={livekitUrl}
        onChange={set("livekitUrl")}
        placeholder="wss://your-project.livekit.cloud"
        helper="From LiveKit Cloud → your project → Settings, or your own server's URL."
      />
      <Stack direction="row" spacing={1.5}>
        <Field
          label="API key"
          required
          mono
          type="password"
          value={key}
          onChange={set("key")}
          placeholder="Paste the key"
          helper="Settings → Keys → Create new API key. Stored encrypted; used only to join test rooms."
          sx={{ flex: 1 }}
        />
        <Field
          label="API secret"
          required
          mono
          type="password"
          value={apiSecret}
          onChange={set("apiSecret")}
          placeholder="Paste the secret"
          helper="Shown once, when the key is created. Never shown again."
          sx={{ flex: 1 }}
        />
      </Stack>
      <Field
        label="Agent name"
        required
        mono
        value={agentName}
        onChange={set("agentName")}
        placeholder="e.g. support-agent"
        helper="The name the worker registers with, WorkerOptions(agent_name=…). It must match exactly; it isn't a display name."
      />
      <Field
        label="System prompt"
        required
        multiline
        minRows={4}
        value={systemPrompt}
        onChange={set("systemPrompt")}
        placeholder="You are a friendly returns agent for Acme…"
        helper="LiveKit doesn't expose your agent's prompt, so paste it here. It seeds scenarios and the judge. It never changes the live agent."
      />
      <ToggleRow
        title="Agent speaks first"
        body="The simulator waits for its greeting."
        checked={agentSpeaksFirst}
        onChange={set("agentSpeaksFirst")}
      />
      <LiveKitRoomConfig />
      <Field
        label="GitHub repo (optional)"
        mono
        value={repoUrl}
        onChange={set("repoUrl")}
        placeholder="https://github.com/your-org/your-agent"
        helper="Lets us read the agent's tools and prompts to seed matching scenarios."
      />
    </FormCard>
  );
}
LiveKitForm.propTypes = { onBusyChange: PropTypes.func };
