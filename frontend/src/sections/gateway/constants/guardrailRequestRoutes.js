// The routes whose request a Before LLM check reads, and what it reads on
// each (api_contracts/gateway/guardrail-request-routes.json, which the
// gateway's contract test and requestRoutes.test.jsx both read). On every
// route listed, a request a check blocks is
// refused before the model gets it, and a check's mask or redaction reaches
// the model or the request is refused. Where unread is set, a check that
// would block or mask the request cannot read that part of it, so it refuses
// the request whatever its text. A route with a note is served only as the
// note says.
export const REQUEST_CHECK_ROUTES = [
  {
    route: "POST /v1/chat/completions",
    reads: "Every message's text, tool calls and tools",
  },
  {
    route: "POST /v1/completions",
    reads: "The prompt, or each prompt of a list",
    unread: "A prompt of token ids",
  },
  {
    route: "POST /v1/messages",
    reads:
      "The system prompt, every turn's text, documents' and search results' text, tool results, tool calls and tools",
  },
  {
    route: "POST /v1/messages/count_tokens",
    reads: "The prompt whose tokens are counted, as on /v1/messages",
  },
  {
    route: "POST /v1beta/models/{model}:generateContent",
    reads:
      "The system instruction, every content's text, function responses and code execution results, function calls and tools",
  },
  {
    route: "POST /v1beta/models/{model}:streamGenerateContent",
    reads: "As generateContent",
  },
  {
    route: "POST /v1beta/models/{model}:countTokens",
    reads: "The request whose tokens are counted, as generateContent",
  },
  {
    route: "POST /v1beta/models/{model}:embedContent",
    reads: "The content's text parts and the title",
  },
  {
    route: "POST /v1/responses",
    reads:
      "The instructions, the input's text, tool outputs and a stored prompt's variables, streamed, not streamed or in the background",
  },
  {
    route: "POST /v1/embeddings",
    reads: "The input: a string, or each string of a list",
    unread: "Token ids",
  },
  { route: "POST /v1/images/generations", reads: "The prompt" },
  { route: "POST /v1/audio/speech", reads: "The input text" },
  { route: "POST /v1/audio/speech/stream", reads: "The input text" },
  {
    route: "POST /v1/audio/transcriptions",
    reads: "The prompt",
    unread: "The audio file",
  },
  {
    route: "POST /v1/audio/translations",
    reads: "The prompt",
    unread: "The audio file",
  },
  { route: "POST /v1/rerank", reads: "The query and each document" },
  {
    route: "POST /v1/search",
    reads: "The query: a string, or each string of a list",
    note: "No provider in this release serves search: the route answers 501.",
  },
  {
    route: "POST /v1/ocr",
    reads: "Nothing: the input is a document",
    unread:
      "The document (a document or image URL, inline data, or a file at the provider)",
  },
  {
    route: "POST /v1/videos",
    reads: "The prompt, before the job is stored",
    note: "No provider in this release serves video: the route answers 501.",
  },
  { route: "POST /v1/assistants", reads: "The instructions and the tools" },
  {
    route: "POST /v1/assistants/{assistant_id}",
    reads: "The instructions and the tools",
  },
  { route: "POST /v1/threads", reads: "Each message's text" },
  {
    route: "POST /v1/threads/{thread_id}/messages",
    reads: "The message's text",
  },
  {
    route: "POST /v1/threads/{thread_id}/runs",
    reads:
      "The instructions, additional instructions, additional messages' text and the tools",
  },
  {
    route: "POST /v1/threads/runs",
    reads: "The thread's messages' text, the instructions and the tools",
  },
  {
    route: "POST /v1/threads/{thread_id}/runs/{run_id}/submit_tool_outputs",
    reads: "Each tool output",
  },
  {
    route: "POST /v1/vector_stores/{vector_store_id}/search",
    reads: "The query: a string, or each string of a list",
  },
  {
    route: "POST /v1/a2a",
    reads: "Each task's message, as a chat request (also at /a2a)",
  },
  {
    route: "POST /-/batches",
    reads: "Each item, as a chat request run as the batch key's organization",
  },
];

// The APIs this gateway has no route for: no request reaches a model there.
export const REQUEST_CHECK_NOT_SERVED = [
  "POST /v1/moderations",
  "POST /v1/images/edits",
  "POST /v1/images/variations",
];
