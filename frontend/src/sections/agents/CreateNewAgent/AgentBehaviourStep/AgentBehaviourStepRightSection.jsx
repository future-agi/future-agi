import { Box, Divider, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React, { useMemo } from "react";
import { useWatch } from "react-hook-form";
import { languageOptions } from "src/components/agent-definitions/helper";
import { AGENT_TYPES } from "../../constants";

const fields = [
  "agentType",
  "agentName",
  "languages",
  "observabilityEnabled",
  "provider",
  "assistantId",
  // "apiEndpoint",
  "authenticationMethod",
  "apiKey",
  "username",
  "password",
  "token",
  "headers",
  "model",
  // LiveKit fields — only surfaced when provider is livekit/livekit_bridge
  "livekitUrl",
  "livekitApiKey",
  "livekitAgentName",
  "livekitMaxConcurrency",
  "livekitConfigJson",
];

// Map field names to human-readable labels
const labels = {
  agentType: "Agent type",
  agentName: "Agent name",
  languages: "Languages",
  provider: "Voice/Chat Provider",
  // apiEndpoint: "API Endpoint",
  authenticationMethod: "Authentication Method",
  apiKey: "Provider API Key",
  observabilityEnabled: "Observability",
  assistantId: "Assistant ID",
  username: "Username",
  password: "Password",
  token: "Token",
  headers: "Headers",
  model: "Model Used",
  livekitUrl: "LiveKit Server URL",
  livekitApiKey: "LiveKit API Key",
  livekitAgentName: "LiveKit Agent Name",
  livekitMaxConcurrency: "Max Concurrent Sessions",
  livekitConfigJson: "Room Config JSON",
};

// Fields specific to LiveKit-backed voice agents (LiveKit server + agent config).
const LIVEKIT_FIELDS = [
  "livekitUrl",
  "livekitApiKey",
  "livekitAgentName",
  "livekitMaxConcurrency",
  "livekitConfigJson",
];

// Generic provider/auth fields that LiveKit-routed providers bypass entirely.
const LIVEKIT_BYPASS_FIELDS = [
  "apiKey",
  "assistantId",
  "authenticationMethod",
  "observabilityEnabled",
];

// Fields tied to the api_key auth flow — hidden when auth isn't api_key.
const API_KEY_AUTH_FIELDS = ["apiKey", "assistantId", "observabilityEnabled"];

// Fields irrelevant to chat agents (which don't expose provider/auth/LiveKit config).
const CHAT_AGENT_EXCLUDED_FIELDS = [
  "provider",
  "authenticationMethod",
  ...LIVEKIT_FIELDS,
];

// Credentials cleared when auth method is "noAuth".
const NO_AUTH_CLEARED_FIELDS = [
  "apiKey",
  "assistantId",
  "username",
  "password",
  "token",
];

const BASIC_AUTH_FIELDS = ["username", "password"];

const AgentBehaviourStepRightSection = ({ control }) => {
  // Get values for all fields
  const watchedValues = useWatch({ control, name: fields });
  const { filteredFields, filteredValues } = useMemo(() => {
    // Create an object from watched values
    const valuesObj = fields.reduce((acc, field, index) => {
      acc[field] = watchedValues[index];
      return acc;
    }, {});

    // Required fields by agent definition:
    //   - CHAT agents:   agentType, name, description, model, systemPrompt,
    //                    (+ apiKey/assistantId/observabilityEnabled/headers when auth is api_key)
    //   - VOICE agents:  agentType, name, description, provider, authenticationMethod,
    //                    voice/language settings, and provider-specific auth fields
    //   - LiveKit VOICE: agentType, name, description, provider, plus LIVEKIT_FIELDS
    //                    (bypasses apiKey / assistantId / authenticationMethod / observabilityEnabled)
    const excludeFields = new Set();

    const isLiveKit =
      valuesObj?.provider === "livekit" ||
      valuesObj?.provider === "livekit_bridge";

    // LiveKit fields are only relevant when the provider is LiveKit.
    if (!isLiveKit) {
      LIVEKIT_FIELDS.forEach((f) => excludeFields.add(f));
    } else {
      // Providers that route through LiveKit don't use the generic
      // api_key / assistant_id / auth method / observability path.
      LIVEKIT_BYPASS_FIELDS.forEach((f) => excludeFields.add(f));
    }

    // API-key-only fields drop out for non-api_key auth flows.
    if (valuesObj?.authenticationMethod !== "api_key") {
      API_KEY_AUTH_FIELDS.forEach((f) => excludeFields.add(f));
    }

    // Voice agents configure voices/providers, not a chat model.
    if (valuesObj?.agentType === AGENT_TYPES.VOICE) {
      excludeFields.add("model");
    }

    // Chat agents don't expose provider/auth/LiveKit config.
    if (valuesObj?.agentType === AGENT_TYPES.CHAT) {
      CHAT_AGENT_EXCLUDED_FIELDS.forEach((f) => excludeFields.add(f));
    }

    // Custom headers are only meaningful for chat agents using api_key auth.
    if (
      valuesObj?.agentType !== AGENT_TYPES.CHAT ||
      valuesObj?.authenticationMethod !== "api_key"
    ) {
      excludeFields.add("headers");
    }

    // Basic-auth credentials are only shown for basicAuth.
    if (valuesObj?.authenticationMethod !== "basicAuth") {
      BASIC_AUTH_FIELDS.forEach((f) => excludeFields.add(f));
    }

    // Bearer token is only shown for bearerToken auth.
    if (valuesObj?.authenticationMethod !== "bearerToken") {
      excludeFields.add("token");
    }

    // No-auth agents don't expose any credential fields.
    if (valuesObj?.authenticationMethod === "noAuth") {
      NO_AUTH_CLEARED_FIELDS.forEach((f) => excludeFields.add(f));
    }

    const newFields = fields.filter((field) => !excludeFields.has(field));
    const newValues = newFields.reduce((acc, field) => {
      acc[field] = valuesObj[field];
      return acc;
    }, {});

    return { filteredFields: newFields, filteredValues: newValues };
  }, [watchedValues]);

  const formatValue = (field, value) => {
    if (field === "observabilityEnabled") {
      return value ? "ON" : "OFF";
    }

    if (field === "languages") {
      if (!value || value.length === 0) return "-";
      const labels = value
        .map((code) => {
          const lang = languageOptions.find((opt) => opt.value === code);
          return lang ? lang.label : code;
        })
        .filter(Boolean);
      return labels.join(", ");
    }

    if (field === "headers") {
      if (!value || value.length === 0) return "-";
      const filtered = value.filter(
        (header) => header?.key?.trim() !== "" && header?.value?.trim() !== "",
      );
      if (filtered.length === 0) return "-";
      return filtered.map((h) => `${h.key}: ${h.value}`).join(", ");
    }

    if (field === "agentType") {
      if (value === AGENT_TYPES.CHAT) return "Chat";
      if (value === AGENT_TYPES.VOICE) return "Voice";
    }

    if (field === "livekitApiKey") {
      if (!value) return "-";
      // Show first/last two chars; the actual secret is never rendered.
      if (value.length <= 6) return "••••••";
      return `${value.slice(0, 2)}••••${value.slice(-2)}`;
    }

    if (field === "livekitConfigJson") {
      if (
        !value ||
        (typeof value === "object" && Object.keys(value).length === 0)
      ) {
        return "-";
      }
      try {
        return typeof value === "string" ? value : JSON.stringify(value);
      } catch {
        return "-";
      }
    }

    if (field === "livekitMaxConcurrency") {
      return value ? String(value) : "5";
    }

    return value || "-";
  };

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
      <Box>
        <Typography typography="m3" fontWeight="fontWeightMedium">
          Summary
        </Typography>
        <Typography typography="s1" color="text.secondary">
          Review your configuration before creating the agent
        </Typography>
      </Box>
      <Box
        bgcolor="background.paper"
        border="1px solid"
        borderColor="divider"
        borderRadius={1}
        sx={{
          display: "flex",
          flexDirection: "column",
          gap: 1,
          p: 2,
          maxHeight: "calc(100vh - 120px)",
          overflowY: "auto",
        }}
      >
        {filteredFields.map((field, index) => (
          <React.Fragment key={field}>
            <Box display="flex" flexDirection={"column"} gap={0} py={0.5}>
              <Typography typography="s1" fontWeight="fontWeightMedium">
                {labels[field] || field}
              </Typography>
              <Typography typography="s1" fontWeight="fontWeightRegular">
                {formatValue(field, filteredValues[field])}
              </Typography>
            </Box>
            {index < filteredFields.length - 1 && <Divider />}
          </React.Fragment>
        ))}
      </Box>
    </Box>
  );
};

AgentBehaviourStepRightSection.propTypes = {
  control: PropTypes.object,
};

export default AgentBehaviourStepRightSection;
