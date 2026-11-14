// OpenAI-compatible deterministic mock. Reply is a pure function of the last
// user message so specs can assert exact output end-to-end.
import { createServer } from "node:http";
import { pathToFileURL } from "node:url";

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

// Typed fixtures are intentionally not a model: pass/fail/refusal depends only
// on an explicit marker in synthetic state. No real provider is contacted.
const decisionModels = new Set([
  "gpt-6-luna", "openai/gpt-6-luna-decisions",
  "jev-latest", "jev-1.13.0", "jev-1.14.0",
  "typesafe/jev-1.13", "typesafe/jev-1.14", "~typesafe/jev-latest",
  "pplx-decider-v1.2", "pplx-decider-flash-v1.2",
]);

function decisions(body, nativeOpenAI) {
  const input = nativeOpenAI ? body.input : body.state;
  const questions = body.questions;
  if (!decisionModels.has(body.model) || input === undefined ||
      (nativeOpenAI ? !Array.isArray(questions) :
        !questions || typeof questions !== "object" || Array.isArray(questions))) return null;
  const entries = nativeOpenAI ? questions.map((q) => [q?.name, q]) : Object.entries(questions);
  if (!entries.length || entries.length > 400 || new Set(entries.map(([id]) => id)).size !== entries.length) return null;
  const state = typeof input === "string" ? input : JSON.stringify(input);
  const passed = state.includes("e2e-decision:pass");
  const refused = state.includes("e2e-decision:refuse");
  const answers = [];
  for (const [id, q] of entries) {
    if (typeof id !== "string" || !id || !q || typeof q !== "object") return null;
    const kind = q.type;
    if (!(nativeOpenAI ? ["predicate", "choice", "score"] : ["noul", "choice", "score"]).includes(kind)) return null;
    let answer;
    if (refused) answer = { type: "refusal" };
    else if (kind === "noul" || kind === "predicate") {
      answer = { type: kind, [nativeOpenAI ? "probability" : "noul"]: passed ? 0.9 : 0.1 };
    } else {
      const score = kind === "score";
      const options = nativeOpenAI ? (score ? q.levels : q.choices) : q.criteria;
      if (!options || typeof options !== "object" || ((nativeOpenAI || score) && !Array.isArray(options))) return null;
      if (nativeOpenAI && options.some((option) => !option || typeof option !== "object")) return null;
      const labels = nativeOpenAI ? options.map((option, index) => score ? String(index) : option.value)
        : score ? options.map((_, index) => String(index)) : Object.keys(options);
      if (!labels.length || labels.length > 255 || (score && (labels.length < 2 || labels.length > 10)) ||
          !labels.every((label) => typeof label === "string") || new Set(labels).size !== labels.length) return null;
      const chosen = passed ? labels.length - 1 : 0;
      const distribution = Object.fromEntries(labels.map((label, index) => [label, index === chosen ? 1 : 0]));
      answer = { type: kind, confidence: 1 };
      if (score) {
        answer.score = chosen;
        if (!nativeOpenAI) answer.legend = Object.fromEntries(options.map((level, index) => [String(index), level]));
      } else answer.choice = labels[chosen];
      answer.probabilities = nativeOpenAI ? labels.map((label, index) => ({
        value: score ? index : label, ...(score ? { label: options[index].label } : {}),
        probability: distribution[label],
      })) : distribution;
    }
    answers.push([id, { ...answer, ...(nativeOpenAI ? { name: id } : {}) }]);
  }
  return {
    model: body.model === "~typesafe/jev-latest" ? "typesafe/jev-1.14" : body.model,
    answers: nativeOpenAI ? answers.map(([, answer]) => answer) : Object.fromEntries(answers),
    usage: { input_tokens: 37, output_tokens: 0 },
  };
}

// Import-safe for socket-free unit tests. The Compose command below still starts
// the same listener; the new serving contract is text-only transport coverage.
export function handleRequest(req, res) {
  const path = req.url.split("?")[0];
  const serving = path.startsWith("/model/v1/");
  const decision = ["/v1/systemone", "/v1/decisions", "/api/alpha/decisions"].includes(path);
  let raw = "";
  let bytes = 0;
  let tooLarge = false;
  req.on("data", (c) => {
    if (tooLarge) return;
    bytes += Buffer.byteLength(c);
    if ((serving || decision) && bytes > 262144) {
      tooLarge = true;
      raw = "";
      return json(res, 413, { error: { message: "serving body exceeds 256 KiB" } });
    }
    raw += c;
  });
  req.on("error", (err) => {
    console.error(`request aborted: ${err.message}`);
    res.destroy();
  });
  req.on("end", () => {
    if (tooLarge) return;
    if ((path === "/model/v1/models" && req.method !== "GET") ||
        (path === "/model/v1/embed" && req.method !== "POST")) {
      return json(res, 405, { error: { message: "method not allowed" } });
    }
    let body;
    try {
      body = raw ? JSON.parse(raw) : {};
    } catch {
      body = null;
    }
    // Rejects bad syntax and valid-but-non-object JSON (`null`, `42`, `"x"`)
    // alike: the route handlers read properties off `body` unguarded.
    if (typeof body !== "object" || body === null || (serving && Array.isArray(body))) {
      return json(res, 400, {
        error: { message: "invalid JSON body", type: "invalid_request_error" },
      });
    }
    if (decision) {
      if (req.method !== "POST") return json(res, 405, { error: { message: "method not allowed" } });
      const nativeOpenAI = path === "/v1/decisions" && Array.isArray(body.questions);
      const connection = path === "/api/alpha/decisions" ? "openrouter"
        : path === "/v1/systemone" ? "typesafe" : nativeOpenAI ? "openai" : "perplexity";
      const auth = req.headers?.authorization;
      if (auth !== "Bearer e2e-mock" && auth !== `Bearer e2e-decision-${connection}-key`) {
        return json(res, 401, { error: { message: "synthetic decision credential required" } });
      }
      // The gateway's OpenRouter policy: one model (no "models"/"route"), same-model
      // host failover allowed, and every parameter supported.
      if (connection === "openrouter" && (body.provider?.allow_fallbacks !== true || body.provider?.require_parameters !== true || body.models || body.route)) {
        return json(res, 400, { error: { message: "gateway decision routing policy required" } });
      }
      const response = decisions(body, nativeOpenAI);
      if (!response) return json(res, 400, { error: { message: "invalid synthetic decision request" } });
      return json(res, 200, response);
    }
    if (path === "/model/v1/models") {
      return json(res, 200, { models: ["text_embedding"] });
    }
    if (path === "/model/v1/embed") {
      const inputs = Array.isArray(body.text) ? body.text : [body.text];
      // tracer/queries/eval_clustering.py _EMBED_BATCH_SIZE=64; the real
      // ModelServingClient expects one vector per input; this mock uses eight dimensions.
      if (Object.keys(body).sort().join(",") !== "input_type,text" ||
          body.input_type !== "text" || inputs.length < 1 || inputs.length > 64 ||
          !inputs.every(text => typeof text === "string" && text.trim().length > 0)) {
        return json(res, 400, { error: { message: "expected input_type=text and 1..64 nonempty texts" } });
      }
      return json(res, 200, { embeddings: inputs.map(() => Array(8).fill(0.125)) });
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
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  createServer(handleRequest).listen(PORT, () => console.log(`mock-llm on :${PORT}`));
}
