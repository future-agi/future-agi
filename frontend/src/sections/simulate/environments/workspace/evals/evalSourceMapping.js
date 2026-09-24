// §10 input mapping — which run artifact each of an eval's required keys reads,
// resolved by the environment's modality. This mirrors the backend
// `_SOURCE_BY_KEY_VOICE` / `_SOURCE_BY_KEY_TEXT` dicts: the backend resolves the
// mapping server-side (the add endpoint takes only a name), so the UI shows it
// read-only — left = the eval's required key, right = the source it maps to.
export const SOURCE_BY_KEY = {
  voice: {
    // The combined recording; a per-channel mapping resolves empty on
    // combined-only providers.
    conversation: "voice_recording",
    // Audio-analysing evals ask for the recording itself, not a transcript.
    input_audio: "voice_recording",
    // A single-output eval on a call is judging the same conversation.
    output: "voice_recording",
    agent_prompt: "agent_prompt",
    system_prompt: "agent_prompt",
  },
  text: {
    // A chat run has no recording, so the conversation is its transcript text.
    conversation: "transcript",
    output: "transcript",
    agent_prompt: "agent_prompt",
    system_prompt: "agent_prompt",
  },
};

// The environment's modality drives the mapping. Voice envs read recordings;
// everything else is a chat/text run reading transcripts.
export const modalityOf = (env) => (env?.agentType === "voice" ? "voice" : "text");

// Resolve one required key → its source value for a modality. Null when that
// modality has no source for the key (e.g. a per-channel key on a combined-only
// provider) — the caller renders a dash rather than inventing a source.
export const sourceForKey = (key, modality) => SOURCE_BY_KEY[modality]?.[key] ?? null;

// Readable label for a snake_case mapping term (key or value).
export const humanizeMappingTerm = (term) =>
  String(term || "")
    .replace(/_/g, " ")
    .replace(/^\w/, (c) => c.toUpperCase());

// The full read-only mapping rows for an eval's required keys under a modality:
// [{ key, value }] with value null when unmapped.
export const mappingRowsFor = (requiredKeys = [], modality) =>
  (Array.isArray(requiredKeys) ? requiredKeys : []).map((key) => ({
    key,
    value: sourceForKey(key, modality),
  }));
