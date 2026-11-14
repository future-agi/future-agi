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

/**
 * Properties for the dataset run-prompt event: the dataset id, the model and
 * its options (the request config uses snake_case names), the selected tool ids, and the number and total text length of
 * the prompt messages (not the messages themselves).
 */
export const runPromptEventFields = (datasetId, submitObject) => {
  const config = submitObject?.config ?? {};
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
      tools: config.tools?.map((tool) => tool.id) || [],
    },
    model_name: config.model,
    model_options: {
      maxTokens: config.max_tokens ?? config.maxTokens,
      presencePenalty: config.presence_penalty ?? config.presencePenalty,
      temperature: config.temperature,
      topP: config.top_p ?? config.topP,
      responseFormat: config.response_format ?? config.responseFormat,
    },
  };
};
