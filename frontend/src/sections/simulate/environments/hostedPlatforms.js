import { AGENT_TYPES } from "src/sections/agents/constants";

// Hosted-platform providers grouped by agent type — the roster only makes sense
// once we know what kind of agent is being connected. Only the two live agent
// types (voice, chat) keep rosters; coming-soon types cannot be selected, so
// their rosters would be dead data. The five voice logos live in
// components/platformLogos.js — this module stays svg-free so it is runnable
// on its own.
export const HOSTED_PLATFORMS_BY_TYPE = {
  [AGENT_TYPES.VOICE]: [
    {
      id: "vapi",
      name: "Vapi",
      icon: "solar:phone-calling-rounded-linear",
      idLabel: "Assistant ID",
      idPlaceholder: "asst_9f2c…",
      keyLabel: "Vapi API key",
    },
    {
      id: "retell",
      name: "Retell AI",
      icon: "solar:microphone-3-linear",
      idLabel: "Agent ID",
      idPlaceholder: "agent_9f2c…",
      keyLabel: "Retell API key",
    },
    {
      // "Others" — an agent that already answers a phone number. The platform
      // dials it; the pasted system prompt only seeds scenarios. Backend
      // connector `phone`. Rendered with a Solar icon, not a brand logo.
      id: "other",
      name: "Others",
      isOther: true,
      icon: "solar:dialog-2-linear",
      idLabel: "Phone number",
      idPlaceholder: "+1 415 555 1234",
      keyLabel: "API key",
    },
    {
      id: "bland",
      name: "Bland.ai",
      icon: "solar:phone-linear",
      idLabel: "Pathway ID",
      idPlaceholder: "pathway_9f2c…",
      keyLabel: "Bland API key",
      // Not a real connector yet (submit would 400) — surfaced but not selectable.
      comingSoon: true,
    },
    {
      id: "elevenlabs",
      name: "ElevenLabs",
      icon: "solar:soundwave-linear",
      idLabel: "Agent ID",
      idPlaceholder: "agent_9f2c…",
      keyLabel: "ElevenLabs API key",
      // Not a real connector yet (submit would 400) — surfaced but not selectable.
      comingSoon: true,
    },
    {
      id: "livekit",
      name: "LiveKit",
      icon: "solar:server-minimalistic-linear",
      isLiveKit: true,
      idLabel: "Agent name",
      idPlaceholder: "returns-line-agent",
      keyLabel: "LiveKit API key",
    },
  ],
  [AGENT_TYPES.CHAT]: [
    {
      // Maps to `retell_chat`; listed first so it's the default when Chat is
      // selected. Copilot Studio and Foundry follow; the rest are not
      // connectors yet, so they're surfaced coming-soon (not selectable).
      id: "retell_chat",
      name: "Retell AI",
      icon: "solar:chat-round-line-linear",
      idLabel: "Chat agent ID",
      idPlaceholder: "agent_9f2c…",
      keyLabel: "Retell API key",
    },
    // Microsoft agents are read through the customer's Entra app, so they take
    // several fields instead of one ID and one key. `secret` names the source
    // field the value travels in (`apiKey`/`apiSecret` are never persisted);
    // every other field is public agent config, keyed by `name`. `agentId`
    // marks the field that names the environment.
    {
      id: "copilot_studio",
      name: "Copilot Studio",
      icon: "solar:chat-round-dots-linear",
      fields: [
        { name: "tenant_id", label: "Tenant ID", placeholder: "00000000-0000-0000-0000-000000000000", required: true },
        { name: "client_id", label: "Client ID", placeholder: "00000000-0000-0000-0000-000000000000", required: true },
        {
          name: "client_secret",
          label: "Client secret",
          placeholder: "Secret value…",
          secret: "apiKey",
          required: true,
          helper: "From your Entra app's Certificates & secrets. Copy the secret's Value.",
        },
        {
          name: "dataverse_url",
          label: "Dataverse environment URL",
          placeholder: "https://org123.crm.dynamics.com",
          required: true,
          helper: "Power Platform admin center › your environment › Environment URL.",
        },
        {
          name: "agent_id",
          label: "Agent ID",
          placeholder: "00000000-0000-0000-0000-000000000000",
          required: true,
          agentId: true,
          helper: "In Copilot Studio: your agent › Settings › Advanced › Metadata.",
        },
        {
          name: "directline_secret",
          label: "Direct Line secret",
          placeholder: "Secret 1…",
          secret: "apiSecret",
          helper: "Optional. Settings › Security › Web channel security. Only needed to chat with a published agent.",
        },
      ],
    },
    {
      id: "foundry",
      name: "Foundry",
      icon: "solar:cloud-linear",
      fields: [
        { name: "tenant_id", label: "Tenant ID", placeholder: "00000000-0000-0000-0000-000000000000", required: true },
        { name: "client_id", label: "Client ID", placeholder: "00000000-0000-0000-0000-000000000000", required: true },
        {
          name: "client_secret",
          label: "Client secret",
          placeholder: "Secret value…",
          secret: "apiKey",
          required: true,
          helper: "From your Entra app's Certificates & secrets. The app needs the Foundry User role on the Foundry resource.",
        },
        {
          name: "project_endpoint",
          label: "Project endpoint",
          placeholder: "https://your-resource.services.ai.azure.com/api/projects/your-project",
          required: true,
          helper: "On your Foundry project's overview page.",
        },
        { name: "agent_name", label: "Agent name", placeholder: "order-assistant", required: true, agentId: true },
      ],
    },
    {
      id: "openai_assistants",
      name: "OpenAI Assistants",
      icon: "solar:magic-stick-3-linear",
      idLabel: "Assistant ID",
      idPlaceholder: "asst_9f2c…",
      keyLabel: "OpenAI API key",
      comingSoon: true,
    },
    {
      id: "langgraph",
      name: "LangGraph Cloud",
      icon: "solar:diagram-up-linear",
      idLabel: "Deployment URL",
      idPlaceholder: "https://…",
      keyLabel: "LangSmith API key",
      comingSoon: true,
    },
    {
      id: "crewai",
      name: "CrewAI",
      icon: "solar:users-group-rounded-linear",
      idLabel: "Crew ID",
      idPlaceholder: "crew_9f2c…",
      keyLabel: "CrewAI API key",
      comingSoon: true,
    },
    {
      id: "claude_agents",
      name: "Claude Agents",
      icon: "solar:atom-linear",
      idLabel: "Agent name",
      idPlaceholder: "support-agent",
      keyLabel: "Anthropic API key",
      comingSoon: true,
    },
  ],
};

export const HOSTED_EMPTY_ROSTER_COPY =
  "No hosted platforms for this agent type yet. Try Source repository instead.";
