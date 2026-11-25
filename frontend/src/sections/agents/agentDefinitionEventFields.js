// Fields sent with the create/update agent definition analytics events. They
// are picked by name from the request payload: the agent kind, provider and
// auth method, plus flags and counts. Names, descriptions, phone numbers,
// credentials, headers and provider config are not part of the event.
const pickString = (value) => (typeof value === "string" ? value : undefined);
const pickBoolean = (value) => (typeof value === "boolean" ? value : undefined);

export const agentDefinitionEventFields = (payload = {}) => {
  const concurrency = Number(payload.livekit_max_concurrency);
  const fields = {
    agent_type: pickString(payload.agent_type),
    provider: pickString(payload.provider),
    authentication_method: pickString(payload.authentication_method),
    language_count: Array.isArray(payload.languages)
      ? payload.languages.length
      : undefined,
    inbound: pickBoolean(payload.inbound),
    target_speaks_first: pickBoolean(payload.target_speaks_first),
    observability_enabled: pickBoolean(payload.observability_enabled),
    has_knowledge_base: Boolean(payload.knowledge_base),
    has_contact_number: Boolean(payload.contact_number),
    max_concurrency:
      payload.livekit_max_concurrency != null && Number.isFinite(concurrency)
        ? concurrency
        : undefined,
  };
  return Object.fromEntries(
    Object.entries(fields).filter(([, value]) => value !== undefined),
  );
};
