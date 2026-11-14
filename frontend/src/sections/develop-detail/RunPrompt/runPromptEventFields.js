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
 * its options, the selected tool ids, and the number and total text length of
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
      toolChoice: config.toolChoice,
      tools: config.tools?.map((tool) => tool.id) || [],
    },
    model_name: config.model,
    model_options: {
      maxTokens: config.maxTokens,
      presencePenalty: config.presencePenalty,
      temperature: config.temperature,
      topP: config.topP,
      responseFormat: config.responseFormat,
    },
  };
};
