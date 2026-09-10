// OpenAI-compatible deterministic mock. Reply is a pure function of the last
// user message so specs can assert exact output end-to-end.
import { createServer } from "node:http";
import { fileURLToPath } from "node:url";
import nodePath from "node:path";
import zlib from "node:zlib";

const PORT = process.env.PORT || 8080;
const MODELS = ["gpt-4o-mini", "gpt-4o", "text-embedding-3-small"];

// Ring buffer of every /v1/* hit, exposed via GET /__e2e/requests?path=... so
// specs can assert on what the backend actually sent the provider.
const LOG = [];
const LOG_CAP = 200;
const record = (method, path, request) => {
  LOG.push({ ts: Date.now(), method, path, request });
  if (LOG.length > LOG_CAP) LOG.shift();
};

// Pulls the text strictly between two literal markers, trimmed. Returns null
// if either marker is missing so callers can fall back safely.
const between = (text, start, end) => {
  const from = text.indexOf(start);
  if (from === -1) return null;
  const contentStart = from + start.length;
  const to = text.indexOf(end, contentStart);
  if (to === -1) return null;
  return text.slice(contentStart, to).trim();
};

// Stage-aware rules for the Generate/Improve Prompt pipeline
// (futureagi/ee/agenthub/prompt_generate_agent/{prompt_generate,prompts}.py),
// layered on top of the default echo so every other chat call (run_template,
// evals, etc.) is unaffected. Each stage is identified by a marker that's
// unique to that prompt template in prompts.py:
//   - EXECUTE_IMPROVEMENT_PROMPT is the only stage with a <change_plan> tag.
//   - IDENTIFY_CHANGES_PRESERVATION_PROMPT is the only stage with a
//     <user_intention_analysis> tag.
//   - GENERATION_PROMPT is the only stage that opens with "Based on the
//     analysis, create an optimized prompt...".
//   - ANALYSIS_AND_PLANNING_PROMPT is the only stage with a
//     <task_description> tag.
const reply = (messages) => {
  const last = [...(messages ?? [])].reverse().find((m) => m.role === "user");
  const content = typeof last?.content === "string" ? last.content : JSON.stringify(last?.content ?? "");

  // Improve, step 3 (execute-improvement): echo the original prompt back
  // verbatim (so callers can assert template variables survived) plus a
  // fallible marker. No "Variables to add/remove" directives are emitted by
  // step 2 below, so the pipeline's default keeps every original variable
  // allowed — the echoed original_prompt always validates.
  if (content.includes("<change_plan>")) {
    const original = between(content, "<original_prompt>", "</original_prompt>") ?? "";
    return `${original}\n\ne2e-improved`;
  }

  // Improve, step 2 (identify-changes): no variable directive lines, so
  // _compute_allowed_template_variables falls back to preserving every
  // variable found in the original prompt.
  if (content.includes("<user_intention_analysis>")) {
    return "change-plan-ok";
  }

  // Generate, step 2 (generate-initial-prompt): this is the *final* prompt
  // text returned to the caller (no further parsing), so echo the task
  // description straight through with a fallible marker.
  if (content.startsWith("Based on the analysis, create an optimized prompt")) {
    const description = between(content, "Task Description:", "Analysis Results:") ?? "";
    return `e2e-generated-prompt: ${description}`;
  }

  // Generate, step 1 (analyze-prompt-description): the analysis text is fed
  // into step 2 as context but never parsed, so any deterministic echo works.
  if (content.includes("<task_description>")) {
    const description = between(content, "<task_description>", "</task_description>") ?? "";
    return `analysis-ok: ${description}`;
  }

  return `echo: ${content}`;
};

const json = (res, code, body) => {
  res.writeHead(code, { "Content-Type": "application/json" });
  res.end(JSON.stringify(body));
};

// A 1x1 transparent PNG, built (not hand-typed) so a single wrong base64 char
// can't silently corrupt it: signature + IHDR + IDAT(a raw zero pixel) + IEND,
// each chunk CRC32'd for real.
const crc32 = (buf) => zlib.crc32(buf);
const pngChunk = (type, data) => {
  const typeBuf = Buffer.from(type, "ascii");
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length, 0);
  const crcBuf = Buffer.alloc(4);
  crcBuf.writeUInt32BE(crc32(Buffer.concat([typeBuf, data])) >>> 0, 0);
  return Buffer.concat([len, typeBuf, data, crcBuf]);
};
const buildPng1x1 = () => {
  const signature = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(1, 0); // width
  ihdr.writeUInt32BE(1, 4); // height
  ihdr.writeUInt8(8, 8); // bit depth
  ihdr.writeUInt8(6, 9); // color type: RGBA
  ihdr.writeUInt8(0, 10); // compression
  ihdr.writeUInt8(0, 11); // filter
  ihdr.writeUInt8(0, 12); // interlace
  // One scanline: filter byte 0 + one transparent RGBA pixel.
  const raw = Buffer.from([0, 0, 0, 0, 0]);
  const idatData = zlib.deflateSync(raw);
  return Buffer.concat([
    signature,
    pngChunk("IHDR", ihdr),
    pngChunk("IDAT", idatData),
    pngChunk("IEND", Buffer.alloc(0)),
  ]);
};
export const PNG_1X1 = buildPng1x1();
export const PNG_1X1_B64 = PNG_1X1.toString("base64");

// Valid RIFF/WAVE PCM16 mono buffer: `seconds` of silence at 8000 Hz.
export const wavBuffer = (seconds = 1) => {
  const sampleRate = 8000;
  const numChannels = 1;
  const bitsPerSample = 16;
  const numSamples = Math.round(seconds * sampleRate);
  const dataSize = numSamples * numChannels * (bitsPerSample / 8);
  const blockAlign = numChannels * (bitsPerSample / 8);
  const byteRate = sampleRate * blockAlign;
  const buf = Buffer.alloc(44 + dataSize);
  buf.write("RIFF", 0, "ascii");
  buf.writeUInt32LE(36 + dataSize, 4);
  buf.write("WAVE", 8, "ascii");
  buf.write("fmt ", 12, "ascii");
  buf.writeUInt32LE(16, 16); // fmt chunk length
  buf.writeUInt16LE(1, 20); // PCM
  buf.writeUInt16LE(numChannels, 22);
  buf.writeUInt32LE(sampleRate, 24);
  buf.writeUInt32LE(byteRate, 28);
  buf.writeUInt16LE(blockAlign, 32);
  buf.writeUInt16LE(bitsPerSample, 34);
  buf.write("data", 36, "ascii");
  buf.writeUInt32LE(dataSize, 40);
  // Data bytes default to 0 (silence) from Buffer.alloc.
  return buf;
};

// Splits a multipart/form-data body into named parts. Returns null (never
// throws) on anything malformed so the caller can 400 instead of crashing
// the shared, restart:"no" mock process.
const parseMultipart = (buf, contentType) => {
  const match = /boundary=(?:"([^"]+)"|([^;]+))/i.exec(contentType ?? "");
  const boundary = match?.[1] ?? match?.[2];
  if (!boundary) return null;
  const delimiter = `--${boundary}`;
  const text = buf.toString("latin1"); // 1 char = 1 byte, safe for offset math
  const rawParts = text.split(delimiter).slice(1, -1); // drop preamble + trailing --
  const parts = [];
  for (let part of rawParts) {
    if (part.startsWith("\r\n")) part = part.slice(2);
    if (part.endsWith("\r\n")) part = part.slice(0, -2);
    const headerEnd = part.indexOf("\r\n\r\n");
    if (headerEnd === -1) continue;
    const headerText = part.slice(0, headerEnd);
    const bodyText = part.slice(headerEnd + 4);
    const nameMatch = /name="([^"]*)"/i.exec(headerText);
    const filenameMatch = /filename="([^"]*)"/i.exec(headerText);
    parts.push({
      name: nameMatch?.[1],
      filename: filenameMatch?.[1],
      // Re-encode back to raw bytes for a correct byte length / payload.
      buffer: Buffer.from(bodyText, "latin1"),
    });
  }
  return parts;
};

const server = createServer((req, res) => {
  const chunks = [];
  req.on("data", (c) => chunks.push(c));
  req.on("error", (err) => {
    console.error(`request aborted: ${err.message}`);
    res.destroy();
  });
  req.on("end", () => {
    const path = req.url.split("?")[0];
    const contentType = req.headers["content-type"] ?? "";

    if (req.method === "GET" && path === "/__e2e/requests") {
      const url = new URL(req.url, "http://mock-llm");
      const filterPath = url.searchParams.get("path");
      const matches = LOG.filter((entry) => !filterPath || entry.path === filterPath);
      return json(res, 200, matches);
    }

    const buf = Buffer.concat(chunks);

    if (contentType.toLowerCase().startsWith("multipart/form-data")) {
      if (path !== "/v1/audio/transcriptions") {
        return json(res, 404, { error: { message: `no route ${req.url}` } });
      }
      const parts = parseMultipart(buf, contentType);
      if (!parts) {
        return json(res, 400, {
          error: { message: "invalid multipart body", type: "invalid_request_error" },
        });
      }
      const filePart = parts.find((p) => p.name === "file");
      const modelPart = parts.find((p) => p.name === "model");
      if (!filePart || !filePart.filename) {
        return json(res, 400, {
          error: { message: "missing file part", type: "invalid_request_error" },
        });
      }
      const fields = {};
      for (const p of parts) if (p.name && p.name !== "file") fields[p.name] = p.buffer.toString("utf8");
      record(req.method, path, { filename: filePart.filename, bytes: filePart.buffer.length, fields });
      return json(res, 200, {
        text: `echo-transcript: ${filePart.filename} ${filePart.buffer.length}b`,
      });
    }

    let body;
    try {
      body = buf.length ? JSON.parse(buf.toString("utf8")) : {};
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

    if (path.startsWith("/v1/")) record(req.method, path, body);

    if (path === "/v1/models") {
      return json(res, 200, {
        object: "list",
        data: MODELS.map((id) => ({ id, object: "model", owned_by: "e2e" })),
      });
    }
    if (path === "/v1/chat/completions") {
      const content = reply(body.messages);
      const model = body.model ?? "gpt-4o-mini";
      const last = [...(body.messages ?? [])].reverse().find((m) => m.role === "user");
      const lastText = typeof last?.content === "string" ? last.content : JSON.stringify(last?.content ?? "");
      const slow = body.stream && lastText.includes("[[slow]]");
      if (body.stream) {
        res.writeHead(200, {
          "Content-Type": "text/event-stream",
          "Cache-Control": "no-cache",
        });
        const chunk = (delta, finish = null) => {
          if (res.destroyed) return false;
          res.write(
            `data: ${JSON.stringify({
              id: "chatcmpl-e2e",
              object: "chat.completion.chunk",
              model,
              choices: [{ index: 0, delta, finish_reason: finish }],
            })}\n\n`,
          );
          return true;
        };
        const parts = content.split(/(?<=\s)/);
        const stream = async () => {
          try {
            if (!chunk({ role: "assistant" })) return;
            // Zero-width split after whitespace: the deltas concatenate back
            // to `content` byte-for-byte, so streamed and non-streamed output
            // match.
            for (const part of parts) {
              if (slow) await new Promise((r) => setTimeout(r, 750));
              if (!chunk({ content: part })) return;
            }
            if (!chunk({}, "stop")) return;
            if (res.destroyed) return;
            res.write("data: [DONE]\n\n");
            res.end();
          } catch (err) {
            console.error(`stream failed: ${err.message}`);
            if (!res.destroyed) res.destroy();
          }
        };
        stream();
        return;
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
    if (path === "/v1/images/generations") {
      return json(res, 200, {
        created: 0,
        data: [
          {
            b64_json: PNG_1X1_B64,
            revised_prompt: `echo-image: ${body.prompt}`,
          },
        ],
      });
    }
    if (path === "/v1/audio/speech") {
      const buf = wavBuffer(1);
      res.writeHead(200, {
        "Content-Type": "audio/wav",
        "Content-Length": buf.length,
      });
      return res.end(buf);
    }
    json(res, 404, { error: { message: `no route ${req.url}` } });
  });
});

// Guard so `e2e/scripts/gen-media-fixtures.mjs` can `import { wavBuffer,
// PNG_1X1 }` from this file without booting a second server. In the
// container the entrypoint is `node /srv/server.mjs`, so this still starts.
const isMain = process.argv[1] && nodePath.resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isMain) {
  server.listen(PORT, () => console.log(`mock-llm on :${PORT}`));
}
