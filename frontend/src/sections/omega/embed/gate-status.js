// The status a framed surface shows, as a pure step: what it shows now and
// what the omega-embed/1 bridge just reported give what it shows next.
// use-embed-session.js runs it; the timers and the no-hello diagnosis live
// there.
//
// When does a surface count as connected?
// - With `requireGate` (Studio, the EE Console: both pass it), only once the
//   child's gate says "ready". Handing a key over proves nothing: the child
//   may turn it down or never answer. A key handed over shows "checking"
//   until the child's gate reports; a gate state other than "ready",
//   "needs-key" or "refused" changes nothing, and no gate within the
//   session's bound shows needs-key. A key alone never turns a surface
//   connected.
// - Without it (the default), as soon as a key is handed over. The child's
//   gate can still turn it to needs-key or refused.
//
// A new document in the frame (the bridge reports it only with
// `requireDoc`, the EE Console): the frame reloaded in place, or a hello named
// a document not seen before. Whatever the old document proved ends there:
// every status goes back to connecting, and the new document is connected
// only by its own steps above.
export const EMBED_STATUS = Object.freeze({
  connecting: "connecting",
  checking: "checking",
  connected: "connected",
  needsKey: "needs-key",
  refused: "refused",
  diagnosing: "diagnosing",
  timeout: "timeout",
});

// What the bridge and the session's timers report.
export const GATE_EVENT = Object.freeze({
  keySent: "key-sent",
  noKey: "no-key",
  gate: "gate",
  // No gate from the child within the session's bound.
  noVerdict: "no-verdict",
  // The frame holds a new document (requireDoc).
  document: "document",
});

const GATE_TO_STATUS = Object.freeze({
  ready: EMBED_STATUS.connected,
  "needs-key": EMBED_STATUS.needsKey,
  refused: EMBED_STATUS.refused,
});

// Waiting for the child's word: the bound turns these into needs-key.
const AWAITING_VERDICT = new Set([
  EMBED_STATUS.connecting,
  EMBED_STATUS.checking,
]);

/**
 * @param {string} current  the status shown now (an EMBED_STATUS value)
 * @param {{kind: string, state?: string}} event  a GATE_EVENT, with the
 *   gate's state for "gate"
 * @param {{requireGate?: boolean}} [options]
 * @returns {string} the status to show next; `current` when nothing changes
 */
export function nextEmbedStatus(current, event, { requireGate = false } = {}) {
  switch (event?.kind) {
    case GATE_EVENT.keySent:
      if (!requireGate) return EMBED_STATUS.connected;
      // A child that already proved a key stays connected: Studio reports
      // a repeated "ready" only once, so "checking" would never end.
      return current === EMBED_STATUS.connected
        ? current
        : EMBED_STATUS.checking;
    case GATE_EVENT.noKey:
      // Nothing to hand over: the child's gate decides. A repeat hello after
      // the key must not hide a working frame; a child that answers after
      // the no-hello timeout is alive after all.
      return current === EMBED_STATUS.timeout ||
        current === EMBED_STATUS.diagnosing
        ? EMBED_STATUS.connecting
        : current;
    case GATE_EVENT.gate: {
      const next = GATE_TO_STATUS[event.state];
      if (next) return next;
      return requireGate ? current : EMBED_STATUS.connected;
    }
    case GATE_EVENT.noVerdict:
      return AWAITING_VERDICT.has(current) ? EMBED_STATUS.needsKey : current;
    case GATE_EVENT.document:
      return EMBED_STATUS.connecting;
    default:
      return current;
  }
}
