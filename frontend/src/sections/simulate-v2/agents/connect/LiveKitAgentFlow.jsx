import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Button, CircularProgress, Collapse, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { Field, NoteLine } from "../../environments/StartEnvironment";
import { keyRefOf } from "../../_mock/secretRefs";
import ConnectSteps from "./ConnectSteps";
import SecretRefField from "./SecretRefField";
import { TextLink } from "./connectBits";
import { useConnectSubmitLabel } from "./connectSubmit";

const STEPS = ["Connect"];
const MONO = "ui-monospace, Menlo, monospace";

/*
  A LiveKit agent, by its project: the same fields the product's own LiveKit
  form asks for — server URL, API key and secret, the agent name its worker
  registers with — plus room config and how many sessions may run at once.
  Test connection checks the worker is registered before Connect.
*/
export default function LiveKitAgentFlow({ title, onSave, initial = null, note = null }) {
  const submitLabel = useConnectSubmitLabel();
  const [url, setUrl] = useState(initial?.livekitUrl || "");
  const [keyRef, setKeyRef] = useState(initial?.apiKeyRef || "");
  const [secretRef, setSecretRef] = useState(initial?.apiSecretRef || "");
  const [agentName, setAgentName] = useState(initial?.agentName || "");
  const [more, setMore] = useState(false);
  const [roomConfig, setRoomConfig] = useState("");
  const [maxSessions, setMaxSessions] = useState("5");
  /* idle · testing · ok · fail — reset by any change to what's tested. */
  const [test, setTest] = useState("idle");
  const touched = (fn) => (v) => { fn(v); setTest("idle"); };

  const savedKey = keyRefOf(keyRef, "vault://acme/sim/livekit_api_key");
  const savedSecret = keyRefOf(secretRef, "vault://acme/sim/livekit_api_secret");
  const urlOk = /^wss?:\/\/|^https?:\/\//.test(url.trim());
  const jsonOk = (() => { if (!roomConfig.trim()) return true; try { JSON.parse(roomConfig); return true; } catch { return false; } })();
  const sessions = Number(maxSessions);
  const sessionsOk = Number.isInteger(sessions) && sessions >= 1 && sessions <= 100;
  const filled = urlOk && !!savedKey && !!savedSecret && !!agentName.trim() && jsonOk && sessionsOk;

  const runTest = () => {
    setTest("testing");
    /* A worker name with a space can't be registered — the one failure we can show. */
    setTimeout(() => setTest(/\s/.test(agentName.trim()) ? "fail" : "ok"), 900);
  };

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
    versionNote: "Connected by its LiveKit project.",
    observability: false,
    callDirection: "inbound",
    contact: { mode: "web", inboundCalls: true, agentSpeaksFirst: false },
    flowReach: { direction: "inbound", whoSpeaksFirst: "caller" },
  };

  const next = {
    label: submitLabel,
    icon: "solar:link-circle-linear",
    disabled: !filled || test !== "ok",
    hint: !urlOk ? "Add the server URL" : !savedKey || !savedSecret ? "Add the API key and secret"
      : !agentName.trim() ? "Add the agent name" : !jsonOk ? "Room config isn't valid JSON"
        : !sessionsOk ? "Sessions at once: 1 to 100" : test !== "ok" ? "Test the connection" : null,
    onClick: () => onSave(source),
  };

  return (
    <ConnectSteps
      title={title}
      subtitle="A LiveKit agent, by its project. Add the project's URL and key, and the name the agent's worker registers with — we join its rooms as the caller."
      steps={STEPS}
      active={0}
      next={next}
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

        {/* Checked before Connect: the URL answers, the key is accepted, the worker is registered. */}
        <Stack
          direction="row" alignItems="center" spacing={1.25}
          sx={{ px: 1.5, py: 1.125, border: "1px solid", borderColor: "divider", borderRadius: 1 }}
        >
          {test === "testing" ? <CircularProgress size={14} thickness={5} sx={{ color: "text.subtitle", flexShrink: 0 }} />
            : test === "ok" ? <Iconify icon="solar:check-circle-bold" width={16} sx={{ color: "#16A34A", flexShrink: 0 }} />
              : test === "fail" ? <Iconify icon="solar:close-circle-bold" width={16} sx={{ color: "#DC2626", flexShrink: 0 }} />
                : <Iconify icon="solar:plug-circle-linear" width={16} sx={{ color: "text.subtitle", flexShrink: 0 }} />}
          <Typography sx={{ typography: "s2", flex: 1, minWidth: 0, color: test === "idle" ? "text.secondary" : "text.primary" }}>
            {test === "testing" ? "Checking the server, the key and the worker…"
              : test === "ok" ? <>{"Connected — worker "}<Box component="span" sx={{ fontFamily: MONO }}>{agentName.trim()}</Box>{" is registered and accepts test rooms."}</>
                : test === "fail" ? "The server answered, but no worker is registered under that name. Check it matches agent_name exactly, and that the worker is running."
                  : "Test the connection before connecting."}
          </Typography>
          <Button
            size="small" variant="outlined" disabled={!filled || test === "testing"} onClick={runTest}
            sx={{ typography: "s3", fontWeight: 700, height: 26, color: "text.primary", borderColor: "divider", flexShrink: 0 }}
          >
            {test === "ok" || test === "fail" ? "Test again" : "Test connection"}
          </Button>
        </Stack>
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
