// Shared rate-limit buckets in Redis, so a limit holds across gateway replicas (CP P5, REL4).
//
// One Lua script takes a token from every bucket of a request atomically, on the Redis
// server's clock (`TIME`), so replicas with skewed clocks still agree. The client is a
// small RESP2 implementation over one connection (the gateway has no npm dependencies):
// commands are pipelined, each has a short deadline, and a closed or slow connection fails
// at once so the limiter can decide by OD-07 (reads fall back, writes and model starts are
// refused) instead of holding requests (ratelimit.mjs).
import net from "node:net";
import tls from "node:tls";

// KEYS: bucket keys. ARGV: capacity and refill per millisecond for each key, in order.
// Returns {1, 0} when every bucket had a token (and spends one from each), else {0, waitMs}.
export const TAKE_SCRIPT = `
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local levels = {}
local wait = 0
for i = 1, #KEYS do
  local cap = tonumber(ARGV[i * 2 - 1])
  local rate = tonumber(ARGV[i * 2])
  local state = redis.call('HMGET', KEYS[i], 'tk', 'ts')
  local tokens = tonumber(state[1])
  local ts = tonumber(state[2])
  if tokens == nil or ts == nil then tokens = cap; ts = now end
  if now > ts then tokens = math.min(cap, tokens + (now - ts) * rate) end
  levels[i] = tokens
  if tokens < 1 then
    local w = math.ceil((1 - tokens) / rate)
    if w > wait then wait = w end
  end
end
if wait > 0 then return {0, wait} end
for i = 1, #KEYS do
  local cap = tonumber(ARGV[i * 2 - 1])
  local rate = tonumber(ARGV[i * 2])
  redis.call('HSET', KEYS[i], 'tk', tostring(levels[i] - 1), 'ts', tostring(now))
  redis.call('PEXPIRE', KEYS[i], math.ceil(cap / rate) + 1000)
end
return {1, 0}
`;

export function encodeCommand(args) {
  const parts = [`*${args.length}\r\n`];
  for (const arg of args) {
    const value = Buffer.from(String(arg), "utf8");
    parts.push(`$${value.length}\r\n`, value, "\r\n");
  }
  return Buffer.concat(parts.map((part) => (Buffer.isBuffer(part) ? part : Buffer.from(part, "utf8"))));
}

/** Parse one RESP2 value from `buffer` at `offset`; `undefined` when more bytes are needed. */
export function parseReply(buffer, offset = 0) {
  if (offset >= buffer.length) return undefined;
  const end = buffer.indexOf("\r\n", offset);
  if (end === -1) return undefined;
  const type = String.fromCharCode(buffer[offset]);
  const line = buffer.toString("utf8", offset + 1, end);
  const next = end + 2;
  if (type === "+") return { value: line, offset: next };
  if (type === "-") return { value: new RedisReplyError(line), offset: next };
  if (type === ":") return { value: Number(line), offset: next };
  if (type === "$") {
    const length = Number(line);
    if (length === -1) return { value: null, offset: next };
    if (buffer.length < next + length + 2) return undefined;
    return { value: buffer.toString("utf8", next, next + length), offset: next + length + 2 };
  }
  if (type === "*") {
    const count = Number(line);
    if (count === -1) return { value: null, offset: next };
    const values = [];
    let cursor = next;
    for (let index = 0; index < count; index++) {
      const item = parseReply(buffer, cursor);
      if (item === undefined) return undefined;
      values.push(item.value);
      cursor = item.offset;
    }
    return { value: values, offset: cursor };
  }
  throw new Error(`Unsupported RESP reply type ${JSON.stringify(type)}`);
}

export class RedisReplyError extends Error {}

/**
 * @param {{ host: string, port: number, tls?: boolean, username?: string, password?: string, db?: number,
 *           commandTimeoutMs?: number, connectTimeoutMs?: number }} options
 */
export function createRedisClient(options) {
  const { commandTimeoutMs = 150, connectTimeoutMs = 1_000 } = options;
  let socket = null;
  let ready = null;
  let buffer = Buffer.alloc(0);
  let pending = [];
  let closed = false;
  let retryAt = 0;
  let isReady = false;

  function failAll(error) {
    const waiting = pending;
    pending = [];
    for (const entry of waiting) entry.settle(error);
  }

  function onData(chunk) {
    buffer = buffer.length === 0 ? chunk : Buffer.concat([buffer, chunk]);
    let offset = 0;
    for (;;) {
      let parsed;
      try {
        parsed = parseReply(buffer, offset);
      } catch (error) {
        socket?.destroy(error);
        return;
      }
      if (parsed === undefined) break;
      offset = parsed.offset;
      pending.shift()?.settle(null, parsed.value);
    }
    buffer = offset === buffer.length ? Buffer.alloc(0) : buffer.subarray(offset);
  }

  function send(args, timeoutMs = commandTimeoutMs) {
    return new Promise((resolve, reject) => {
      if (!socket || socket.destroyed) {
        reject(new Error("redis connection closed"));
        return;
      }
      let done = false;
      // A connection that misses a deadline is treated as wedged (a proxy that accepted and went
      // quiet, a paused server): it is closed, so every waiting command fails now and the next one
      // reconnects after the backoff, instead of all of them queueing behind a dead socket.
      const owner = socket;
      const timer = setTimeout(() => {
        entry.settle(new Error("redis command timed out"));
        owner.destroy();
      }, timeoutMs);
      timer.unref?.();
      const entry = {
        settle(error, value) {
          if (done) return;
          done = true;
          clearTimeout(timer);
          if (error) reject(error);
          else if (value instanceof RedisReplyError) reject(value);
          else resolve(value);
        },
      };
      pending.push(entry);
      socket.write(encodeCommand(args));
    });
  }

  function connect() {
    if (ready) return ready;
    if (closed) return Promise.reject(new Error("redis client closed"));
    if (Date.now() < retryAt) return Promise.reject(new Error("redis reconnect backoff"));
    ready = new Promise((resolve, reject) => {
      const connectOptions = { host: options.host, port: options.port };
      const raw = options.tls ? tls.connect({ ...connectOptions, servername: options.host }) : net.connect(connectOptions);
      socket = raw;
      raw.setNoDelay(true);
      raw.setTimeout(connectTimeoutMs, () => raw.destroy(new Error("redis connect timed out")));
      raw.on("data", onData);
      raw.once(options.tls ? "secureConnect" : "connect", async () => {
        raw.setTimeout(0);
        try {
          if (options.password) {
            await send(options.username ? ["AUTH", options.username, options.password] : ["AUTH", options.password], connectTimeoutMs);
          }
          if (options.db) await send(["SELECT", options.db], connectTimeoutMs);
          isReady = true;
          resolve();
        } catch (error) {
          raw.destroy(error);
        }
      });
      raw.on("error", () => {});
      raw.once("close", () => {
        isReady = false;
        failAll(new Error("redis connection closed"));
        socket = null;
        buffer = Buffer.alloc(0);
        if (ready) {
          ready = null;
          retryAt = Date.now() + 500;
        }
        reject(new Error("redis connection closed"));
      });
    });
    ready.catch(() => {});
    return ready;
  }

  // A request never waits longer than one command deadline for a (re)connect: the connect
  // carries on in the background and the limiter applies OD-07 meanwhile.
  function connectWithin(ms) {
    let timer;
    const deadline = new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error("redis connect pending")), ms);
      timer.unref?.();
    });
    return Promise.race([connect(), deadline]).finally(() => clearTimeout(timer));
  }

  return {
    async command(args) {
      if (!isReady) await connectWithin(Math.max(commandTimeoutMs, 250));
      return send(args);
    },
    connected: () => isReady,
    /** Open the connection in the background (errors are left for the next command). */
    warm() {
      if (!isReady) connect().catch(() => {});
    },
    close() {
      closed = true;
      socket?.destroy();
    },
  };
}

/** A rate-limit store on Redis (see ratelimit.mjs for the bucket contract). */
export function createRedisStore(options, client = createRedisClient(options)) {
  return {
    kind: "redis",
    client,
    async take(buckets) {
      const argv = buckets.flatMap((bucket) => [bucket.capacity, bucket.perMs]);
      const reply = await client.command(["EVAL", TAKE_SCRIPT, buckets.length, ...buckets.map((bucket) => bucket.key), ...argv]);
      if (!Array.isArray(reply) || reply.length !== 2) throw new Error("unexpected redis reply");
      return { allowed: Number(reply[0]) === 1, retryAfterMs: Number(reply[1]) };
    },
    warm() {
      client.warm?.();
    },
    close() {
      client.close();
    },
  };
}
