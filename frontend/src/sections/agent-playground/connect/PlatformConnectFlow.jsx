import PropTypes from "prop-types";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useSnackbar } from "notistack";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Box, Button, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { useAuthContext } from "src/auth/hooks";
import axios, { endpoints } from "src/utils/axios";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { AGENT_TYPES } from "src/sections/agents/constants";
import Field from "src/sections/simulate/environments/components/Field";
import { COUNTRY_BY_ISO } from "src/sections/simulate/environments/components/countryCodes";
import {
  isValidPhoneNumber,
  nationalDigits,
} from "src/sections/simulate/environments/components/phoneNumber";
import { HOSTED_PLATFORMS_BY_TYPE } from "src/sections/simulate/environments/hostedPlatforms";
import ContactInformation from "src/sections/simulate/environments/panels/ContactInformation";
import { CONNECTED_AGENTS_PATH } from "../agentsTabs";
import { ACTION_SX } from "./connectStyles";

// How each way saves its agent. Vapi, Retell and LiveKit take the labels the
// build drawer uses for them; a phone number is the "others" provider.
const rosterEntry = (id) =>
  (HOSTED_PLATFORMS_BY_TYPE[AGENT_TYPES.VOICE] || []).find(
    (p) => p.id === id,
  ) || {};
const PLATFORMS = {
  vapi: { provider: "vapi", ...rosterEntry("vapi") },
  retell: { provider: "retell", ...rosterEntry("retell") },
  livekit: {
    provider: "livekit_bridge",
    livekit: true,
    ...rosterEntry("livekit"),
    idLabel: "LiveKit agent name",
  },
  phone: { provider: "others", phoneOnly: true },
};

const DEFAULT_CONCURRENCY = 5;
/* Digits the agent API saves for a contact number, country code included
   (AgentDefinitionCreateRequestSerializer.validate). */
const SAVED_DIGITS = { min: 10, max: 12 };
const COMMIT_MESSAGE = "Connected from Agents";

// "+14155551234" from the country picker and the typed number.
const fullNumber = (dial, number) => {
  const raw = String(number || "").trim();
  if (!raw) return "";
  return raw.startsWith("+")
    ? `+${nationalDigits(dial, raw)}`
    : `${dial}${nationalDigits(dial, raw)}`;
};

// The create API's refusal, as one line: a field's error, or its own message.
const refusalText = (error) => {
  const body = error?.response?.data || error;
  if (body?.details?.non_field_errors?.length)
    return body.details.non_field_errors.join(" ");
  if (body?.details) {
    const fields = Object.values(body.details).flat().join(" ");
    if (fields) return fields;
  }
  return (
    body?.error || body?.message || "We couldn't save this agent. Try again."
  );
};

/**
 * Connect a voice agent that runs on Vapi, Retell AI, a LiveKit project or a
 * phone number. It is saved to Agents and opens there; no environment is
 * built from here.
 */
export default function PlatformConnectFlow({ platform }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { enqueueSnackbar } = useSnackbar();
  const { role } = useAuthContext();
  const canConnect =
    !!RolePermission.SIMULATION_AGENT[PERMISSIONS.CREATE][role];
  const spec = PLATFORMS[platform];
  const [form, setForm] = useState({
    name: "",
    agentId: "",
    apiKey: "",
    livekitUrl: "",
    apiSecret: "",
    simMode: spec.phoneOnly ? "phone" : "web",
    countryIso: "US",
    contactNumber: "",
    inboundCalls: true,
    agentSpeaksFirst: false,
  });
  const set = (field) => (value) =>
    setForm((prev) => ({ ...prev, [field]: value }));

  const dial = COUNTRY_BY_ISO[form.countryIso]?.dial || "";
  const byPhone = spec.phoneOnly || form.simMode === "phone";
  /* The agent API takes 10 to 12 digits with the country code, a narrower
     range than the phone field allows, so a number it would refuse is
     caught here, before Connect agent. */
  const digits = fullNumber(dial, form.contactNumber).replace(/\D/g, "");
  const savable =
    digits.length >= SAVED_DIGITS.min && digits.length <= SAVED_DIGITS.max;
  const phoneOk =
    !byPhone || (isValidPhoneNumber(dial, form.contactNumber) && savable);
  const missing = [
    !form.name.trim() && "Name the agent",
    !spec.phoneOnly &&
      !form.agentId.trim() &&
      `Add the ${(spec.idLabel || "agent ID").toLowerCase()}`,
    !spec.phoneOnly &&
      !form.apiKey.trim() &&
      `Add the ${spec.keyLabel || "API key"}`,
    spec.livekit && !form.livekitUrl.trim() && "Add the LiveKit URL",
    spec.livekit && !form.apiSecret.trim() && "Add the LiveKit API secret",
    !phoneOk &&
      (form.contactNumber.trim() && !savable
        ? `Use ${SAVED_DIGITS.min}–${SAVED_DIGITS.max} digits with the country code`
        : "Add a valid contact number"),
  ].filter(Boolean);

  const inbound = spec.phoneOnly || spec.livekit ? true : form.inboundCalls;
  const payload = () =>
    Object.fromEntries(
      Object.entries({
        agent_type: AGENT_TYPES.VOICE,
        agent_name: form.name.trim(),
        provider: spec.provider,
        languages: ["en"],
        commit_message: COMMIT_MESSAGE,
        inbound,
        target_speaks_first: form.agentSpeaksFirst,
        contact_number: byPhone ? fullNumber(dial, form.contactNumber) : "",
        ...(spec.livekit
          ? {
              livekit_agent_name: form.agentId.trim(),
              livekit_url: form.livekitUrl.trim(),
              livekit_api_key: form.apiKey.trim(),
              livekit_api_secret: form.apiSecret.trim(),
              livekit_config_json: {},
              livekit_max_concurrency: DEFAULT_CONCURRENCY,
            }
          : {}),
        ...(!spec.livekit && !spec.phoneOnly
          ? {
              authentication_method: "api_key",
              api_key: form.apiKey.trim(),
              assistant_id: form.agentId.trim(),
              livekit_max_concurrency: DEFAULT_CONCURRENCY,
            }
          : {}),
      }).filter(([, value]) => value !== "" && value != null),
    );

  const save = useMutation({
    mutationFn: async () =>
      (await axios.post(endpoints.agentDefinitions.create, payload())).data,
    onSuccess: (data) => {
      const agentId = data?.agent?.id;
      queryClient.invalidateQueries({ queryKey: ["agents", "connected"] });
      enqueueSnackbar(`${form.name.trim()} connected`, { variant: "success" });
      if (agentId) navigate(`${CONNECTED_AGENTS_PATH}/${agentId}/overview`);
      else navigate(CONNECTED_AGENTS_PATH);
    },
    onError: (error) =>
      enqueueSnackbar(refusalText(error), { variant: "error" }),
  });

  const hint = !canConnect
    ? "You don't have permission to connect agents"
    : missing[0] || "It's saved to Agents";

  return (
    <Box sx={{ display: "flex", flexDirection: "column", minHeight: "100%" }}>
      <Stack spacing={1.75} sx={{ p: 2.5, flex: 1 }}>
        <Field
          label="Agent name"
          required
          placeholder="Returns line"
          value={form.name}
          onChange={set("name")}
          inputProps={{ "aria-label": "Agent name" }}
        />
        {!spec.phoneOnly && (
          <Field
            label={spec.idLabel || "Agent ID"}
            required
            placeholder={spec.idPlaceholder}
            value={form.agentId}
            onChange={set("agentId")}
            autoComplete="new-password"
            mono
            inputProps={{ "aria-label": spec.idLabel || "Agent ID" }}
          />
        )}
        {spec.livekit && (
          <Field
            label="LiveKit URL"
            required
            placeholder="wss://your-project.livekit.cloud"
            value={form.livekitUrl}
            onChange={set("livekitUrl")}
            mono
            inputProps={{ "aria-label": "LiveKit URL" }}
          />
        )}
        {!spec.phoneOnly && (
          <Field
            label={spec.keyLabel || "API key"}
            required
            placeholder="sk-…"
            value={form.apiKey}
            onChange={set("apiKey")}
            type="password"
            autoComplete="new-password"
            mono
            helper="Used only to call the agent on your behalf."
            inputProps={{ "aria-label": spec.keyLabel || "API key" }}
          />
        )}
        {spec.livekit && (
          <Field
            label="LiveKit API secret"
            required
            placeholder="secret…"
            value={form.apiSecret}
            onChange={set("apiSecret")}
            type="password"
            autoComplete="new-password"
            mono
            inputProps={{ "aria-label": "LiveKit API secret" }}
          />
        )}
        <ContactInformation
          mode={form.simMode}
          onMode={set("simMode")}
          countryIso={form.countryIso}
          onCountryIso={set("countryIso")}
          contactNumber={form.contactNumber}
          onContactNumber={set("contactNumber")}
          inboundCalls={inbound}
          onInboundCalls={set("inboundCalls")}
          agentSpeaksFirst={form.agentSpeaksFirst}
          onAgentSpeaksFirst={set("agentSpeaksFirst")}
          phoneOnly={!!spec.phoneOnly}
          inboundOnly={!!spec.livekit}
        />
      </Stack>
      <Stack
        direction="row"
        alignItems="center"
        spacing={2}
        sx={{
          px: 2.5,
          py: 1.5,
          borderTop: "1px solid",
          borderColor: "divider",
          position: "sticky",
          bottom: 0,
          bgcolor: "background.paper",
        }}
      >
        <Box flex={1} />
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {hint}
        </Typography>
        <Button
          variant="contained"
          color="primary"
          disabled={!canConnect || missing.length > 0 || save.isPending}
          onClick={() => save.mutate()}
          endIcon={<Iconify icon="solar:alt-arrow-right-linear" width={14} />}
          sx={ACTION_SX}
        >
          Connect agent
        </Button>
      </Stack>
    </Box>
  );
}

PlatformConnectFlow.propTypes = {
  platform: PropTypes.oneOf(["vapi", "retell", "livekit", "phone"]).isRequired,
};
