// Synthetic wire envelopes only. No SDK, credential lookup or outbound request.
export const PNG =
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aB1sAAAAASUVORK5CYII=";
const wav = Buffer.alloc(46);
wav.write("RIFF");
wav.writeUInt32LE(38, 4);
wav.write("WAVEfmt ", 8);
wav.writeUInt32LE(16, 16);
wav.writeUInt16LE(1, 20);
wav.writeUInt16LE(1, 22);
wav.writeUInt32LE(8000, 24);
wav.writeUInt32LE(16000, 28);
wav.writeUInt16LE(2, 32);
wav.writeUInt16LE(16, 34);
wav.write("data", 36);
wav.writeUInt32LE(2, 40);
export const WAV = wav.toString("base64");

export function usageFields(kind, mode = "full", thinking = false, cost) {
  if (mode === "absent") return {};
  const names =
    kind === "anthropic"
      ? ["input_tokens", "output_tokens"]
      : kind === "gemini"
        ? ["promptTokenCount", "candidatesTokenCount"]
        : ["prompt_tokens", "completion_tokens"];
  const usage = {};
  if (mode !== "output") usage[names[0]] = mode === "zero" ? 0 : 7;
  if (mode !== "input") usage[names[1]] = mode === "zero" || thinking ? 0 : 7;
  if (kind === "gemini" && thinking) usage.thoughtsTokenCount = 7;
  if (["full", "zero"].includes(mode) && kind !== "anthropic")
    usage[kind === "gemini" ? "totalTokenCount" : "total_tokens"] =
      mode === "zero" ? 0 : 14;
  if (cost !== undefined) usage.cost = cost;
  return { [kind === "gemini" ? "usageMetadata" : "usage"]: usage };
}

const json = (res, body) => {
  res.writeHead(200, { "Content-Type": "application/json" });
  res.end(JSON.stringify(body));
};
const start = (res) =>
  res.writeHead(200, {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache",
  });
const event = (res, type, body) =>
  res.write(`event: ${type}\ndata: ${JSON.stringify({ type, ...body })}\n\n`);

export function nativeReply(req, res, path, body, options = {}) {
  const { usage: mode, thinking, image, cost, served } = options;
  if (path === "/v1/messages") {
    const model = served ?? body.model ?? "claude-fixture";
    const text = `echo: ${JSON.stringify(body.messages?.at(-1)?.content ?? "")}`;
    const usage = usageFields("anthropic", mode, false, cost);
    const message = {
      id: "msg-e2e",
      type: "message",
      role: "assistant",
      model,
      content: [{ type: "text", text }],
      stop_reason: "end_turn",
      stop_sequence: null,
      ...usage,
    };
    if (!body.stream) {
      json(res, message);
      return true;
    }
    start(res);
    const initial = usage.usage ? { usage: { ...usage.usage } } : {};
    if (initial.usage && "output_tokens" in initial.usage)
      initial.usage.output_tokens = 0;
    event(res, "message_start", {
      message: { ...message, content: [], stop_reason: null, ...initial },
    });
    event(res, "content_block_start", {
      index: 0,
      content_block: { type: "text", text: "" },
    });
    event(res, "content_block_delta", {
      index: 0,
      delta: { type: "text_delta", text },
    });
    event(res, "content_block_stop", { index: 0 });
    const terminal =
      usage.usage && "output_tokens" in usage.usage
        ? { usage: { output_tokens: usage.usage.output_tokens } }
        : {};
    event(res, "message_delta", {
      delta: { stop_reason: "end_turn", stop_sequence: null },
      ...terminal,
    });
    event(res, "message_stop", {});
    res.end();
    return true;
  }
  const match =
    /^\/(?:v1|v1beta|v1beta1)\/(?:projects\/[^/]+\/locations\/[^/]+\/publishers\/google\/)?models\/([^/:]+):(generateContent|streamGenerateContent|predict)$/.exec(
      path,
    );
  if (!match) return false;
  const [, requested, action] = match;
  if (action === "predict") {
    if (!requested.startsWith("lyria")) return false;
    json(res, {
      predictions: [{ audioContent: WAV, mimeType: "audio/wav" }],
      model: served ?? requested,
      usage: { generations: 1, ...(cost !== undefined ? { cost } : {}) },
    });
    return true;
  }
  const parts = thinking
    ? []
    : image || /image/.test(requested)
      ? [{ inlineData: { mimeType: "image/png", data: PNG } }]
      : [{ text: "echo: gemini fixture" }];
  const response = {
    modelVersion: served ?? requested,
    candidates: [
      {
        index: 0,
        content: { role: "model", parts },
        finishReason: thinking ? "MAX_TOKENS" : "STOP",
      },
    ],
    ...usageFields("gemini", mode, thinking, cost),
  };
  if (action === "streamGenerateContent") {
    start(res);
    res.write(`data: ${JSON.stringify(response)}\n\n`);
    res.end();
  } else json(res, response);
  return true;
}
