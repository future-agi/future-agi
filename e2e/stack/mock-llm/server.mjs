// OpenAI-compatible deterministic mock. Reply is a pure function of the last
// user message so specs can assert exact output end-to-end.
import { createHmac } from "node:crypto";
import { readFileSync } from "node:fs";
import { createServer } from "node:http";
import { pathToFileURL } from "node:url";
import { nativeReply, usageFields, PNG } from "./wire.mjs";

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
const SCOPED = /^\/s\/([A-Za-z0-9_.-]{1,80})\/([^/]+)(\/.*)$/;
const LISTED = /^\/__requests\/([A-Za-z0-9_.-]{1,80})$/;
const recorded = new Map();
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
      ((key === "lat" && n <= 120000) ||
        (key === "status" && n >= 400 && n <= 599) ||
        (key === "failures" && n <= 100) ||
        (["thinking", "image"].includes(key) && n === 1) ||
        (key === "usage" &&
          ["full", "absent", "input", "output", "zero"].includes(value)) ||
        (key === "cost" && /^\d{1,6}(?:\.\d{1,9})?$/.test(value)) ||
        (key === "served" &&
          value.length <= 160 &&
          /^[A-Za-z0-9_.-]+(?:\/[A-Za-z0-9_.-]+)*$/.test(value)));
    if (!ok) return { ...scoped, invalid: directive };
    seen.add(key);
    if (key === "lat") scoped.latencyMs = n;
    else if (["status", "failures", "thinking", "image"].includes(key))
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

// Import-safe for socket-free unit tests. The Compose command below still starts
// the same listener; the new serving contract is text-only transport coverage.
export function handleRequest(req, res) {
  const scoped = parseScoped(req.url.split("?")[0]);
  const path = scoped ? scoped.inner : req.url.split("?")[0];
  const serving = path.startsWith("/model/v1/");
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
    raw += c;
  });
  req.on("error", (err) => {
    console.error(`request aborted: ${err.message}`);
    res.destroy();
  });
  req.on("end", () => {
    if (tooLarge) return;
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
    if (!recorded.has(scoped.scenario)) recorded.set(scoped.scenario, []);
    recorded.get(scoped.scenario).push({
      at: new Date().toISOString(),
      method: req.method,
      path: req.url,
      headers: req.headers,
      body: parseRecorded(raw),
    });
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
    const content = reply(body.messages);
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
    return json(res, 200, {
      id: "chatcmpl-e2e",
      object: "chat.completion",
      model,
      choices: [
        {
          index: 0,
          message: {
            role: "assistant",
            content,
            ...(images ? { images } : {}),
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
