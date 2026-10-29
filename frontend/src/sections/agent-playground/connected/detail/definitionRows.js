import { humanize, sourceLabel } from "../connectedAgentLabels";

// Rows for the Definition cards. Each row is { label, value } or
// { label, items }; `mono` and `multiline` shape how the value reads.

const sentence = (word) => {
  const s = String(word).replace(/[_-]+/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
};

const join = (parts) => parts.filter((p) => p != null && p !== "").join(" · ");

const TYPE_LABELS = { voice: "Voice", text: "Chat", chat: "Chat" };

const TRACING = {
  traceai: "traceAI",
  opentelemetry: "OpenTelemetry",
  none: "None",
};

const pick = (...values) => values.find((v) => v != null && v !== "") ?? null;

/**
 * What the Definition cards read for one version: its own snapshot and, for
 * a code version, where it was read from (`source`) and what the SDK stored
 * (`stored`). Nothing is filled in from the agent's current values, so an
 * older version shows only what it had. Where the agent lives is the one
 * exception: the agent's source when the snapshot doesn't say.
 */
export const factsOf = (agent = {}, version = {}, stored = null) => {
  const config = version.config ?? {};
  return {
    source: version.source ?? null,
    telephony: stored?.telephony ?? [],
    name: pick(config.agent_name),
    type: pick(config.agent_type),
    description: pick(config.description),
    languages: config.languages ?? [],
    inbound: pick(config.inbound),
    agentFirst: pick(config.target_speaks_first),
    contactNumber: pick(config.contact_number),
    model: pick(config.model, config.model_details?.model),
    provider: pick(config.provider, version.source?.kind, agent.source),
    assistantId: pick(config.assistant_id),
    apiKey: version.apiKey ?? null,
    livekitUrl: config.livekit_url ?? null,
    livekitAgent: config.livekit_agent_name ?? null,
    livekitApiKey: config.livekit_api_key ?? null,
    maxConcurrency: config.livekit_max_concurrency ?? null,
  };
};

const rowsOf = (entries) =>
  entries.filter((row) => row.items || (row.value != null && row.value !== ""));

/** Identity: name, type and languages. A hosted agent's prompt is under Configuration. */
export const identityRows = (facts) =>
  rowsOf([
    { label: "Name", value: facts.name },
    {
      label: "Type",
      value: facts.type
        ? TYPE_LABELS[facts.type] || humanize(facts.type)
        : null,
    },
    facts.languages.length
      ? { label: "Languages", items: facts.languages }
      : { label: "Languages", value: null },
  ]);

/** Source: where the agent lives and what we read it with. */
export const sourceRows = (facts, { isCode }) => {
  const rows = [
    {
      label: "Lives in",
      value: facts.provider ? sourceLabel(facts.provider) : null,
    },
  ];
  if (isCode) {
    const { repository, ref, commit_sha: sha, subfolder } = facts.source ?? {};
    rows.push(
      {
        label: "Repository",
        value: repository ? repository.replace(/^https?:\/\//, "") : null,
        mono: true,
      },
      { label: "Branch", value: ref, mono: true },
      { label: "Folder", value: subfolder, mono: true },
      { label: "Commit", value: sha ? sha.slice(0, 7) : null, mono: true },
    );
  } else {
    rows.push(
      { label: "Assistant ID", value: facts.assistantId, mono: true },
      {
        label: "API key",
        value: facts.apiKey ?? "Not saved",
        mono: facts.apiKey != null,
      },
    );
  }
  if (facts.provider === "livekit") {
    rows.push(
      { label: "LiveKit URL", value: facts.livekitUrl, mono: true },
      { label: "LiveKit agent", value: facts.livekitAgent, mono: true },
      { label: "LiveKit API key", value: facts.livekitApiKey, mono: true },
    );
  }
  return rowsOf(rows);
};

/** Reach: how a conversation gets to the agent and who opens it. */
export const reachRows = (facts) => {
  const rows = [];
  const opener = (agentWord, otherWord) =>
    facts.agentFirst === true
      ? `${agentWord} first`
      : facts.agentFirst === false
        ? `${otherWord} first`
        : null;
  // A code agent's numbers are what the SDK found in its telephony setup;
  // a hosted agent's is the one saved with it.
  const numbers = facts.telephony.length
    ? facts.telephony.map((t) => t.number).filter(Boolean)
    : [facts.contactNumber].filter(Boolean);
  if (facts.type === "voice") {
    rows.push(
      {
        label: "Voice",
        value: join([
          numbers.length ? "Phone call" : "Web call",
          typeof facts.inbound === "boolean"
            ? facts.inbound
              ? "inbound"
              : "outbound"
            : null,
          opener("agent speaks", "caller speaks"),
        ]),
      },
      numbers.length > 1
        ? { label: "Phone numbers", items: numbers, mono: true }
        : { label: "Phone number", value: numbers[0] ?? null, mono: true },
    );
  } else if (facts.type) {
    rows.push({
      label: "Chat",
      value: join([
        typeof facts.inbound === "boolean"
          ? facts.inbound
            ? "Inbound"
            : "Outbound"
          : null,
        opener("agent writes", "user writes"),
      ]),
    });
  }
  if (facts.maxConcurrency != null) {
    const n = Number(facts.maxConcurrency);
    rows.push({
      label: "At once",
      value: `Up to ${n} conversation${n === 1 ? "" : "s"}`,
    });
  }
  return rowsOf(rows);
};

/** Configuration of a hosted agent: the model and the prompt it runs with. */
export const hostedConfigurationRows = (facts) =>
  rowsOf([
    { label: "Model", value: facts.model },
    { label: "Prompt", value: facts.description, multiline: true },
  ]);

/** What the SDK stored for a code agent version (the stored Agent). A part the code doesn't describe is left out. */
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
        : null,
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
        : null,
    },
    {
      label: "Prompts",
      value:
        (agent.behaviour ?? [])
          .map((b) => b.instructions)
          .filter(Boolean)
          .join("\n\n") || null,
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
      : { label: "Telephony", value: null },
    {
      label: "Tracing library",
      value: TRACING[agent.instrumentation] ?? humanize(agent.instrumentation),
    },
    agent.gaps?.length
      ? { label: "Gaps", items: agent.gaps.map(sentence) }
      : { label: "Gaps", value: "None" },
  );
  return rowsOf(rows);
};
