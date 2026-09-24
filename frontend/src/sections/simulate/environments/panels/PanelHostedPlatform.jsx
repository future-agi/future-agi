import PropTypes from "prop-types";
import { useReducer } from "react";
import { Box, Stack, Typography } from "@mui/material";
import { AGENT_TYPES } from "src/sections/agents/constants";
import Field from "../components/Field";
import Label from "../components/Label";
import ChipCard from "../components/ChipCard";
import ContinueRow from "../components/ContinueRow";
import PlatformLogo from "../components/PlatformLogo";
import { PLATFORM_LOGOS } from "../components/platformLogos";
import { ENTRY_AGENT_TYPES, CALL_DIRECTION, CALL_DIRECTION_LABEL } from "../agentTypes";
import { HOSTED_PLATFORMS_BY_TYPE, HOSTED_EMPTY_ROSTER_COPY } from "../hostedPlatforms";

/* Type gate first — the platform roster only makes sense once we know what
   kind of agent is being connected. Default to voice because that's what the
   codebase actually integrates with today, and seed the platform to that
   type's first entry so the picker opens with a selection. */
const initial = {
  agentType: AGENT_TYPES.VOICE,
  platform: (HOSTED_PLATFORMS_BY_TYPE[AGENT_TYPES.VOICE] || [])[0]?.id || "",
  id: "",
  key: "",
  repoUrl: "",
  callDirection: CALL_DIRECTION.INBOUND,
};

function reducer(s, a) {
  if (a.type === "reset") return initial;
  const value = typeof a.value === "function" ? a.value(s[a.field]) : a.value;
  return { ...s, [a.field]: value };
}

export default function PanelHostedPlatform({ onBuild }) {
  const [form, dispatch] = useReducer(reducer, initial);
  const set = (field) => (value) => dispatch({ field, value });
  const { agentType, platform, id, key, repoUrl, callDirection } = form;
  const platforms = HOSTED_PLATFORMS_BY_TYPE[agentType] || [];

  /* When agent type flips, the current platform selection is likely stale.
     Snap to the first platform of the new type and clear the credentials so
     we do not carry an OpenAI key into a Retell form. */
  const pickAgentType = (nextType) => {
    const first = (HOSTED_PLATFORMS_BY_TYPE[nextType] || [])[0];
    set("agentType")(nextType);
    set("platform")(first?.id || "");
    set("id")("");
    set("key")("");
    set("repoUrl")("");
  };

  const chosen = platforms.find((p) => p.id === platform) || platforms[0];
  const canGo = !!chosen && !!id.trim() && !!key.trim();

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
            {platforms.map((p) => (
              <ChipCard
                key={p.id}
                logo={<PlatformLogo id={p.id} name={p.name} brand={p.brand} />}
                /* Wordmark logos already spell the name — don't repeat it. */
                label={PLATFORM_LOGOS[p.id]?.type === "wordmark" ? null : p.name}
                on={platform === p.id}
                comingSoon={p.comingSoon}
                onClick={() => set("platform")(p.id)}
              />
            ))}
          </Box>
        )}
      </Box>
      {chosen && (
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
          <Field
            label="GitHub repo"
            placeholder="https://github.com/your-org/your-agent"
            value={repoUrl} onChange={set("repoUrl")}
            mono
            helper="Optional. Lets us read the agent's tools + prompts to seed matching scenarios."
          />
          {agentType === AGENT_TYPES.VOICE && (
            <Box>
              <Label>Call direction</Label>
              <Box sx={{ display: "grid", gap: 0.75, gridTemplateColumns: "1fr 1fr", mt: 0.75 }}>
                <ChipCard label={CALL_DIRECTION_LABEL[CALL_DIRECTION.INBOUND]} on={callDirection === CALL_DIRECTION.INBOUND} onClick={() => set("callDirection")(CALL_DIRECTION.INBOUND)} />
                <ChipCard label={CALL_DIRECTION_LABEL[CALL_DIRECTION.OUTBOUND]} on={callDirection === CALL_DIRECTION.OUTBOUND} onClick={() => set("callDirection")(CALL_DIRECTION.OUTBOUND)} />
              </Box>
            </Box>
          )}
        </>
      )}
      <ContinueRow
        disabled={!canGo}
        hint={!chosen ? "Pick a supported path above" : "Fill both fields"}
        onClick={() => onBuild?.({
          kind: "platform",
          agentType,
          provider: chosen?.id,
          agentId: id.trim(),
          apiKey: key.trim(),
          ...(repoUrl.trim() ? { repoUrl: repoUrl.trim() } : {}),
          ...(agentType === AGENT_TYPES.VOICE ? { callDirection } : {}),
        })}
      />
    </Stack>
  );
}
PanelHostedPlatform.propTypes = { onBuild: PropTypes.func };
