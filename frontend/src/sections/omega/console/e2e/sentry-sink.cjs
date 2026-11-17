/* eslint-env node */
// A local stand-in for Sentry's ingest, so the e2e can read exactly what the
// real Future AGI build sends (CP P8, OBS1). The build's DSN points here
// (http://<key>@localhost:<fake port>/7); the browser SDK posts envelopes to
// /api/7/envelope/. Each envelope is kept whole as text (to search it for
// anything that must never leave the page) and its event and transaction
// items are parsed.
const ENVELOPE_PATH = "/api/7/envelope/";
const PROJECT_ID = 7;
const PUBLIC_KEY = "e2epublickey";

const sentryDsn = (port) =>
  `http://${PUBLIC_KEY}@localhost:${port}/${PROJECT_ID}`;

function parseEnvelope(buffer) {
  let offset = 0;
  const readLine = () => {
    const end = buffer.indexOf(10, offset);
    const stop = end === -1 ? buffer.length : end;
    const line = buffer.subarray(offset, stop);
    offset = stop + 1;
    return line;
  };
  const items = [];
  let header = null;
  try {
    header = JSON.parse(readLine().toString("utf8"));
  } catch {
    return { header: null, items };
  }
  while (offset < buffer.length) {
    const line = readLine();
    if (line.length === 0) continue;
    let itemHeader;
    try {
      itemHeader = JSON.parse(line.toString("utf8"));
    } catch {
      break;
    }
    let payload;
    if (Number.isInteger(itemHeader.length)) {
      payload = buffer.subarray(offset, offset + itemHeader.length);
      offset += itemHeader.length + 1;
    } else {
      payload = readLine();
    }
    let parsed = null;
    if (itemHeader.type === "event" || itemHeader.type === "transaction") {
      try {
        parsed = JSON.parse(payload.toString("utf8"));
      } catch {
        parsed = null;
      }
    }
    items.push({ type: itemHeader.type, payload: parsed });
  }
  return { header, items };
}

const CORS = {
  "access-control-allow-origin": "*",
  "access-control-allow-headers": "*",
  "access-control-allow-methods": "POST, OPTIONS",
};

/** Answers one request if it is for the sink; returns whether it did. */
function handleSentry(req, res, url, store) {
  if (url.pathname !== ENVELOPE_PATH) return false;
  if (req.method === "OPTIONS") {
    res.writeHead(204, CORS);
    res.end();
    return true;
  }
  const chunks = [];
  req.on("data", (chunk) => chunks.push(chunk));
  req.on("end", () => {
    const buffer = Buffer.concat(chunks);
    const text = buffer.toString("utf8");
    const { items } = parseEnvelope(buffer);
    store.push({
      at: Date.now(),
      origin: req.headers.origin || null,
      text,
      events: items
        .filter((item) => item.type === "event")
        .map((i) => i.payload),
      transactions: items
        .filter((item) => item.type === "transaction")
        .map((i) => i.payload),
      types: items.map((item) => item.type),
    });
    res.writeHead(200, { "content-type": "application/json", ...CORS });
    res.end("{}");
  });
  return true;
}

module.exports = { ENVELOPE_PATH, handleSentry, parseEnvelope, sentryDsn };
