import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { DEFAULT_SCENARIOS } from "../../../panels/scenarioCountRules";
import { BTN_SX, T12 } from "../../drawerStyles";
import useDrawerForm from "../useDrawerForm";
import {
  ChipToggle,
  Field,
  FormCard,
  Label,
  SegmentedToggle,
  useUiOnlyBuild,
} from "../formBits";
import SecretField from "../shared/SecretField";
import { sampleWebhookUrlFor } from "./sampleData";
import { EndpointConsent, HowWeTestIt } from "./endpointBits";

const TYPES = [
  { id: "voice", label: "Voice", icon: "solar:phone-calling-rounded-linear" },
  { id: "chat", label: "Chat", icon: "solar:chat-round-linear" },
  {
    id: "computer",
    label: "Computer use",
    icon: "solar:monitor-linear",
    soon: true,
  },
];
/* Where an endpoint's reply sits in its response: a JSON path. */
const REPLY_PATH = /^\$(\.[A-Za-z_][\w-]*|\[\d+\])+$/;
const ENDPOINT_URL = {
  http: /^https?:\/\/\S+$/i,
  websocket: /^wss?:\/\/\S+$/i,
};

const SAMPLE_CHECKS = [
  { label: "Endpoint", detail: "Answered a test turn" },
  { label: "Reply path", detail: "Found the agent's reply in the response" },
];

/**
 * An agent endpoint: we can only call it, over HTTP or a WebSocket, so it
 * needs where the reply sits in its response next to the URL and the token.
 * It can't be copied, so simulations drive the real agent once someone says
 * so. UI only: preflight is a sample and nothing builds.
 */
export default function EndpointForm({ onBusyChange }) {
  const build = useUiOnlyBuild(SAMPLE_CHECKS);
  const [form, set] = useDrawerForm(
    {
      agentType: "chat",
      transport: "http",
      endpoint: "",
      replyPath: "",
      token: "",
      callDirection: "inbound",
      showPrompt: false,
      systemPrompt: "",
      linkedRepo: "",
      consent: null,
      scenarioCount: DEFAULT_SCENARIOS,
    },
    build,
  );
  const {
    agentType,
    transport,
    endpoint,
    replyPath,
    token,
    callDirection,
    showPrompt,
    systemPrompt,
    linkedRepo,
    consent,
    scenarioCount,
  } = form;
  const ws = transport === "websocket";
  const urlOk = ENDPOINT_URL[transport].test(endpoint.trim());
  const pathOk = REPLY_PATH.test(replyPath.trim());
  const missing = !endpoint.trim()
    ? "Add the endpoint"
    : !urlOk
      ? `Use a ${ws ? "wss://" : "https://"} address`
      : !pathOk
        ? "Say where the reply sits"
        : !consent?.acknowledgedAt
          ? "Acknowledge that simulations drive the real agent"
          : null;

  return (
    <FormCard
      uiOnly
      intro="We can only call it, so improvements end in a suggested fix."
      build={build}
      scenarioCount={scenarioCount}
      onScenarioCount={set("scenarioCount")}
      canRun={!missing}
      blockedReason="Add the endpoint and where the reply sits, and acknowledge it drives the real agent. Then preflight checks it."
      onRun={build.runPreflight}
      hint={missing}
      onBusyChange={onBusyChange}
    >
      <Box>
        <Label>Agent type</Label>
        <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.75, mt: 0.75 }}>
          {TYPES.map((t) => (
            <ChipToggle
              key={t.id}
              icon={t.icon}
              label={t.label}
              soon={t.soon}
              on={agentType === t.id}
              onClick={() => set("agentType")(t.id)}
            />
          ))}
        </Box>
      </Box>
      <Box>
        <Typography sx={{ ...T12, fontWeight: 600, mb: 0.5 }}>
          Speaks
        </Typography>
        <SegmentedToggle
          label="Speaks"
          value={transport}
          onChange={set("transport")}
          options={[
            { value: "http", label: "HTTP" },
            { value: "websocket", label: "WebSocket" },
          ]}
        />
      </Box>
      <Stack direction="row" spacing={1.5} alignItems="flex-start">
        <Field
          label={ws ? "WebSocket URL" : "Endpoint URL"}
          required
          mono
          value={endpoint}
          onChange={set("endpoint")}
          placeholder={
            ws
              ? "wss://api.yourapp.com/agent/stream"
              : "https://api.yourapp.com/agent/step"
          }
          error={
            endpoint.trim() && !urlOk
              ? ws
                ? "A WebSocket URL starts with wss:// or ws://"
                : "An HTTP endpoint starts with https:// or http://"
              : null
          }
          helper={
            ws
              ? "One socket per conversation; each turn is sent on it."
              : "Every simulated turn POSTs here."
          }
          sx={{ flex: 1.4 }}
        />
        <Field
          label="Where the reply sits"
          required
          mono
          value={replyPath}
          onChange={set("replyPath")}
          placeholder="$.output.text"
          error={
            replyPath.trim() && !pathOk
              ? "A JSON path, like $.output.text or $.messages[0].content"
              : null
          }
          helper="The JSON path to the agent's reply."
          sx={{ flex: 1 }}
        />
      </Stack>
      <SecretField
        label="Auth token (optional)"
        value={token}
        onChange={set("token")}
        placeholder="Paste the token"
      />
      {agentType === "voice" && (
        <Box>
          <Label>Call direction</Label>
          <Box
            sx={{
              display: "grid",
              gap: 0.75,
              gridTemplateColumns: "1fr 1fr",
              mt: 0.75,
            }}
          >
            <ChipToggle
              label="Inbound: we call your agent"
              on={callDirection === "inbound"}
              onClick={() => set("callDirection")("inbound")}
              fullWidth
            />
            <ChipToggle
              label="Outbound: your agent dials us"
              on={callDirection === "outbound"}
              onClick={() => set("callDirection")("outbound")}
              fullWidth
            />
          </Box>
        </Box>
      )}
      {agentType === "chat" && (
        <Box>
          <Button
            onClick={() => set("showPrompt")((o) => !o)}
            startIcon={
              <Iconify
                icon={
                  showPrompt
                    ? "solar:alt-arrow-up-linear"
                    : "solar:alt-arrow-down-linear"
                }
                width={12}
              />
            }
            sx={{ ...BTN_SX, px: 0.75, color: "text.secondary" }}
          >
            {showPrompt
              ? "Hide system prompt override"
              : "System prompt override (optional)"}
          </Button>
          {showPrompt && (
            <Field
              multiline
              value={systemPrompt}
              onChange={set("systemPrompt")}
              placeholder="Leave blank to use whatever your endpoint already does."
              inputProps={{ "aria-label": "System prompt override" }}
              sx={{ mt: 1 }}
            />
          )}
        </Box>
      )}
      <Field
        label="Linked repository (optional)"
        mono
        value={linkedRepo}
        onChange={set("linkedRepo")}
        placeholder="https://github.com/your-org/your-agent"
        helper="Lets Sync see prompt and tool changes."
      />
      {endpoint.trim() && (
        <>
          <HowWeTestIt webhook={!!consent?.webhookAt} />
          <EndpointConsent
            value={consent}
            onChange={set("consent")}
            webhookUrl={sampleWebhookUrlFor(endpoint.trim())}
          />
        </>
      )}
    </FormCard>
  );
}
EndpointForm.propTypes = { onBusyChange: PropTypes.func };
