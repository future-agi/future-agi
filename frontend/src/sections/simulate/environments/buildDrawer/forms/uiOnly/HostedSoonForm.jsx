import PropTypes from "prop-types";
import { useConnectMode } from "../../connectNewAgent";
import { useEffect, useMemo, useState } from "react";
import { Box, Typography } from "@mui/material";
import { DEFAULT_SCENARIOS } from "../../../panels/scenarioCountRules";
import { T12 } from "../../drawerStyles";
import useDrawerForm from "../useDrawerForm";
import VoiceContact, { contactNumberError } from "../VoiceContact";
import {
  ChipToggle,
  FormCard,
  HintLine,
  Label,
  MONO,
  ToggleRow,
  useUiOnlyBuild,
} from "../formBits";
import BusyRow from "../shared/BusyRow";
import FoundAgentsList from "../shared/FoundAgentsList";
import SecretField from "../shared/SecretField";
import {
  HOSTED_SOON,
  PLATFORM_KEY_HELPER,
  assistantsFor,
  observeSlugOf,
  phoneParts,
  structureOf,
} from "./sampleData";

const LISTING_MS = 700;
const TYPES = [
  { id: "voice", label: "Voice", icon: "solar:phone-calling-rounded-linear" },
  { id: "chat", label: "Chat", icon: "solar:chat-round-linear" },
];

/**
 * A hosted platform without a connector yet, on Vel's one screen: the key,
 * then the agent picked from the account's own list, how test calls reach a
 * voice agent, and observability. UI only: the list is sample agents,
 * preflight is a sample and nothing builds.
 */
export default function HostedSoonForm({ item, onBusyChange }) {
  const platformId = item.setup.platform;
  const platform = HOSTED_SOON[platformId];
  const { name } = platform;
  // Connecting for a world opens on the world's agent type when the platform has it.
  const connectMode = useConnectMode();
  const wanted = { voice: "voice", text: "chat" }[connectMode?.modality];
  const build = useUiOnlyBuild([
    { label: platform.keyLabel, detail: `Accepted by ${name}` },
    { label: "Agent", detail: `Found on this ${name} account` },
  ]);
  const [form, set, setForm] = useDrawerForm(
    {
      agentType: platform.channels.includes(wanted)
        ? wanted
        : platform.channels[0],
      chatChannel: platform.chatChannels?.[0]?.id || null,
      key: "",
      agentId: "",
      agentName: "",
      simMode: "web",
      countryIso: "US",
      contactNumber: "",
      inboundCalls: true,
      agentSpeaksFirst: false,
      observability: true,
      scenarioCount: DEFAULT_SCENARIOS,
    },
    build,
  );
  const { agentType, chatChannel, key, agentId, observability, scenarioCount } =
    form;
  const voice = agentType === "voice";
  const hasKey = !!key.trim();
  const channels = !voice ? platform.chatChannels || [] : [];
  const channel = channels.find((c) => c.id === chatChannel) || channels[0];
  const assistants = useMemo(
    () => assistantsFor(platformId, agentType),
    [platformId, agentType],
  );
  const picked = assistants.find((a) => a.id === agentId) || null;
  const structure = structureOf(platformId, picked);
  const idNote = platform.idNote?.[agentType];
  const calls = voice ? "calls" : "conversations";

  // With a key, the account's agents are listed right below.
  const [listing, setListing] = useState(false);
  useEffect(() => {
    if (!hasKey) return undefined;
    setListing(true);
    const t = setTimeout(() => setListing(false), LISTING_MS);
    return () => clearTimeout(t);
  }, [hasKey]);

  // One key serves both channels; only the agent picked goes.
  const pickType = (next) => {
    setForm((s) => ({ ...s, agentType: next, agentId: "", agentName: "" }));
    build.resetPreflight();
  };
  // The number it answers on is the one a phone simulation dials.
  const pickAgent = (a) => {
    const parts = phoneParts(a.phone);
    setForm((s) => ({
      ...s,
      agentId: a.id,
      agentName: a.name,
      countryIso: parts.countryIso,
      contactNumber: parts.number,
    }));
    build.resetPreflight();
  };

  const phoneError =
    voice && form.simMode === "phone"
      ? contactNumberError(form.countryIso, form.contactNumber)
      : null;
  const missing = !hasKey
    ? "Add the key"
    : !agentId
      ? "Pick the agent"
      : phoneError;
  const project = observeSlugOf(form.agentName || agentId);

  return (
    <FormCard
      uiOnly
      intro="An assistant on a managed platform. Add the key, pick the assistant, and its config comes in as v1. Everything else can wait for the environment's page."
      build={build}
      scenarioCount={scenarioCount}
      onScenarioCount={set("scenarioCount")}
      canRun={!missing}
      blockedReason={
        phoneError && agentId
          ? "Add a valid number to call, then run preflight."
          : "Add the key and pick the agent, then preflight checks them."
      }
      onRun={build.runPreflight}
      hint={missing}
      onBusyChange={onBusyChange}
    >
      {(platform.channels.length > 1 || idNote) && (
        <Box>
          {platform.channels.length > 1 && (
            <>
              <Label>{`Voice or chat. ${name} does both`}</Label>
              <Box
                sx={{ display: "flex", flexWrap: "wrap", gap: 0.75, mt: 0.75 }}
              >
                {TYPES.map((t) => (
                  <ChipToggle
                    key={t.id}
                    label={t.label}
                    icon={t.icon}
                    on={agentType === t.id}
                    onClick={() => pickType(t.id)}
                  />
                ))}
              </Box>
            </>
          )}
          {idNote && (
            <Typography sx={{ ...T12, color: "text.secondary", mt: 0.75 }}>
              {idNote}
            </Typography>
          )}
        </Box>
      )}
      {channels.length > 0 && (
        <Box>
          <Label>Chat channel</Label>
          <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.75, mt: 0.75 }}>
            {channels.map((c) => (
              <ChipToggle
                key={c.id}
                icon={c.icon || "solar:chat-round-linear"}
                label={c.label}
                on={channel?.id === c.id}
                onClick={() => set("chatChannel")(c.id)}
              />
            ))}
          </Box>
          <Typography sx={{ ...T12, color: "text.secondary", mt: 0.75 }}>
            {channel?.id === "whatsapp"
              ? "Simulated customers write to it on WhatsApp, the way your customers do."
              : `Simulated customers write to it over ${name}'s ${channel?.label.toLowerCase()} channel.`}
          </Typography>
        </Box>
      )}
      <SecretField
        label={platform.keyLabel}
        required
        value={key}
        onChange={set("key")}
        helper={PLATFORM_KEY_HELPER}
      />

      {hasKey &&
        (listing ? (
          <BusyRow>{`Listing agents on ${name}…`}</BusyRow>
        ) : (
          <>
            {assistants.length ? (
              <Box>
                <FoundAgentsList
                  sample
                  agents={assistants}
                  agentId={agentId || null}
                  onPick={pickAgent}
                  heading={
                    assistants.length === 1
                      ? `1 agent on this ${name} account`
                      : `${assistants.length} agents on this ${name} account. Which one do you mean?`
                  }
                  searchHint="name, ID or number"
                  searchOf={(a) => `${a.name} ${a.id} ${a.phone || ""}`}
                  describe={(a) => (
                    <>
                      <Box component="span" sx={{ fontFamily: MONO }}>
                        {a.id}
                      </Box>
                      {voice ? ` · ${a.phone || "Web only"}` : ""}
                      {a.updated ? ` · updated ${a.updated}` : ""}
                    </>
                  )}
                />
                <Typography sx={{ ...T12, color: "text.secondary", mt: 0.75 }}>
                  {`Every agent this key can see is listed. Missing one? The key is from another ${name} account or workspace.`}
                </Typography>
              </Box>
            ) : (
              <Box
                sx={{
                  px: 1.5,
                  py: 1.25,
                  borderRadius: "6px",
                  bgcolor: "background.neutral",
                }}
              >
                <Typography sx={{ ...T12, fontWeight: 600 }}>
                  {`No ${voice ? "voice" : "chat"} agents on this ${name} account`}
                </Typography>
                <Typography
                  sx={{ ...T12, color: "text.secondary", mt: 0.25 }}
                >{`If your agent isn't listed, the key is from another ${name} account or workspace. Paste the key from yours.`}</Typography>
              </Box>
            )}
            {agentId && structure && (
              <Typography sx={{ ...T12, color: "text.secondary" }}>
                {`Read from ${name}: ${structure}.`}
              </Typography>
            )}
            {voice && agentId && (
              <VoiceContact form={form} set={set} phoneError={phoneError} />
            )}
            {agentId && (
              <ToggleRow
                title="Enable observability"
                checked={observability}
                onChange={set("observability")}
                body={
                  observability ? (
                    <>
                      {`Pull its production ${calls} into Observe project `}
                      <Box
                        component="span"
                        sx={{ fontFamily: MONO, color: "text.primary" }}
                      >
                        {project}
                      </Box>
                      , created when you connect
                    </>
                  ) : (
                    `Off. Its production ${calls} stay out of Observe. Turn it on later from the agent's Next steps.`
                  )
                }
                hint={
                  observability
                    ? `We read its ${voice ? "call" : "conversation"} logs with the same key, so real failures show up in the Error Feed.`
                    : undefined
                }
              />
            )}
            {platform.needsVersioning && agentId && (
              <Typography
                component="div"
                sx={{ ...T12, color: "text.secondary" }}
              >
                <HintLine
                  text="Changes ship as a branch once versioning is on for the agent. Switch it on from Finish setting up."
                  hint={platform.versioningNote}
                />
              </Typography>
            )}
          </>
        ))}
    </FormCard>
  );
}
HostedSoonForm.propTypes = {
  item: PropTypes.object.isRequired,
  onBusyChange: PropTypes.func,
};
