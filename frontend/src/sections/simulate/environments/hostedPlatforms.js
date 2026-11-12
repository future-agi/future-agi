import { AGENT_TYPES } from "src/sections/agents/constants";

// Hosted-platform providers grouped by agent type — the roster only makes sense
// once we know what kind of agent is being connected. Only the two live agent
// types (voice, chat) keep rosters; the other types cannot be selected, so
// their rosters would be dead data. Chat platforms that are not connectors in
// this release are listed disabled (not selectable). The voice logos live in
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
      // The only real chat connector today (maps to `retell_chat`); listed first
      // so it's the default when Chat is selected. The rest are not connectors
      // in this release, so they're shown disabled (not selectable).
      id: "retell_chat",
      name: "Retell AI",
      icon: "solar:chat-round-line-linear",
      idLabel: "Chat agent ID",
      idPlaceholder: "agent_9f2c…",
      keyLabel: "Retell API key",
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
