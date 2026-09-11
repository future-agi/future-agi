// OpenAI-compatible deterministic mock. Reply is a pure function of the last
// user message so specs can assert exact output end-to-end.
import { createServer } from "node:http";

const PORT = process.env.PORT || 8080;
const MODELS = ["gpt-4o-mini", "gpt-4o", "text-embedding-3-small"];

const reply = (messages) => {
  const last = [...(messages ?? [])].reverse().find((m) => m.role === "user");
  return `echo: ${typeof last?.content === "string" ? last.content : JSON.stringify(last?.content ?? "")}`;
};

const json = (res, code, body) => {
  res.writeHead(code, { "Content-Type": "application/json" });
  res.end(JSON.stringify(body));
};

createServer((req, res) => {
  let raw = "";
  req.on("data", (c) => {
    raw += c;
  });
  req.on("error", (err) => {
    console.error(`request aborted: ${err.message}`);
    res.destroy();
  });
  req.on("end", () => {
    let body;
    try {
      body = raw ? JSON.parse(raw) : {};
    } catch {
      body = null;
    }
    // Rejects bad syntax and valid-but-non-object JSON (`null`, `42`, `"x"`)
    // alike: the route handlers read properties off `body` unguarded.
    if (typeof body !== "object" || body === null) {
      return json(res, 400, {
        error: { message: "invalid JSON body", type: "invalid_request_error" },
      });
    }
    const path = req.url.split("?")[0];
    if (path === "/v1/models") {
      return json(res, 200, {
        object: "list",
        data: MODELS.map((id) => ({ id, object: "model", owned_by: "e2e" })),
      });
    }
    if (path === "/v1/chat/completions") {
      const content = reply(body.messages);
      const model = body.model ?? "gpt-4o-mini";
      if (body.stream) {
        res.writeHead(200, {
          "Content-Type": "text/event-stream",
          "Cache-Control": "no-cache",
        });
        const chunk = (delta, finish = null) =>
          res.write(
            `data: ${JSON.stringify({
              id: "chatcmpl-e2e",
              object: "chat.completion.chunk",
              model,
              choices: [{ index: 0, delta, finish_reason: finish }],
            })}\n\n`,
          );
        chunk({ role: "assistant" });
        // Zero-width split after whitespace: the deltas concatenate back to
        // `content` byte-for-byte, so streamed and non-streamed output match.
        for (const part of content.split(/(?<=\s)/)) chunk({ content: part });
        chunk({}, "stop");
        res.write("data: [DONE]\n\n");
        return res.end();
      }
      return json(res, 200, {
        id: "chatcmpl-e2e",
        object: "chat.completion",
        model,
        choices: [
          {
            index: 0,
            message: { role: "assistant", content },
            finish_reason: "stop",
          },
        ],
        usage: { prompt_tokens: 7, completion_tokens: 7, total_tokens: 14 },
      });
    }
    if (path === "/v1/embeddings") {
      const inputs = Array.isArray(body.input) ? body.input : [body.input];
      return json(res, 200, {
        object: "list",
        model: body.model ?? "text-embedding-3-small",
        data: inputs.map((_, index) => ({
          object: "embedding",
          index,
          embedding: Array(8).fill(0.125),
        })),
        usage: { prompt_tokens: 1, total_tokens: 1 },
      });
    }
    json(res, 404, { error: { message: `no route ${req.url}` } });
  });
}).listen(PORT, () => console.log(`mock-llm on :${PORT}`));
