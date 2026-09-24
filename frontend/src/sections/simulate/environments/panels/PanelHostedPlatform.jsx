import { useReducer } from "react";
import { Box, Stack, Typography } from "@mui/material";
import { AGENT_TYPES } from "src/sections/agents/constants";
import Field from "../components/Field";
import Label from "../components/Label";
import ChipCard from "../components/ChipCard";
import ContinueRow from "../components/ContinueRow";
import PlatformLogo from "../components/PlatformLogo";
import { PLATFORM_LOGOS } from "../components/platformLogos";
import { COUNTRY_BY_ISO } from "../components/countryCodes";
import ContactInformation from "./ContactInformation";
import ScenarioCount from "./ScenarioCount";
import { DEFAULT_SCENARIOS, isValidScenarioCount } from "./scenarioCountRules";
import RuntimePreflight from "./RuntimePreflight";
import ParallelismField from "./ParallelismField";
import usePanelBuild from "../hooks/usePanelBuild";
import { ENTRY_AGENT_TYPES } from "../agentTypes";
import { HOSTED_PLATFORMS_BY_TYPE, HOSTED_EMPTY_ROSTER_COPY } from "../hostedPlatforms";

/* Type gate first — the platform roster only makes sense once we know what kind
   of agent is being connected. Default to voice (what we integrate with today)
   and seed the platform to that type's first entry. `simMode` is web (WebRTC) or
   phone (PSTN); `inboundCalls` is the old call-direction binary (on = inbound);
   `agentSpeaksFirst` waits for the agent's greeting. `otherPrompt` is the
   Others-only system prompt. */
const initial = {
  agentType: AGENT_TYPES.VOICE,
  platform: (HOSTED_PLATFORMS_BY_TYPE[AGENT_TYPES.VOICE] || [])[0]?.id || "",
  id: "",
  key: "",
  repoUrl: "",
  simMode: "web",
  countryIso: "US",
  contactNumber: "",
  inboundCalls: true,
  agentSpeaksFirst: false,
  otherPrompt: "",
  scenarioCount: DEFAULT_SCENARIOS,
};

function reducer(s, a) {
  if (a.type === "reset") return initial;
  const value = typeof a.value === "function" ? a.value(s[a.field]) : a.value;
  return { ...s, [a.field]: value };
}

export default function PanelHostedPlatform() {
  const [form, dispatch] = useReducer(reducer, initial);
  const build = usePanelBuild();
  // Any edit invalidates a prior preflight result, so re-disable Build.
  const set = (field) => (value) => {
    dispatch({ field, value });
    build.resetPreflight();
  };
  const {
    agentType, platform, id, key, repoUrl,
    simMode, countryIso, contactNumber, inboundCalls, agentSpeaksFirst, otherPrompt,
    scenarioCount,
  } = form;
  const platforms = HOSTED_PLATFORMS_BY_TYPE[agentType] || [];
  // Coming-soon (not-yet-a-connector) platforms sort to the end, so the
  // selectable ones lead the row. Stable, so each group keeps its roster order.
  const orderedPlatforms = [...platforms].sort(
    (a, b) => Number(!!a.comingSoon) - Number(!!b.comingSoon),
  );

  /* When agent type flips, snap to the first platform of the new type and clear
     the credentials so we do not carry a key into another provider's form. */
  const pickAgentType = (nextType) => {
    const first = (HOSTED_PLATFORMS_BY_TYPE[nextType] || [])[0];
    set("agentType")(nextType);
    set("platform")(first?.id || "");
    set("id")("");
    set("key")("");
    set("repoUrl")("");
    set("otherPrompt")("");
  };

  const chosen = platforms.find((p) => p.id === platform) || platforms[0];
  const isOther = !!chosen?.isOther;
  // Others has no WebRTC path, so it requires a number regardless of simMode;
  // other voice envs only require it in Phone mode.
  const phoneRequired = agentType === AGENT_TYPES.VOICE && (isOther || simMode === "phone");
  const phoneOk = !phoneRequired || !!contactNumber.trim();
  const credsOk = isOther ? !!otherPrompt.trim() : (!!id.trim() && !!key.trim());
  const canGo = !!chosen && credsOk && phoneOk;

  const buildSource = () => ({
    kind: "platform",
    agentType,
    provider: chosen?.id,
    scenarioCount: Number(scenarioCount) || undefined,
    ...(isOther
      ? { agentMode: "prompt", prompt: otherPrompt.trim() }
      : { agentId: id.trim(), apiKey: key.trim() }),
    ...(repoUrl.trim() ? { repoUrl: repoUrl.trim() } : {}),
    ...(agentType === AGENT_TYPES.VOICE
      ? (() => {
          const contactMode = isOther ? "phone" : simMode;
          return {
            callDirection: inboundCalls ? "inbound" : "outbound",
            contact: {
              mode: contactMode,
              inboundCalls,
              agentSpeaksFirst,
              ...(contactMode === "phone"
                ? {
                    countryIso,
                    countryCode: COUNTRY_BY_ISO[countryIso]?.dial || "",
                    number: contactNumber.trim(),
                  }
                : {}),
            },
          };
        })()
      : {}),
  });

  return (
    <Stack spacing={1.75} sx={{ p: 2.5 }}>
      <Box>
        <Label>Agent type</Label>
        <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.75, mt: 0.75 }}>
          {ENTRY_AGENT_TYPES.map((t) => (
            <ChipCard
              key={t.id}
              icon={t.icon}
              label={t.label}
              comingSoon={t.comingSoon}
              on={agentType === t.id}
              onClick={() => pickAgentType(t.id)}
            />
          ))}
        </Box>
      </Box>
      <Box>
        <Label>Platform</Label>
        {platforms.length === 0 ? (
          <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 1 }}>
            {HOSTED_EMPTY_ROSTER_COPY}
          </Typography>
        ) : (
          <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.75, mt: 0.75 }}>
            {orderedPlatforms.map((p) => (
              <ChipCard
                key={p.id}
                /* "Others" has no brand logo — render its Solar icon instead. */
                icon={p.isOther ? p.icon : undefined}
                logo={p.isOther ? undefined : <PlatformLogo id={p.id} name={p.name} brand={p.brand} />}
                /* Wordmark logos already spell the name — don't repeat it. */
                label={PLATFORM_LOGOS[p.id]?.type === "wordmark" ? null : p.name}
                comingSoon={p.comingSoon}
                on={platform === p.id}
                onClick={() => set("platform")(p.id)}
              />
            ))}
          </Box>
        )}
      </Box>
      {chosen && (
        <>
          {isOther ? (
            <Field
              label="System prompt"
              required
              placeholder="You are a friendly returns agent for Acme…"
              value={otherPrompt} onChange={set("otherPrompt")}
              multiline
              helper="The platform dials the number below with its own telephony. The prompt only seeds scenarios; it never changes the live agent."
            />
          ) : (
            <>
              <Field
                label={chosen.idLabel || "Agent ID"}
                required
                placeholder={chosen.idPlaceholder}
                value={id} onChange={set("id")}
                mono
              />
              <Field
                label={chosen.keyLabel || "API key"}
                required
                placeholder="sk-…"
                value={key} onChange={set("key")}
                type="password"
                autoComplete="off"
                mono
                helper="Stored encrypted; used only to invoke the agent on your behalf."
              />
            </>
          )}
          {agentType === AGENT_TYPES.VOICE && (
            <ContactInformation
              mode={simMode} onMode={set("simMode")}
              countryIso={countryIso} onCountryIso={set("countryIso")}
              contactNumber={contactNumber} onContactNumber={set("contactNumber")}
              inboundCalls={inboundCalls} onInboundCalls={set("inboundCalls")}
              agentSpeaksFirst={agentSpeaksFirst} onAgentSpeaksFirst={set("agentSpeaksFirst")}
              phoneOnly={isOther}
            />
          )}
          <Field
            label="GitHub repo"
            placeholder="https://github.com/your-org/your-agent"
            value={repoUrl} onChange={set("repoUrl")}
            mono
            helper="Optional. Lets us read the agent's tools + prompts to seed matching scenarios."
          />
        </>
      )}
      <ScenarioCount value={scenarioCount} onChange={set("scenarioCount")} />
      <ParallelismField
        value={build.parallelism}
        input={build.parallelismInput}
        onChange={build.setParallelism}
        enabled={build.parallelismEnabled}
        admitted={build.admittedParallelism}
      />
      <RuntimePreflight
        status={build.status}
        canRun={canGo}
        onRun={() => build.runPreflight(buildSource())}
        checks={build.checks}
        state={build.state}
        error={build.error}
      />
      <ContinueRow
        disabled={!build.readyToSubmit || !isValidScenarioCount(scenarioCount)}
        busy={build.committing}
        hint={build.status === "done" ? "Resolve the checks above" : "Run preflight to continue"}
        onClick={build.commitBuild}
      />
    </Stack>
  );
}
