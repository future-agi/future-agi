// ── Audio detection helpers (pure, no React) ──────────────────
// Keys whose string value should be rendered as an audio player.
// Handles both snake_case and squashed (no-separator) forms.
const AUDIO_KEY_NAMES = new Set([
  "recording_url",
  "recordingurl",
  "stereo_recording_url",
  "stereorecordingurl",
  "mono_recording_url",
  "monorecordingurl",
  "audio_url",
  "audiourl",
  "combined_url",
  "combinedurl",
  "assistant_url",
  "assistanturl",
  "customer_url",
  "customerurl",
]);

// Object keys whose value is a recording descriptor we should unpack
// into multiple labeled players.
const RECORDING_OBJECT_KEYS = new Set(["recording", "recordings"]);

export function isAudioUrlString(val) {
  if (typeof val !== "string") return false;
  const lower = val.toLowerCase();
  if (lower.startsWith("data:audio")) return true;
  if (!/^https?:\/\//.test(lower) && !lower.startsWith("blob:")) return false;
  // Strip query string and fragment before checking extension
  const withoutQs = lower.split("?")[0].split("#")[0];
  return /\.(mp3|wav|ogg|m4a|aac|flac|webm)$/.test(withoutQs);
}

export function isAudioKey(key) {
  if (typeof key !== "string") return false;
  const normalized = key.toLowerCase().replace(/[_-]/g, "");
  return (
    AUDIO_KEY_NAMES.has(key.toLowerCase()) || AUDIO_KEY_NAMES.has(normalized)
  );
}

export function isRecordingObjectKey(key) {
  return (
    typeof key === "string" && RECORDING_OBJECT_KEYS.has(key.toLowerCase())
  );
}

// Walk a recording object and flatten labeled URL entries.
// Accepts shapes like:
//   { stereoUrl, monoRecordingUrl, mono: { combinedUrl, assistantUrl, customerUrl } }
export function collectRecordingTracks(recordingObj) {
  const tracks = [];
  if (!recordingObj || typeof recordingObj !== "object") return tracks;

  const walk = (obj, prefix) => {
    for (const [k, v] of Object.entries(obj)) {
      if (v == null || v === "") continue;
      const label = prefix ? `${prefix}.${k}` : k;
      if (typeof v === "string") {
        // Heuristic: any URL-looking string under a recording object is an audio track.
        if (
          /^https?:\/\//.test(v) ||
          v.startsWith("blob:") ||
          v.startsWith("data:audio")
        ) {
          tracks.push({ label, url: v });
        }
      } else if (typeof v === "object" && !Array.isArray(v)) {
        walk(v, label);
      }
    }
  };
  walk(recordingObj, "");
  return tracks;
}
