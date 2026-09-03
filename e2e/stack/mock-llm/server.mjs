// OpenAI-compatible deterministic mock. Reply is a pure function of the last
// user message so specs can assert exact output end-to-end.
import { createServer } from "node:http";

const PORT = process.env.PORT || 8080;
const MODELS = ["gpt-4o-mini", "gpt-4o", "text-embedding-3-small"];
// Self-hosted EE exchanges its license for a short-lived service token before
// any managed-AI call (ee/licensing/activation_client.py). Unstubbed, that
// exchange goes to https://api.futureagi.com and fails closed here, surfacing
// as ACTIVATION_FAILED -> the user-facing "Evaluation failed" on every agent
// eval. The token we hand back is the gateway's own internal key, so the
// managed lane lands on the same agentcc-gateway -> mock-llm path the OSS
// direct-provider lane uses and both modes stay deterministic.
const GATEWAY_URL = process.env.ACTIVATION_GATEWAY_URL ?? "http://agentcc-gateway:8080";
const GATEWAY_KEY =
  process.env.AGENTCC_INTERNAL_API_KEY ?? "local-dev-only-shared-secret-replace-me";

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
    if (path === "/v1/self-hosted/activations") {
      // `scope` must not be "oss": call_managed_service rejects that as
      // NO_ENTERPRISE_LICENSE before it ever reaches the gateway.
      return json(res, 200, {
        access_token: GATEWAY_KEY,
        gateway_url: GATEWAY_URL,
        expires_in: 3600,
        allowed_services: ["chat", "embeddings"],
        allowed_models: MODELS,
        scope: "enterprise",
      });
    }
    if (path === "/telemetry/register/") {
      // Deployment telemetry registers even when it is switched off:
      // FUTURE_AGI_TELEMETRY_DISABLED downgrades the payload to instance id +
      // version and stops the heartbeats, but sender.py still POSTs that one
      // minimal registration — and the receiver announces every registration
      // in a real Slack channel. The suite fabricates a tenant per worker per
      // run, so the only way to stop announcing throwaway users is to keep the
      // call inside the stack. 200 with no signing secret: the minimal kind
      // never asks for one.
      return json(res, 200, {});
    }
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
