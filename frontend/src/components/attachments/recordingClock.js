import { formatClock } from "../VoiceDetailDrawerV2/transcriptUtils";
export const recordingClock = (value, duration) =>
  duration < 1
    ? `${formatClock(value)}.${Math.floor(value * 10 + 1e-6) % 10}`
    : formatClock(value);
