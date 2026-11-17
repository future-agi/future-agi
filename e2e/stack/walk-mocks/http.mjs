import { createServer as httpServer } from "node:http";
import { createServer as httpsServer } from "node:https";
import { readFileSync } from "node:fs";
export const json = (res, code, body) => {
  res.writeHead(code, {
    "Content-Type": "application/json",
    "Cache-Control": "no-store",
  });
  res.end(JSON.stringify(body));
};
export async function read(req, limit = 1048576) {
  const chunks = [];
  let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > limit) throw new Error("body too large");
    chunks.push(chunk);
  }
  return Buffer.concat(chunks).toString("utf8");
}
export function safe(handler) {
  return (req, res) =>
    Promise.resolve(handler(req, res)).catch(() => {
      if (!res.headersSent) json(res, 400, { error: "invalid_request" });
      else res.destroy();
    });
}
export function listen(data, control) {
  // Control listener is published on loopback only by Compose; never proxied.
  httpServer(safe(control)).listen(8081, "0.0.0.0");
  httpsServer(
    {
      key: readFileSync("/certs/key.pem"),
      cert: readFileSync("/certs/cert.pem"),
    },
    safe(data),
  ).listen(8443, "0.0.0.0");
}
export const localRedirect = (raw) => {
  try {
    const u = new URL(raw);
    return (
      !u.username &&
      !u.password &&
      !u.hash &&
      ((["localhost", "127.0.0.1", "[::1]"].includes(u.hostname) &&
        ["http:", "https:"].includes(u.protocol)) ||
        (u.hostname.endsWith(".e2e-public.test") && u.protocol === "https:"))
    );
  } catch {
    return false;
  }
};
export const escape = (value) =>
  String(value).replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
