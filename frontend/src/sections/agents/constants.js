export const AGENT_TAB_IDS = {
  AGENT_CONFIGURATION: "AgentConfiguration",
  PERFORMANCE_ANALYTICS: "PerformanceAnalytics",
  CALL_LOGS: "CallLogs",
  WORKFLOW: "Workflow",
};

export const CALL_LOGS_TAB_IDS = {
  LIVE_CALL_LOGS: "live",
  TEST_CALL_LOGS: "test",
};
export const callLogsTabData = (agentType) => [
  {
    label: agentType === AGENT_TYPES.CHAT ? "Live Chat logs" : "Live call logs",
    value: "live",
    disabled: true,
  },
  {
    label: agentType === AGENT_TYPES.CHAT ? "Test Chat logs" : "Test call logs",
    value: "test",
    disabled: false,
  },
];
export const PROVIDER_CHOICES = {
  RETELL: "retell",
  VAPI: "vapi",
  LIVEKIT_BRIDGE: "livekit_bridge",
};

/** Returns true for any LiveKit-based provider. */
export const isLiveKitProvider = (provider) =>
  provider === "livekit_bridge" || provider === "livekit";

/**
 * Canonical copy for the Inbound/Outbound call toggle. Same semantic for
 * every provider — picking the right string based on provider left the
 * three forms drifting. All three forms (vapi, retell, livekit) read
 * from this single source of truth. [TH-4133]
 */
export const INBOUND_OUTBOUND_COPY = {
  inbound: {
    title: "Inbound Calls",
    description: "Allows the agent to take inbound calls",
    tooltip: "This agent will receive the call from simulated customers",
  },
  outbound: {
    title: "Outbound Calls",
    description: "This agent will call the simulated customers",
    tooltip: "This agent will call the simulated customers",
  },
};

export const callStatusCellStyle = {
  "in-progress": {
    sx: {
      color: "orange.700",
      backgroundColor: "orange.o10",
    },
    icon: "/assets/icons/navbar/ic_new_clock.svg",
  },
  completed: {
    sx: {
      color: "green.700",
      backgroundColor: "green.o10",
    },
    icon: "/assets/icons/agent/call_completed.svg",
  },
  failed: {
    sx: {
      color: "red.700",
      backgroundColor: "red.o10",
    },
    icon: "/assets/icons/agent/call_failed.svg",
  },
  dropped: {
    sx: {
      color: "blue.700",
      backgroundColor: "blue.o10",
    },
    icon: "/assets/icons/agent/call_dropped.svg",
  },
  "not-connected": {
    sx: {
      color: "red.700",
      backgroundColor: "red.o10",
    },
    icon: "/assets/icons/ic_failed.svg",
  },
};

export const AGENT_TYPES = {
  CHAT: "text",
  VOICE: "voice",
};

export const VOICE_CHAT_PROVIDERS = [
  { label: "Vapi", value: "vapi" },
  { label: "Retell", value: "retell" },
  // Hidden until LiveKit server stability is restored.
  // { label: "LiveKit", value: "livekit_bridge" },
  // { label: "ElevenLabs", value: "elevenlabs" },
  { label: "Others", value: "others" },
];

export const AUTH_METHODS = [{ label: "API Key", value: "api_key" }];

export const OTHER_AUTH_METHODS = [
  { label: "basic AUTH", value: "basicAuth" },
  { label: "BEARER TOKEN", value: "bearerToken" },
  {
    label: "NO AUTH",
    value: "noAuth",
  },
  {
    label: "OAUTH 2.0 (coming soon)",
    value: "oauth2_0",
    disabled: true,
  },
  {
    label: "DIGEST AUTH (coming soon)",
    value: "bigestAuth",
    disabled: true,
  },
];

export const AUTH_METHODS_BY_PROVIDER = {
  vapi: AUTH_METHODS,
  retell: AUTH_METHODS,
  elevenlabs: AUTH_METHODS,
  others: OTHER_AUTH_METHODS,
};

export const stepsInfo = [
  {
    title: "Select agent definition",
    description:
      "Start from scratch to create a clear, goal-oriented prompt tailored to your needs.",
    imageSrc: "/assets/agents/help/select-agent-def.svg",
  },
  {
    title: "Generate workflow and add personas",
    description:
      "Start with a ready-made prompt template. Select an option and tailor it to fit your specific needs.",
    imageSrc: "/assets/agents/help/gen-workkflow.svg",
  },
  {
    title: "Review scenarios for tests",
    description:
      "Refine what you have to make your output clearer, smarter, and more effective.",
    imageSrc: "/assets/agents/help/review-scenatios.svg",
  },
];

export const VAPI_STEPS = [
  {
    label: "1. Login to",
    linkText: "VAPI",
    link: "https://dashboard.vapi.ai",
  },
  {
    label: "2. Navigate to",
    linkText: "API Keys",
    link: "https://dashboard.vapi.ai/org/api-keys",
  },
  {
    label: "3. Copy the Private Key",
  },
  {
    label: "4. Navigate to",
    linkText: "Assistant",
    link: "https://dashboard.vapi.ai/assistants/",
  },
  {
    label: "5. Select the assistant you want to use",
  },
  {
    label: "6. Copy the Assistant ID written below the assistant name",
  },
];

// NOTE: AI-generated steps — might need verification
export const RETELL_STEPS = [
  {
    label: "1. Log into your Retell AI dashboard.",
  },
  {
    label: "2. Go to 'Settings' → 'API Keys'.",
  },
  {
    label:
      "3. Click 'Create API Key' and copy it — you'll use it for authorization.",
  },
  {
    label: "4. Navigate to the 'Agents' section to view or create an agent.",
  },
  {
    label:
      "5. Copy the 'agent_id' from the agent details or from the 'List Agents' API response.",
  },
];

export const ELEVENLABS_STEPS = [
  {
    label: "1. Log into your ElevenLabs account.",
  },
  {
    label: "2. Open your Profile and go to the 'API Keys' section.",
  },
  {
    label:
      "3. Click 'Create API Key', give it a name, and copy the key (you won’t see it again).",
  },
  {
    label: "4. Use this key in requests as 'xi-api-key: YOUR_API_KEY'.",
  },
  {
    label:
      "5. To get a specific Voice or Model ID, go to 'Voices' or 'Models' in the dashboard or use the respective API endpoints.",
  },
];

// LiveKit setup guidance shown in the right-side Resources panel when the
// LiveKit provider is selected. Covers all four LiveKit-specific fields:
// Server URL, API Key, API Secret, Agent Name. [TH-4132]
export const LIVEKIT_STEPS = [
  {
    label: "1. Log in to",
    linkText: "LiveKit Cloud",
    link: "https://cloud.livekit.io",
  },
  {
    label:
      "2. Open your project → Settings → Keys → 'Create new API key' or use the existing API key if there. Copy the API Key and API Secret and also the Livekit URL",
  },
  {
    label: "3. Copy your Livekit URL. Paste it into 'LiveKit Server URL'",
  },
  {
    label:
      "4. Agent Name is the name your LiveKit worker registers with in WorkerOptions(agent_name=...). It must match exactly not a display name.",
  },
  {
    label:
      "5. Start your LiveKit agent worker if self-hosted or else deploy your agent on livekit clou so it registers with your LiveKit project, then click 'Test Connection' to verify.",
  },
  {
    label:
      "6. For Livekit Cloud go to Settings -> Project, Scroll down and check Plan quotas and check Concurrent Session Limit. For self-hosted livekit you can set the concurrency based on the agent worker size and number of agent workers.",
  },
  {
    label:
      "7. Set Inbound and Outbound based on whether your agent greets first or not, or based on the requirements.",
  },
];

export const PROVIDER_STEPS_MAPPER = {
  vapi: VAPI_STEPS,
  retell: RETELL_STEPS,
  elevenlabs: ELEVENLABS_STEPS,
  livekit: LIVEKIT_STEPS,
  livekit_bridge: LIVEKIT_STEPS,
};

/**
 * Validate LiveKit credentials by calling the backend endpoint.
 * @param {{ livekitUrl: string, livekitApiKey: string, livekitApiSecret: string, livekitAgentName: string }} params
 * @returns {Promise<{ valid: boolean, error?: string }>}
 */
export const validateLiveKitCredentials = async ({
  livekitUrl,
  livekitApiKey,
  livekitApiSecret,
  livekitAgentName,
  agentDefinitionId,
}) => {
  // Importing here to avoid circular dependency issues at module level
  const { default: axios, endpoints } = await import("src/utils/axios");
  try {
    const response = await axios.post(
      endpoints.runTests.validateLiveKitCredentials,
      {
        livekit_url: livekitUrl,
        api_key: livekitApiKey,
        api_secret: livekitApiSecret,
        agent_name: livekitAgentName,
        ...(agentDefinitionId
          ? { agent_definition_id: agentDefinitionId }
          : {}),
      },
    );
    const result = response.data?.result || response.data;
    if (result?.valid) {
      return { valid: true };
    }
    return { valid: false, error: result?.error || "Validation failed" };
  } catch (err) {
    return {
      valid: false,
      error: err.response?.data?.error || err.message || "Validation failed",
    };
  }
};
