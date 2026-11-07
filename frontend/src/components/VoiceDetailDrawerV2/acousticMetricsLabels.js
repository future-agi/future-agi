import { createContext } from "react";

// Detail transport state is separate from the API's analysis envelope. Legacy
// drawers keep playback mounted while the acoustic area loads or fails.
export const AcousticMetricsLoadContext = createContext(null);

export const ACOUSTIC_METRICS = [
  ["average_pitch_hz", "Average pitch (Hz)"],
  ["estimated_snr_db", "Estimated SNR (dB)"],
  ["voice_quality_index", "Voice quality index (0–5)"],
];

export function isAcousticMetricsVisible(data) {
  if (data === undefined) return false;
  if (data?.schema_version != null && data.schema_version !== 1) return true;
  if (data?.state === "unsupported") return true;
  return !ACOUSTIC_METRICS.every(([key]) => {
    const entry = data?.metrics?.[key];
    return entry?.state === "unavailable" && entry.reason === "not_enabled";
  });
}

const CAPTURE_LABELS = {
  provider_received_track: "Received over the call",
  provider_source_track: "Agent source audio",
  provider_recording: "Provider recording",
  bridge_received: "Received over the call",
  livekit_egress: "Room recording",
  unknown: "Unknown origin",
};
const TRANSPORT_LABELS = {
  sip: "Phone (SIP)",
  web_bridge: "Web call",
  livekit_room: "LiveKit room",
  phone: "Phone",
};

export function captureOriginLabel(value) {
  if (value == null) return "—";
  return Object.hasOwn(CAPTURE_LABELS, value)
    ? CAPTURE_LABELS[value]
    : "Unknown origin";
}

export function transportLabel(value) {
  if (value == null) return "—";
  return Object.hasOwn(TRANSPORT_LABELS, value)
    ? TRANSPORT_LABELS[value]
    : "Unknown transport";
}

// Committed copy of the public reason enums in ENGINEERING-DESIGN 5.4.
export const UNAVAILABLE_REASONS = [
  "not_analyzed",
  "not_applicable",
  "not_enabled",
  "no_recording",
  "unknown_agent_track",
  "mixed_speakers",
  "unsupported_format",
  "unsupported_sample_rate",
  "limit_exceeded",
  "empty_audio",
  "no_voice",
  "insufficient_audio",
  "insufficient_voice",
  "insufficient_coverage",
  "no_voiced_frames",
  "no_noise_floor",
  "out_of_domain",
  "not_validated",
  "model_unavailable",
  "other_unavailable",
];

export const FAILED_REASONS = [
  "invalid_audio",
  "nonfinite_output",
  "storage_unavailable",
  "provider_access_denied",
  "timeout",
  "analysis_error",
  "other_failed",
];

export const REASON_LABELS = {
  not_analyzed: "Not analyzed",
  not_applicable: "Not applicable to this call",
  not_enabled: "Not enabled",
  no_recording: "No recording",
  unknown_agent_track: "Tested-agent track could not be identified",
  mixed_speakers: "Multiple speakers in the agent track",
  unsupported_format: "Unsupported audio format",
  unsupported_sample_rate: "Unsupported sample rate",
  limit_exceeded: "Recording exceeds analysis limits",
  empty_audio: "Empty recording",
  no_voice: "No speech detected",
  insufficient_audio: "Not enough audio",
  insufficient_voice: "Not enough speech",
  insufficient_coverage: "Not enough speech covered",
  no_voiced_frames: "No voiced frames detected",
  no_noise_floor: "No separable noise floor",
  out_of_domain: "Outside the calibrated range or call type",
  not_validated: "Calibration not validated",
  model_unavailable: "Quality model unavailable",
  other_unavailable: "Unavailable",
  invalid_audio: "Audio could not be decoded",
  nonfinite_output: "Analysis produced an invalid result",
  storage_unavailable: "Recording could not be retrieved",
  provider_access_denied: "Recording access denied",
  timeout: "Analysis timed out",
  analysis_error: "Audio analysis error",
  other_failed: "Analysis failed",
};

export const STATE_LABELS = {
  complete: "Complete",
  partial: "Partial",
  pending: "Processing audio metrics",
  not_requested: "Unavailable",
  unavailable: "Unavailable",
  failed: "Failed",
  stale: "Stale",
};

export function formatMetricValue(entry) {
  if (entry?.state !== "available" || !Number.isFinite(entry.value)) return "—";
  return entry.value.toFixed(entry.unit === "index" ? 2 : 1);
}
