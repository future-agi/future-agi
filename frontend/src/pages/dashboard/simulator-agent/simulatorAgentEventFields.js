const SETTING_FIELDS = [
  "model",
  "agentType",
  "llmTemperature",
  "voiceProvider",
  "voiceName",
  "interruptSensitivity",
  "conversationSpeed",
  "finishedSpeakingSensitivity",
  "maxCallDurationInMinutes",
  "initialMessageDelay",
];

/**
 * Properties for the simulator agent create and edit events: the agent's
 * settings, the prompt length and whether a first message is set. The name,
 * prompt and first message text are not included.
 */
export const simulatorAgentEventFields = (values) => {
  const fields = {};
  SETTING_FIELDS.forEach((name) => {
    if (values?.[name] !== undefined) fields[name] = values[name];
  });
  fields.prompt_length =
    typeof values?.prompt === "string" ? values.prompt.length : 0;
  fields.has_initial_message =
    typeof values?.initialMessage === "string" &&
    values.initialMessage.trim() !== "";
  return fields;
};
