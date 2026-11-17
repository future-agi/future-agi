// OpenAI-compatible deterministic mock. Defaults to echoing the last user
// message; per-scenario reply fixtures support exact judge outputs in e2e.
import { createHmac } from "node:crypto";
import { readFileSync } from "node:fs";
import { createServer } from "node:http";
import { StringDecoder } from "node:string_decoder";
import { pathToFileURL } from "node:url";
import { nativeReply, usageFields, PNG, WAV } from "./wire.mjs";

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

// Scoped routes (e2e/lib/upstream.ts, H2): /s/<scenario>/<directives>/<route>
// answers <route> exactly as the unscoped mock does, after recording the request
// under <scenario> ({ at, method, path, headers, body }; GET /__requests/<scenario>
// lists them, oldest first). <directives> is `-` or a comma list of lat=<ms>
// (answer after that delay, at most 120000) and status=<code> (answer that
// status, 400-599, with an OpenAI error body instead of the route). Recorded in
// memory for the life of the container; scenarios are minted per test.
//
// Three directives shape a non-stream /v1/chat/completions answer (other
// routes ignore them; at most one of the three per request):
// - refusal=1: the assistant message is a model refusal, content null and
//   refusal set to REFUSAL below. refusal=echo: the same, refusal set to the
//   usual echo of the last user message (so a check's own pattern can match).
// - audio=transcript: content null and audio { id, data (a tiny WAV, base64),
//   expires_at, transcript }, the transcript being the usual echo of the last
//   user message. audio=bare: the same audio without transcript.
// - reply=<name>: content is the exact UTF-8 body registered by
//   POST /__replies/<scenario>/<name> (raw text, not JSON; 200 { stored: true }).
//   Scenario and name are [A-Za-z0-9_.-]{1,80}. Missing names answer
//   404 { error: "reply not registered" }, never echo.
// A stream request with any of these answers 400 (non-stream only). usage= applies.
// POST /__replies/<scenario> replaces its ordered JSON match list:
// [{ system_contains?, user_contains?, body }]. body is a string; at least one
// selector is required, each a string of >=12 characters; no other keys. All
// supplied selectors must occur in the concatenated text of that role's messages
// (string content or text blocks). Success: 200 { stored: true }; invalid: 400.
// Rules apply only to non-stream /s/<scenario>/-/v1/chat/completions and unscoped
// /v1/chat/completions. Unscoped calls try scenarios in first match-list registration
// order, then rules in list order, and record a hit under the matching scenario.
// Replacing a list keeps its position; no match leaves the usual echo unchanged.
// DELETE /__replies/<scenario> clears names and rules (200 { cleared: true }),
// not request logs; re-registering after DELETE starts a new position. All state
// is in memory; registrations share the existing 256 KiB request-body limit.
const REFUSAL = "I can't help with that request.";
const AUDIO_ID = "audio-e2e";
const AUDIO_EXPIRES_AT = 4102444800; // 2100-01-01T00:00:00Z: never expired
const EXCLUSIVE = ["refusal", "audio", "reply"];
const SCOPED = /^\/s\/([A-Za-z0-9_.-]{1,80})\/([^/]+)(\/.*)$/;
const LISTED = /^\/__requests\/([A-Za-z0-9_.-]{1,80})$/;
const REPLIES =
  /^\/__replies\/([A-Za-z0-9_.-]{1,80})(?:\/([A-Za-z0-9_.-]{1,80}))?$/;
const recorded = new Map();
const namedReplies = new Map();
const replyRules = new Map();
// Value-free global counter for default-off proofs. Counts at arrival, before
// parsing/serving (including failed and scoped POSTs); never reset by a reader.
let inferenceRequests = 0;
const censusCounts = new Map();
const CENSUS_PROBE = /^e2e-census-[a-f0-9]{24}$/;

// Loopback-forwarder probes (e2e/lib/forward-probe.mjs), the same route in mock-llm,
// webhook-sink and mock-slack (keep the three identical): GET /__probe/<key id>/<nonce>
// records the nonce and answers { answer: HMAC-SHA256(key, nonce) } (hex), the key being
// the one the harness planted inside this container for that inspection (docker exec
// over stdin, at /tmp/fwdprobe-keys/<key id>; it never crosses the network), so only
// this container can answer a probe sent through a runner's host forwarder; no
// well-formed key, no answer. GET /__probed/<nonce> answers how often it arrived (read
// from inside the container). Apart from every flow route; bounded.
const PROBE_KEYS = "/tmp/fwdprobe-keys/";
const PROBE = /^\/__probe\/(fwdprobe-[0-9a-f]{32})\/([A-Za-z0-9_.-]{1,80})$/;
const PROBED = /^\/__probed\/([A-Za-z0-9_.-]{1,80})$/;
const probeArrivals = new Map();

/** [status, body] for a forwarder probe route, or null for every other request. */
function probeRoute(method, path) {
  const probe = method === "GET" && PROBE.exec(path);
  if (probe) {
    const [, keyId, nonce] = probe;
    if (!probeArrivals.has(nonce) && probeArrivals.size >= 4096)
      probeArrivals.delete(probeArrivals.keys().next().value);
    probeArrivals.set(nonce, (probeArrivals.get(nonce) ?? 0) + 1);
    let key = "";
    try {
      key = readFileSync(PROBE_KEYS + keyId, "utf8");
    } catch {
      key = "";
    }
    if (!/^[0-9a-f]{64}$/.test(key))
      return [404, { ok: false, error: "no probe key" }];
    const hmac = createHmac("sha256", Buffer.from(key, "hex"));
    return [200, { answer: hmac.update(nonce).digest("hex") }];
  }
  const probed = method === "GET" && PROBED.exec(path);
  return probed ? [200, { count: probeArrivals.get(probed[1]) ?? 0 }] : null;
}

export function parseScoped(path) {
  const match = SCOPED.exec(path);
  if (!match) return null;
  const [, scenario, directives, inner] = match;
  const scoped = { scenario, inner, latencyMs: 0, status: 0 };
  if (directives === "-") return scoped;
  const seen = new Set();
  for (const directive of directives.split(",")) {
    const [key, encoded, ...extra] = directive.split("=");
    let value = encoded;
    if (key === "served") {
      try {
        value = decodeURIComponent(encoded);
      } catch {
        return { ...scoped, invalid: directive };
      }
    }
    const n = /^\d{1,6}$/.test(value ?? "") ? Number(value) : NaN;
    const ok =
      !extra.length &&
      !seen.has(key) &&
      !(EXCLUSIVE.includes(key) && EXCLUSIVE.some((other) => seen.has(other))) &&
      ((key === "lat" && n <= 120000) ||
        (key === "status" && n >= 400 && n <= 599) ||
        (key === "failures" && n <= 100) ||
        (["thinking", "image", "refusal"].includes(key) && n === 1) ||
        (key === "refusal" && value === "echo") ||
        (key === "audio" && ["transcript", "bare"].includes(value)) ||
        (key === "reply" && /^[A-Za-z0-9_.-]{1,80}$/.test(value ?? "")) ||
        (key === "usage" &&
          ["full", "absent", "input", "output", "zero"].includes(value)) ||
        (key === "cost" && /^\d{1,6}(?:\.\d{1,9})?$/.test(value)) ||
        (key === "served" &&
          value.length <= 160 &&
          /^[A-Za-z0-9_.-]+(?:\/[A-Za-z0-9_.-]+)*$/.test(value)));
    if (!ok) return { ...scoped, invalid: directive };
    seen.add(key);
    if (key === "lat") scoped.latencyMs = n;
    else if (key === "refusal" && value === "echo") scoped.refusal = value;
    else if (
      ["status", "failures", "thinking", "image", "refusal"].includes(key)
    )
      scoped[key] = n;
    else scoped[key] = key === "cost" ? Number(value) : value;
  }
  return scoped;
}

const parseRecorded = (raw) => {
  try {
    return raw ? JSON.parse(raw) : null;
  } catch {
    return raw;
  }
};

function recordRequest(scenario, req, raw) {
  if (!recorded.has(scenario)) recorded.set(scenario, []);
  recorded.get(scenario).push({
    at: new Date().toISOString(),
    method: req.method,
    path: req.url,
    headers: req.headers,
    body: parseRecorded(raw),
  });
}

function registerReplies(req, res, match, raw) {
  const [, scenario, name] = match;
  if (req.method === "DELETE" && !name) {
    namedReplies.delete(scenario);
    replyRules.delete(scenario);
    return json(res, 200, { cleared: true });
  }
  if (req.method !== "POST")
    return json(res, 405, { error: { message: "method not allowed" } });
  if (name) {
    if (!namedReplies.has(scenario)) namedReplies.set(scenario, new Map());
    namedReplies.get(scenario).set(name, raw);
  } else {
    const rules = parseRecorded(raw);
    const selectors = ["system_contains", "user_contains"];
    if (
      !Array.isArray(rules) ||
      !rules.every(
        (rule) =>
          rule !== null &&
          typeof rule === "object" &&
          !Array.isArray(rule) &&
          typeof rule.body === "string" &&
          Object.keys(rule).every((key) =>
            ["body", ...selectors].includes(key),
          ) &&
          selectors.some((key) => Object.hasOwn(rule, key)) &&
          selectors.every(
            (key) =>
              !Object.hasOwn(rule, key) ||
              (typeof rule[key] === "string" && rule[key].length >= 12),
          ),
      )
    )
      return json(res, 400, { error: "invalid reply rules" });
    replyRules.set(scenario, rules);
  }
  return json(res, 200, { stored: true });
}

function matchedReply(messages, scenario) {
  const text = (role) =>
    (Array.isArray(messages) ? messages : [])
      .filter((message) => message?.role === role)
      .map((message) =>
        typeof message.content === "string"
          ? message.content
          : Array.isArray(message.content)
            ? message.content
                .filter(
                  (block) => block?.type === "text" && typeof block.text === "string",
                )
                .map((block) => block.text)
                .join("")
            : "",
      )
      .join("");
  const system = text("system");
  const user = text("user");
  const candidates = scenario
    ? [[scenario, replyRules.get(scenario) ?? []]]
    : replyRules;
  for (const [scope, rules] of candidates) {
    const rule = rules.find(
      (rule) =>
        (rule.system_contains === undefined ||
          system.includes(rule.system_contains)) &&
        (rule.user_contains === undefined || user.includes(rule.user_contains)),
    );
    if (rule) return { scenario: scope, body: rule.body };
  }
}

// Decision fixtures (/v1/systemone, /v1/decisions, /api/alpha/decisions).
// Typed fixtures are intentionally not a model: pass/fail/refusal depends only
// on an explicit marker in synthetic state. No real provider is contacted.
const DECISION_PATHS = ["/v1/systemone", "/v1/decisions", "/api/alpha/decisions"];
const decisionModels = new Set([
  "gpt-6-luna",
  "openai/gpt-6-luna-decisions",
  "jev-latest",
  "jev-1.13.0",
  "jev-1.14.0",
  "typesafe/jev-1.13",
  "typesafe/jev-1.14",
  "~typesafe/jev-latest",
  "pplx-decider-v1.2",
  "pplx-decider-flash-v1.2",
]);

function decisions(body, nativeOpenAI) {
  const input = nativeOpenAI ? body.input : body.state;
  const questions = body.questions;
  if (
    !decisionModels.has(body.model) ||
    input === undefined ||
    (nativeOpenAI
      ? !Array.isArray(questions)
      : !questions || typeof questions !== "object" || Array.isArray(questions))
  )
    return null;
  const entries = nativeOpenAI
    ? questions.map((q) => [q?.name, q])
    : Object.entries(questions);
  if (
    !entries.length ||
    entries.length > 400 ||
    new Set(entries.map(([id]) => id)).size !== entries.length
  )
    return null;
  const state = typeof input === "string" ? input : JSON.stringify(input);
  const passed = state.includes("e2e-decision:pass");
  const refused = state.includes("e2e-decision:refuse");
  const answers = [];
  for (const [id, q] of entries) {
    if (typeof id !== "string" || !id || !q || typeof q !== "object")
      return null;
    const kind = q.type;
    if (
      !(
        nativeOpenAI
          ? ["predicate", "choice", "score"]
          : ["noul", "choice", "score"]
      ).includes(kind)
    )
      return null;
    let answer;
    if (refused) answer = { type: "refusal" };
    else if (kind === "noul" || kind === "predicate") {
      answer = {
        type: kind,
        [nativeOpenAI ? "probability" : "noul"]: passed ? 0.9 : 0.1,
      };
    } else {
      const score = kind === "score";
      const options = nativeOpenAI
        ? score
          ? q.levels
          : q.choices
        : q.criteria;
      if (
        !options ||
        typeof options !== "object" ||
        ((nativeOpenAI || score) && !Array.isArray(options))
      )
        return null;
      if (
        nativeOpenAI &&
        options.some((option) => !option || typeof option !== "object")
      )
        return null;
      const labels = nativeOpenAI
        ? options.map((option, index) => (score ? String(index) : option.value))
        : score
          ? options.map((_, index) => String(index))
          : Object.keys(options);
      if (
        !labels.length ||
        labels.length > 255 ||
        (score && (labels.length < 2 || labels.length > 10)) ||
        !labels.every((label) => typeof label === "string") ||
        new Set(labels).size !== labels.length
      )
        return null;
      const chosen = passed ? labels.length - 1 : 0;
      const distribution = Object.fromEntries(
        labels.map((label, index) => [label, index === chosen ? 1 : 0]),
      );
      answer = { type: kind, confidence: 1 };
      if (score) {
        answer.score = chosen;
        if (!nativeOpenAI)
          answer.legend = Object.fromEntries(
            options.map((level, index) => [String(index), level]),
          );
      } else answer.choice = labels[chosen];
      answer.probabilities = nativeOpenAI
        ? labels.map((label, index) => ({
            value: score ? index : label,
            ...(score ? { label: options[index].label } : {}),
            probability: distribution[label],
          }))
        : distribution;
    }
    answers.push([id, { ...answer, ...(nativeOpenAI ? { name: id } : {}) }]);
  }
  return {
    model: body.model === "~typesafe/jev-latest" ? "typesafe/jev-1.14" : body.model,
    answers: nativeOpenAI
      ? answers.map(([, answer]) => answer)
      : Object.fromEntries(answers),
    usage: { input_tokens: 37, output_tokens: 0 },
  };
}

/** Answers a decision route; false for every other path. */
function decisionReply(req, res, path, body) {
  if (!DECISION_PATHS.includes(path)) return false;
  if (req.method !== "POST") {
    json(res, 405, { error: { message: "method not allowed" } });
    return true;
  }
  const nativeOpenAI = path === "/v1/decisions" && Array.isArray(body.questions);
  const connection =
    path === "/api/alpha/decisions"
      ? "openrouter"
      : path === "/v1/systemone"
        ? "typesafe"
        : nativeOpenAI
          ? "openai"
          : "perplexity";
  const auth = req.headers?.authorization;
  if (auth !== "Bearer e2e-mock" && auth !== `Bearer e2e-decision-${connection}-key`) {
    json(res, 401, {
      error: { message: "synthetic decision credential required" },
    });
    return true;
  }
  // The gateway's OpenRouter policy: one model (no "models"/"route"), same-model
  // host failover allowed, and every parameter supported.
  if (
    connection === "openrouter" &&
    (body.provider?.allow_fallbacks !== true ||
      body.provider?.require_parameters !== true ||
      body.models ||
      body.route)
  ) {
    json(res, 400, {
      error: { message: "gateway decision routing policy required" },
    });
    return true;
  }
  const response = decisions(body, nativeOpenAI);
  if (!response)
    json(res, 400, { error: { message: "invalid synthetic decision request" } });
  else json(res, 200, response);
  return true;
}

// Import-safe for socket-free unit tests. The Compose command below still starts
// the same listener; the new serving contract is text-only transport coverage.
export function handleRequest(req, res) {
  const scoped = parseScoped(req.url.split("?")[0]);
  const path = scoped ? scoped.inner : req.url.split("?")[0];
  const serving = path.startsWith("/model/v1/");
  const registration = !scoped && REPLIES.exec(path);
  const decoder = registration ? new StringDecoder("utf8") : null;
  if (req.method === "POST" && !path.startsWith("/__")) inferenceRequests++;
  let raw = "";
  let bytes = 0;
  let tooLarge = false;
  req.on("data", (c) => {
    if (tooLarge) return;
    bytes += Buffer.byteLength(c);
    if (bytes > 262144) {
      tooLarge = true;
      raw = "";
      return json(res, 413, {
        error: { message: "serving body exceeds 256 KiB" },
      });
    }
    raw += decoder ? decoder.write(c) : c;
  });
  req.on("error", (err) => {
    console.error(`request aborted: ${err.message}`);
    res.destroy();
  });
  req.on("end", () => {
    if (tooLarge) return;
    if (decoder) raw += decoder.end();
    if (registration) return registerReplies(req, res, registration, raw);
    // Per-W00 marker travels in the normal user message, through the real
    // gateway serialization. Only counters persist, never message bodies.
    if (req.method === "POST") {
      const messages = parseRecorded(raw)?.messages;
      if (Array.isArray(messages)) {
        const markers = new Set(
          messages
            .filter(
              (m) =>
                m?.role === "user" &&
                typeof m.content === "string" &&
                CENSUS_PROBE.test(m.content),
            )
            .map((m) => m.content),
        );
        for (const marker of markers)
          censusCounts.set(marker, (censusCounts.get(marker) ?? 0) + 1);
      }
    }
    if (!scoped) {
      const routed = probeRoute(req.method, path);
      if (routed) return json(res, ...routed);
      const marker = path.startsWith("/__counts/")
        ? path.slice("/__counts/".length)
        : "";
      if (CENSUS_PROBE.test(marker) && req.method === "GET")
        return json(res, 200, {
          inferenceRequests: censusCounts.get(marker) ?? 0,
        });
      if (path === "/__counts" && req.method === "GET")
        return json(res, 200, { inferenceRequests });
      const listed = LISTED.exec(path);
      if (listed && req.method === "GET") {
        return json(res, 200, {
          scenario: listed[1],
          requests: recorded.get(listed[1]) ?? [],
        });
      }
      return answer(req, res, path, serving, raw);
    }
    if (scoped.invalid) {
      return json(res, 400, {
        error: {
          message: `invalid mock-llm directive ${scoped.invalid}`,
          type: "invalid_request_error",
        },
      });
    }
    recordRequest(scoped.scenario, req, raw);
    const attempt = recorded.get(scoped.scenario).length;
    const status =
      scoped.status || (attempt <= (scoped.failures ?? 0) ? 503 : 0);
    const respond = () =>
      status
        ? json(res, status, {
            error: {
              message: `mock-llm scenario ${scoped.scenario} answers ${status}`,
              type: "server_error",
            },
          })
        : answer(req, res, path, serving, raw, scoped);
    if (scoped.latencyMs) setTimeout(respond, scoped.latencyMs);
    else respond();
  });
}

function answer(req, res, path, serving, raw, options = {}) {
  if (path === "/__health" && req.method === "GET")
    return json(res, 200, { ok: true });
  // MODEL_SERVING_URL points here; the product probes GET /health before
  // embedding (agentic_eval/core/embeddings/serving_client.py _probe). Emulate
  // the real serving app (model_serving/app/main.py `@app.get("/health")`):
  // 200 application/json {"status":"healthy"}.
  if (path === "/health" && req.method === "GET")
    return json(res, 200, { status: "healthy" });
  if (
    req.method !== "POST" &&
    !["/v1/models", "/model/v1/models"].includes(path)
  )
    return json(res, 405, { error: { message: "method not allowed" } });
  if (
    (path === "/model/v1/models" && req.method !== "GET") ||
    (path === "/model/v1/embed" && req.method !== "POST")
  ) {
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
  if (typeof body !== "object" || body === null || Array.isArray(body)) {
    return json(res, 400, {
      error: { message: "invalid JSON body", type: "invalid_request_error" },
    });
  }
  if (decisionReply(req, res, path, body)) return;
  if (nativeReply(req, res, path, body, options)) return;
  if (path === "/model/v1/models") {
    return json(res, 200, { models: ["text_embedding"] });
  }
  if (path === "/model/v1/embed") {
    const inputs = Array.isArray(body.text) ? body.text : [body.text];
    // tracer/queries/eval_clustering.py _EMBED_BATCH_SIZE=64; the real
    // ModelServingClient expects one vector per input; this mock uses eight dimensions.
    if (
      Object.keys(body).sort().join(",") !== "input_type,text" ||
      body.input_type !== "text" ||
      inputs.length < 1 ||
      inputs.length > 64 ||
      !inputs.every(
        (text) => typeof text === "string" && text.trim().length > 0,
      )
    ) {
      return json(res, 400, {
        error: { message: "expected input_type=text and 1..64 nonempty texts" },
      });
    }
    return json(res, 200, {
      embeddings: inputs.map(() => Array(8).fill(0.125)),
    });
  }
  if (path === "/v1/models") {
    return json(res, 200, {
      object: "list",
      data: MODELS.map((id) => ({ id, object: "model", owned_by: "e2e" })),
    });
  }
  if (path === "/v1/chat/completions") {
    let content = reply(body.messages);
    const model = options.served ?? body.model ?? "gpt-4o-mini";
    const usage = usageFields("openai", options.usage, false, options.cost);
    const images = options.image
      ? [
          {
            type: "image_url",
            image_url: { url: `data:image/png;base64,${PNG}` },
          },
        ]
      : undefined;
    const shaped = options.refusal
      ? `refusal=${options.refusal}`
      : options.audio
        ? `audio=${options.audio}`
        : options.reply
          ? `reply=${options.reply}`
          : "";
    if (body.stream && shaped) {
      return json(res, 400, {
        error: {
          message: `mock-llm directive ${shaped} answers non-stream chat completions only`,
          type: "invalid_request_error",
        },
      });
    }
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
      if (images) chunk({ images });
      chunk({}, "stop");
      if (usage.usage)
        res.write(
          `data: ${JSON.stringify({ id: "chatcmpl-e2e", object: "chat.completion.chunk", model, choices: [], ...usage })}\n\n`,
        );
      res.write("data: [DONE]\n\n");
      return res.end();
    }
    if (options.reply) {
      const names = namedReplies.get(options.scenario);
      if (!names?.has(options.reply))
        return json(res, 404, { error: "reply not registered" });
      content = names.get(options.reply);
    } else if (
      !options.scenario ||
      req.url.split("?")[0] === `/s/${options.scenario}/-/v1/chat/completions`
    ) {
      const matched = matchedReply(body.messages, options.scenario);
      if (matched) {
        content = matched.body;
        if (!options.scenario) recordRequest(matched.scenario, req, raw);
      }
    }
    return json(res, 200, {
      id: "chatcmpl-e2e",
      object: "chat.completion",
      model,
      choices: [
        {
          index: 0,
          message: {
            role: "assistant",
            content: options.refusal || options.audio ? null : content,
            ...(images ? { images } : {}),
            ...(options.refusal
              ? { refusal: options.refusal === "echo" ? content : REFUSAL }
              : {}),
            ...(options.audio
              ? {
                  audio: {
                    id: AUDIO_ID,
                    data: WAV,
                    expires_at: AUDIO_EXPIRES_AT,
                    ...(options.audio === "transcript"
                      ? { transcript: content }
                      : {}),
                  },
                }
              : {}),
          },
          finish_reason: "stop",
        },
      ],
      ...usage,
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
}

if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(process.argv[1]).href
) {
  createServer(handleRequest).listen(PORT, () =>
    console.log(`mock-llm on :${PORT}`),
  );
}
