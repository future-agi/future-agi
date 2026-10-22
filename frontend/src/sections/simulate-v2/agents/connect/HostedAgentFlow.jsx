import PropTypes from "prop-types";
import { useEffect, useMemo, useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Button, CircularProgress, IconButton, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { CALL_LOG_PROVIDERS, SANDBOX_IMPORT_PROVIDERS, assistantsFor, importedStructureOf, observeSlugOf, phoneParts } from "../../_mock/connectAgent";
import { AGENT_TYPES, COUNTRY_BY_ISO, HOSTED_PLATFORMS_BY_TYPE, PLATFORM_KEY_HELPER, PLATFORM_LOGOS, firstLive, isLivePlatform } from "../../environments/connectOptions";
import { ChipCard, Field, Label, NoteLine, PlatformLogo } from "../../environments/StartEnvironment";
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

const MONO = "ui-monospace, Menlo, monospace";
/* One screen: the platform, the key, the assistant — then Connect. The rest
   (how calls reach it, a linked repository, constraints, ElevenLabs
   versioning) has working defaults and is set on the agent's page, in Finish
   setting up. A sealed copy (Vapi, Retell) is an optional choice here. */
const STEPS = ["Connect"];

/**
 * A hosted platform, on one screen: the key, then the agent — picked from
 * the account's own list rather than typed — and Connect. The assistant's
 * config comes in as v1; how it's reached and its constraints start at the
 * defaults and are set on the agent's page.
 */
export default function HostedAgentFlow({ title, onSave, prefill, initial = null, lockedType = null, onPlatform }) {
  const submitLabel = useConnectSubmitLabel();
  const [listing, setListing] = useState(false);
  const navigate = useNavigate();
  /* What's in Agents already — a platform agent connected twice is opened instead. */
  const { agents: connectedAgents } = useConnectedAgents();
  const [agentType, setAgentType] = useState(lockedType || initial?.agentType || "voice");
  /* Picked on the directory ("Vapi", "Retell chat"): the drawer is that
     platform's — its header names it — so there is nothing to switch. */
  const platformLocked = !!initial?.platform;
  /* No "Others" here: a prompt with no platform behind it is an agent built
     in Future AGI, which has its own way in. */
  const platforms = (HOSTED_PLATFORMS_BY_TYPE[agentType] || []).filter((p) => !p.isOther);
  const [platformId, setPlatformId] = useState(initial?.platform || firstLive(platforms)?.id || "");
  const platform = platforms.find((p) => p.id === platformId && isLivePlatform(p)) || firstLive(platforms) || null;
  /* The drawer's header names the platform being connected — it follows a switch here. */
  useEffect(() => { if (platform) onPlatform?.(platform); }, [platform?.id]); // eslint-disable-line react-hooks/exhaustive-deps
  /* The key itself, or a reference into the team's secret manager. */
  /* Connecting from an Observe project whose call logs come through
     Observe's own integration: that integration already holds the key, so
     its reference comes along — never the key itself. */
  const [keyRef, setKeyRef] = useState(initial?.apiKeyRef || "");
  /* What's recorded is where the key is held — the reference typed, or the
     platform's slot a typed key is stored encrypted under. */
  const savedRef = keyRefOf(keyRef, platform ? `vault://acme/sim/${platform.id}_api_key` : "");
  const refOk = !!savedRef;
  /* A Vapi key without create scope can't make the test copy — the person
     re-issues it, or tests with settings overridden for one call (still
     live). The access check reads the scope off the referenced key. */
  const [scopeChoice, setScopeChoice] = useState(null);
  const lacksScope = !!platform && refOk && canCloneProvider(platform.id) && keyLacksCreateScope(keyRef);
  /* ElevenLabs branches need versioning on — asked in Finish setting up,
     and only shipping a branch waits for it. */
  const testing = PLATFORM_TESTING[platform?.id] || null;

  const assistants = useMemo(() => assistantsFor(platform?.id, agentType), [platform?.id, agentType]);
  /* The chat channel a simulation reaches it on, where the platform has
     several (ElevenLabs chat or WhatsApp; Bland messaging). */
  const channels = agentType === "chat" ? platform?.chatChannels || [] : [];
  const [chatChannel, setChatChannel] = useState(initial?.chatChannel || null);
  const channel = channels.find((c) => c.id === chatChannel) || channels[0] || null;
  const [pickedId, setPickedId] = useState(null);
  /* A pasted ID comes in typed — no need to list the account to find it. */
  const [typing, setTyping] = useState(!!initial?.agentId);
  const [typedId, setTypedId] = useState(initial?.agentId || "");
  const picked = typing ? null : assistants.find((a) => a.id === pickedId) || null;
  const agentId = typing ? typedId.trim() : picked?.id || "";
  /* What the import brings: a squad, flow or workflow's members, and for
     Retell whether it's a single prompt or a conversation flow. A typed ID
     reads as a single prompt unless it names a flow. */
  const imported = importedStructureOf(platform?.id, picked || (agentId ? { id: agentId, retellKind: /flow/i.test(agentId) ? "conversation_flow" : "single_prompt" } : null));
  /* Optional (Vapi, Retell): import a copy into our sandbox with the code
     behind its tools, so it's tested sealed rather than live. */
  const canImport = SANDBOX_IMPORT_PROVIDERS.includes(platform?.id);
  const [importCopy, setImportCopy] = useState(false);
  /* Collapsed until asked for — the main flow tests the live assistant. */
  const [sealedOpen, setSealedOpen] = useState(false);
  const [toolFiles, setToolFiles] = useState([]);
  const sealed = canImport && importCopy && toolFiles.length > 0;

  /* A typed ID's name, when the call logs that gave the ID carried it too. */
  const [name, setName] = useState(initial?.name || "");
  /* Defaults until the agent's page says otherwise: reach it as a web call,
     inbound, the caller speaks first. Vapi, Retell and Bland call logs are
     read with the same key, so those agents are linked to an Observe project
     straight away; any other platform picks its project in Finish setting up. */
  const linkedRepo = "";
  const watch = CALL_LOG_PROVIDERS.includes(platform?.id);
  const simMode = "web";
  const [countryIso, setCountryIso] = useState("US");
  const [contactNumber, setContactNumber] = useState("");
  const inboundCalls = true;
  const agentSpeaksFirst = false;
  const voice = agentType === "voice";

  const pickType = (id) => {
    setAgentType(id);
    setPlatformId(firstLive((HOSTED_PLATFORMS_BY_TYPE[id] || []).filter((p) => !p.isOther))?.id || "");
    setKeyRef("");
    setScopeChoice(null);
    setPickedId(null);
    setChatChannel(null);
    setImportCopy(false);
    setSealedOpen(false);
    setToolFiles([]);
  };
  const pickAssistant = (a) => {
    setPickedId(a.id);
    setName(a.name);
    /* The number it answers on is the one a phone simulation dials. */
    const parts = phoneParts(a.phone);
    setCountryIso(parts.countryIso);
    setContactNumber(parts.number);
  };

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
    ...(linkedRepo.trim() && { linkedRepo: linkedRepo.trim() }),
    name: name.trim(),
    versionNote: `Imported from ${platform?.name || "the platform"}.`,
    ...(lacksScope && scopeChoice === "mocked" && { replicationMode: "exported_mocked" }),
    ...(imported.retellKind && { retellKind: imported.retellKind }),
    ...(imported.members.length && { members: imported.members }),
    ...(sealed && { importedCopy: true, toolCodeUploaded: true, toolCodeFiles: toolFiles.map((f) => f.name) }),
    ...(voice ? {
      callDirection: inboundCalls ? "inbound" : "outbound",
      contact: {
        mode: simMode, inboundCalls, agentSpeaksFirst,
        ...(simMode === "phone" ? { countryIso, countryCode: COUNTRY_BY_ISO[countryIso]?.dial || "", number: contactNumber.trim() } : {}),
      },
      /* Reach the Agent step already asked — the confirm step adds the rest. */
      flowReach: { direction: inboundCalls ? "inbound" : "outbound", whoSpeaksFirst: agentSpeaksFirst ? "agent" : "caller" },
    } : {}),
  };
  const observeProject = prefill?.project || observeSlugOf(name.trim() || agentId);

  /* With a usable key, the account's assistants are listed right below. */
  useEffect(() => {
    if (!refOk || !platform) return undefined;
    setListing(true);
    const t = setTimeout(() => setListing(false), 700);
    return () => clearTimeout(t);
  }, [refOk, platform?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const next = {
    label: submitLabel,
    icon: "solar:link-circle-linear",
    disabled: !platform || !refOk || (lacksScope && !scopeChoice) || !agentId || (canImport && importCopy && !toolFiles.length),
    hint: !platform ? "No platform for this agent type yet" : !refOk ? "Add the key"
      : lacksScope && !scopeChoice ? "Choose how to go on without create scope" : !agentId ? "Pick the agent"
        : "Upload the code behind its tools, or don't import a copy",
    onClick: () => onSave({ ...source, name: (name || picked?.name || "").trim() }),
  };

  return (
    <ConnectSteps
      title={title}
      subtitle="An assistant on a managed platform. Add the key, pick the assistant — its config comes in as v1. Everything else can wait for the agent's page."
      steps={STEPS}
      active={0}
      next={next}
    >
      <Stack spacing={1.75}>
        {!lockedType && !platformLocked && <Box>
          <Label>Agent type</Label>
          <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.75, mt: 0.75 }}>
            {AGENT_TYPES.map((t) => (
              <ChipCard key={t.id} icon={t.icon} label={t.label} comingSoon={t.comingSoon} on={agentType === t.id} onClick={() => pickType(t.id)} />
            ))}
          </Box>
        </Box>}
        <Box>
          {!platformLocked && <Label>Platform</Label>}
          {!platformLocked && <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.75, mt: 0.75 }}>
            {platforms.map((p) => (
              <ChipCard
                key={p.id}
                logo={<PlatformLogo id={p.id} name={p.name} brand={p.brand} icon={p.icon} />}
                label={PLATFORM_LOGOS[p.id]?.type === "wordmark" ? null : p.name}
                comingSoon={p.comingSoon}
                on={platform?.id === p.id}
                onClick={() => { setPlatformId(p.id); setPickedId(null); setChatChannel(null); setImportCopy(false); setSealedOpen(false); setToolFiles([]); }}
              />
            ))}
          </Box>}
          {!platform && (
            <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 1 }}>
              None for this agent type yet — connect it by its endpoint.
            </Typography>
          )}
          {platform?.idNote && (
            <Typography component="div" sx={{ typography: "s3", color: "text.subtitle", mt: 0.75 }}><NoteLine text={platform.idNote} /></Typography>
          )}
        </Box>
        {channels.length > 0 && (
          <Box>
            <Label>Chat channel</Label>
            <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.75, mt: 0.75 }}>
              {channels.map((c) => (
                <ChipCard key={c.id} icon={c.icon || "solar:chat-round-linear"} label={c.label} on={channel?.id === c.id} onClick={() => setChatChannel(c.id)} />
              ))}
            </Box>
            <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.75 }}>
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
            value={keyRef} onChange={(v) => { setKeyRef(v); setScopeChoice(null); }}
            helper={initial?.keyFrom && keyRef === initial.apiKeyRef
              ? `The key your ${initial.keyFrom} already uses. ${PLATFORM_KEY_HELPER}`
              : PLATFORM_KEY_HELPER}
          />
        )}

        {platform && refOk && (listing ? (
          <Stack direction="row" alignItems="center" spacing={1} sx={{ px: 1.5, py: 1.25, borderRadius: 1, bgcolor: "background.neutral" }}>
            <CircularProgress size={13} thickness={5} sx={{ color: "text.subtitle" }} />
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>{`Listing assistants on ${platform.name}…`}</Typography>
          </Stack>
        ) : (
          <>
            {lacksScope && (
              <CloneScopeChoice
                providerName={platform?.name}
                choice={scopeChoice}
                onReissue={() => { setKeyRef(""); setScopeChoice(null); }}
                onMocked={() => setScopeChoice("mocked")}
              />
            )}
            {typing ? (
              <Box>
                <Field
                  label={platform?.idLabel || "Agent ID"}
                  required mono
                  placeholder={platform?.idPlaceholder}
                  value={typedId} onChange={setTypedId}
                  helper={`The ID from the ${platform?.name} dashboard.`}
                />
                <TextLink onClick={() => setTyping(false)} sx={{ mt: 0.75 }}>Pick from the list instead</TextLink>
              </Box>
            ) : (
              <Box>
                {/* The same list as a repository's agents: search, radio rows,
                    ones already in Agents marked and opened instead. */}
                <FoundAgentsList
                  agents={assistants}
                  agentId={picked?.id || null}
                  onPick={pickAssistant}
                  heading={assistants.length === 1 ? `1 agent on this ${platform?.name} account` : `${assistants.length} agents on this ${platform?.name} account — which one do you mean?`}
                  searchHint="name, ID or number"
                  searchOf={(a) => `${a.name} ${a.id} ${a.phone || ""}`}
                  describe={(a, connected) => (
                    <>
                      <Box component="span" sx={{ fontFamily: MONO }}>{a.id}</Box>
                      {voice ? ` · ${a.phone || "Web only"}` : ""}
                      {a.updated ? ` · updated ${a.updated}` : ""}
                      {connected ? " · already in Agents" : ""}
                    </>
                  )}
                  connectedOf={(a) => connectedAgents.find((x) => x.values?.provider === platform?.id && x.values?.agentId === a.id) || null}
                  onOpenConnected={(rec) => navigate(`${paths.dashboard.agents}/connected/${agentIdOf(rec.key)}`)}
                />
                <TextLink onClick={() => setTyping(true)} sx={{ mt: 0.75 }}>Not listed? Enter an ID instead</TextLink>
              </Box>
            )}
            {agentId && imported.structure && (
              <Typography component="div" sx={{ typography: "s3", color: "text.secondary" }}>
                <HintLine
                  hint={platform?.id === "retell" ? (imported.retellKind === "conversation_flow"
                    ? "Each node's instructions come in as a member, and improvements can propose changes node by node."
                    : "One prompt for the whole call.") : null}
                >
                  {`Read from ${platform?.name}: ${imported.structure}.`}
                </HintLine>
              </Typography>
            )}
            {/* Optional (Vapi, Retell): a sealed copy instead of the live assistant. */}
            {agentId && canImport && (sealedOpen ? (
              <SandboxImport
                providerName={platform?.name}
                on={importCopy}
                onToggle={(on) => { setImportCopy(on); if (!on) { setToolFiles([]); setSealedOpen(false); } }}
                files={toolFiles}
                onFiles={setToolFiles}
              />
            ) : (
              <TextLink onClick={() => { setSealedOpen(true); setImportCopy(true); }}>Test a sealed copy instead</TextLink>
            ))}
            {agentId && (
              <Typography component="div" sx={{ typography: "s3", color: "text.subtitle" }}>
                {watch || prefill?.project ? (
                  <HintLine hint={watch ? `We pull its real traffic from ${platform?.name} with the same key, so failures show up in the Error Feed.` : "The project this issue came from — Fix finds this agent through it."}>
                    {`Its production ${voice ? "calls" : "conversations"} land in Observe project ${observeProject} — change it, and how simulations reach it, on the agent's page.`}
                  </HintLine>
                ) : (
                  <HintLine hint="Its Observe project is what lets Fix find this agent behind an Error Feed issue.">
                    {`Choose the Observe project its production ${voice ? "calls" : "conversations"} land in from Finish setting up, on the agent's page.`}
                  </HintLine>
                )}
              </Typography>
            )}
            {testing?.needsVersioning && agentId && (
              <Typography component="div" sx={{ typography: "s3", color: "text.subtitle" }}>
                <HintLine hint={testing.note}>
                  Changes ship as a branch once versioning is on for the agent — switch it on from Finish setting up.
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

/*
  The optional sealed path for Vapi and Retell: import a copy of the
  assistant into our sandbox and upload the code behind its tools, so the
  tools answer from the seeded world instead of your real systems. Built-in
  platform actions aren't tool code, so they still run for real.
*/
function SandboxImport({ providerName, on, onToggle, files, onFiles }) {
  const add = (list) => {
    const added = Array.from(list || []).map((f) => ({ name: f.name, size: f.size }));
    onFiles([...files, ...added.filter((a) => !files.some((f) => f.name === a.name))]);
  };
  return (
    <Box sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1, overflow: "hidden" }}>
      <Stack direction="row" alignItems="center" spacing={1.5} sx={{ px: 1.75, py: 1.25 }}>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography sx={{ typography: "s2", fontWeight: 700 }}>Test a sealed copy instead</Typography>
          <Typography component="div" sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}>
            <HintLine hint={`We import a copy of the ${providerName || "platform"} assistant into our sandbox and run your tool code against the seeded world. Results carry the stronger Sealed proof.`}>
              Optional · import a copy and upload the code behind its tools — they answer from the seeded world
            </HintLine>
          </Typography>
        </Box>
        <Button
          size="small" variant={on ? "outlined" : "text"}
          onClick={() => onToggle(!on)}
          sx={{ typography: "s2", fontWeight: 700, flexShrink: 0, color: on ? "text.primary" : "text.secondary" }}
        >
          {on ? "Don't import" : "Import a copy"}
        </Button>
      </Stack>
      {on && (
        <Stack spacing={1} sx={{ px: 1.75, py: 1.25, borderTop: "1px solid", borderColor: "divider" }}>
          <Stack direction="row" alignItems="center" spacing={1}>
            <Button component="label" size="small" variant="outlined" startIcon={<Iconify icon="solar:cloud-upload-linear" width={14} />} sx={{ typography: "s2", fontWeight: 700 }}>
              Choose tool code
              <input hidden multiple type="file" onChange={(e) => add(e.target.files)} />
            </Button>
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>The handlers your tools&apos; server URLs call · .py .ts .js .zip</Typography>
          </Stack>
          {files.map((f) => (
            <Stack key={f.name} direction="row" alignItems="center" spacing={1} sx={{ px: 1, py: 0.5, borderRadius: 1, bgcolor: (t) => alpha(t.palette.text.primary, 0.04) }}>
              <Iconify icon="solar:document-linear" width={13} sx={{ color: "text.subtitle" }} />
              <Typography noWrap sx={{ typography: "s2", fontFamily: MONO, flex: 1, minWidth: 0 }}>{f.name}</Typography>
              <IconButton size="small" onClick={() => onFiles(files.filter((x) => x.name !== f.name))}>
                <Iconify icon="solar:trash-bin-minimalistic-linear" width={13} sx={{ color: "text.subtitle" }} />
              </IconButton>
            </Stack>
          ))}
          <Typography component="div" sx={{ typography: "s3", color: "text.secondary" }}>
            <HintLine hint="Transfers, end call and voicemail aren't your code, so they still run for real in an imported copy. Scenarios that transfer place a real transfer.">
              Built-in platform actions still run for real.
            </HintLine>
          </Typography>
        </Stack>
      )}
    </Box>
  );
}
SandboxImport.propTypes = { providerName: PropTypes.string, on: PropTypes.bool, onToggle: PropTypes.func, files: PropTypes.array, onFiles: PropTypes.func };

