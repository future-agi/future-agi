import React, {
  useCallback,
  useEffect,
  useImperativeHandle,
  forwardRef,
} from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import { alpha, useTheme } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import useFalconStore from "src/sections/falcon-ai/store/useFalconStore";
import { useFalconSocket } from "src/sections/falcon-ai/hooks/useFalconSocket";
import { createConversation } from "src/sections/falcon-ai/hooks/useFalconAPI";
import MessageList from "src/sections/falcon-ai/components/MessageList";
import ChatInput from "src/sections/falcon-ai/components/ChatInput";
import useImagineStore from "./useImagineStore";

/**
 * Compact Falcon chat panel embedded in the Imagine tab.
 * Creates a dedicated conversation with trace context.
 */
const ImagineChatPanel = forwardRef(function ImagineChatPanel(
  { traceId, projectId, entityType = "trace" },
  ref,
) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";

  const currentConversationId = useFalconStore((s) => s.currentConversationId);
  const setCurrentConversation = useFalconStore(
    (s) => s.setCurrentConversation,
  );
  const addMessage = useFalconStore((s) => s.addMessage);
  const setStreaming = useFalconStore((s) => s.setStreaming);
  const imagineConversationId = useImagineStore((s) => s.conversationId);
  const setImagineConversationId = useImagineStore((s) => s.setConversationId);

  const { sendChat, sendStop } = useFalconSocket();

  // When the imagine tab opens, switch to its dedicated conversation
  // (or create one on first message)
  useEffect(() => {
    if (
      imagineConversationId &&
      imagineConversationId !== currentConversationId
    ) {
      setCurrentConversation(imagineConversationId);
    }
  }, [imagineConversationId, currentConversationId, setCurrentConversation]);

  const handleSend = useCallback(
    async (text) => {
      let convId = imagineConversationId;

      if (!convId) {
        try {
          const resp = await createConversation(text.slice(0, 50), "imagine");
          convId = resp.result?.id || resp.id;
          setImagineConversationId(convId);
          setCurrentConversation(convId);
        } catch {
          return;
        }
      }

      // Ensure we're on the right conversation
      if (convId !== useFalconStore.getState().currentConversationId) {
        setCurrentConversation(convId);
      }

      const userMsg = {
        id: `user-${Date.now()}`,
        role: "user",
        content: text,
        created_at: new Date().toISOString(),
      };
      addMessage(userMsg);

      const assistantMsgId = `assistant-${Date.now()}`;
      addMessage({
        id: assistantMsgId,
        role: "assistant",
        content: "",
        created_at: new Date().toISOString(),
      });
      setStreaming(true, assistantMsgId);

      const TRACE_INSTRUCTION =
        "You are in IMAGINE mode — the user is building a custom visualization dashboard " +
        "for a specific trace. Your primary job is to: " +
        "1) Use get_trace/get_span tools to fetch trace data, " +
        "2) Analyze the data and decide what visualizations would be most helpful, " +
        "3) Call render_widget to create charts, tables, metrics cards, etc. in the user's view. " +
        "The trace ID is: " +
        traceId +
        ". Fetch it first, then build widgets.\n\n" +
        "CRITICAL: Use dataBinding instead of static config so views work across traces. " +
        "dataBinding examples:\n" +
        "- bar_chart: {dataBinding: {seriesFromSpans: {name: 'Latency', valuePath: 'latency_ms'}, categoryPath: 'name', labelFormat: '{name} ({observation_type})'}}\n" +
        "- pie_chart: {dataBinding: {groupBy: 'observation_type', aggregate: 'count'}}\n" +
        "- metric_card simple: {dataBinding: {valuePath: 'summary.totalDurationMs', valueFormat: '{value}ms'}}\n" +
        "- metric_card computed: {dataBinding: {compute: 'max(spans.latency_ms, observation_type=llm)', valueFormat: '{value}ms'}}\n" +
        "- metric_card overhead: {dataBinding: {compute: 'summary.totalDurationMs - max(spans.latency_ms, observation_type=llm)', valueFormat: '{value}ms'}}\n" +
        "  Aggregates: max/min/sum/avg/count(spans.field) with optional filter: max(spans.field, observation_type=llm)\n" +
        "- key_value: {dataBinding: {items: [{key: 'Trace ID', valuePath: 'trace.id'}, {key: 'Input', valuePath: 'rootSpan.input', format: 'truncate:80'}]}}\n" +
        "- data_table: {dataBinding: {rowsFromSpans: true, columns: [{field: 'name', headerName: 'Span'}, {field: 'latency_ms', headerName: 'Latency'}]}}\n" +
        "- markdown analysis: {dynamicAnalysis: {prompt: 'Summarize findings...', contextFields: ['summary', 'rootSpan.input']}}\n" +
        "Span fields: name, observation_type, latency_ms, total_tokens, status, model, input, output.\n" +
        "Summary fields: totalSpans, totalDurationMs, totalTokens, totalCost.\n" +
        "NEVER use static config with hardcoded values — ALWAYS use dataBinding or dynamicAnalysis.\n" +
        "For markdown analysis, use dynamicAnalysis so it re-runs on each trace.\n" +
        "For computed metrics (like overhead, LLM-only latency), use compute expressions.";

      const VOICE_INSTRUCTION =
        "You are in IMAGINE mode — the user is building a custom visualization dashboard " +
        "for a voice call. This is NOT a standard LLM trace — it's a phone/voice agent call " +
        "with a rich transcript, audio recordings, latency metrics, and cost breakdown.\n\n" +
        "The call ID is: " +
        traceId +
        ".\n\n" +
        "Available data in traceData.trace:\n" +
        "- transcript: array of {speaker_role, message/content, time, duration, startTimeSeconds, endTimeSeconds} — the full call conversation\n" +
        "- call_summary: AI-generated summary of the call\n" +
        "- recordings: audio URLs (stereo, mono, assistant, customer channels)\n" +
        "- provider: voice provider (e.g. vapi, retell, livekit)\n" +
        "- status: call completion status\n" +
        "- customerLatencyMetrics: {systemMetrics: {endpointing, transcriber, model, voice}} — pipeline latency in ms\n" +
        "- customerCostBreakdown: {llm: {cost, promptTokens, completionTokens}, stt: {cost}, tts: {cost}} — per-component costs\n" +
        "- evalOutputs: evaluation results (name → score/reason)\n\n" +
        "traceData.transcript is a shortcut to the transcript array.\n\n" +
        "Best widget types for voice calls:\n" +
        "- data_table with rowsFrom: show the transcript as a table\n" +
        "  {dataBinding: {rowsFrom: 'transcript', columns: [{field: 'speaker_role', headerName: 'Speaker'}, {field: 'message', headerName: 'Message'}, {field: 'duration', headerName: 'Duration (s)'}]}}\n" +
        "- pie_chart with groupFrom: speaker turn distribution\n" +
        "  {dataBinding: {groupFrom: 'transcript', groupBy: 'speaker_role', aggregate: 'count'}}\n" +
        "- bar_chart with seriesFrom: duration per turn\n" +
        "  {dataBinding: {seriesFrom: 'transcript', seriesConfig: {name: 'Duration', valuePath: 'duration'}, categoryPath: 'speaker_role'}}\n" +
        "- key_value: call metadata (provider, status, duration, ended reason)\n" +
        "  {dataBinding: {items: [{key: 'Provider', valuePath: 'trace.provider'}, {key: 'Summary', valuePath: 'trace.call_summary'}]}}\n" +
        "- metric_card: computed values\n" +
        "  {dataBinding: {compute: 'count(spans.name)', valueFormat: '{value} turns'}} or {dataBinding: {valuePath: 'trace.status'}}\n" +
        "- markdown with dynamicAnalysis: call quality assessment, agent behavior analysis, compliance check\n" +
        "  {dynamicAnalysis: {prompt: 'Analyze this voice call for quality issues. Focus on interruptions, silence gaps, and whether the agent followed the scenario.', contextFields: ['trace.call_summary', 'transcript']}}\n\n" +
        "CRITICAL: Use dataBinding / dynamicAnalysis — NEVER hardcode data values.\n" +
        "Voice calls typically have 0-1 observation spans, so span-based widgets will be mostly empty. " +
        "For transcript-based widgets use rowsFrom/seriesFrom/groupFrom: 'transcript'. " +
        "For call metadata use valuePath: 'trace.fieldName'. " +
        "Focus on transcript, latency metrics, cost breakdown, and AI analysis.";

      const context = {
        page: "imagine",
        entity_type: entityType,
        entity_id: traceId,
        extra: {
          projectId,
          instruction:
            entityType === "voice" ? VOICE_INSTRUCTION : TRACE_INSTRUCTION,
        },
      };
      sendChat(text, convId, context);
    },
    [
      imagineConversationId,
      traceId,
      projectId,
      entityType,
      setImagineConversationId,
      setCurrentConversation,
      addMessage,
      setStreaming,
      sendChat,
    ],
  );

  // Expose send function to parent via ref
  useImperativeHandle(ref, () => ({ send: handleSend }), [handleSend]);

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        borderTop: "1px solid",
        borderColor: "divider",
        bgcolor: isDark ? "background.default" : alpha("#f8f9fa", 0.5),
      }}
    >
      {/* Header */}
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 0.75,
          px: 1.5,
          py: 0.75,
          borderBottom: "1px solid",
          borderColor: "divider",
          flexShrink: 0,
        }}
      >
        <Iconify
          icon="mdi:creation"
          width={16}
          sx={{ color: "primary.main" }}
        />
        <Typography
          sx={{ fontSize: 12, fontWeight: 600, color: "text.primary" }}
        >
          Falcon AI
        </Typography>
        <Typography sx={{ fontSize: 11, color: "text.disabled", ml: "auto" }}>
          Trace context attached
        </Typography>
      </Box>

      {/* Messages */}
      <Box sx={{ flex: 1, overflow: "auto", minHeight: 0 }}>
        <MessageList onQuickAction={handleSend} />
      </Box>

      {/* Input */}
      <Box sx={{ flexShrink: 0 }}>
        <ChatInput onSend={handleSend} onStop={sendStop} />
      </Box>
    </Box>
  );
});

ImagineChatPanel.propTypes = {
  traceId: PropTypes.string,
  projectId: PropTypes.string,
  entityType: PropTypes.oneOf(["trace", "voice"]),
};

export default ImagineChatPanel;
