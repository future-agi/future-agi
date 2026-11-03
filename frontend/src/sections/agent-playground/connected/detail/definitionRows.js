import { humanize, sourceLabel } from "../connectedAgentLabels";

// Rows for the Definition card. Each row is { label, value } or
// { label, items }; `mono` and `multiline` shape how the value reads.

const EMPTY = "—";

const sentence = (word) => {
  const s = String(word).replace(/[_-]+/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
};

const join = (parts) => parts.filter((p) => p != null && p !== "").join(" · ");

const directionOf = ({ inbound, target_speaks_first: agentFirst }) => {
  if (typeof inbound !== "boolean") return null;
  const way = inbound ? "Inbound" : "Outbound";
  if (agentFirst === true) return `${way} · agent speaks first`;
  if (agentFirst === false) return `${way} · caller speaks first`;
  return way;
};

const TRACING = {
  traceai: "traceAI",
  opentelemetry: "OpenTelemetry",
  none: "None",
};

/** What a hosted (or older) agent version was set up with. */
export const hostedRows = ({ config = {}, apiKey }) => {
  const rows = [];
  const add = (label, value, extra = {}) => {
    if (value != null && value !== "") rows.push({ label, value, ...extra });
  };
  add("Provider", config.provider ? sourceLabel(config.provider) : null);
  add("Assistant ID", config.assistant_id, { mono: true });
  rows.push({
    label: "API key",
    value: apiKey ?? "Not saved",
    mono: apiKey != null,
  });
  add("Contact number", config.contact_number, { mono: true });
  add("Direction", directionOf(config));
  if (config.languages?.length)
    rows.push({ label: "Languages", items: config.languages });
  add("Prompt", config.description, { multiline: true });
  add("Model", config.model || config.model_details?.model);
  if (config.provider === "livekit") {
    add("LiveKit URL", config.livekit_url, { mono: true });
    add("LiveKit agent", config.livekit_agent_name, { mono: true });
    add("LiveKit API key", config.livekit_api_key, { mono: true });
    add(
      "Max concurrent calls",
      config.livekit_max_concurrency != null
        ? String(config.livekit_max_concurrency)
        : null,
    );
  }
  return rows;
};

/** What the SDK stored for a code agent version (the stored Agent). */
export const storedAgentRows = ({ agent }) => {
  const { model, voice, provider_ref: ref } = agent;
  const rows = [
    { label: "Kind", value: humanize(agent.kind) },
    { label: "Type", value: humanize(agent.type) },
  ];
  if (ref) {
    rows.push({
      label: "Provider",
      value: join([sourceLabel(ref.provider), ref.id, ref.version]),
    });
  }
  rows.push(
    {
      label: "Model",
      value: model
        ? join([
            model.name,
            model.temperature != null
              ? `temperature ${model.temperature}`
              : null,
            model.max_tokens != null ? `max ${model.max_tokens} tokens` : null,
          ])
        : EMPTY,
    },
    {
      label: "Voice",
      value: voice
        ? join([
            voice.voice_id,
            voice.provider,
            voice.language,
            voice.speed != null ? `speed ${voice.speed}` : null,
          ])
        : EMPTY,
    },
    {
      label: "Prompts",
      value:
        (agent.behaviour ?? [])
          .map((b) => b.instructions)
          .filter(Boolean)
          .join("\n\n") || EMPTY,
      multiline: true,
    },
    {
      label: `Tools · ${(agent.tools ?? []).length}`,
      items: (agent.tools ?? []).map((t) => t.name),
      mono: true,
    },
    agent.telephony?.length
      ? {
          label: "Telephony",
          items: agent.telephony.map((t) => join([t.number, t.provider])),
        }
      : { label: "Telephony", value: EMPTY },
    {
      label: "Tracing library",
      value: TRACING[agent.instrumentation] ?? humanize(agent.instrumentation),
    },
    agent.gaps?.length
      ? { label: "Gaps", items: agent.gaps.map(sentence) }
      : { label: "Gaps", value: "None" },
  );
  return rows;
};
