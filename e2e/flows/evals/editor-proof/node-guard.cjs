"use strict";
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const net = require("node:net");
const dns = require("node:dns");
const directory = process.env.EVAL_NODE_GUARD_DIR || process.env.EVAL_GUARD_DIR;
if (!directory) throw new Error("EDITOR_GUARD_DIRECTORY_REQUIRED");
fs.mkdirSync(directory, { recursive: true });
const service = process.env.EVAL_GUARD_SERVICE || "host";
const own = new Set([os.hostname()]);
for (const rows of Object.values(os.networkInterfaces()))
  for (const row of rows || []) own.add(row.address);
const services = new Set(
  (process.env.EVAL_GUARD_SERVICES || "").split(",").filter(Boolean),
);
const state = { allowedCalls: 0 };
globalThis.__editorGuard = state;
function record(kind, layer, hostname, port) {
  fs.appendFileSync(
    path.join(directory, `node-${service}-${process.pid}.jsonl`),
    JSON.stringify({
      kind,
      layer,
      hostname: String(hostname),
      port,
      service,
      pid: process.pid,
      time: Date.now(),
    }) + "\n",
    { mode: 0o600 },
  );
}
function check(hostname, port, layer, parse = false) {
  const host = String(hostname || "localhost").replace(/^\[|\]$/g, "");
  if (
    host === "localhost" ||
    host === "::1" ||
    (net.isIP(host) === 4 && host.startsWith("127."))
  ) {
    state.allowedCalls += 1;
    return;
  }
  if (own.has(host)) record("own-host", layer, host, port);
  else if (services.has(host)) {
    /* Exact compose service names, never a suffix. */
  } else if (parse && net.isIP(host))
    record("literal-parse", layer, host, port);
  else {
    record(
      process.env.EVAL_GUARD_CONTROL === "1" ? "refusal-control" : "refused",
      layer,
      host,
      port,
    );
    throw new Error("EDITOR_NONLOCAL_REFUSED");
  }
  state.allowedCalls += 1;
}
const connect = net.Socket.prototype.connect;
net.Socket.prototype.connect = function (...args) {
  // net.connect passes an already-normalized argument tuple to Socket.connect.
  const supplied = Array.isArray(args[0]) ? args[0] : args;
  const options = net._normalizeArgs(supplied)[0];
  if (!options.path)
    check(options.host, Number(options.port || 0), "net.Socket.connect");
  return Reflect.apply(connect, this, args);
};
function guardDNS(target, promise = false) {
  for (const name of [
    "lookup",
    "lookupService",
    "resolve",
    "resolve4",
    "resolve6",
    "resolveAny",
    "resolveCaa",
    "resolveCname",
    "resolveMx",
    "resolveNaptr",
    "resolveNs",
    "resolvePtr",
    "resolveSoa",
    "resolveSrv",
    "resolveTxt",
    "reverse",
  ]) {
    if (typeof target[name] !== "function") continue;
    const original = target[name];
    target[name] = function (host, ...args) {
      try {
        check(
          host,
          name === "lookupService" ? args[0] : 0,
          `dns.${name}`,
          name === "lookup",
        );
      } catch (error) {
        if (promise) return Promise.reject(error);
        throw error;
      }
      return Reflect.apply(original, this, [host, ...args]);
    };
  }
}
guardDNS(dns);
guardDNS(dns.Resolver.prototype);
guardDNS(dns.promises, true);
guardDNS(dns.promises.Resolver.prototype, true);
// An explicit connector covers native fetch as well as socket and DNS hooks.
const undici = require(process.env.EVAL_UNDICI_PATH || "undici");
const connector = undici.buildConnector({});
undici.setGlobalDispatcher(
  new undici.Agent({
    connect(options, callback) {
      try {
        check(
          options.hostname,
          Number(options.port || (options.protocol === "https:" ? 443 : 80)),
          "undici.connector",
        );
      } catch (error) {
        callback(error);
        return;
      }
      connector(options, callback);
    },
  }),
);
record("installed", "node.socket", os.hostname(), 0);
for (const host of own) record("own-host-snapshot", "node.socket", host, 0);
