// The parent side of omega-embed/1, without React.
//
// It listens on the host window for messages from one frame. A message counts
// only when event.source is that frame's window AND event.origin is the
// frame's expected origin. The handshake:
//   child  -> parent  embed.hello {protocol, doc?, want?}
//   parent -> child   embed.key {token, doc?, left?}   (targetOrigin = expected origin)
// A hello with `want` (CP P7, 2-8) is answered with up to `want` keys from one
// mint, sent back to back, each saying how many are still `left`. The keys
// come from getTokens(want) (or getToken() for one) at that moment and are not
// kept afterwards; a mint that fails sends nothing. Every later message
// (route, open, gate, telemetry, badge) is ignored until a hello arrives.
//
// The frame's document (RL 1275A). A child may name its document in every
// hello and gate (`doc`, a nonce drawn once per document load); every key that
// answers such a hello echoes its doc. With `requireDoc` (the EE Console):
// - a hello or gate that names no document is ignored;
// - the doc of the hello Future AGI answers is the frame's current document,
//   and a gate counts only when it names that doc;
// - a previously unseen hello immediately ends its predecessor, aborts its
//   mint BEFORE reporting `document`, and is answered without waiting for load;
// - load never proves which document sent an earlier hello: that document may
//   have been aborted. Every load suspends readiness and cancels its mint, but
//   keeps the candidate nonce (not a load credit, and not yet retired);
// - only a fresh, one-use host probe's proof can resume that same candidate.
//   A different, non-retired hello replaces it immediately. Ordinary queued
//   hellos/gates cannot restore a suspended candidate's readiness;
// - retired identities are retained for the bridge's lifetime. Stateful
//   minters must ignore aborted results, not just outgoing keys.
// Safe pre-load auth AND post-load liveness require a probe-aware child. The
// older EE child ignores probes: after a load following hello it stays closed,
// rather than inheriting readiness. Arrival-order inference cannot fix that.
// No event the bridge reports carries a doc.
import { EMBED_ADDED_MESSAGE, keysWanted } from "./additions";
import {
  EMBED_DOCUMENT_MESSAGE,
  EMBED_MESSAGE,
  documentProbeMessage,
  keyMessage,
  navigateMessage,
  normalizeEmbedPath,
  parseChildMessage,
  sameOriginUrl,
  themeMessage,
} from "./protocol";

const PRODUCT_AVAILABILITY = new Set([
  "admitted",
  "paused",
  "withheld",
  "unknown",
]);

const isEmbedShaped = (data) =>
  data !== null &&
  typeof data === "object" &&
  typeof data.type === "string" &&
  data.type.startsWith("embed.");

/**
 * @param {object} options
 * @param {() => (Window|null)} options.getFrameWindow  the frame's contentWindow
 * @param {string} options.expectedOrigin  the only origin the frame may speak from
 * @param {(context?: {signal: AbortSignal}) => (string|null|Promise<string|null>)} [options.getToken]  one key
 * @param {(want: number, context?: {signal: AbortSignal}) => (string[]|Promise<string[]>)} [options.getTokens]
 *   up to `want` keys from one mint; preferred over getToken when given.
 *   With requireDoc, context.signal aborts when that document ends. A stateful
 *   minter must check it before applying either a successful or failed result.
 * @param {(event: object) => void} [options.onEvent]
 *   receives {kind: 'hello'|'key-sent'|'no-key'|'route'|'open'|'gate'|
 *   'telemetry'|'badge'|'document'|'rejected', ...}
 * @param {Window} [options.hostWindow]
 * @param {boolean} [options.requireDoc]  the child names its document (RL 1275A)
 */
export function createEmbedBridge({
  getFrameWindow,
  expectedOrigin,
  getToken,
  getTokens,
  onEvent = () => {},
  hostWindow = window,
  requireDoc = false,
}) {
  if (!expectedOrigin || expectedOrigin === "null") {
    throw new Error("omega-embed: an expected origin is required");
  }

  let disposed = false;
  let open = false; // a hello has been accepted from the current frame
  let minting = null; // the hello whose keys are being minted: {doc}
  let theme = null;
  let productAvailability = null; // unset for surfaces outside the Console
  let sentProductAvailability = null;
  let pendingPath = null;
  let routesSinceHello = 0;

  // A WindowProxy/load does not identify its document. Do not infer ownership
  // from hello order or keep an unclaimed load as admission credit.
  let currentDoc = null;
  let suspended = false;
  let loadChallenge = null;
  // A live proof survives a retryable mint failure. Its gate stays updateable
  // until a successful remint, and never survives another load or retirement.
  let revalidation = null;
  let documentMint = null;
  // Never evict: nonce randomness prevents collisions, not observed replays.
  const retiredDocs = new Set();

  const emit = (event) => {
    if (!disposed) onEvent(event);
  };

  const post = (message) => {
    const frameWindow = getFrameWindow();
    if (!frameWindow) return false;
    frameWindow.postMessage(message, expectedOrigin);
    return true;
  };

  const flushProductAvailability = () => {
    if (
      disposed ||
      !open ||
      productAvailability === null ||
      productAvailability === sentProductAvailability
    ) {
      return;
    }
    // Display state only: no identity, key, document nonce or extra fields.
    if (
      post({
        type: "product.availability",
        version: 1,
        products: { sandboxes: productAvailability },
      })
    ) {
      sentProductAvailability = productAvailability;
    }
  };

  const flushAfterHello = () => {
    if (theme) post(themeMessage(theme));
    if (pendingPath) {
      post(navigateMessage(pendingPath));
      pendingPath = null;
    }
    flushProductAvailability();
  };

  const cancelDocumentMint = () => {
    documentMint?.abort();
    documentMint = null;
    minting = null;
    revalidation = null;
    open = false;
    sentProductAvailability = null;
    routesSinceHello = 0;
  };

  // A new identity or frame element really retires the old doc. A bare load
  // cannot: it might be this candidate completing its own slow load.
  const forgetDocument = () => {
    cancelDocumentMint();
    if (currentDoc !== null) retiredDocs.add(currentDoc);
    currentDoc = null;
    suspended = false;
    loadChallenge = null;
  };

  const admitHello = (doc, proved = false) => {
    if (!doc || retiredDocs.has(doc)) return false;
    if (doc === currentDoc) {
      if (suspended && !proved) return false;
      if (proved) {
        suspended = false;
        loadChallenge = null;
        documentMint = new AbortController();
      }
      return true;
    }
    forgetDocument(); // cancellation precedes the readiness reset below
    currentDoc = doc;
    documentMint = new AbortController();
    emit({ kind: "document", cause: "hello" });
    return true;
  };

  const newLoadChallenge = () => {
    try {
      const bytes = hostWindow.crypto.getRandomValues(new Uint8Array(16));
      return hostWindow
        .btoa(String.fromCharCode(...bytes))
        .replace(/\+/g, "-")
        .replace(/\//g, "_")
        .replace(/=+$/, "");
    } catch {
      // No weak fallback: without fresh entropy the candidate stays suspended.
      return null;
    }
  };

  const ignoreForDoc = (type) =>
    emit({ kind: "rejected", reason: "doc", type });

  // Up to `want` usable keys; anything that is not a non-empty string is
  // dropped, and a failed mint is no keys at all.
  async function mintKeys(want, context) {
    try {
      if (getTokens) {
        const tokens = await (context
          ? getTokens(want, context)
          : getTokens(want));
        return Array.isArray(tokens)
          ? tokens
              .filter((t) => typeof t === "string" && t.length > 0)
              .slice(0, want)
          : [];
      }
      const token = getToken
        ? await (context ? getToken(context) : getToken())
        : null;
      return typeof token === "string" && token.length > 0 ? [token] : [];
    } catch {
      return [];
    }
  }

  // A hello while this document's mint is in flight is dropped. A load or
  // replacement hello cancels its context. A proof may carry the living
  // child's current gate: ready waits for a successful remint, including a
  // later refill after failure. Newer gates update the retained verdict.
  async function handshake(frameWindow, want, doc) {
    if (minting && minting.doc === doc) return;
    const context = requireDoc ? { signal: documentMint.signal } : undefined;
    const attempt = { doc, context, revalidation };
    minting = attempt;
    let tokens;
    try {
      tokens = await mintKeys(want, context);
    } finally {
      if (minting === attempt) minting = null;
    }
    // The frame may have navigated or unmounted while the keys were minted,
    // or (requireDoc) loaded another document in the same window.
    const gone =
      disposed ||
      getFrameWindow() !== frameWindow ||
      (requireDoc && (context.signal.aborted || doc !== currentDoc));
    if (gone) {
      tokens.length = 0;
      return;
    }
    open = true;
    routesSinceHello = 0;
    const count = tokens.length;
    if (count > 0) {
      // Back to back, so the child reads one answer: `left` counts down to
      // the last key, which carries none. Each echoes the hello's doc.
      tokens.forEach((token, index) => {
        frameWindow.postMessage(
          keyMessage(token, count - 1 - index, doc),
          expectedOrigin,
        );
      });
      tokens.length = 0;
      emit({ kind: "key-sent", count, want });
    } else {
      emit({ kind: "no-key", want });
    }
    const provedGate = attempt.revalidation?.gate;
    if (count > 0) revalidation = null;
    if (provedGate && (count > 0 || provedGate !== "ready")) {
      emit({ kind: "gate", state: provedGate });
    }
    flushAfterHello();
  }

  function handleMessage(event) {
    if (disposed) return;
    const frameWindow = getFrameWindow();
    const fromFrame = Boolean(frameWindow) && event.source === frameWindow;
    if (!fromFrame || event.origin !== expectedOrigin) {
      if (isEmbedShaped(event.data)) {
        emit({
          kind: "rejected",
          reason: fromFrame ? "origin" : "source",
          origin: event.origin,
          type: event.data.type,
        });
      }
      return;
    }
    const message = parseChildMessage(event.data);
    if (!message) return;

    if (message.type === EMBED_DOCUMENT_MESSAGE.proof) {
      if (!requireDoc) return;
      if (
        !suspended ||
        !loadChallenge ||
        message.challenge !== loadChallenge ||
        !admitHello(message.doc, true)
      ) {
        ignoreForDoc(message.type);
        return;
      }
      revalidation = { gate: message.state };
      emit({ kind: "hello", build: message.build ?? null });
      handshake(frameWindow, keysWanted(message), message.doc);
      return;
    }
    if (message.type === EMBED_MESSAGE.hello) {
      if (requireDoc && !admitHello(message.doc)) {
        ignoreForDoc(message.type);
        return;
      }
      emit({ kind: "hello", build: message.build ?? null });
      handshake(frameWindow, keysWanted(message), message.doc);
      return;
    }
    // After a live proof, keep newer gates during AND between mint attempts.
    // In particular, a failed attempt must not turn a subsequent ready into
    // admission before a successful remint. Before proof, queued candidate
    // gates still change nothing because revalidation is absent and open=false.
    if (
      requireDoc &&
      message.type === EMBED_MESSAGE.gate &&
      message.doc === currentDoc &&
      revalidation
    ) {
      revalidation.gate = message.state;
      if (!open || message.state === "ready") return;
    }
    if (!open) return;

    switch (message.type) {
      case EMBED_MESSAGE.route:
        routesSinceHello += 1;
        emit({
          kind: "route",
          path: message.path,
          first: routesSinceHello === 1,
        });
        break;
      case EMBED_MESSAGE.open: {
        const url = sameOriginUrl(message.href, hostWindow.location.origin);
        emit(
          url
            ? { kind: "open", url }
            : { kind: "rejected", reason: "open", type: message.type },
        );
        break;
      }
      case EMBED_MESSAGE.gate:
        // requireDoc: only the current document's word counts.
        if (requireDoc && (!message.doc || message.doc !== currentDoc)) {
          ignoreForDoc(message.type);
          break;
        }
        emit({ kind: "gate", state: message.state });
        break;
      case EMBED_ADDED_MESSAGE.telemetry:
        emit({ kind: "telemetry", message });
        break;
      case EMBED_ADDED_MESSAGE.badge:
        emit({ kind: "badge", inbox: message.inbox });
        break;
      default:
        break;
    }
  }

  hostWindow.addEventListener("message", handleMessage);

  return {
    // Parent route changed: tell the child (or hold it until the hello).
    navigate(path) {
      const clean = normalizeEmbedPath(path);
      if (!clean) return;
      if (open) post(navigateMessage(clean));
      else pendingPath = clean;
    },
    setTheme(mode) {
      theme = mode === "dark" ? "dark" : "light";
      if (open) post(themeMessage(theme));
    },
    // Hold the latest classifier value until the existing hello boundary.
    // Refills do not resend it; a new or revalidated session does.
    setProductAvailability(sandboxes) {
      if (disposed) return;
      productAvailability = PRODUCT_AVAILABILITY.has(sandboxes)
        ? sandboxes
        : "unknown";
      flushProductAvailability();
    },
    // Every load invalidates readiness and its pending mint. It neither
    // consumes an inferred load credit nor retires an ambiguous candidate.
    // Only a response to THIS load's fresh probe can resume that candidate.
    // Without requireDoc a load changes nothing (Studio compatibility).
    documentLoaded() {
      if (!requireDoc || disposed) return;
      cancelDocumentMint();
      suspended = currentDoc !== null;
      loadChallenge = suspended ? newLoadChallenge() : null;
      emit({ kind: "document", cause: "load" });
      if (loadChallenge) post(documentProbeMessage(loadChallenge));
    },
    // A replacement frame element retires its predecessor, with no load credit.
    reset() {
      forgetDocument();
    },
    isOpen: () => open,
    dispose() {
      disposed = true;
      forgetDocument();
      hostWindow.removeEventListener("message", handleMessage);
    },
  };
}
