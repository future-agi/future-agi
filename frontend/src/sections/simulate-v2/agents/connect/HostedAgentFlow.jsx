import PropTypes from "prop-types";
import { useEffect, useMemo, useState } from "react";
import { Box, CircularProgress, Stack, Typography } from "@mui/material";
import {
  assistantsFor,
  importedStructureOf,
  observeSlugOf,
  phoneParts,
} from "../../_mock/connectAgent";
import {
  AGENT_TYPES,
  COUNTRY_BY_ISO,
  HOSTED_PLATFORMS_BY_TYPE,
  PLATFORM_KEY_HELPER,
  PLATFORM_LOGOS,
  firstLive,
  isLivePlatform,
} from "../../environments/connectOptions";
import {
  ChipCard,
  ContactInformation,
  Field,
  Label,
  NoteLine,
  PlatformLogo,
  ToggleRow,
} from "../../environments/StartEnvironment";
import {
  credentialCheck,
  e164Of,
  factCheck,
  phoneNumberError,
  usePreflight,
} from "./preflight";
import { PLATFORM_TESTING } from "../../_mock/rsiModel";
import ConnectSteps from "./ConnectSteps";
import { TextLink } from "./connectBits";
import { CloneScopeChoice, HintLine } from "./replicationBits";
import { canCloneProvider, keyLacksCreateScope } from "../../_mock/replication";
import { keyRefOf } from "../../_mock/secretRefs";
import SecretRefField from "./SecretRefField";
import { useConnectSubmitLabel } from "./connectSubmit";
import { useNavigate } from "react-router-dom";
import { paths } from "src/routes/paths";
import { useConnectedAgents } from "../../store";
import { agentIdOf } from "../../_mock/agentRegistry";
import FoundAgentsList from "./FoundAgentsList";
import LiveKitAgentFlow from "./LiveKitAgentFlow";
import PlatformPreview, { PlatformVersionPicker } from "./PlatformPreview";
import {
  platformAddCli,
  platformPreviewOf,
  versionsOf,
} from "../../_mock/platformPreview";

const MONO = "ui-monospace, Menlo, monospace";
/* One screen: the platform, the key, the assistant, how test calls reach a
   voice agent (web or a phone number), observability — then Connect. The
   rest (a linked repository, constraints, ElevenLabs versioning, a sealed
   copy for Vapi and Retell) has working defaults and is set on the agent's
   page — never something Connect waits for. */
const STEPS = ["Connect"];

/**
 * A hosted platform, on one screen: the key, then the agent — picked from
 * the account's own list rather than typed — and Connect. The assistant's
 * config comes in as v1; how it's reached and its constraints start at the
 * defaults and are set on the agent's page.
 */
export default function HostedAgentFlow({
  title,
  onSave,
  prefill,
  initial = null,
  lockedType = null,
  onPlatform,
}) {
  const submitLabel = useConnectSubmitLabel();
  const [listing, setListing] = useState(false);
  const navigate = useNavigate();
  /* What's in Agents already — a platform agent connected twice is opened instead. */
  const { agents: connectedAgents } = useConnectedAgents();
  const [agentType, setAgentType] = useState(
    lockedType || initial?.agentType || "voice",
  );
  /* Picked on the directory ("Vapi", "Retell chat"): the drawer is that
     platform's — its header names it — so there is nothing to switch. */
  const platformLocked = !!initial?.platform;
  /* A platform that does both (Vapi, Retell…) asks which, in its own setup. */
  const bothChannels = platformLocked && (initial?.channels || []).length > 1;
  /* No "Others" here: a prompt with no platform behind it is an agent built
     in Future AGI, which has its own way in. */
  const platforms = (HOSTED_PLATFORMS_BY_TYPE[agentType] || []).filter(
    (p) => !p.isOther,
  );
  const [platformId, setPlatformId] = useState(
    initial?.platform || firstLive(platforms)?.id || "",
  );
  const platform =
    platforms.find((p) => p.id === platformId && isLivePlatform(p)) ||
    firstLive(platforms) ||
    null;
  /* The drawer's header names the platform being connected — it follows a switch here. */
  useEffect(() => {
    if (platform) onPlatform?.(platform);
  }, [platform?.id]); // eslint-disable-line react-hooks/exhaustive-deps
  /* The key itself, or a reference into the team's secret manager. */
  /* Connecting from an Observe project whose call logs come through
     Observe's own integration: that integration already holds the key, so
     its reference comes along — never the key itself. */
  const [keyRef, setKeyRef] = useState(initial?.apiKeyRef || "");
  /* What's recorded is where the key is held — the reference typed, or the
     platform's slot a typed key is stored encrypted under. */
  const savedRef = keyRefOf(
    keyRef,
    platform ? `vault://acme/sim/${platform.id}_api_key` : "",
  );
  const refOk = !!savedRef;
  /* A Vapi key without create scope can't make the test copy — the person
     re-issues it, or tests with settings overridden for one call (still
     live). The access check reads the scope off the referenced key. */
  const [scopeChoice, setScopeChoice] = useState(null);
  const lacksScope =
    !!platform &&
    refOk &&
    canCloneProvider(platform.id) &&
    keyLacksCreateScope(keyRef);
  /* ElevenLabs branches need versioning on — asked in Finish setting up,
     and only shipping a branch waits for it. */
  const testing = PLATFORM_TESTING[platform?.id] || null;

  const assistants = useMemo(
    () => assistantsFor(platform?.id, agentType),
    [platform?.id, agentType],
  );
  /* The chat channel a simulation reaches it on, where the platform has
     several (ElevenLabs chat or WhatsApp; Bland messaging). */
  const channels = agentType === "chat" ? platform?.chatChannels || [] : [];
  const [chatChannel, setChatChannel] = useState(initial?.chatChannel || null);
  const channel =
    channels.find((c) => c.id === chatChannel) || channels[0] || null;
  /* The key lists every assistant on the account, so the agent is always
     picked from the list. An ID that came with the page (an account's row,
     an issue's call logs) is picked there when it's listed; one that isn't
     listed is shown as given, to check against the key. */
  const startAssistant = initial?.agentId
    ? assistantsFor(initial?.platform, initial?.agentType || agentType).find(
        (a) => a.id === initial.agentId,
      ) || null
    : null;
  const listedAtStart = !!startAssistant;
  const [pickedId, setPickedId] = useState(
    listedAtStart ? initial.agentId : null,
  );
  const [typing, setTyping] = useState(!!initial?.agentId && !listedAtStart);
  const [typedId, setTypedId] = useState(initial?.agentId || "");
  const picked = typing
    ? null
    : assistants.find((a) => a.id === pickedId) || null;
  const agentId = typing ? typedId.trim() : picked?.id || "";
  /* What the import brings: a squad, flow or workflow's members, and for
     Retell whether it's a single prompt or a conversation flow. A typed ID
     reads as a single prompt unless it names a flow. */
  const imported = importedStructureOf(
    platform?.id,
    picked ||
      (agentId
        ? {
            id: agentId,
            retellKind: /flow/i.test(agentId)
              ? "conversation_flow"
              : "single_prompt",
          }
        : null),
  );
  /* Its versions, where the platform has them — the run is pinned to one. */
  const versions = useMemo(
    () => versionsOf(platform?.id, picked),
    [platform?.id, picked],
  );
  const [versionId, setVersionId] = useState(null);
  const version =
    versions.find((x) => x.id === versionId) || versions[0] || null;
  /* A typed ID's name, when the call logs that gave the ID carried it too. */
  const [name, setName] = useState(initial?.name || startAssistant?.name || "");
  /* How test calls reach a voice agent: a web call (no telephony), or a
     phone number dialled over PSTN — the agent's own number by default.
     Inbound, and who speaks first, can be changed later on the agent's page
     too. */
  const linkedRepo = "";
  /* Observability: on by default — its production calls are pulled with the
     same key into an Observe project named after it (or the issue's project
     when connecting from one). No tracing to add, nothing to wait for. */
  const [watch, setWatch] = useState(true);
  const [simMode, setSimMode] = useState("web");
  /* Picked from the start: the number it answers on is the one a phone simulation dials. */
  const [countryIso, setCountryIso] = useState(() =>
    startAssistant?.phone ? phoneParts(startAssistant.phone).countryIso : "US",
  );
  const [contactNumber, setContactNumber] = useState(() =>
    startAssistant?.phone ? phoneParts(startAssistant.phone).number : "",
  );
  const [inboundCalls, setInboundCalls] = useState(true);
  const [agentSpeaksFirst, setAgentSpeaksFirst] = useState(false);
  const voice = agentType === "voice";
  const dial = COUNTRY_BY_ISO[countryIso]?.dial || "";
  const phoneError =
    voice && simMode === "phone" ? phoneNumberError(dial, contactNumber) : null;

  const pickType = (id) => {
    setAgentType(id);
    /* Picked on the directory: the platform stays — only the channel changes. */
    if (!platformLocked)
      setPlatformId(
        firstLive(
          (HOSTED_PLATFORMS_BY_TYPE[id] || []).filter((p) => !p.isOther),
        )?.id || "",
      );
    /* One key per platform: the same key serves its voice and chat agents. */
    if (!platformLocked) {
      setKeyRef("");
      setScopeChoice(null);
    }
    setPickedId(null);
    setChatChannel(null);
  };
  const pickAssistant = (a) => {
    setPickedId(a.id);
    setVersionId(null);
    setName(a.name);
    /* The number it answers on is the one a phone simulation dials. */
    const parts = phoneParts(a.phone);
    setCountryIso(parts.countryIso);
    setContactNumber(parts.number);
  };

  const observeProject =
    prefill?.project || observeSlugOf(name.trim() || agentId);
  const source = {
    kind: "platform",
    agentType,
    provider: platform?.id,
    agentId,
    ...(refOk && { apiKeyRef: savedRef }),
    ...(picked?.model && { model: picked.model }),
    /* Recorded as it reads on the agent's Connections — "WhatsApp". */
    ...(channel && { chatChannel: channel.label }),
    observability: watch,
    ...(watch && {
      observeProject: {
        mode: prefill?.project ? "existing" : "create",
        id: observeProject,
      },
    }),
    ...(linkedRepo.trim() && { linkedRepo: linkedRepo.trim() }),
    name: name.trim(),
    versionNote: `Imported from ${platform?.name || "the platform"}${version ? ` ${version.label}` : ""}.`,
    /* The provider version a run is pinned to — or none, recorded as unpinned. */
    providerVersion: version?.label || null,
    providerPinned: !!version?.published,
    ...(lacksScope &&
      scopeChoice === "mocked" && { replicationMode: "exported_mocked" }),
    ...(imported.retellKind && { retellKind: imported.retellKind }),
    ...(imported.members.length && { members: imported.members }),
    ...(voice
      ? {
          callDirection: inboundCalls ? "inbound" : "outbound",
          contact: {
            mode: simMode,
            inboundCalls,
            agentSpeaksFirst,
            ...(simMode === "phone"
              ? {
                  countryIso,
                  countryCode: dial,
                  number: contactNumber.trim(),
                  e164: e164Of(dial, contactNumber),
                }
              : {}),
          },
          /* Reach the Agent step already asked — the confirm step adds the rest. */
          flowReach: {
            direction: inboundCalls ? "inbound" : "outbound",
            whoSpeaksFirst: agentSpeaksFirst ? "agent" : "caller",
          },
        }
      : {}),
  };

  /* With a usable key, the account's assistants are listed right below. */
  useEffect(() => {
    if (!refOk || !platform) return undefined;
    setListing(true);
    const t = setTimeout(() => setListing(false), 700);
    return () => clearTimeout(t);
  }, [refOk, platform?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  /* Preflight: the key, the assistant on that account, and the number a phone test dials. */
  const preflight = usePreflight(
    [
      credentialCheck({
        id: "key",
        label: platform?.keyLabel || `${platform?.name || "Platform"} API key`,
        ref: refOk ? savedRef : "",
        provider: platform?.name,
      }),
      factCheck({
        id: "agent",
        label: "Assistant",
        ok: !!agentId && !/missing|unknown/i.test(agentId),
        okText: `${name.trim() || agentId} found on ${platform?.name}${imported.structure ? ` · ${imported.structure}` : ""}`,
        failText: `No assistant ${agentId} on this ${platform?.name} account.`,
      }),
      ...(lacksScope
        ? [
            factCheck({
              id: "scope",
              label: "Create scope",
              ok: scopeChoice === "mocked",
              okText:
                "The key can't create a test copy — tests use settings overridden for one call, still live.",
              failText: "The key can't create a test copy.",
            }),
          ]
        : []),
      ...(voice && simMode === "phone"
        ? [
            factCheck({
              id: "phone",
              label: "Number to call",
              ok: !phoneError,
              okText: `Dials ${e164Of(dial, contactNumber)} over PSTN.`,
              failText: phoneError || "",
            }),
          ]
        : []),
    ],
    [
      platform?.id,
      savedRef,
      agentId,
      scopeChoice,
      simMode,
      countryIso,
      contactNumber,
    ],
  );
  const next = {
    label: submitLabel,
    icon: "solar:link-circle-linear",
    disabled:
      !platform ||
      !refOk ||
      (lacksScope && !scopeChoice) ||
      !agentId ||
      !!phoneError,
    hint: !platform
      ? "No platform for this agent type yet"
      : !refOk
        ? "Add the key"
        : lacksScope && !scopeChoice
          ? "Choose how to go on without create scope"
          : !agentId
            ? "Pick the agent"
            : phoneError || "",
    onClick: () =>
      onSave({ ...source, name: (name || picked?.name || "").trim() }),
  };

  /* LiveKit connects by its project, not an API key and an assistant list. */
  if (platform?.id === "livekit") {
    return (
      <LiveKitAgentFlow
        title={title}
        onSave={onSave}
        initial={initial}
        note={platform.idNote}
      />
    );
  }

  return (
    <ConnectSteps
      title={title}
      subtitle="An assistant on a managed platform. Add the key, pick the assistant — its config comes in as v1. Everything else can wait for the agent's page."
      steps={STEPS}
      active={0}
      next={next}
      preflight={{
        state: preflight,
        canRun: !!platform && refOk && !!agentId && !phoneError,
        blockedReason:
          !refOk || !agentId
            ? "Add the key and pick the agent — then preflight checks them."
            : phoneError
              ? "Add a valid number to call — then run preflight."
              : null,
        summary: agentId
          ? `Read ${name.trim() || agentId}'s config from ${platform?.name}.`
          : null,
      }}
    >
      <Stack spacing={1.75}>
        {!lockedType && (!platformLocked || bothChannels) && (
          <Box>
            <Label>
              {bothChannels
                ? `Voice or chat — ${platform?.name || "this platform"} does both`
                : "Agent type"}
            </Label>
            <Box
              sx={{ display: "flex", flexWrap: "wrap", gap: 0.75, mt: 0.75 }}
            >
              {AGENT_TYPES.filter(
                (t) => !bothChannels || initial.channels.includes(t.id),
              ).map((t) => (
                <ChipCard
                  key={t.id}
                  icon={t.icon}
                  label={t.label}
                  comingSoon={t.comingSoon}
                  on={agentType === t.id}
                  onClick={() => pickType(t.id)}
                />
              ))}
            </Box>
          </Box>
        )}
        <Box>
          {!platformLocked && <Label>Platform</Label>}
          {!platformLocked && (
            <Box
              sx={{ display: "flex", flexWrap: "wrap", gap: 0.75, mt: 0.75 }}
            >
              {platforms.map((p) => (
                <ChipCard
                  key={p.id}
                  logo={
                    <PlatformLogo
                      id={p.id}
                      name={p.name}
                      brand={p.brand}
                      icon={p.icon}
                    />
                  }
                  label={
                    PLATFORM_LOGOS[p.id]?.type === "wordmark" ? null : p.name
                  }
                  comingSoon={p.comingSoon}
                  on={platform?.id === p.id}
                  onClick={() => {
                    setPlatformId(p.id);
                    setPickedId(null);
                    setChatChannel(null);
                  }}
                />
              ))}
            </Box>
          )}
          {!platform && (
            <Typography
              sx={{ typography: "s3", color: "text.subtitle", mt: 1 }}
            >
              None for this agent type yet — connect it by its endpoint.
            </Typography>
          )}
          {platform?.idNote && (
            <Typography
              component="div"
              sx={{ typography: "s3", color: "text.subtitle", mt: 0.75 }}
            >
              <NoteLine text={platform.idNote} />
            </Typography>
          )}
        </Box>
        {channels.length > 0 && (
          <Box>
            <Label>Chat channel</Label>
            <Box
              sx={{ display: "flex", flexWrap: "wrap", gap: 0.75, mt: 0.75 }}
            >
              {channels.map((c) => (
                <ChipCard
                  key={c.id}
                  icon={c.icon || "solar:chat-round-linear"}
                  label={c.label}
                  on={channel?.id === c.id}
                  onClick={() => setChatChannel(c.id)}
                />
              ))}
            </Box>
            <Typography
              sx={{ typography: "s3", color: "text.subtitle", mt: 0.75 }}
            >
              {channel?.id === "whatsapp"
                ? "Simulated customers write to it on WhatsApp, the way your customers do."
                : `Simulated customers write to it over ${platform?.name}'s ${channel?.label.toLowerCase()} channel.`}
            </Typography>
          </Box>
        )}
        {platform && (
          <SecretRefField
            label={platform.keyLabel || "API key"}
            required
            placeholder={`vault://acme/sim/${platform.id}_api_key`}
            value={keyRef}
            onChange={(v) => {
              setKeyRef(v);
              setScopeChoice(null);
            }}
            helper={
              initial?.keyFrom && keyRef === initial.apiKeyRef
                ? `The key your ${initial.keyFrom} already uses. ${PLATFORM_KEY_HELPER}`
                : PLATFORM_KEY_HELPER
            }
          />
        )}

        {platform &&
          refOk &&
          (listing ? (
            <Stack
              direction="row"
              alignItems="center"
              spacing={1}
              sx={{
                px: 1.5,
                py: 1.25,
                borderRadius: 1,
                bgcolor: "background.neutral",
              }}
            >
              <CircularProgress
                size={13}
                thickness={5}
                sx={{ color: "text.subtitle" }}
              />
              <Typography
                sx={{ typography: "s2", color: "text.secondary" }}
              >{`Listing assistants on ${platform.name}…`}</Typography>
            </Stack>
          ) : (
            <>
              {lacksScope && (
                <CloneScopeChoice
                  providerName={platform?.name}
                  choice={scopeChoice}
                  onReissue={() => {
                    setKeyRef("");
                    setScopeChoice(null);
                  }}
                  onMocked={() => setScopeChoice("mocked")}
                />
              )}
              {typing ? (
                <Box>
                  <Field
                    label={platform?.idLabel || "Agent ID"}
                    required
                    mono
                    placeholder={platform?.idPlaceholder}
                    value={typedId}
                    onChange={setTypedId}
                    helper={`The ID from the ${platform?.name} dashboard.`}
                  />
                  <TextLink onClick={() => setTyping(false)} sx={{ mt: 0.75 }}>
                    Pick from the list instead
                  </TextLink>
                </Box>
              ) : !assistants.length ? (
                /* Nothing on the account: the key is the thing to check, not the ID. */
                <Box
                  sx={{
                    px: 1.5,
                    py: 1.25,
                    borderRadius: 1,
                    bgcolor: "background.neutral",
                  }}
                >
                  <Typography
                    sx={{ typography: "s2", fontWeight: 600 }}
                  >{`No ${voice ? "voice" : "chat"} agents on this ${platform?.name} account`}</Typography>
                  <Typography
                    sx={{ typography: "s3", color: "text.secondary", mt: 0.25 }}
                  >
                    {`If your agent isn't listed, the key is from another ${platform?.name} account or workspace — paste the key from yours.`}
                  </Typography>
                </Box>
              ) : (
                <Box>
                  {/* The same list as a repository's agents: search, radio rows,
                    ones already in Agents marked and opened instead. */}
                  <FoundAgentsList
                    agents={assistants}
                    agentId={picked?.id || null}
                    onPick={pickAssistant}
                    heading={
                      assistants.length === 1
                        ? `1 agent on this ${platform?.name} account`
                        : `${assistants.length} agents on this ${platform?.name} account — which one do you mean?`
                    }
                    searchHint="name, ID or number"
                    searchOf={(a) => `${a.name} ${a.id} ${a.phone || ""}`}
                    describe={(a, connected) => (
                      <>
                        <Box component="span" sx={{ fontFamily: MONO }}>
                          {a.id}
                        </Box>
                        {voice ? ` · ${a.phone || "Web only"}` : ""}
                        {a.updated ? ` · updated ${a.updated}` : ""}
                        {connected ? " · already in Agents" : ""}
                      </>
                    )}
                    connectedOf={(a) =>
                      connectedAgents.find(
                        (x) =>
                          x.values?.provider === platform?.id &&
                          x.values?.agentId === a.id,
                      ) || null
                    }
                    onOpenConnected={(rec) =>
                      navigate(
                        `${paths.dashboard.agents}/connected/${agentIdOf(rec.key)}`,
                      )
                    }
                  />
                  <Typography
                    sx={{ typography: "s3", color: "text.subtitle", mt: 0.75 }}
                  >
                    {`Every agent this key can see is listed. Missing one? The key is from another ${platform?.name} account or workspace.`}
                  </Typography>
                </Box>
              )}
              {agentId && imported.structure && (
                <Typography
                  component="div"
                  sx={{ typography: "s3", color: "text.secondary" }}
                >
                  <HintLine
                    hint={
                      platform?.id === "retell"
                        ? imported.retellKind === "conversation_flow"
                          ? "Each node's instructions come in as a member, and improvements can propose changes node by node."
                          : "One prompt for the whole call."
                        : null
                    }
                  >
                    {`Read from ${platform?.name}: ${imported.structure}.`}
                  </HintLine>
                </Typography>
              )}
              {/* Which version a run pins to, then what we'll read — tools marked redirected or live. */}
              {agentId && picked && (
                <PlatformVersionPicker
                  provider={platform?.id}
                  versions={versions}
                  value={version?.id || ""}
                  onChange={setVersionId}
                />
              )}
              {agentId && picked && (
                <PlatformPreview
                  preview={platformPreviewOf(platform?.id, picked, {
                    voice,
                  })}
                  cli={platformAddCli({
                    provider: platform?.id,
                    agentId,
                    name: name.trim() || picked?.name,
                    keyRef: refOk ? savedRef : "",
                    voice,
                  })}
                />
              )}
              {/* How test calls reach it — a web call, or its number over PSTN. */}
              {voice && agentId && (
                <ContactInformation
                  mode={simMode}
                  onMode={setSimMode}
                  countryIso={countryIso}
                  onCountryIso={setCountryIso}
                  contactNumber={contactNumber}
                  onContactNumber={setContactNumber}
                  inboundCalls={inboundCalls}
                  onInboundCalls={setInboundCalls}
                  agentSpeaksFirst={agentSpeaksFirst}
                  onAgentSpeaksFirst={setAgentSpeaksFirst}
                />
              )}
              {voice &&
                agentId &&
                phoneError &&
                simMode === "phone" &&
                contactNumber.trim() && (
                  <Typography
                    sx={{ typography: "s3", color: "error.main", mt: -1 }}
                  >
                    {phoneError}
                  </Typography>
                )}
              {/* Observability: its production calls, pulled with the same key. */}
              {agentId && (
                <ToggleRow
                  checked={watch}
                  onChange={setWatch}
                  title="Enable observability"
                  body={
                    watch ? (
                      <>
                        {`Pull its production ${voice ? "calls" : "conversations"} into Observe project `}
                        <Box
                          component="span"
                          sx={{ fontFamily: MONO, color: "text.primary" }}
                        >
                          {observeProject}
                        </Box>
                        {prefill?.project ? "" : " — created when you connect"}
                      </>
                    ) : (
                      `Off — its production ${voice ? "calls" : "conversations"} stay out of Observe. Turn it on later from the agent's Next steps.`
                    )
                  }
                  hint={
                    watch
                      ? `We read its ${voice ? "call" : "conversation"} logs with the same key, so real failures show up in the Error Feed.`
                      : null
                  }
                />
              )}
              {testing?.needsVersioning && agentId && (
                <Typography
                  component="div"
                  sx={{ typography: "s3", color: "text.subtitle" }}
                >
                  <HintLine hint={testing.note}>
                    Changes ship as a branch once versioning is on for the agent
                    — switch it on from Finish setting up.
                  </HintLine>
                </Typography>
              )}
            </>
          ))}
      </Stack>
    </ConnectSteps>
  );
}

HostedAgentFlow.propTypes = {
  title: PropTypes.string,
  /* Told which platform is picked, so the drawer's header can name it. */
  onPlatform: PropTypes.func,
  /* Set when connecting for one environment — the type is fixed, not a choice. */
  lockedType: PropTypes.string,
  initial: PropTypes.object,
  limits: PropTypes.object.isRequired,
  onLimits: PropTypes.func.isRequired,
  onSave: PropTypes.func.isRequired,
  onSwitch: PropTypes.func,
  profile: PropTypes.object.isRequired,
  onProfile: PropTypes.func.isRequired,
  prefill: PropTypes.object,
};
