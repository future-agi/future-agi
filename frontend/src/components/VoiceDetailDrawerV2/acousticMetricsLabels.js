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
  not_analyzed: "not analyzed",
  not_applicable: "not applicable",
  not_enabled: "not enabled",
  no_recording: "no recording",
  unknown_agent_track: "unknown tested-agent track",
  mixed_speakers: "mixed speakers",
  unsupported_format: "unsupported format",
  unsupported_sample_rate: "unsupported sample rate",
  limit_exceeded: "limit exceeded",
  empty_audio: "empty audio",
  no_voice: "no voice",
  insufficient_audio: "insufficient audio",
  insufficient_voice: "insufficient voice",
  insufficient_coverage: "insufficient coverage",
  no_voiced_frames: "no voiced frames",
  no_noise_floor: "no noise floor",
  out_of_domain: "out of domain",
  not_validated: "not validated",
  model_unavailable: "model unavailable",
  other_unavailable: "Unavailable",
  invalid_audio: "invalid audio",
  nonfinite_output: "non-finite output",
  storage_unavailable: "storage unavailable",
  provider_access_denied: "provider access denied",
  timeout: "analysis timed out",
  analysis_error: "analysis error",
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
