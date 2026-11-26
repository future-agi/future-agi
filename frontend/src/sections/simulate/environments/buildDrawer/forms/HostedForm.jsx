import PropTypes from "prop-types";
import { useState } from "react";
import { Box } from "@mui/material";
import { AGENT_TYPES } from "src/sections/agents/constants";
import usePanelBuild from "../../hooks/usePanelBuild";
import { DEFAULT_SCENARIOS } from "../../panels/scenarioCountRules";
import { COUNTRY_BY_ISO } from "../../components/countryCodes";
import { HOSTED_PLATFORMS_BY_TYPE } from "../../hostedPlatforms";
import useDrawerForm from "./useDrawerForm";
import { useConnectMode } from "../connectNewAgent";
import VoiceContact, { contactNumberError } from "./VoiceContact";
import UiOnlyFlag from "../../components/UiOnlyFlag";
import {
  ChipToggle,
  Field,
  FormCard,
  Label,
  MONO,
  useUiOnlyBuild,
} from "./formBits";
import FlaggedToggleRow from "./extras/FlaggedToggleRow";

const TYPES = [
  {
    id: AGENT_TYPES.VOICE,
    key: "voice",
    label: "Voice",
    icon: "solar:phone-calling-rounded-linear",
  },
  {
    id: AGENT_TYPES.CHAT,
    key: "chat",
    label: "Chat",
    icon: "solar:chat-round-linear",
  },
];
// "Assistant ID" reads as "assistant ID" mid-sentence.
const lowerFirst = (label) => label.charAt(0).toLowerCase() + label.slice(1);
const platformOf = (id) =>
  Object.values(HOSTED_PLATFORMS_BY_TYPE)
    .flat()
    .find((p) => p.id === id);
// The Observe project a connected agent's calls land in, named after it.
const observeSlugOf = (name) =>
  String(name || "agent")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/(^-|-$)/g, "") || "agent";

/**
 * Vapi or Retell AI: the key and the assistant's ID, then how a voice agent is
 * reached. `preset` names the platform id each agent type builds with; a null
 * side (Vapi chat) has no connector yet, so picking it makes the form UI only
 * with the other side's key and ID fields. Observability has no API yet either:
 * it works and is flagged, but its value is never sent.
 */
export default function HostedForm({ item, onBusyChange }) {
  const { preset } = item.setup;
  // Connecting for a chat world opens on Chat.
  const connectMode = useConnectMode();
  const live = usePanelBuild();
  const [observe, setObserve] = useState(false);
  const [form, set, setForm] = useDrawerForm(
    {
      agentType:
        connectMode?.modality === "text" ? AGENT_TYPES.CHAT : AGENT_TYPES.VOICE,
      id: "",
      key: "",
      repoUrl: "",
      simMode: "web",
      countryIso: "US",
      contactNumber: "",
      inboundCalls: true,
      agentSpeaksFirst: false,
      scenarioCount: DEFAULT_SCENARIOS,
    },
    { resetPreflight: () => resetBoth() },
  );
  const {
    agentType,
    id,
    key,
    repoUrl,
    simMode,
    countryIso,
    contactNumber,
    inboundCalls,
    agentSpeaksFirst,
    scenarioCount,
  } = form;
  const voice = agentType === AGENT_TYPES.VOICE;
  const type = TYPES.find((t) => t.id === agentType);
  const uiOnly = !preset[type.key];
  const platform =
    platformOf(preset[type.key]) ||
    platformOf(Object.values(preset).find(Boolean));
  const name = item.name;
  const sample = useUiOnlyBuild([
    { label: platform.keyLabel, detail: `Accepted by ${name}` },
    {
      label: platform.idLabel,
      detail: `${id.trim()} answers ${voice ? "calls" : "chats"}`,
    },
  ]);
  const build = uiOnly ? sample : live;
  const resetBoth = () => {
    live.resetPreflight();
    sample.resetPreflight();
  };

  // A new type is another connector, so the ID typed for the old one goes.
  const pickType = (next) => {
    setForm((s) => ({ ...s, agentType: next, id: "" }));
    resetBoth();
  };

  const phone = voice && simMode === "phone";
  const phoneError = phone
    ? contactNumberError(countryIso, contactNumber)
    : null;
  const missing = !key.trim()
    ? "Add the key"
    : !id.trim()
      ? `Add the ${lowerFirst(platform.idLabel)}`
      : phoneError;
  const canRun = !missing;

  const buildSource = () => ({
    kind: "platform",
    agentType,
    provider: platform.id,
    scenarioCount: Number(scenarioCount) || undefined,
    agentId: id.trim(),
    apiKey: key.trim(),
    ...(repoUrl.trim() ? { repoUrl: repoUrl.trim() } : {}),
    ...(voice
      ? {
          callDirection: inboundCalls ? "inbound" : "outbound",
          contact: {
            mode: simMode,
            inboundCalls,
            agentSpeaksFirst,
            ...(phone
              ? {
                  countryIso,
                  countryCode: COUNTRY_BY_ISO[countryIso]?.dial || "",
                  number: contactNumber.trim(),
                }
              : {}),
          },
        }
      : {}),
  });

  return (
    <FormCard
      intro="An assistant on a managed platform. Add the key and the assistant's ID. Everything else can wait for the environment's page."
      uiOnly={uiOnly}
      build={build}
      scenarioCount={scenarioCount}
      onScenarioCount={set("scenarioCount")}
      canRun={canRun}
      blockedReason={
        phoneError && key.trim() && id.trim()
          ? "Add a valid number to call, then run preflight."
          : `Add the key and the ${lowerFirst(platform.idLabel)}, then preflight checks them.`
      }
      onRun={() =>
        uiOnly ? sample.runPreflight() : live.runPreflight(buildSource())
      }
      hint={missing}
      onBusyChange={onBusyChange}
    >
      <Box>
        <Label>{`Voice or chat. ${name} does both`}</Label>
        <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.75, mt: 0.75 }}>
          {TYPES.map((t) => (
            <ChipToggle
              key={t.id}
              label={
                preset[t.key] ? (
                  t.label
                ) : (
                  <Box
                    component="span"
                    sx={{
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 0.75,
                    }}
                  >
                    {t.label}
                    <UiOnlyFlag />
                  </Box>
                )
              }
              icon={t.icon}
              on={agentType === t.id}
              onClick={() => pickType(t.id)}
            />
          ))}
        </Box>
      </Box>
      <Field
        label={platform.keyLabel}
        required
        mono
        type="password"
        value={key}
        onChange={set("key")}
        placeholder="Paste the key"
        helper="Stored encrypted. Used only to call the agent for test runs, never shown again."
      />
      {key.trim() && (
        <Field
          label={platform.idLabel}
          required
          mono
          value={id}
          onChange={set("id")}
          placeholder={platform.idPlaceholder}
          helper={`The ID from the ${name} dashboard.`}
        />
      )}
      {key.trim() && voice && (
        <VoiceContact form={form} set={set} phoneError={phoneError} />
      )}
      {key.trim() && (
        <FlaggedToggleRow
          title="Enable observability"
          checked={observe}
          onChange={setObserve}
          body={
            observe ? (
              <span>
                {`Pull its production ${voice ? "calls" : "conversations"} into Observe project `}
                <Box
                  component="span"
                  sx={{ fontFamily: MONO, color: "text.primary" }}
                >
                  {observeSlugOf(id.trim())}
                </Box>
                , created when you connect.
              </span>
            ) : (
              `Off. Its production ${voice ? "calls" : "conversations"} stay out of Observe. Turn it on later from the agent's Next steps.`
            )
          }
          hint={
            observe
              ? `We read its ${voice ? "call" : "conversation"} logs with the same key, so real failures show up in the Error Feed.`
              : undefined
          }
        />
      )}
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
HostedForm.propTypes = {
  item: PropTypes.object.isRequired,
  onBusyChange: PropTypes.func,
};
