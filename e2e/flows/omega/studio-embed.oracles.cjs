/* eslint-env node */
// J07 pure oracles: every decision the walk makes from observations it already
// holds in memory, kept here so each one runs offline (node, no browser) against
// its counterexamples. Nothing here touches a page, a socket or a file, and every
// result is a boolean, a count or a fixed code.
//
// Some functions are also serialized into pages by source text (structuredTexts,
// idbScanInPage): those stay self-contained ES5-style functions with no closure
// over this module.
"use strict";

// ---------------------------------------------------------------- deadlines

const realNow = () => performance.now();
const realSleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const TIMED_OUT = Symbol("j07.timedOut");

/** Resolves to the value, or TIMED_OUT once `ms` elapses (never rejects for a slow read). */
function withDeadline(promise, ms) {
  let timer;
  const expiry = new Promise((resolve) => {
    timer = setTimeout(() => resolve(TIMED_OUT), Math.max(0, ms));
  });
  return Promise.race([Promise.resolve(promise), expiry]).finally(() => clearTimeout(timer));
}

/**
 * Polls `fn` until it returns true within `ms`. A true that arrives only after the
 * deadline (a slow awaited read) is a miss, never a late success; each read is
 * bounded by the remaining budget.
 */
async function poll(fn, ms, every = 100, deps = {}) {
  const now = deps.now ?? realNow;
  const sleep = deps.sleep ?? realSleep;
  const until = now() + ms;
  for (;;) {
    const remaining = until - now();
    if (remaining < 0) return false;
    let ok = false;
    try {
      const value = await withDeadline(fn(), remaining);
      ok = value !== TIMED_OUT && Boolean(value);
    } catch {
      ok = false;
    }
    if (now() > until) return false;
    if (ok) return true;
    await sleep(Math.max(0, Math.min(every, until - now())));
  }
}

/**
 * A22 agreement-then-hold over injected observations (adopted from OSS
 * gone-and-cut agreeAndHold). `observe()` returns { agree, gate }; `retry()` is
 * the existing "Try again" action, bounded by the remaining budget. The deadline
 * is checked after every awaited read: agreement first seen after `ms` fails.
 *
 * The hold is SAMPLED, not assumed: the agreeing observation is sample 0, and every
 * later sample must complete within `maxGapMs` of the previous one (the 250 ms
 * cadence plus one bounded read; default 2 x sampleMs). Each hold read is bounded by
 * that remaining gap budget, so a slow read that would consume the window fails as
 * HOLD_SAMPLE_GAP instead of ending the hold successfully, and the hold continues
 * until a sample completes at or after `holdMs`: the samples span the whole window.
 */
async function agreeAndHold({ observe, retry, ms, holdMs = 3_000, sampleMs = 250, maxGapMs, now = realNow, sleep = realSleep }) {
  const gapLimit = Number.isFinite(maxGapMs) && maxGapMs > 0 ? maxGapMs : sampleMs * 2;
  const began = now();
  let retries = 0;
  const fail = (code, agreed, samples, extra = {}) => ({ ok: false, code, agreed, samples, retries, maxGap: 0, span: 0, ...extra });
  for (;;) {
    const remaining = ms - (now() - began);
    if (remaining <= 0) return fail("NO_AGREEMENT_WITHIN_DEADLINE", null, 0);
    let seen;
    try {
      seen = await withDeadline(observe(), remaining);
    } catch {
      seen = null;
    }
    if (now() - began > ms) return fail("NO_AGREEMENT_WITHIN_DEADLINE", null, 0);
    if (seen && seen !== TIMED_OUT && seen.gate === "unreachable") {
      retries += 1;
      try {
        await withDeadline(retry(Math.max(1, Math.min(1_000, ms - (now() - began)))), Math.max(1, ms - (now() - began)));
      } catch {
        /* the action itself is bounded; the next read decides */
      }
      await sleep(200);
      continue;
    }
    if (seen && seen !== TIMED_OUT && seen.agree === true) break;
    await sleep(50);
  }
  const holdFrom = now();
  const agreed = holdFrom - began;
  if (agreed > ms) return fail("NO_AGREEMENT_WITHIN_DEADLINE", agreed, 0);
  let samples = 0;
  let last = holdFrom;
  let maxGap = 0;
  while (last - holdFrom < holdMs) {
    await sleep(Math.max(0, Math.min(sampleMs, last + gapLimit - now())));
    const budget = last + gapLimit - now();
    if (budget <= 0) return fail("HOLD_SAMPLE_GAP", agreed, samples, { maxGap: Math.round(now() - last), span: Math.round(last - holdFrom) });
    let seen;
    try {
      seen = await withDeadline(observe(), budget);
    } catch {
      seen = null;
    }
    const at = now();
    const gap = at - last;
    maxGap = Math.max(maxGap, gap);
    if (seen === TIMED_OUT || gap > gapLimit)
      return fail("HOLD_SAMPLE_GAP", agreed, samples, { maxGap: Math.round(maxGap), span: Math.round(last - holdFrom) });
    if (!seen || seen.agree !== true) return fail("AGREEMENT_NOT_HELD", agreed, samples, { maxGap: Math.round(maxGap), span: Math.round(at - holdFrom) });
    samples += 1;
    last = at;
  }
  const span = last - holdFrom;
  if (samples === 0 || span < holdMs || maxGap > gapLimit)
    return fail("HOLD_SAMPLE_GAP", agreed, samples, { maxGap: Math.round(maxGap), span: Math.round(span) });
  return { ok: true, code: null, agreed: Math.round(agreed), samples, retries, maxGap: Math.round(maxGap), span: Math.round(span) };
}

// ---------------------------------------------------------------- structured clone walker

/**
 * Every string a structured-clone value can carry: own keys and values of objects
 * and arrays, Map keys/values, Set members, boxed strings, RegExp source, Error
 * fields, and the bytes of ArrayBuffers and views (as latin1 and as UTF-16LE).
 * Blobs are returned for an asynchronous read. Host objects it cannot read are
 * counted as `unsupported` (incomplete coverage), never treated as clean.
 * Self-contained: serialized into pages by source text.
 */
function structuredTexts(value) {
  var out = { texts: [], blobs: [], unsupported: 0 };
  var seen = typeof Set === "function" ? new Set() : null;
  var toString = Object.prototype.toString;
  var latin1 = function (bytes) {
    var s = "";
    for (var i = 0; i < bytes.length; i += 4096) {
      s += String.fromCharCode.apply(null, Array.prototype.slice.call(bytes, i, Math.min(bytes.length, i + 4096)));
    }
    return s;
  };
  var utf16 = function (bytes) {
    var s = "";
    for (var i = 0; i + 1 < bytes.length; i += 2) s += String.fromCharCode(bytes[i] | (bytes[i + 1] << 8));
    return s;
  };
  var bytesOf = function (v, tag) {
    if (tag === "[object ArrayBuffer]" || tag === "[object SharedArrayBuffer]") return new Uint8Array(v);
    if (typeof ArrayBuffer !== "undefined" && ArrayBuffer.isView && ArrayBuffer.isView(v)) {
      return new Uint8Array(v.buffer, v.byteOffset, v.byteLength);
    }
    return null;
  };
  var visit = function (v, depth) {
    if (v === null || v === undefined) return;
    var type = typeof v;
    if (type === "string") { out.texts.push(v); return; }
    if (type === "number" || type === "boolean" || type === "bigint") return;
    if (type !== "object") { out.unsupported += 1; return; }
    if (seen) {
      if (seen.has(v)) return;
      seen.add(v);
    }
    if (depth > 64) { out.unsupported += 1; return; }
    var tag;
    try { tag = toString.call(v); } catch (e) { out.unsupported += 1; return; }
    var bytes = bytesOf(v, tag);
    if (bytes) { out.texts.push(latin1(bytes), utf16(bytes)); return; }
    if (tag === "[object Blob]" || tag === "[object File]") {
      out.blobs.push(v);
      if (typeof v.name === "string") out.texts.push(v.name);
      if (typeof v.type === "string") out.texts.push(v.type);
      return;
    }
    if (tag === "[object Map]") {
      v.forEach(function (val, key) { visit(key, depth + 1); visit(val, depth + 1); });
      return;
    }
    if (tag === "[object Set]") {
      v.forEach(function (val) { visit(val, depth + 1); });
      return;
    }
    if (tag === "[object Date]") return;
    if (tag === "[object RegExp]") { out.texts.push(String(v.source), String(v.flags)); return; }
    if (tag === "[object Error]") {
      out.texts.push(String(v.name), String(v.message));
      if (typeof v.stack === "string") out.texts.push(v.stack);
      if ("cause" in v) visit(v.cause, depth + 1);
      return;
    }
    if (tag === "[object String]") out.texts.push(String(v.valueOf()));
    if (tag === "[object FileList]") {
      for (var f = 0; f < v.length; f += 1) visit(v[f], depth + 1);
      return;
    }
    if (tag === "[object ImageData]") { visit(v.data, depth + 1); return; }
    var plain = tag === "[object Object]" || tag === "[object Array]" || tag === "[object String]"
      || tag === "[object Number]" || tag === "[object Boolean]" || tag === "[object Arguments]";
    if (!plain) out.unsupported += 1; // CryptoKey, FileSystemHandle, ImageBitmap, ...: unread bytes
    var keys;
    try { keys = Object.keys(v); } catch (e) { out.unsupported += 1; return; }
    for (var k = 0; k < keys.length; k += 1) {
      out.texts.push(keys[k]);
      var item;
      try { item = v[keys[k]]; } catch (e) { out.unsupported += 1; continue; }
      visit(item, depth + 1);
    }
  };
  visit(value, 0);
  return out;
}

/**
 * A07 in-page IndexedDB scan: enumerates every database and object store of the
 * document's origin and walks names, key paths, index names, keys and values with
 * `walk` (structuredTexts, passed by source), then reads every Blob. Unopenable
 * databases and unreadable stores count as `unreadable`. Self-contained.
 */
async function idbScanInPage(walk) {
  var out = { enumerated: false, databases: 0, stores: 0, records: 0, texts: [], unsupported: 0, unreadable: 0 };
  var blobs = [];
  var take = function (value) {
    var r = walk(value);
    for (var i = 0; i < r.texts.length; i += 1) out.texts.push(r.texts[i]);
    for (var j = 0; j < r.blobs.length; j += 1) blobs.push(r.blobs[j]);
    out.unsupported += r.unsupported;
  };
  var factory = window.indexedDB;
  if (!factory || typeof factory.databases !== "function") return out;
  out.enumerated = true;
  var list = [];
  try { list = await factory.databases(); } catch (e) { out.unreadable += 1; return out; }
  for (var d = 0; d < list.length; d += 1) {
    var name = list[d] && list[d].name;
    if (typeof name !== "string") { out.unreadable += 1; continue; }
    out.databases += 1;
    out.texts.push(name);
    var db = await new Promise(function (resolve) {
      var request;
      try { request = factory.open(name); } catch (e) { resolve(null); return; }
      request.onsuccess = function () { resolve(request.result); };
      request.onerror = function () { resolve(null); };
      request.onblocked = function () { resolve(null); };
      // A database that vanished meanwhile must not be created by this read.
      request.onupgradeneeded = function () { try { request.transaction.abort(); } catch (e) { /* aborted */ } };
    });
    if (!db) { out.unreadable += 1; continue; }
    var stores = Array.prototype.slice.call(db.objectStoreNames);
    for (var s = 0; s < stores.length; s += 1) {
      out.stores += 1;
      var got = await new Promise(function (resolve) {
        try {
          var tx = db.transaction(stores[s], "readonly");
          var store = tx.objectStore(stores[s]);
          var meta = [store.name, store.keyPath];
          Array.prototype.forEach.call(store.indexNames, function (index) {
            meta.push(index);
            try { meta.push(store.index(index).keyPath); } catch (e) { /* listed name kept */ }
          });
          var all = store.getAll();
          var keys = store.getAllKeys();
          tx.oncomplete = function () { resolve({ ok: true, meta: meta, values: all.result, keys: keys.result }); };
          tx.onerror = function () { resolve({ ok: false }); };
          tx.onabort = function () { resolve({ ok: false }); };
        } catch (e) {
          resolve({ ok: false });
        }
      });
      if (!got.ok) { out.unreadable += 1; continue; }
      out.records += (got.values || []).length;
      take(got.meta);
      take(got.keys);
      take(got.values);
    }
    try { db.close(); } catch (e) { /* closed */ }
  }
  // The observer's captured native Blob reader (a page override cannot hide the bytes).
  var api = window[Symbol.for("j07.observer")];
  var readBytes = api && typeof api.blobBytes === "function" ? api.blobBytes : null;
  for (var b = 0; b < blobs.length; b += 1) {
    try {
      if (!readBytes) throw new Error("no native reader");
      var bytes = await readBytes(blobs[b]);
      take(bytes);
    } catch (e) {
      out.unreadable += 1;
    }
  }
  return out;
}

// ---------------------------------------------------------------- observer analyses

const isWriteOp = (op) => op.startsWith("storage.") || op.startsWith("idb.") || op === "cookie.set"
  || op === "pushState" || op === "window.open";

/** Writes or navigations any document made while a connect fragment was in its address bar. */
function connectPhaseWrites(docs) {
  let count = 0;
  for (const doc of docs) {
    for (const event of doc.events) {
      if (event.kind !== "op" || typeof event.op !== "string") continue;
      if (event.before === true && isWriteOp(event.op)) count += 1;
      if (event.op === "replaceState" && event.before === true && event.after === true) count += 1;
    }
  }
  return count;
}

/** Token occurrences any observed write/history operation carried (structured, memory-only counts). */
function writeHits(docs) {
  let hits = 0;
  for (const doc of docs) for (const event of doc.events) if (event.kind === "op") hits += event.hits ?? 0;
  return hits;
}

/** Blob reads per document: started (op read.blob.start) and settled (op read.blob), by ordinal. */
function blobReadCounts(doc) {
  const started = new Set();
  const settled = new Set();
  for (const event of doc.events) {
    if (event.kind !== "op" || typeof event.blob !== "number") continue;
    if (event.op === "read.blob.start") started.add(event.blob);
    else if (event.op === "read.blob") settled.add(event.blob);
  }
  let pending = 0;
  for (const id of started) if (!settled.has(id)) pending += 1;
  return { started: started.size, settled: settled.size, pending };
}

/** Blob reads a write started that never reported back (a discarded document, a hung read). */
function pendingBlobReads(docs) {
  return docs.reduce((sum, doc) => sum + blobReadCounts(doc).pending, 0);
}

/**
 * The storage barrier for one live document: its in-page counts say every Blob read it
 * started has settled, and Node has received the settle event of each of them.
 *   inPage: { doc, started, settled } from the observer API; docs: census.docs()
 */
function blobBarrierSettled(inPage, docs) {
  if (!inPage || typeof inPage.doc !== "string" || inPage.started !== inPage.settled) return false;
  const doc = docs.find((item) => item.doc === inPage.doc);
  const seen = doc ? blobReadCounts(doc) : { started: 0, settled: 0, pending: 0 };
  return seen.started === inPage.started && seen.settled === inPage.started && seen.pending === 0;
}

/**
 * Write payloads the observer could not read, so A07 write coverage is INCOMPLETE:
 * every unreadable structured-clone value of any observed write/history operation,
 * every failed or unsupported Blob read (op read.blob), and every Blob read that was
 * started and never settled. Never zero just because a read failed quietly.
 */
function writeUnsupported(docs) {
  let count = 0;
  for (const doc of docs) {
    for (const event of doc.events) {
      if (event.kind === "op" && typeof event.op === "string" && event.op !== "read.blob.start") count += event.unsupported ?? 0;
    }
    count += blobReadCounts(doc).pending;
  }
  return count;
}

/**
 * A03 for a document that started with a connect fragment:
 *  - no executable non-intake script was inserted before the intake while the fragment was live;
 *  - its FIRST operation is the intake's native replaceState that removes connect/t;
 *  - a witness taken before the first FOLLOWING script ran (never DOMContentLoaded alone)
 *    saw no token and no extra history entry.
 */
function intakeVerdict(doc) {
  const events = doc ? doc.events : [];
  const ops = events.filter((event) => event.kind === "op");
  const first = ops[0];
  const firstOpScrub = Boolean(first) && first.op === "replaceState" && first.before === true
    && first.after === false && first.argConnect === false;
  const intakeSeen = events.some((event) => event.kind === "script" && event.intake === true);
  const preIntakeScripts = events.filter((event) => event.kind === "script" && event.pre === true).length;
  const witness = events.find((event) => event.kind === "witness");
  const witnessed = Boolean(witness) && witness.at === "next-script";
  const witnessClean = witnessed && witness.connect === false && witness.historyDelta === 0 && (witness.hits ?? 0) === 0;
  return {
    intakeSeen,
    preIntakeScripts,
    firstOpScrub,
    witnessed,
    witnessClean,
    pass: intakeSeen && preIntakeScripts === 0 && firstOpScrub && witnessed && witnessClean,
  };
}

// ---------------------------------------------------------------- A10/A12 sender witness

const ORIGIN_SHAPE = /^https?:\/\/[A-Za-z0-9.[\]:-]+$/;

/**
 * A10 over the host's own postMessage calls into the bound frame (sender side;
 * MessageEvent never exposes targetOrigin): every embed.* send to the bound frame
 * names exactly `studioOrigin`, none uses '*', and every key send was witnessed.
 */
function senderVerdict(posts, studioOrigin) {
  const embed = posts.filter((post) => typeof post.type === "string" && post.type.startsWith("embed."));
  const toBound = embed.filter((post) => post.toBound === true);
  const keyPosts = embed.filter((post) => post.hasToken === true);
  const wildcard = embed.filter((post) => post.targetOrigin === "*").length;
  const notPinned = toBound.filter((post) => post.targetOrigin !== studioOrigin).length;
  const keyNotPinned = keyPosts.filter((post) => post.targetOrigin !== studioOrigin || post.toBound !== true).length;
  return {
    embed: embed.length,
    toBound: toBound.length,
    keyPosts: keyPosts.length,
    wildcard,
    notPinned,
    keyNotPinned,
    pinnedOriginShaped: ORIGIN_SHAPE.test(String(studioOrigin)),
    pass: wildcard === 0 && notPinned === 0 && keyNotPinned === 0 && ORIGIN_SHAPE.test(String(studioOrigin)),
  };
}

/**
 * A12 pending-replacement precondition and outcome from the host seam state:
 * an accepted hello was seen from the bound frame, its first frame-window read
 * queued the replacement before the token result resolved, and the bridge read
 * the frame window again while the replacement was in effect (its post-mint
 * recheck). Outcome: no key was posted during the seam and none was received by
 * either window.
 */
function pendingReplacementVerdict(seam, receivedKeys) {
  const established = Boolean(seam) && seam.helloSeen === true && seam.pendingSeen === true
    && seam.redirectedReads >= 1 && seam.stage === "done";
  const delivered = (seam ? seam.keyPosts : 1) + receivedKeys;
  return { established, delivered, pass: established && delivered === 0 };
}

// ---------------------------------------------------------------- A21/A22 cut and restore

/**
 * A21 cut witness, evaluated while forwarding is still refused: the proxy reset at
 * least one live socket pair, and the exact witnessed run stream ended as a
 * failure inside [cutWall, evaluatedAt]. A no-op cut, a natural completion
 * ('finished') or a closure seen only after restore fails.
 */
function cutWitness({ ended, cutWall, evaluatedAt, destroyed }) {
  const closedDuringRefusal = Boolean(ended) && ended.ts >= cutWall && ended.ts <= evaluatedAt;
  const failed = Boolean(ended) && ended.kind === "failed";
  return {
    destroyed: Number.isInteger(destroyed) ? destroyed : 0,
    closedDuringRefusal,
    failed,
    pass: Number.isInteger(destroyed) && destroyed > 0 && closedDuringRefusal && failed,
  };
}

const RANK = Object.freeze({
  queued: 0, pending: 0, paused: 1, running: 2, waiting: 2, interrupted: 2, succeeded: 4, failed: 4, cancelled: 4,
});
const rankOf = (status) => (typeof status === "string" && Object.prototype.hasOwnProperty.call(RANK, status) ? RANK[status] : null);
const isoMs = (value) => {
  if (typeof value !== "string") return null;
  const ms = Date.parse(value);
  return Number.isFinite(ms) ? ms : null;
};

function sseData(text) {
  const out = [];
  for (const frame of String(text).split(/\r?\n\r?\n/)) {
    const data = frame.split(/\r?\n/).filter((line) => line.startsWith("data:")).map((line) => line.slice(5).replace(/^ /, ""));
    if (data.length === 0) continue;
    try {
      out.push(JSON.parse(data.join("\n")));
    } catch {
      /* not JSON: no state */
    }
  }
  return out;
}

/** Run states a response body carries for `runId` (JSON: any object with that id; SSE: its studio.end frame). */
function runStates(candidate, runId) {
  const states = [];
  if (candidate.kind === "sse") {
    for (const data of sseData(candidate.text)) {
      if (data && data.kind === "studio.end" && data.runId === runId && typeof data.status === "string") {
        states.push({ status: data.status, updatedAt: null });
      }
    }
    return states;
  }
  let parsed;
  try {
    parsed = JSON.parse(candidate.text);
  } catch {
    return states;
  }
  const seen = new Set();
  const visit = (node) => {
    if (!node || typeof node !== "object" || seen.has(node)) return;
    seen.add(node);
    if (!Array.isArray(node) && node.id === runId && typeof node.status === "string") {
      states.push({ status: node.status, updatedAt: typeof node.updatedAt === "string" ? node.updatedAt : null });
    }
    for (const value of Object.values(node)) visit(value);
  };
  visit(parsed);
  return states;
}

/**
 * A22: a post-restore response for the same run carries a state strictly later
 * than the snapshot taken at the cut (higher status rank, or the same rank with a
 * later updatedAt). A stale reconciliation (same state) or no readable response fails.
 */
function newerThanCut(cut, candidates, runId) {
  const cutRank = rankOf(cut && cut.status);
  const cutAt = isoMs(cut && cut.updatedAt);
  let states = 0;
  let newer = 0;
  for (const candidate of candidates) {
    for (const state of runStates(candidate, runId)) {
      states += 1;
      const rank = rankOf(state.status);
      if (rank === null || cutRank === null) continue;
      const later = rank > cutRank
        || (rank === cutRank && cutAt !== null && isoMs(state.updatedAt) !== null && isoMs(state.updatedAt) > cutAt);
      if (later) newer += 1;
    }
  }
  return { candidates: candidates.length, states, newer, pass: newer > 0 };
}

// ---------------------------------------------------------------- A15/A16 theme choice

/**
 * The frame's own explicit theme command by real UI. Studio's toggle cycles its stored
 * CHOICE (bound OSS theme.ts: system -> light -> dark -> system; 'system' removes the
 * persisted key), so a rendered color alone is not the choice: system can render light.
 * `read()` returns { choice, stored, rendered }, each 'light' | 'dark' | 'system' | 'none'
 * | 'other' (the page maps them; no raw value leaves it). `click()` presses the toggle;
 * `next(previous)` waits (bounded) for the choice to move and returns the new state.
 * Done only when the explicit choice AND the rendered theme are `want`, and, with
 * `requireStored`, the persisted key holds exactly `want`. At most `maxClicks` presses.
 */
async function chooseTheme({ read, click, next, want, requireStored = false, maxClicks = 3 }) {
  const done = (state) => Boolean(state) && state.choice === want && state.rendered === want
    && (!requireStored || state.stored === want);
  let state = await read();
  let clicks = 0;
  while (!done(state) && clicks < maxClicks) {
    await click();
    clicks += 1;
    state = await next(state);
  }
  return { ok: done(state), clicks, choice: state ? state.choice : "other", stored: state ? state.stored : "other",
    rendered: state ? state.rendered : "other" };
}

// ---------------------------------------------------------------- A26/A27 log coverage

const countIn = (text, needles) => {
  if (typeof text !== "string" || text === "") return 0;
  let hits = 0;
  for (const needle of needles) {
    if (typeof needle !== "string" || needle === "") continue;
    for (let at = text.indexOf(needle); at >= 0; at = text.indexOf(needle, at + needle.length)) hits += 1;
  }
  return hits;
};

/**
 * Studio access-log coverage, PER (DECLARED FILE, CHILD) PAIR: every started child got
 * its own non-secret start marker (when it started) and end marker (just before it was
 * stopped). For EVERY declared studio-access file and EVERY child, the file holds both
 * of that child's markers, start before end, or neither: a file with the start but not
 * the end is a truncated sink for that child (endUncovered), a file with the end but not
 * the start lost the child's head (startUncovered), and an end that precedes every start
 * is out of order (orderInverted). A complete aggregate file never excuses a truncated
 * per-child file: each file is judged on its own. Some declared file must hold each
 * child's start (none: that child's log was not declared, BLOCKED). Every declared file
 * must be attributable: a file holding no marker of any child is foreignFiles (a file
 * holding only partial windows already fails by the counts above, so a passing file
 * always holds at least one complete start..end window), and none may be empty.
 *   children: [{ start, end, startSent, endSent }]; texts: declared file contents
 */
function studioLogCoverage(children, texts) {
  const marked = (value) => typeof value === "string" && value !== "";
  const files = Array.isArray(texts) ? texts.map((text) => (typeof text === "string" ? text : "")) : [];
  let startUnsent = 0;
  let endUnsent = 0;
  let undeclared = 0;
  let endUncovered = 0;
  let startUncovered = 0;
  let orderInverted = 0;
  let unmarked = 0;
  const touched = files.map(() => false);
  for (const child of children) {
    if (child.startSent !== true) startUnsent += 1;
    if (child.endSent !== true) endUnsent += 1;
    if (!marked(child.start) || !marked(child.end) || child.start === child.end) {
      unmarked += 1;
      continue;
    }
    let holdsStart = 0;
    files.forEach((text, index) => {
      const firstStart = text.indexOf(child.start);
      const lastEnd = text.lastIndexOf(child.end);
      const hasStart = firstStart >= 0;
      const hasEnd = lastEnd >= 0;
      if (hasStart) holdsStart += 1;
      if (hasStart || hasEnd) touched[index] = true;
      if (hasStart && !hasEnd) endUncovered += 1;
      else if (!hasStart && hasEnd) startUncovered += 1;
      else if (hasStart && hasEnd && lastEnd < firstStart) orderInverted += 1;
    });
    if (holdsStart === 0) undeclared += 1;
  }
  const foreignFiles = touched.filter((ok) => !ok).length;
  const emptyFiles = files.filter((text) => text.length === 0).length;
  return {
    children: children.length, files: files.length, startUnsent, endUnsent, undeclared, unmarked, endUncovered, startUncovered,
    orderInverted, foreignFiles, emptyFiles,
    blocked: children.length === 0 || undeclared > 0,
    pass: children.length > 0 && files.length > 0 && startUnsent === 0 && endUnsent === 0 && undeclared === 0 && unmarked === 0
      && endUncovered === 0 && startUncovered === 0 && orderInverted === 0 && foreignFiles === 0 && emptyFiles === 0,
  };
}

// ---------------------------------------------------------------- teardown closure

/**
 * Whether one browser context may be SEALED (its census accepted as final). Closure is
 * proven, never inferred: `outcome` is how context.close() settled ('resolved',
 * 'rejected' or 'timeout'), `closeEvent` whether the context's own 'close' event was
 * witnessed, `listed` whether the browser still lists it in browser.contexts(). In
 * Playwright 1.62.1 close() marks the context 'closing' (isClosed() true) BEFORE its
 * pre-close hooks and the channel close run, so a rejected close can leave a live
 * context whose every later close() is a no-op: a rejected or timed-out close is never
 * sealed, even if the context closes later (CONTEXT_CLOSE_FAILED), and a resolved close
 * still needs both witnesses (CONTEXT_NOT_CLOSED).
 */
function closeVerdict({ outcome, closeEvent, listed }) {
  if (outcome !== "resolved") return { closed: false, code: "CONTEXT_CLOSE_FAILED" };
  if (closeEvent !== true || listed !== false) return { closed: false, code: "CONTEXT_NOT_CLOSED" };
  return { closed: true, code: null };
}

// ---------------------------------------------------------------- A22 node agreement

/**
 * The canvas node-id set observed BEFORE the cut (round-4 review): A22 must compare every
 * restored observation with the graph the frame showed while the run was live, not with
 * whatever set first agrees. ok requires a nonempty canvas, no element without an id and
 * no duplicated id (element count === distinct ids).
 */
function canvasNodeKey({ nodes, total, unidentified = 0 }) {
  const ids = nodes && typeof nodes === "object" ? Object.keys(nodes) : [];
  const codes = [];
  if (!Number.isInteger(total) || total <= 0 || ids.length === 0) codes.push("pre-cut-canvas-empty");
  if (unidentified !== 0 || ids.some((id) => id === "")) codes.push("pre-cut-node-unidentified");
  if (Number.isInteger(total) && total !== ids.length) codes.push("pre-cut-node-duplicated");
  return { ok: codes.length === 0, codes, key: ids.slice().sort().join("\n") };
}

/**
 * The node half of ONE A22 agreement observation, taken only after direct truth reported
 * the run succeeded (packet A22: "every node be done"). Every observation, the initial
 * agreeing one and every hold sample, must see: the run succeeded per direct truth, a
 * nonempty canvas (`total` studio-node elements, every one with an identity), EVERY node
 * element in the terminal success state 'done' (`done` === `total`; 'static', null,
 * 'failed', 'running', 'waiting' or a missing status all break agreement), exactly one
 * element per node id (no duplicated card), and the pre-cut canvas node-id set
 * (canvasNodeKey) passed as `pinned`.
 *   nodes: { [nodeId]: status }; total/done: in-page element counts; returns codes + key.
 */
function nodesDoneAgreement({ run, nodes, total, done, unidentified = 0, pinned = null }) {
  const codes = [];
  const entries = nodes && typeof nodes === "object" ? Object.entries(nodes) : [];
  if (run !== "succeeded") codes.push("run-not-succeeded");
  if (!Number.isInteger(total) || total <= 0 || entries.length === 0) codes.push("nodes-empty");
  if (unidentified !== 0 || entries.some(([id]) => id === "")) codes.push("node-unidentified");
  if (!Number.isInteger(done) || done !== total || entries.some(([, status]) => status !== "done")) codes.push("node-not-done");
  // Duplicate cards collapse into one map entry: the element count must equal the distinct ids.
  if (Number.isInteger(total) && total !== entries.length) codes.push("node-duplicated");
  const key = entries.map(([id]) => id).sort().join("\n");
  if (pinned !== null && key !== pinned) codes.push("node-set-changed");
  return { codes, key, agree: codes.length === 0 };
}

const KEYED_LOCAL_LINK = /^Open http:\/\/127\.0\.0\.1:\d{2,5}\/#t=[A-Za-z0-9_-]{43}$/;

/**
 * A27 over one child's COMPLETE stdout and stderr (release-lead ruling (b), OSS
 * 79164e25f3): stdout is a pipe and pages may frame Studio, so the plain local link is
 * printed as the literal `Open <serverUrl>/#t=<key>`, the key occurs EXACTLY ONCE on
 * stdout (inside the one "Open in Future AGI" line, which is the printed link) and
 * ZERO times on stderr. Counts only; `own`/`others` are the token variants in memory.
 */
function childOutputAudit({ stdout, stderr, link, serverUrl, own, others, faPrefix = "Open in Future AGI: " }) {
  const lines = String(stdout).split("\n");
  let allowedLine = 0;
  let faLines = 0;
  let elsewhere = 0;
  let stdoutKeyHits = 0;
  let otherTokenHits = 0;
  let plainOpenLiteral = 0;
  let plainOpenKeyed = 0;
  for (const raw of lines) {
    const line = raw.replace(/\r$/, "");
    const found = countIn(line, own);
    stdoutKeyHits += found;
    otherTokenHits += countIn(line, others);
    if (line === `Open ${serverUrl}/#t=<key>`) plainOpenLiteral += 1;
    if (KEYED_LOCAL_LINK.test(line)) plainOpenKeyed += 1;
    if (line.startsWith(faPrefix)) faLines += 1;
    if (allowedLine === 0 && line.startsWith(faPrefix) && line.slice(faPrefix.length).trim() === link && found === 1) allowedLine = 1;
    else elsewhere += found;
  }
  const stderrHits = countIn(String(stderr), own);
  otherTokenHits += countIn(String(stderr), others);
  return {
    allowedLine, faLines, elsewhere, stdoutKeyHits, stderrHits, otherTokenHits, plainOpenLiteral, plainOpenKeyed,
    pass: allowedLine === 1 && faLines === 1 && elsewhere === 0 && stdoutKeyHits === 1 && stderrHits === 0
      && otherTokenHits === 0 && plainOpenLiteral === 1 && plainOpenKeyed === 0,
  };
}

// ---------------------------------------------------------------- A19 fixture seam

/**
 * The scoped A19 wrong-origin-parent seam: Studio's own document response with
 * only its framing denial removed (CSP frame-ancestors and X-Frame-Options), so a
 * real Studio child can run under an unlisted parent and its own parent-origin
 * check is the only thing left to refuse. Used in that one arm's context only.
 */
function withoutFramingDenial(headers) {
  const out = {};
  let removed = 0;
  for (const [name, value] of Object.entries(headers)) {
    const lower = name.toLowerCase();
    if (lower === "x-frame-options") { removed += 1; continue; }
    if (lower === "content-security-policy") {
      const kept = String(value).split(";").map((part) => part.trim()).filter((part) => part !== "");
      const without = kept.filter((part) => !/^frame-ancestors(\s|$)/i.test(part));
      removed += kept.length - without.length;
      if (without.length > 0) out[name] = without.join("; ");
      continue;
    }
    out[name] = value;
  }
  return { headers: out, removed };
}

module.exports = {
  RANK,
  TIMED_OUT,
  agreeAndHold,
  blobBarrierSettled,
  blobReadCounts,
  childOutputAudit,
  chooseTheme,
  closeVerdict,
  connectPhaseWrites,
  cutWitness,
  idbScanInPage,
  intakeVerdict,
  newerThanCut,
  canvasNodeKey,
  nodesDoneAgreement,
  pendingBlobReads,
  pendingReplacementVerdict,
  poll,
  runStates,
  senderVerdict,
  sseData,
  structuredTexts,
  studioLogCoverage,
  withDeadline,
  withoutFramingDenial,
  writeHits,
  writeUnsupported,
};
