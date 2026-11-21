// Observed, not synthetic: the span the actual OpenAI Python SDK (traceAI
// instrumentation) produced for one image question, as the trace API returned
// it in TH-2379 acceptance. Keys, values and key order are kept verbatim; the
// flattened attributes are what the trace view parses its chat messages from.
export const openaiImageSpanId = "f5035ec6e4334d39";
export const openaiImageInput =
  "Describe the image the user sends. \n What colour is this square? \n {{gen_ai.input.images.0}}";
export const openaiImageOutput = "A teal square with a darker diagonal band.";
export const openaiImageAttributes = {
  "fi.attachment.refs":
    '[{"attachment_id":"7479f8bc-e3c2-42e3-9d80-d856c124a9ad","direction":"input","message_index":1,"mime_type":"image/png","modality":"image","occurrence_id":"5f3f1b6d-d148-4cc6-bce5-1221374ca520","part_index":1,"purpose":"model_input","role":"user"}]',
  "gen_ai.input.images":
    '["fi-attachment:5f3f1b6d-d148-4cc6-bce5-1221374ca520"]',
  "gen_ai.input.messages":
    '[{"role": "system", "content": "Describe the image the user sends."}, {"role": "user", "content": [{"type": "text", "text": "What colour is this square?"}, {"type": "image_url", "image_url": {"url": "fi-attachment:5f3f1b6d-d148-4cc6-bce5-1221374ca520"}}]}]',
  "gen_ai.input.messages.0.message.content":
    "Describe the image the user sends.",
  "gen_ai.input.messages.0.message.role": "system",
  "gen_ai.input.messages.1.message.contents.0.message_content.text":
    "What colour is this square?",
  "gen_ai.input.messages.1.message.contents.0.message_content.type": "text",
  "gen_ai.input.messages.1.message.contents.1.message_content.image":
    "fi-attachment:5f3f1b6d-d148-4cc6-bce5-1221374ca520",
  "gen_ai.input.messages.1.message.contents.1.message_content.type": "image",
  "gen_ai.input.messages.1.message.role": "user",
  "gen_ai.output.messages.0.message.content":
    "A teal square with a darker diagonal band.",
  "gen_ai.output.messages.0.message.role": "assistant",
  "gen_ai.provider.name": "openai",
  "gen_ai.request.model": "gpt-4o-mini",
  "gen_ai.request.parameters": '{"model": "gpt-4o-mini"}',
  "gen_ai.span.kind": "LLM",
  "gen_ai.tool.definitions": "null",
  "input.mime_type": "text/plain",
  "output.mime_type": "text/plain",
  "fi.attachment.schema_version": 1,
  "gen_ai.usage.input_tokens": 20,
  "gen_ai.usage.output_tokens": 9,
  "gen_ai.usage.total_tokens": 29,
  "input.value":
    "Describe the image the user sends. \n What colour is this square? \n {{gen_ai.input.images.0}}",
  "output.value": "A teal square with a darker diagonal band.",
};
export const openaiImageAttachments = [
  {
    reference: {
      attachment_id: "7479f8bc-e3c2-42e3-9d80-d856c124a9ad",
      occurrence_id: "5f3f1b6d-d148-4cc6-bce5-1221374ca520",
      direction: "input",
      modality: "image",
      mime_type: "image/png",
      purpose: "model_input",
      message_index: 1,
      part_index: 1,
    },
    status: "ready",
    capture_complete: true,
  },
];
