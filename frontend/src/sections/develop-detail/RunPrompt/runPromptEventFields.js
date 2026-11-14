// Text length of one prompt message: its content string, or the text parts of
// a multi-part content array.
const messageTextLength = (content) => {
  if (typeof content === "string") return content.length;
  if (!Array.isArray(content)) return 0;
  return content.reduce(
    (total, part) =>
      total + (typeof part?.text === "string" ? part.text.length : 0),
    0,
  );
};

// The response format as a type name, plus the saved schema's id when one is
// chosen. A saved schema is sent in the request as the whole record and an
// inline format as an object; only the type and id are reported.
const responseFormatFields = (responseFormat) => {
  if (!responseFormat || typeof responseFormat !== "object") {
    return { responseFormat };
  }
  if (typeof responseFormat.type === "string") {
    return { responseFormat: responseFormat.type };
  }
  return {
    responseFormat: "json_schema",
    ...(typeof responseFormat.id === "string" && {
      responseSchemaId: responseFormat.id,
    }),
  };
};

// A selected tool: config.tools holds tool ids; older configs held objects.
const toolId = (tool) => (typeof tool === "string" ? tool : tool?.id);

/**
 * Properties for the dataset run-prompt event: the dataset id, the model, its
 * settings (kept by RunPrompt in config.run_prompt_config under snake_case
 * names), the response format type, the selected tool ids, and the number and
 * total text length of the prompt messages (not the messages themselves).
 */
export const runPromptEventFields = (datasetId, submitObject) => {
  const config = submitObject?.config ?? {};
  const settings = config.run_prompt_config ?? {};
  const setting = (snakeName, camelName) =>
    settings[snakeName] ??
    settings[camelName] ??
    config[snakeName] ??
    config[camelName];
  const messages = Array.isArray(config.messages) ? config.messages : [];
  return {
    id: datasetId,
    concurrency: config.concurrency,
    prompt_message_count: messages.length,
    prompt_length: messages.reduce(
      (total, message) => total + messageTextLength(message?.content),
      0,
    ),
    tools_config: {
      toolChoice: config.tool_choice ?? config.toolChoice,
      tools: Array.isArray(config.tools) ? config.tools.map(toolId) : [],
    },
    model_name: config.model,
    model_options: {
      maxTokens: setting("max_tokens", "maxTokens"),
      presencePenalty: setting("presence_penalty", "presencePenalty"),
      frequencyPenalty: setting("frequency_penalty", "frequencyPenalty"),
      temperature: setting("temperature", "temperature"),
      topP: setting("top_p", "topP"),
      ...responseFormatFields(config.response_format ?? config.responseFormat),
    },
  };
};
