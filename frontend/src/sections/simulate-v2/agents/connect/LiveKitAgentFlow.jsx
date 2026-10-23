import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Collapse, Stack, Typography } from "@mui/material";
import { Field, NoteLine } from "../../environments/StartEnvironment";
import { credentialCheck, factCheck, usePreflight } from "./preflight";
import { keyRefOf } from "../../_mock/secretRefs";
import ConnectSteps from "./ConnectSteps";
import SecretRefField from "./SecretRefField";
import { TextLink } from "./connectBits";
import { useConnectSubmitLabel } from "./connectSubmit";

const STEPS = ["Connect"];

/*
  A LiveKit agent, by its project: the same fields the product's own LiveKit
  form asks for — server URL, API key and secret, the agent name its worker
  registers with — plus room config and how many sessions may run at once.
  LiveKit doesn't expose the agent's prompt, so it's pasted here: it seeds
  scenarios and the judge, and never changes the live agent. Preflight
  checks the server, the key and that the worker is registered before Connect.
*/
export default function LiveKitAgentFlow({ title, onSave, initial = null, note = null }) {
  const submitLabel = useConnectSubmitLabel();
  const [url, setUrl] = useState(initial?.livekitUrl || "");
  const [keyRef, setKeyRef] = useState(initial?.apiKeyRef || "");
  const [secretRef, setSecretRef] = useState(initial?.apiSecretRef || "");
  const [agentName, setAgentName] = useState(initial?.agentName || "");
  const [systemPrompt, setSystemPrompt] = useState(initial?.systemPrompt || "");
  const [more, setMore] = useState(false);
  const [roomConfig, setRoomConfig] = useState("");
  const [maxSessions, setMaxSessions] = useState("5");
  const touched = (fn) => (v) => fn(v);

  const savedKey = keyRefOf(keyRef, "vault://acme/sim/livekit_api_key");
  const savedSecret = keyRefOf(secretRef, "vault://acme/sim/livekit_api_secret");
  const urlOk = /^wss?:\/\/|^https?:\/\//.test(url.trim());
  const jsonOk = (() => { if (!roomConfig.trim()) return true; try { JSON.parse(roomConfig); return true; } catch { return false; } })();
  const sessions = Number(maxSessions);
  const sessionsOk = Number.isInteger(sessions) && sessions >= 1 && sessions <= 100;
  const filled = urlOk && !!savedKey && !!savedSecret && !!agentName.trim() && !!systemPrompt.trim() && jsonOk && sessionsOk;

  /* The server answers, the key and secret are accepted, the worker is
     registered. A worker name with a space can't be registered — the one
     worker failure we can show. */
  const preflight = usePreflight([
    factCheck({ id: "server", label: "LiveKit server", ok: urlOk, okText: `${url.trim()} answered.`, failText: "Not a LiveKit URL." }),
    credentialCheck({ id: "key", label: "API key", ref: keyRef.trim() ? savedKey : "", provider: "LiveKit" }),
    credentialCheck({ id: "secret", label: "API secret", ref: secretRef.trim() ? savedSecret : "", provider: "LiveKit" }),
    factCheck({ id: "worker", label: "Agent worker", ok: !!agentName.trim() && !/\s/.test(agentName.trim()), okText: `${agentName.trim()} is registered and accepts test rooms.`, failText: "The server answered, but no worker is registered under that name. Check it matches agent_name exactly, and that the worker is running." }),
  ], [url, keyRef, secretRef, agentName]);

  const source = {
    kind: "platform",
    agentType: "voice",
    provider: "livekit",
    agentId: agentName.trim(),
    apiKeyRef: savedKey,
    apiSecretRef: savedSecret,
    livekitUrl: url.trim(),
    agentName: agentName.trim(),
    ...(roomConfig.trim() && { roomConfig: roomConfig.trim() }),
    maxConcurrency: sessions,
    name: agentName.trim(),
    /* Sent as the build's target system prompt: it becomes the agent's
       description and seeds scenarios and the judge. */
    systemPrompt: systemPrompt.trim(),
    versionNote: "Connected by its LiveKit project.",
    observability: false,
    callDirection: "inbound",
    contact: { mode: "web", inboundCalls: true, agentSpeaksFirst: false },
    flowReach: { direction: "inbound", whoSpeaksFirst: "caller" },
  };

  const next = {
    label: submitLabel,
    icon: "solar:link-circle-linear",
    disabled: !filled || !preflight.ready,
    hint: !urlOk ? "Add the server URL" : !savedKey || !savedSecret ? "Add the API key and secret"
      : !agentName.trim() ? "Add the agent name" : !systemPrompt.trim() ? "Paste the agent's system prompt" : !jsonOk ? "Room config isn't valid JSON"
        : !sessionsOk ? "Sessions at once: 1 to 100" : !preflight.ready ? "Run preflight first" : null,
    onClick: () => onSave(source),
  };

  return (
    <ConnectSteps
      title={title}
      subtitle="A LiveKit agent, by its project. Add the project's URL and key, and the name the agent's worker registers with — we join its rooms as the caller."
      steps={STEPS}
      active={0}
      next={next}
      preflight={{
        state: preflight,
        canRun: urlOk && !!savedKey && !!savedSecret && !!agentName.trim(),
        blockedReason: !(urlOk && savedKey && savedSecret && agentName.trim()) ? "Add the server, key, secret and agent name — then preflight checks them." : null,
      }}
    >
      <Stack spacing={1.75}>
        {note && <Typography component="div" sx={{ typography: "s3", color: "text.subtitle" }}><NoteLine text={note} /></Typography>}
        <Field
          label="LiveKit server URL" required mono
          placeholder="wss://your-project.livekit.cloud"
          value={url} onChange={touched(setUrl)}
          helper="From LiveKit Cloud → your project → Settings, or your own server's URL."
        />
        <Stack direction="row" spacing={1.5}>
          <SecretRefField
            label="API key" required fullWidth
            placeholder="vault://acme/sim/livekit_api_key"
            value={keyRef} onChange={touched(setKeyRef)}
            helper="Settings → Keys → Create new API key. Stored encrypted; used only to join test rooms."
          />
          <SecretRefField
            label="API secret" required fullWidth
            placeholder="vault://acme/sim/livekit_api_secret"
            value={secretRef} onChange={touched(setSecretRef)}
            helper="Shown once, when the key is created. Never shown again, never sent to the improver."
          />
        </Stack>
        <Field
          label="Agent name" required mono
          placeholder="e.g. support-agent"
          value={agentName} onChange={touched(setAgentName)}
          helper="The name the worker registers with — WorkerOptions(agent_name=…). It must match exactly; it isn't a display name."
        />
        <Field
          label="System prompt" required multiline minRows={4}
          placeholder="You are a friendly returns agent for Acme…"
          value={systemPrompt} onChange={setSystemPrompt}
          helper="LiveKit doesn't expose your agent's prompt, so paste it here. It seeds scenarios and the judge — it never changes the live agent."
        />

        <Box>
          <TextLink onClick={() => setMore((m) => !m)}>{more ? "Hide room config and sessions" : "Room config and sessions at once"}</TextLink>
          <Collapse in={more}>
            <Stack spacing={1.5} sx={{ mt: 1.25 }}>
              <Field
                label="Room config JSON" mono multiline minRows={3}
                placeholder='{"metadata": {"locale": "en-US"}}'
                value={roomConfig} onChange={touched(setRoomConfig)}
                helper={jsonOk ? "Optional — passed to every test room the agent is dispatched to." : "Not valid JSON."}
              />
              <Field
                label="Max sessions at once" mono type="number"
                value={maxSessions} onChange={touched(setMaxSessions)}
                helper="LiveKit Cloud: your plan's concurrent session limit (Settings → Project). Self-hosted: what your workers can take."
              />
            </Stack>
          </Collapse>
        </Box>

      </Stack>
    </ConnectSteps>
  );
}

LiveKitAgentFlow.propTypes = {
  title: PropTypes.string,
  onSave: PropTypes.func.isRequired,
  initial: PropTypes.object,
  /* What a LiveKit project can be fixed with — said by the catalogue. */
  note: PropTypes.string,
};
