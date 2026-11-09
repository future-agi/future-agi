/* eslint-env node */
// J07 read-only early observer (E4 contextObserver, profile
// j07-no-storage-init-no-capture). Installed on every fresh context BEFORE its
// first page, exactly once:
//
//   - by the shared runner's E4 hook (consumer-extension/context-hook.cjs), which
//     calls the exported beforeFirstPage(context) inside browser.newContext; or
//   - by this consumer itself on an explicitly declared direct run.
//
// The fixture never installs twice and never skips: acquireCensus() reuses the
// census E4 already installed (validated as THIS module's census by source
// digest, profile, single install and no page at install) when the binding says
// 'e4', refuses when E4 should have installed it and did not, and installs it
// itself only when the binding says 'direct'.
//
// In every document of the context (host, Studio frame, hostile and sibling
// frames, popups) a memory-only init script wraps History, Storage, IndexedDB,
// document.cookie and window.open and listens to popstate/hashchange/message at
// document start. It never writes storage, never navigates and never seeds
// authentication, workspace, theme or rail state. It learns the connect token
// only where the browser hands it to that document (the host's own #connect
// fragment, or an embed.key delivered to a frame), keeps it in closure memory,
// and reports per operation only booleans and occurrence counts. Write payloads
// are walked as structured-clone values (Map, Set, ArrayBuffer, typed arrays,
// Blob, nested objects), never through JSON.
//
// Top-level documents additionally carry the A10/A12 sender witness: the bound
// frame element's contentWindow resolves to a per-window Proxy whose postMessage
// records the message type and the exact targetOrigin argument (an origin, never
// a value) before forwarding the call unchanged, and MessageEvent.source maps the
// same real window to the same Proxy, so `event.source === frameWindow` keeps its
// meaning for the product. The A12 seam (armPending) is described at its code.
//
// Context-level listeners keep the complete request/response/websocket/worker
// census in Node memory; raw URLs and headers are scanned in memory and never
// emitted (ARTIFACT-POLICY observer_rule). Nonlocal attempts are counted and
// aborted locally; any count above zero fails A34.
"use strict";

const crypto = require("node:crypto");
const fs = require("node:fs");

const oracles = require("./studio-embed.oracles.cjs");

const PROFILE = "j07-no-storage-init-no-capture";
const BINDING_NAME = "__j07Observe";
const INSTALLED = Symbol.for("j07.observer.installed");
const CENSUS_BRAND = Symbol.for("j07.observer.census");
/** Identity of this exact observer source: a census installed by any copy of these bytes carries it. */
const MODULE_DIGEST = crypto.createHash("sha256").update(fs.readFileSync(__filename)).digest("hex");
const LOOPBACK_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]"]);
const LOCAL_SCHEMES = new Set(["data:", "blob:", "about:"]);
const INSTALL_MODES = new Set(["e4", "direct"]);

/** A constant, value-free refusal (message "J07-BLOCKED <CODE>"). */
class ObserverRefusal extends Error {
  constructor(code) {
    super(`J07-BLOCKED ${code}`);
    this.name = "J07Blocked";
    this.code = code;
  }
}

function isLocalUrl(value) {
  try {
    const url = new URL(value);
    if (LOCAL_SCHEMES.has(url.protocol)) return true;
    return LOOPBACK_HOSTS.has(url.hostname);
  } catch {
    return false;
  }
}

const originOf = (value) => {
  try {
    return new URL(value).origin;
  } catch {
    return "";
  }
};

/** Raw, encoded and decoded-byte forms of each secret, for in-memory occurrence counts. */
function tokenVariants(secrets) {
  const out = new Set();
  for (const secret of secrets) {
    if (typeof secret !== "string" || secret.length < 8) continue;
    out.add(secret);
    out.add(encodeURIComponent(secret));
    out.add(encodeURIComponent(encodeURIComponent(secret)));
    out.add(Buffer.from(secret, "utf8").toString("base64"));
    out.add(Buffer.from(secret, "utf8").toString("base64url"));
    // A base64url key stored as its 32 raw bytes (binary IndexedDB values).
    if (/^[A-Za-z0-9_-]{16,}$/.test(secret)) {
      const raw = Buffer.from(secret, "base64url");
      if (raw.length >= 8) {
        out.add(raw.toString("latin1"));
        out.add(raw.toString("hex"));
      }
    }
  }
  return [...out];
}

function countHits(text, variants) {
  if (typeof text !== "string" || text === "") return 0;
  let hits = 0;
  for (const needle of variants) {
    for (let at = text.indexOf(needle); at >= 0; at = text.indexOf(needle, at + needle.length)) hits += 1;
  }
  return hits;
}

// ---------------------------------------------------------------- page side
// Serialized into an init script; runs in every frame before any page script.
// Self-contained: `walk` is oracles.structuredTexts, passed by source text.
function pageObserver(config, walk) {
  "use strict";
  var KEY = Symbol.for("j07.observer");
  if (Object.prototype.hasOwnProperty.call(window, KEY)) return;
  var origin = performance.timeOrigin;
  var now = function () {
    return Math.round((origin + performance.now()) * 1000) / 1000;
  };
  var isTop = window.top === window;
  var docId = Math.random().toString(36).slice(2) + "-" + Math.round(now()).toString(36);
  var startHistory = history.length;
  var seq = 0;
  var queue = [];
  var tries = 0;
  var secrets = []; // memory only: the connect token(s) this document was handed
  var keys = []; // memory only: embed.key tokens the PARENT window delivered here
  var keysAny = []; // memory only: every embed.key delivered here, whoever sent it
  var KNOWN = ["embed.hello", "embed.key", "embed.route", "embed.navigate", "embed.theme",
    "embed.open", "embed.gate", "embed.telemetry", "embed.badge"];
  var BOUND_TESTID = "omega-embed-frame";
  var BOUND = 'iframe[data-testid="' + BOUND_TESTID + '"]';

  // Native accessors, captured before any page script, for the observer's own reads.
  var nativeQuery = Document.prototype.querySelector;
  var cwDesc = Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype, "contentWindow");
  var srcDesc = Object.getOwnPropertyDescriptor(MessageEvent.prototype, "source");
  var nativeCW = cwDesc && cwDesc.get;
  var nativeSource = srcDesc && srcDesc.get;
  var realWindowOf = function (element) {
    if (!element) return null;
    try { return nativeCW ? nativeCW.call(element) : element.contentWindow; } catch (e) { return null; }
  };
  var realSourceOf = function (event) {
    try { return nativeSource ? nativeSource.call(event) : event.source; } catch (e) { return null; }
  };
  var query = function (selector) {
    try { return nativeQuery.call(document, selector); } catch (e) { return null; }
  };
  var boundElement = function () { return query(BOUND); };
  var srcOrigin = function (element) {
    try { return new URL(element.getAttribute("src") || "", location.href).origin; } catch (e) { return ""; }
  };

  var decode = function (value) {
    try { return decodeURIComponent(value); } catch (e) { return value; }
  };
  var connectInUrl = function () {
    return /(?:^#|&)connect=/.test(location.hash || "");
  };
  var fragmentToken = function () {
    var m = /(?:^#|&)t=([^&]*)/.exec(location.hash || "");
    return m ? m[1] : null;
  };
  var learn = function (raw) {
    if (!raw) return;
    [raw, decode(raw)].forEach(function (v) {
      if (v && v.length >= 8 && secrets.indexOf(v) < 0) secrets.push(v);
    });
  };
  var decodedBytes = function (s) {
    if (!/^[A-Za-z0-9_-]{16,}$/.test(s)) return null;
    try {
      var b = s.replace(/-/g, "+").replace(/_/g, "/");
      while (b.length % 4) b += "=";
      return atob(b);
    } catch (e) {
      return null;
    }
  };
  var needles = function () {
    var out = [];
    secrets.forEach(function (s) {
      out.push(s, encodeURIComponent(s));
      try {
        var b64 = btoa(s);
        out.push(b64, b64.replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, ""));
      } catch (e) { /* non-latin1 */ }
      var raw = decodedBytes(s);
      if (raw && raw.length >= 8) out.push(raw);
    });
    return out;
  };
  var countIn = function (text, list) {
    var n = 0;
    if (typeof text !== "string" || text === "") return 0;
    list.forEach(function (needle) {
      for (var at = text.indexOf(needle); at >= 0; at = text.indexOf(needle, at + needle.length)) n += 1;
    });
    return n;
  };
  var flush = function () {
    var fn = window[config.binding];
    if (typeof fn !== "function") {
      if (tries++ < 400) setTimeout(flush, 25);
      return;
    }
    while (queue.length) {
      var event = queue.shift();
      try { fn(event); } catch (e) { /* the census counts what arrives */ }
    }
  };
  var send = function (event) {
    seq += 1;
    event.seq = seq;
    event.doc = docId;
    event.top = isTop;
    event.ts = now();
    event.origin = location.origin;
    queue.push(event);
    flush();
  };
  // Blobs cannot be read synchronously: their bytes are counted when they arrive. Every
  // read is announced (read.blob.start) before it begins and reported (read.blob) when it
  // settles, under one ordinal, so a read that never settles stays visible as pending.
  // The reader is Blob.prototype.arrayBuffer captured before any page script, so a page
  // override cannot hide the bytes.
  var nativeBlobBytes = typeof Blob === "function" && Blob.prototype && typeof Blob.prototype.arrayBuffer === "function"
    ? Blob.prototype.arrayBuffer : null;
  var blobBytes = function (blob) {
    if (!nativeBlobBytes) return Promise.reject(new Error("unsupported"));
    try {
      return Promise.resolve(nativeBlobBytes.call(blob)).then(function (buffer) { return new Uint8Array(buffer); });
    } catch (e) {
      return Promise.reject(e);
    }
  };
  var blobStarted = 0;
  var blobSettled = 0;
  var readBlobs = function (blobs) {
    blobs.forEach(function (blob) {
      blobStarted += 1;
      var id = blobStarted;
      send({ kind: "op", op: "read.blob.start", blob: id, before: false, hits: 0, unsupported: 0 });
      var settle = function (hits, unsupported) {
        blobSettled += 1;
        send({ kind: "op", op: "read.blob", blob: id, before: false, hits: hits, unsupported: unsupported });
      };
      blobBytes(blob).then(function (bytes) {
        var r = walk(bytes);
        var list = needles();
        var n = 0;
        r.texts.forEach(function (t) { n += countIn(t, list); });
        settle(n, r.unsupported + r.blobs.length);
      }, function () {
        settle(0, 1);
      });
    });
  };
  /** Token occurrences in any structured-clone value, plus what could not be read. */
  var probe = function () {
    var hits = 0;
    var unsupported = 0;
    var list = needles();
    for (var i = 0; i < arguments.length; i++) {
      var value = arguments[i];
      if (value === undefined || value === null) continue;
      var r;
      try { r = walk(value); } catch (e) { unsupported += 1; continue; }
      unsupported += r.unsupported;
      if (list.length > 0) r.texts.forEach(function (t) { hits += countIn(t, list); });
      if (r.blobs.length > 0) readBlobs(r.blobs);
    }
    return { hits: hits, unsupported: unsupported };
  };
  var define = function (target, name, wrapped) {
    var desc = Object.getOwnPropertyDescriptor(target, name);
    if (!desc || typeof desc.value !== "function") return;
    Object.defineProperty(target, name, {
      value: wrapped, writable: desc.writable, enumerable: desc.enumerable, configurable: desc.configurable,
    });
  };
  var named = function (name, fn) {
    try { Object.defineProperty(fn, "name", { value: name }); } catch (e) { /* cosmetic */ }
    return fn;
  };

  var connectAtStart = connectInUrl();
  if (connectAtStart) learn(fragmentToken());
  send({ kind: "doc-start", connect: connectAtStart, history: startHistory,
    embed: /[?&]embed=1(?:&|$)/.test(location.search || "") });

  // History: the intake's native replaceState is seen here, never altered.
  ["replaceState", "pushState"].forEach(function (name) {
    var native = History.prototype[name];
    define(History.prototype, name, named(name, function (state, title, url) {
      var before = connectInUrl();
      var argConnect = url !== undefined && url !== null && /(?:#|&)(?:connect|t)=/.test(String(url));
      var found = probe(state, url === undefined || url === null ? "" : String(url));
      var result = native.apply(this, arguments);
      send({ kind: "op", op: name, before: before, after: connectInUrl(), argConnect: argConnect,
        hits: found.hits, unsupported: found.unsupported, historyDelta: history.length - startHistory });
      return result;
    }));
  });

  // Storage: every write, either area, counted with the connect phase it ran in.
  var areaOf = function (store) {
    try { if (store === window.localStorage) return "local"; } catch (e) { /* refused */ }
    try { if (store === window.sessionStorage) return "session"; } catch (e) { /* refused */ }
    return "other";
  };
  ["setItem", "removeItem", "clear"].forEach(function (name) {
    var native = Storage.prototype[name];
    define(Storage.prototype, name, named(name, function () {
      var before = connectInUrl();
      var found = probe.apply(null, arguments);
      var result = native.apply(this, arguments);
      send({ kind: "op", op: "storage." + name, area: areaOf(this), before: before, hits: found.hits, unsupported: found.unsupported });
      return result;
    }));
  });

  // IndexedDB: opens and every object-store write, payloads walked as structured clones.
  var wrapIdb = function (proto, names, prefix) {
    if (!proto) return;
    names.forEach(function (name) {
      var native = proto[name];
      if (typeof native !== "function") return;
      define(proto, name, named(name, function () {
        var before = connectInUrl();
        var found = probe.apply(null, arguments);
        send({ kind: "op", op: prefix + name, before: before, hits: found.hits, unsupported: found.unsupported });
        return native.apply(this, arguments);
      }));
    });
  };
  wrapIdb(window.IDBFactory && IDBFactory.prototype, ["open", "deleteDatabase"], "idb.");
  wrapIdb(window.IDBObjectStore && IDBObjectStore.prototype, ["put", "add", "delete", "clear"], "idb.");
  wrapIdb(window.IDBCursor && IDBCursor.prototype, ["update", "delete"], "idb.cursor.");

  // document.cookie writes.
  var cookie = Object.getOwnPropertyDescriptor(Document.prototype, "cookie");
  if (cookie && cookie.set && cookie.configurable) {
    Object.defineProperty(Document.prototype, "cookie", {
      get: cookie.get,
      set: function (value) {
        send({ kind: "op", op: "cookie.set", before: connectInUrl(), hits: probe(value).hits });
        return cookie.set.call(this, value);
      },
      enumerable: cookie.enumerable,
      configurable: true,
    });
  }

  // window.open: a navigation the page starts itself.
  var open = window.open;
  if (typeof open === "function") {
    window.open = named("open", function (url) {
      send({ kind: "op", op: "window.open", before: connectInUrl(),
        hits: probe(url === undefined || url === null ? "" : String(url)).hits });
      return open.apply(this, arguments);
    });
  }

  // Fragment navigations on an open page (registered before any page script).
  ["popstate", "hashchange"].forEach(function (type) {
    window.addEventListener(type, function () {
      var connect = connectInUrl();
      if (connect) learn(fragmentToken());
      send({ kind: "nav", event: type, connect: connect, historyDelta: history.length - startHistory });
    }, true);
  });

  // ------------------------------------------------ A10/A12 sender witness (top documents)
  var instrumented = false;
  var seam = { stage: "idle", helloSeen: false, pendingSeen: false, pendingRead: false, redirect: false,
    replacement: null, redirectedReads: 0, posts: 0, keyPosts: 0 };
  var sendSeam = function () {
    send({ kind: "seam", stage: seam.stage, redirectedReads: seam.redirectedReads, posts: seam.posts, keyPosts: seam.keyPosts });
  };
  var proxies = typeof WeakMap === "function" ? new WeakMap() : null;
  var posts = typeof WeakMap === "function" ? new WeakMap() : null;
  var shape = function (target) {
    var t = target && typeof target === "object" ? target.targetOrigin : target;
    if (t === undefined) return "default";
    if (t === "*" || t === "/") return t;
    if (typeof t === "string" && /^https?:\/\/[A-Za-z0-9.[\]:-]+$/.test(t)) return t;
    return "invalid";
  };
  var postFor = function (real) {
    var existing = posts.get(real);
    if (existing) return existing;
    var fn = named("postMessage", function (message, targetOrigin) {
      var type = message && typeof message === "object" && typeof message.type === "string" ? message.type : null;
      var bound = boundElement();
      var boundWin = bound ? realWindowOf(bound) : null;
      var active = seam.stage !== "idle" && seam.stage !== "done" && seam.stage !== "missed";
      var record = {
        kind: "post",
        type: type === null ? "other" : KNOWN.indexOf(type) >= 0 ? type : type.indexOf("embed.") === 0 ? "embed.other" : "other",
        targetOrigin: shape(targetOrigin),
        toBound: boundWin !== null && real === boundWin,
        toReplacement: seam.replacement !== null && real === seam.replacement,
        redirected: seam.redirect,
        hasToken: type === "embed.key" && typeof message.token === "string" && message.token.length > 0,
        stage: seam.stage,
      };
      if (active) {
        seam.posts += 1;
        if (record.hasToken) seam.keyPosts += 1;
      }
      send(record);
      return Reflect.apply(real.postMessage, real, arguments);
    });
    posts.set(real, fn);
    return fn;
  };
  var proxyFor = function (real) {
    if (!real) return real;
    var existing = proxies.get(real);
    if (existing) return existing;
    var proxy = new Proxy(real, {
      get: function (target, prop) {
        if (prop === "postMessage") return postFor(target);
        var value = target[prop];
        return typeof value === "function" ? value.bind(target) : value;
      },
    });
    proxies.set(real, proxy);
    return proxy;
  };
  if (isTop && nativeCW && nativeSource && proxies && posts && typeof Proxy === "function" && typeof queueMicrotask === "function") {
    try {
      Object.defineProperty(HTMLIFrameElement.prototype, "contentWindow", {
        configurable: cwDesc.configurable,
        enumerable: cwDesc.enumerable,
        get: function () {
          var real = nativeCW.call(this);
          if (!real) return real;
          var isBound = false;
          try { isBound = this.getAttribute("data-testid") === BOUND_TESTID; } catch (e) { isBound = false; }
          if (!isBound) return real;
          // A12 seam, replacement in effect: the bound element resolves to the replacement window.
          if (seam.redirect && seam.replacement) {
            seam.redirectedReads += 1;
            return proxyFor(seam.replacement);
          }
          // A12 seam: the first bound read inside an accepted-hello dispatch is the bridge capturing
          // the frame window before it starts its token acquisition. A microtask queued now runs
          // before that acquisition resolves, and swaps in the replacement until the next task.
          if (seam.pendingRead) {
            seam.pendingRead = false;
            seam.pendingSeen = true;
            seam.stage = "pending";
            queueMicrotask(function () {
              seam.redirect = true;
              seam.stage = "replaced";
              setTimeout(function () {
                seam.redirect = false;
                seam.stage = "done";
                sendSeam();
              }, 0);
            });
          }
          return proxyFor(real);
        },
      });
      Object.defineProperty(MessageEvent.prototype, "source", {
        configurable: srcDesc.configurable,
        enumerable: srcDesc.enumerable,
        get: function () {
          var real = nativeSource.call(this);
          if (!real) return real;
          var known = proxies.get(real);
          if (known) return known;
          var bound = boundElement();
          if (bound && realWindowOf(bound) === real) return proxyFor(real);
          return real;
        },
      });
      instrumented = true;
    } catch (e) {
      instrumented = false; // A10 then fails its precondition; nothing is assumed
    }
  }

  // omega-embed/1 deliveries into this document.
  window.addEventListener("message", function (event) {
    var data = event.data;
    var type = data && typeof data === "object" && typeof data.type === "string" ? data.type : null;
    if (type === null || type.indexOf("embed.") !== 0) return;
    var source = realSourceOf(event);
    var bound = isTop ? boundElement() : null;
    var boundWin = bound ? realWindowOf(bound) : null;
    var record = {
      kind: "message",
      type: KNOWN.indexOf(type) >= 0 ? type : "embed.other",
      from: String(event.origin),
      fromParent: !isTop && source === window.parent,
      fromBoundFrame: boundWin !== null && source === boundWin,
      protocolValid: type === "embed.hello" && data.protocol === "omega-embed/1",
      keyShaped: type === "embed.key" && typeof data.token === "string" && data.token.length > 0,
    };
    if (type === "embed.gate" && ["ready", "needs-key", "refused"].indexOf(data.state) >= 0) record.state = data.state;
    // Every delivered key is recorded, whoever sent it; the parent's keys are kept apart.
    if (record.keyShaped) {
      keysAny.push(data.token);
      learn(data.token);
      record.keyOrdinalAny = keysAny.length;
      if (record.fromParent) {
        keys.push(data.token);
        record.keyOrdinal = keys.length;
      }
    }
    // A12 seam: an accepted-hello candidate from the bound frame on its own src origin.
    if (isTop && seam.stage === "armed" && type === "embed.hello" && record.protocolValid
      && record.fromBoundFrame && String(event.origin) === srcOrigin(bound)) {
      seam.stage = "hello";
      seam.helloSeen = true;
      seam.pendingRead = true;
      setTimeout(function () {
        if (seam.pendingRead) {
          seam.pendingRead = false;
          seam.stage = "missed";
          sendSeam();
        }
      }, 0);
      sendSeam();
    }
    send(record);
  }, true);

  // ------------------------------------------------ A03 first-script order
  // A microtask checkpoint runs before each parser-inserted script executes, so
  // the mutation records arrive before the script they announce runs. Any
  // executable non-intake script inserted before the intake while the fragment is
  // live is reported; the witness is taken only when a FOLLOWING script is
  // inserted in a later batch than the intake (never on DOMContentLoaded alone).
  var JS_TYPE = /^(?:text|application)\/(?:x-)?(?:javascript|ecmascript)$|^text\/(?:jscript|livescript|javascript1\.[0-5])$/;
  var executable = function (node) {
    var type = (node.getAttribute("type") || "").trim().toLowerCase();
    return type === "" || type === "module" || JS_TYPE.test(type);
  };
  // Can this script run before a later parser-blocking inline script? (deferred ones cannot)
  var mayRunEarly = function (node) {
    var type = (node.getAttribute("type") || "").trim().toLowerCase();
    var isAsync = node.hasAttribute("async");
    if (type === "module") return isAsync;
    if (node.hasAttribute("src") && node.hasAttribute("defer") && !isAsync) return false;
    return true;
  };
  var sawIntake = false;
  var witnessed = false;
  try {
    var mo = new MutationObserver(function (records) {
      if (witnessed) return;
      var intakeHere = false;
      var following = false;
      records.forEach(function (r) {
        for (var i = 0; i < r.addedNodes.length; i++) {
          var node = r.addedNodes[i];
          if (node.nodeName !== "SCRIPT") continue;
          if (node.hasAttribute && node.hasAttribute("data-omega-connect-intake")) {
            if (!sawIntake) {
              sawIntake = true;
              intakeHere = true;
              send({ kind: "script", intake: true, connect: connectInUrl() });
            }
          } else if (!sawIntake) {
            if (connectInUrl() && executable(node) && mayRunEarly(node)) send({ kind: "script", pre: true, connect: true });
          } else if (!intakeHere && executable(node)) {
            following = true;
          }
        }
      });
      if (following) {
        witnessed = true;
        send({ kind: "witness", at: "next-script", connect: connectInUrl(),
          historyDelta: history.length - startHistory, hits: countIn(location.href, needles()) });
        mo.disconnect();
      }
    });
    mo.observe(document, { childList: true, subtree: true });
  } catch (e) { /* no witness: A03 reports it missing */ }

  Object.defineProperty(window, KEY, {
    value: Object.freeze({
      // The spec passes a token in and reads back a boolean only.
      keyMatches: function (token) { return keys.indexOf(token) >= 0; },
      keyCount: function () { return keys.length; },
      keyMatchesAny: function (token) { return keysAny.indexOf(token) >= 0; },
      keyCountAny: function () { return keysAny.length; },
      docId: function () { return docId; },
      // Blob reads this document started and settled (counts only), and the native reader.
      blobReads: function () { return { doc: docId, started: blobStarted, settled: blobSettled }; },
      blobBytes: function (blob) { return blobBytes(blob); },
      hostInstrumented: function () { return instrumented; },
      // The bound frame's index in window.frames, read with the native accessor.
      boundIndex: function () {
        var bound = boundElement();
        var real = bound ? realWindowOf(bound) : null;
        for (var i = 0; real && i < window.frames.length; i++) if (window.frames[i] === real) return i;
        return -1;
      },
      /**
       * Arms the A12 seam for the next accepted hello from the bound frame. `name`
       * names a real, already loaded Studio-origin iframe (the replacement window).
       */
      armPending: function (name) {
        if (!instrumented || typeof name !== "string" || !/^[a-z0-9-]+$/.test(name)) return false;
        var replacement = realWindowOf(query('iframe[name="' + name + '"]'));
        if (!replacement) return false;
        seam = { stage: "armed", helloSeen: false, pendingSeen: false, pendingRead: false, redirect: false,
          replacement: replacement, redirectedReads: 0, posts: 0, keyPosts: 0 };
        sendSeam();
        return true;
      },
      seamState: function () {
        return { stage: seam.stage, helloSeen: seam.helloSeen, pendingSeen: seam.pendingSeen,
          redirectedReads: seam.redirectedReads, posts: seam.posts, keyPosts: seam.keyPosts };
      },
    }),
    enumerable: false, configurable: false, writable: false,
  });
}

// Sends malformed and unknown-protocol hellos from a Studio-origin frame before
// Studio's own script runs (A29). Synthetic, value-free messages only.
function malformedHello(config) {
  "use strict";
  if (window.top === window || location.origin !== config.studioOrigin) return;
  var bad = [
    { type: "embed.hello", protocol: "omega-embed/0" },
    { type: "embed.hello" },
    { type: "embed.hello", protocol: "omega-embed/1 " },
    { type: "embed.hello", protocol: 1 },
  ];
  bad.forEach(function (message) {
    try { window.parent.postMessage(message, "*"); } catch (e) { /* counted at the host */ }
  });
}

const script = (fn, config, ...helpers) =>
  `(${fn.toString()})(${[JSON.stringify(config), ...helpers.map((helper) => helper.toString())].join(", ")});`;

// ---------------------------------------------------------------- Node side
const EVENT_FIELDS = {
  kind: "string", op: "string", area: "string", event: "string", type: "string", from: "string",
  at: "string", origin: "string", doc: "string", state: "string", stage: "string",
  before: "boolean", after: "boolean", argConnect: "boolean", connect: "boolean", embed: "boolean",
  fromParent: "boolean", fromBoundFrame: "boolean", protocolValid: "boolean", keyShaped: "boolean",
  top: "boolean", intake: "boolean", pre: "boolean", toBound: "boolean", toReplacement: "boolean",
  redirected: "boolean", hasToken: "boolean",
  hits: "number", unsupported: "number", historyDelta: "number", history: "number", keyOrdinal: "number",
  keyOrdinalAny: "number", seq: "number", ts: "number", redirectedReads: "number", posts: "number", keyPosts: "number",
  blob: "number",
};
const TARGET_ORIGIN = /^(?:\*|\/|default|invalid|https?:\/\/[A-Za-z0-9.[\]:-]+)$/;

class Census {
  constructor(context, options) {
    this[CENSUS_BRAND] = MODULE_DIGEST;
    this.profile = PROFILE;
    this.via = options.via;
    this.installs = 1;
    this.acquisitions = 0;
    this.pagesAtInstall = context.pages().length;
    this.context = context;
    this.label = options.label;
    this.requests = [];
    this.byRequest = new Map();
    this.events = [];
    this.websockets = [];
    this.pages = 0;
    this.popups = 0;
    this.workers = 0;
    this.serviceWorkers = 0;
    this.nonlocalAttempts = 0;
    this.droppedEvents = 0;
    // Sealed once every context of the case is closed: anything observed after the seal
    // is counted (lateEvents) and fails the walk, never silently dropped.
    this.sealed = false;
    this.lateEvents = 0;
    this.studioOrigins = new Set();
    // Bound on one request's actual-header collection (allHeaders); past it the census is incomplete.
    this.headerTimeoutMs = 10_000;
    this.initScripts = 1; // the observer itself
    // A standalone Studio context (A06 persistence check): its main frame IS Studio.
    this.standalone = false;
  }

  setStudioOrigins(origins) {
    for (const origin of origins) this.studioOrigins.add(origin);
  }

  /** Freezes the census as final (call after its context is closed). Idempotent. */
  seal() {
    this.sealed = true;
  }

  late() {
    if (this.sealed) this.lateEvents += 1;
  }

  onRequest(request) {
    this.late();
    const record = {
      ordinal: this.requests.length + 1,
      request,
      url: request.url(),
      method: request.method(),
      resourceType: request.resourceType(),
      isNavigation: request.isNavigationRequest(),
      frameKind: "unknown",
      frameUrl: "",
      ts: Date.now(),
      headers: null,
      headersUnavailable: false,
      body: null,
      response: null,
      ended: null,
    };
    try {
      const frame = request.frame();
      record.frameUrl = frame.url();
      record.frameKind = frame === frame.page().mainFrame() ? "main" : "child";
    } catch {
      record.frameKind = "worker";
    }
    try {
      record.body = request.postDataBuffer();
    } catch {
      record.body = null;
    }
    if (!isLocalUrl(record.url)) this.nonlocalAttempts += 1;
    this.requests.push(record);
    this.byRequest.set(request, record);
    // The ACTUAL header set (allHeaders) is the only complete one. When it rejects or does
    // not arrive within headerTimeoutMs, the request's header census is INCOMPLETE
    // (headersUnavailable): request.headers() is Playwright's PROVISIONAL set, which by its
    // documented contract does not return security-related headers (cookie-related among
    // them) and is not the set the network stack sent, so a field missing from it proves
    // nothing. It is kept only as extra leak evidence (its hits still count).
    let timer;
    let actual;
    try {
      actual = Promise.resolve(request.allHeaders());
    } catch (error) {
      actual = Promise.reject(error);
    }
    actual.catch(() => undefined); // a late rejection after the race must not surface unhandled
    record.headersDone = Promise.race([
      actual,
      new Promise((_, reject) => { timer = setTimeout(() => reject(new Error("timeout")), this.headerTimeoutMs); }),
    ]).then(
      (headers) => {
        if (headers && typeof headers === "object") record.headers = headers;
        else {
          record.headers = null;
          record.headersUnavailable = true;
        }
      },
      () => {
        record.headersUnavailable = true;
        try {
          const provisional = request.headers();
          record.headers = provisional && typeof provisional === "object" ? provisional : null;
        } catch {
          record.headers = null;
        }
      },
    ).finally(() => clearTimeout(timer));
  }

  onResponse(response) {
    const record = this.byRequest.get(response.request());
    if (!record) return;
    let contentType = "";
    try {
      contentType = response.headers()["content-type"] ?? "";
    } catch {
      contentType = "";
    }
    record.response = { status: response.status(), contentType, ts: Date.now() };
  }

  onEnded(request, kind) {
    const record = this.byRequest.get(request);
    if (record && !record.ended) record.ended = { kind, ts: Date.now() };
  }

  onPageEvent(source, event) {
    this.late();
    if (!event || typeof event !== "object") {
      this.droppedEvents += 1;
      return;
    }
    const clean = {};
    for (const [key, type] of Object.entries(EVENT_FIELDS)) {
      if (typeof event[key] === type) clean[key] = event[key];
    }
    if (typeof event.targetOrigin === "string" && TARGET_ORIGIN.test(event.targetOrigin)) clean.targetOrigin = event.targetOrigin;
    if (typeof clean.kind !== "string" || typeof clean.doc !== "string") {
      this.droppedEvents += 1;
      return;
    }
    let frameKind = "unknown";
    try {
      frameKind = source.frame === source.page.mainFrame() ? "main" : "child";
    } catch {
      frameKind = "unknown";
    }
    clean.frameKind = frameKind;
    clean.frame = source.frame;
    this.events.push(clean);
  }

  attachPage(page) {
    this.late();
    this.pages += 1;
    page.on("worker", () => { this.late(); this.workers += 1; });
    page.on("popup", () => { this.late(); this.popups += 1; });
    page.on("websocket", (socket) => {
      this.late();
      const url = socket.url();
      if (!isLocalUrl(url.replace(/^ws/, "http"))) this.nonlocalAttempts += 1;
      this.websockets.push({ url });
    });
  }

  /** Every request has its actual headers (or is counted as unavailable). */
  async settle() {
    await Promise.allSettled(this.requests.map((record) => record.headersDone));
  }

  /** The documents this context saw, in arrival order. */
  docs() {
    const docs = new Map();
    for (const event of this.events) {
      if (!docs.has(event.doc)) docs.set(event.doc, { doc: event.doc, frameKind: event.frameKind, start: null, events: [] });
      const doc = docs.get(event.doc);
      if (event.kind === "doc-start") doc.start = event;
      doc.events.push(event);
    }
    for (const doc of docs.values()) doc.events.sort((a, b) => a.seq - b.seq);
    return [...docs.values()];
  }

  /**
   * Requests whose headers carry `token` in any raw/encoded form (synthetic-key use), for
   * zero-use checks. Fail closed: a request whose ACTUAL headers are unknown (allHeaders
   * rejected or timed out) cannot prove the key was not used, so it counts as a use; its
   * provisional headers are not taken as complete.
   */
  async headerUses(token) {
    await this.settle();
    const variants = tokenVariants([token]);
    return this.requests.filter((record) => record.headersUnavailable === true || record.headers === null
      || Object.values(record.headers).some((value) => countHits(String(value), variants) > 0)).length;
  }

  /**
   * A24/A25/A11/A33/A34 over this context, value-free. `secrets` are every token
   * issued so far (memory only); `bearer` maps a Studio (or proxy) origin to the
   * tokens issued for it (a restarted server on the same port has two).
   */
  async audit(secrets, bearer = new Map()) {
    await this.settle();
    const variants = tokenVariants(secrets);
    const out = {
      requests: this.requests.length,
      urlHits: 0,
      fragmentNavigations: 0,
      refererPresent: 0,
      refererAbsent: 0,
      refererUnavailable: 0,
      headersIncomplete: 0,
      refererHits: 0,
      bearerOnStudioCalls: 0,
      bearerMisplaced: 0,
      studioCallsWithoutBearer: 0,
      otherHeaderHits: 0,
      bodyHits: 0,
      mainToStudio: 0,
      nonlocal: this.nonlocalAttempts,
      websockets: this.websockets.length,
      websocketHits: 0,
      workers: this.workers,
      serviceWorkers: this.serviceWorkers,
      pages: this.pages,
      popups: this.popups,
      droppedEvents: this.droppedEvents,
      lateEvents: this.lateEvents,
    };
    for (const record of this.requests) {
      const cut = record.url.indexOf("#");
      const sent = cut === -1 ? record.url : record.url.slice(0, cut);
      out.urlHits += countHits(sent, variants);
      if (cut !== -1 && record.isNavigation && countHits(record.url.slice(cut), variants) > 0)
        out.fragmentNavigations += 1;
      // Complete only with the ACTUAL header set. Without it the Referer and header-token
      // census of this request is INCOMPLETE (refererUnavailable, headersIncomplete): an
      // absent field in the provisional set is never counted as a proven absence, and no
      // bearer accounting is done from it. Any provisional headers kept are still scanned,
      // so a hit there counts (it can only add failures, never clear one).
      const headers = record.headers;
      const complete = headers !== null && typeof headers === "object" && record.headersUnavailable !== true;
      if (!complete) {
        out.refererUnavailable += 1;
        out.headersIncomplete += 1;
      }
      if (headers !== null && typeof headers === "object") {
        const referer = headers.referer;
        if (referer !== undefined) out.refererHits += countHits(String(referer), variants);
        if (complete) {
          if (referer === undefined) out.refererAbsent += 1;
          else out.refererPresent += 1;
        }
        const target = originOf(record.url);
        const studioCall =
          (record.frameKind === "child" || this.standalone) &&
          this.studioOrigins.has(target) &&
          originOf(record.frameUrl) === target &&
          new URL(record.url).pathname.startsWith("/studio/v1/");
        const auth = headers.authorization;
        const issued = bearer.get(target) ?? [];
        const isIssuedBearer = (value) => issued.some((token) => value === `Bearer ${token}`);
        if (studioCall && complete) {
          if (auth !== undefined && isIssuedBearer(auth)) out.bearerOnStudioCalls += 1;
          else if (auth === undefined) out.studioCallsWithoutBearer += 1;
        }
        for (const [name, value] of Object.entries(headers)) {
          if (name === "referer") continue;
          const found = countHits(String(value), variants);
          if (found === 0) continue;
          if (name === "authorization" && studioCall && isIssuedBearer(String(value))) continue;
          if (name === "authorization") out.bearerMisplaced += found;
          else out.otherHeaderHits += found;
        }
      }
      if (record.body) out.bodyHits += countHits(record.body.toString("utf8"), variants);
      if (!this.standalone && record.frameKind === "main" && this.studioOrigins.has(originOf(record.url))) out.mainToStudio += 1;
    }
    for (const socket of this.websockets) {
      const cut = socket.url.indexOf("#");
      out.websocketHits += countHits(cut === -1 ? socket.url : socket.url.slice(0, cut), variants);
    }
    return out;
  }
}

/**
 * E4 `beforeFirstPage`: installs the memory-only observer and the census on a
 * fresh context. E4 calls it as observe(context) (one argument); a profile, when
 * given, must be this one. Refuses a context that already has pages or an observer.
 */
async function beforeFirstPage(context, options = {}) {
  if (options.profile !== undefined && options.profile !== PROFILE) throw new ObserverRefusal("OBSERVER_PROFILE");
  if (context[INSTALLED] !== undefined) throw new ObserverRefusal("OBSERVER_TWICE");
  if (context.pages().length > 0) throw new ObserverRefusal("OBSERVER_LATE");
  const census = new Census(context, { label: options.label ?? "context", via: options.via ?? "e4" });
  context[INSTALLED] = census;
  await context.exposeBinding(BINDING_NAME, (source, event) => census.onPageEvent(source, event));
  await context.addInitScript({ content: script(pageObserver, { binding: BINDING_NAME }, oracles.structuredTexts) });
  context.on("request", (request) => census.onRequest(request));
  context.on("response", (response) => census.onResponse(response));
  context.on("requestfinished", (request) => census.onEnded(request, "finished"));
  context.on("requestfailed", (request) => census.onEnded(request, "failed"));
  context.on("page", (page) => census.attachPage(page));
  context.on("serviceworker", () => { census.late(); census.serviceWorkers += 1; });
  // Nothing leaves the machine: a nonlocal attempt is counted (onRequest) and aborted here.
  await context.route((url) => !isLocalUrl(url.href), (route) => route.abort("blockedbyclient"));
  return census;
}

/**
 * The fixture's only way to get a context's census. `install` comes from the
 * binding: 'e4' reuses the census the shared runner's hook installed inside
 * newContext (and refuses if it is absent or not this module's); 'direct'
 * installs it here (and refuses if something already did). Never installs twice,
 * never proceeds without one.
 */
async function acquireCensus(context, { label, install }) {
  if (!INSTALL_MODES.has(install)) throw new ObserverRefusal("OBSERVER_INSTALL_MODE");
  const existing = context[INSTALLED];
  if (existing === undefined) {
    if (install === "e4") throw new ObserverRefusal("OBSERVER_E4_ABSENT");
    const census = await beforeFirstPage(context, { label, via: "direct" });
    census.acquisitions = 1;
    return census;
  }
  if (install === "direct") throw new ObserverRefusal("OBSERVER_UNEXPECTED_INSTALL");
  if (!existing || existing[CENSUS_BRAND] !== MODULE_DIGEST || existing.profile !== PROFILE) {
    throw new ObserverRefusal("OBSERVER_FOREIGN");
  }
  if (existing.installs !== 1 || existing.pagesAtInstall !== 0 || context.pages().length > 0) {
    throw new ObserverRefusal("OBSERVER_LATE");
  }
  if (existing.acquisitions > 0) throw new ObserverRefusal("OBSERVER_TWICE");
  existing.acquisitions = 1;
  existing.label = label;
  return existing;
}

/** A29: arm the malformed-hello sender for frames on `studioOrigin` (new documents only). */
async function armMalformedHello(context, studioOrigin) {
  const census = context[INSTALLED];
  if (census) census.initScripts += 1;
  await context.addInitScript({ content: script(malformedHello, { studioOrigin }) });
}

module.exports = {
  BINDING_NAME,
  MODULE_DIGEST,
  ObserverRefusal,
  PROFILE,
  acquireCensus,
  armMalformedHello,
  beforeFirstPage,
  countHits,
  isLocalUrl,
  originOf,
  tokenVariants,
};

