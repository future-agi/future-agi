import _ from "lodash";

export const PERFORMANCE_METRICS_LABEL_MAP = {
  turn_count: "Turn Count",
  tokens: "Token Usage",
  tools_count: "No.of tool callings",
  duration: "Chat Duration",
};

export const VOICE_METRICS_LABEL_MAP = {
  ...PERFORMANCE_METRICS_LABEL_MAP,
  duration: "Call Duration",
  total_turns: "Turn Count",
  avg_agent_latency_ms: "Avg Agent Latency (ms)",
  user_wpm: "User WPM",
  bot_wpm: "Bot WPM",
  talk_ratio: "Talk Ratio",
};

export const getPerformanceMetricsLabel = (metric, isVoice = false) => {
  const map = isVoice ? VOICE_METRICS_LABEL_MAP : PERFORMANCE_METRICS_LABEL_MAP;
  return map[metric] || metric;
};

export const getChangeText = (isVoice = false) =>
  isVoice ? "from baseline call" : "from baseline chat";

export const formatIfFloat = (value) => {
  if (typeof value !== "number") return value;
  return Number.isInteger(value) ? value : value.toFixed(2);
};

export const transformToConversations = (data) => {
  const baseSessionTranscripts =
    data?.base_session_transcripts ?? data?.baseSessionTranscripts;
  const comparisonCallTranscripts =
    data?.comparison_call_transcripts ?? data?.comparisonCallTranscripts;

  // Normalize voice transcript roles to chat roles (assistant/user)
  // Voice uses: "bot" = AI assistant, "agent" = human/customer caller
  const normalizeRole = (role) => {
    const lower = (role || "").toLowerCase();
    if (lower === "agent" || lower === "customer" || lower === "user")
      return "user";
    if (lower === "bot" || lower === "assistant") return "assistant";
    return lower;
  };

  const transformMessages = (transcripts, label) => {
    const conversations = [];

    for (let i = 0; i < transcripts?.length; i++) {
      const t = transcripts[i];
      const rawMessages = t.messages || [];
      // Handle both string (voice) and array (chat) message formats
      const messages = Array.isArray(rawMessages) ? rawMessages : [rawMessages];
      const role = normalizeRole(t.role);

      for (let j = 0; j < messages.length; j++) {
        conversations.push({
          id: t.id || `msg_${conversations.length + 1}`,
          role,
          content: _.trimEnd(messages[j]),
          timeStamp: t.created_at,
          agentName: role === "user" ? "User" : "Assistant",
          align: role === "user" ? "flex-end" : "flex-start",
        });
      }
    }

    return {
      id: `session_${label.toLowerCase()}`,
      title: `${label === "A" ? "Baseline" : "Replayed"} Session Transcript`,
      label,
      conversations,
    };
  };

  return {
    baselineSession: transformMessages(baseSessionTranscripts, "A"),
    replayedSession: transformMessages(comparisonCallTranscripts, "B"),
  };
};
