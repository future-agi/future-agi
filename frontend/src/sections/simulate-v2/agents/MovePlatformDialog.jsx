import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import { Box, Button, Dialog, Stack, Typography } from "@mui/material";
import {
  HOSTED_PLATFORMS_BY_TYPE,
  PLATFORM_KEY_HELPER,
  PLATFORM_LOGOS,
  isLivePlatform,
} from "../environments/connectOptions";
import {
  ChipCard,
  Field,
  Label,
  PlatformLogo,
} from "../environments/StartEnvironment";
import SecretRefField from "./connect/SecretRefField";
import { keyRefOf, preflightRecord } from "../_mock/secretRefs";
import { PROVIDER_LABELS } from "../_mock/agentRegistry";

/**
 * The same agent, reached somewhere new: moved to another platform (Vapi →
 * Retell), or rebuilt on the same one under a new assistant ID. It stays
 * this agent — same page, versions, runs and environments — and only how we
 * reach it changes, so no version is made here. The next Sync reads it from
 * where it is now; if its config changed, that's the next version, and every
 * environment testing it sees it like any other.
 *
 * Only platforms of its own type are offered: a voice agent stays a voice
 * agent. A different kind of agent is a different agent — it's connected on
 * its own and builds its own environment.
 */
export default function MovePlatformDialog({ open, onClose, agent, onSave }) {
  const channel =
    agent?.typeId === "voice_platform" || agent?.values?.contact
      ? "voice"
      : "chat";
  const platforms = (HOSTED_PLATFORMS_BY_TYPE[channel] || []).filter(
    (p) => !p.isOther && isLivePlatform(p),
  );
  const fromProvider = agent?.values?.provider || null;
  const fromId = agent?.values?.agentId || "";
  const [platformId, setPlatformId] = useState(fromProvider);
  const [agentId, setAgentId] = useState("");
  const [keyRef, setKeyRef] = useState("");
  useEffect(() => {
    if (!open) return;
    setPlatformId(
      platforms.some((p) => p.id === fromProvider)
        ? fromProvider
        : platforms[0]?.id || null,
    );
    setAgentId("");
    setKeyRef("");
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!agent) return null;
  const platform = platforms.find((p) => p.id === platformId) || null;
  const samePlatform = platformId === fromProvider;
  const fromLabel = PROVIDER_LABELS[fromProvider] || "its platform";
  /* A new platform needs its own key; the same one keeps the key it has unless replaced. */
  const savedRef = keyRefOf(
    keyRef,
    platform ? `vault://acme/sim/${platform.id}_api_key` : "",
  );
  const sameId =
    samePlatform &&
    agentId.trim().toLowerCase() === String(fromId).toLowerCase();
  const ready =
    !!platform && !!agentId.trim() && !sameId && (samePlatform || !!savedRef);

  const save = () => {
    const ref = savedRef || agent.values?.apiKeyRef || null;
    onSave(
      {
        provider: platform.id,
        agentId: agentId.trim(),
        ...(ref && {
          hasApiKey: true,
          apiKeyRef: ref,
          apiKeyMigrated: false,
          apiKeyCheck: preflightRecord(ref, platform.id),
        }),
        movedPlatform: {
          from: { provider: fromProvider, agentId: fromId || null },
          to: { provider: platform.id, agentId: agentId.trim() },
          at: new Date().toISOString(),
        },
        ...(agent.values?.disconnected && { disconnected: null }),
      },
      samePlatform
        ? `${agent.name} now points at its new ID — same agent and history. Sync to read it as the next version.`
        : `${agent.name} moved to ${platform.name} — same agent and history. Sync to read it as the next version.`,
    );
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      PaperProps={{
        sx: {
          borderRadius: 2,
          maxWidth: 560,
          width: "100%",
          bgcolor: "background.paper",
          backgroundImage: "none",
          border: "1px solid",
          borderColor: "divider",
        },
      }}
    >
      <Box sx={{ px: 3, pt: 2.5, pb: 2 }}>
        <Typography sx={{ typography: "m3", fontWeight: 600 }}>
          Move to another platform or ID
        </Typography>
        <Typography
          sx={{ typography: "s2", color: "text.secondary", mt: 0.75 }}
        >
          {`For when ${agent.name} moves off ${fromLabel}, or is rebuilt under a new ID. It stays this agent — its versions, runs and environments carry on. Nothing is versioned now; the next Sync reads it from where it is.`}
        </Typography>

        <Stack spacing={2} sx={{ mt: 2.25 }}>
          <Box>
            <Label>{`${channel === "voice" ? "Voice" : "Chat"} platform`}</Label>
            <Box
              sx={{ display: "flex", flexWrap: "wrap", gap: 0.75, mt: 0.75 }}
            >
              {platforms.map((p) => (
                <ChipCard
                  key={p.id}
                  logo={
                    <PlatformLogo id={p.id} name={p.name} brand={p.brand} />
                  }
                  label={
                    PLATFORM_LOGOS[p.id]?.type === "wordmark" ? null : p.name
                  }
                  on={platformId === p.id}
                  onClick={() => {
                    setPlatformId(p.id);
                    setKeyRef("");
                  }}
                  tag={p.id === fromProvider ? "now" : undefined}
                />
              ))}
            </Box>
          </Box>
          {platform && (
            <>
              <Field
                label={`New ${platform.idLabel || "agent ID"}`}
                required
                mono
                fullWidth
                placeholder={platform.idPlaceholder}
                value={agentId}
                onChange={setAgentId}
                helper={
                  sameId
                    ? "That's the ID it has now."
                    : samePlatform && fromId
                      ? `Now ${fromId}.`
                      : undefined
                }
              />
              <SecretRefField
                label={
                  samePlatform
                    ? `${platform.keyLabel || "API key"} (optional)`
                    : platform.keyLabel || "API key"
                }
                required={!samePlatform}
                placeholder={`vault://acme/sim/${platform.id}_api_key`}
                value={keyRef}
                onChange={setKeyRef}
                helper={
                  samePlatform
                    ? `Leave empty to keep the key it uses now. ${PLATFORM_KEY_HELPER}`
                    : `${platform.name} needs its own key. ${PLATFORM_KEY_HELPER}`
                }
                fullWidth
              />
            </>
          )}
        </Stack>
      </Box>
      <Stack
        direction="row"
        justifyContent="flex-end"
        spacing={1}
        sx={{ px: 3, py: 1.75, borderTop: "1px solid", borderColor: "divider" }}
      >
        <Button size="small" onClick={onClose} sx={{ color: "text.secondary" }}>
          Cancel
        </Button>
        <Button
          size="small"
          variant="contained"
          color="primary"
          disabled={!ready}
          onClick={save}
        >
          {samePlatform
            ? "Use the new ID"
            : platform
              ? `Move to ${platform.name}`
              : "Move"}
        </Button>
      </Stack>
    </Dialog>
  );
}

MovePlatformDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  agent: PropTypes.object,
  /* (valuesPatch, message) — the agent page's own save. */
  onSave: PropTypes.func.isRequired,
};
