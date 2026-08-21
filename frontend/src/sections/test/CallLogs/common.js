import { AGENT_TYPES } from "src/sections/agents/constants";
import { getChatTurnContent } from "src/components/ChatDetailDrawerV2/chatTranscriptUtils";

export const CallType = {
  INBOUND: "Inbound",
  OUTBOUND: "Outbound",
};

export const CallStatus = {
  COMPLETED: "completed",
  FAILED: "failed",
  PENDING: "pending",
  REGISTERED: "registered",
  ONGOING: "ongoing",
  CANCELLED: "cancelled",
};

export const CallSentimentOptions = {
  POSITIVE: "Positive",
  NEGATIVE: "Negative",
  NEUTRAL: "Neutral",
};

export const getContentMessage = (
  transcript,
  simulationCallType = transcript?.simulation_call_type ??
    transcript?.simulationCallType,
) => {
  if (!transcript) return "";

  return simulationCallType === AGENT_TYPES.CHAT
    ? getChatTurnContent(transcript)
    : transcript?.content ??
        transcript?.raw_content ??
        transcript?.rawContent ??
        "";
};
