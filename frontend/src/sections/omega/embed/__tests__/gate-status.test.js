// The gate status as a pure step (gate-status.js): every event from every
// status, in both modes. With requireGate (Studio) nothing but the child's
// "ready" may lead to connected; without it (the Console) the old contract
// holds: a key Future AGI minted connects as soon as it is handed over.
import { describe, expect, it } from "vitest";

import { EMBED_STATUS, GATE_EVENT, nextEmbedStatus } from "../gate-status";
import { EMBED_STATUS as REEXPORTED } from "../use-embed-session";
import { EMBED_STATUS as PUBLIC } from "..";

const ALL = Object.values(EMBED_STATUS);
const keySent = { kind: GATE_EVENT.keySent };
const noKey = { kind: GATE_EVENT.noKey };
const noVerdict = { kind: GATE_EVENT.noVerdict };
const gate = (state) => ({ kind: GATE_EVENT.gate, state });
const gated = { requireGate: true };

describe("gate status, requireGate (the key came from a link)", () => {
  it("checks a key that was handed over instead of calling it connected", () => {
    for (const current of ALL.filter((s) => s !== EMBED_STATUS.connected)) {
      expect(nextEmbedStatus(current, keySent, gated)).toBe(
        EMBED_STATUS.checking,
      );
    }
  });

  it("keeps a frame that already proved a key connected when another key goes out", () => {
    // Studio reports a repeated "ready" only once, so "checking" would never end.
    expect(nextEmbedStatus(EMBED_STATUS.connected, keySent, gated)).toBe(
      EMBED_STATUS.connected,
    );
  });

  it("is connected only on the child's ready", () => {
    for (const current of ALL) {
      expect(nextEmbedStatus(current, gate("ready"), gated)).toBe(
        EMBED_STATUS.connected,
      );
    }
    const notReady = [
      keySent,
      noKey,
      noVerdict,
      gate("needs-key"),
      gate("refused"),
      gate("bogus"),
      gate(undefined),
      { kind: "hello" },
      { kind: "route", path: "/" },
      { kind: "telemetry" },
      { kind: "nonsense" },
      null,
      undefined,
    ];
    for (const current of ALL.filter((s) => s !== EMBED_STATUS.connected)) {
      for (const event of notReady) {
        expect(nextEmbedStatus(current, event, gated)).not.toBe(
          EMBED_STATUS.connected,
        );
      }
    }
  });

  it("takes the child's refused and needs-key from any status", () => {
    for (const current of ALL) {
      expect(nextEmbedStatus(current, gate("refused"), gated)).toBe(
        EMBED_STATUS.refused,
      );
      expect(nextEmbedStatus(current, gate("needs-key"), gated)).toBe(
        EMBED_STATUS.needsKey,
      );
    }
  });

  it("ignores a gate state it does not know", () => {
    for (const current of ALL) {
      expect(nextEmbedStatus(current, gate("bogus"), gated)).toBe(current);
    }
  });

  it("asks for a key again when the child never gives its word on a key being checked", () => {
    expect(nextEmbedStatus(EMBED_STATUS.checking, noVerdict, gated)).toBe(
      EMBED_STATUS.needsKey,
    );
    expect(nextEmbedStatus(EMBED_STATUS.connecting, noVerdict, gated)).toBe(
      EMBED_STATUS.needsKey,
    );
    for (const current of [
      EMBED_STATUS.connected,
      EMBED_STATUS.refused,
      EMBED_STATUS.needsKey,
      EMBED_STATUS.timeout,
      EMBED_STATUS.diagnosing,
    ]) {
      expect(nextEmbedStatus(current, noVerdict, gated)).toBe(current);
    }
  });

  it("leaves a key being checked alone on a trailing hello we hold no key for", () => {
    expect(nextEmbedStatus(EMBED_STATUS.checking, noKey, gated)).toBe(
      EMBED_STATUS.checking,
    );
  });

  it("walks the forged and the real key the way the tab shows them", () => {
    const walk = (events) =>
      events.reduce(
        (path, event) => [...path, nextEmbedStatus(path.at(-1), event, gated)],
        [EMBED_STATUS.connecting],
      );
    expect(walk([keySent, noKey, gate("refused")])).toEqual([
      "connecting",
      "checking",
      "checking",
      "refused",
    ]);
    expect(walk([keySent, gate("ready")])).toEqual([
      "connecting",
      "checking",
      "connected",
    ]);
    expect(walk([keySent, noVerdict])).toEqual([
      "connecting",
      "checking",
      "needs-key",
    ]);
  });
});

describe("gate status, default (a key Future AGI minted: the Console)", () => {
  it("connects as soon as the key is handed over, as before", () => {
    for (const current of ALL) {
      expect(nextEmbedStatus(current, keySent)).toBe(EMBED_STATUS.connected);
    }
  });

  it("still takes the child's gate", () => {
    expect(nextEmbedStatus(EMBED_STATUS.connected, gate("refused"))).toBe(
      EMBED_STATUS.refused,
    );
    expect(nextEmbedStatus(EMBED_STATUS.connected, gate("needs-key"))).toBe(
      EMBED_STATUS.needsKey,
    );
    expect(nextEmbedStatus(EMBED_STATUS.refused, gate("ready"))).toBe(
      EMBED_STATUS.connected,
    );
    // The old fallback for a gate state it does not know.
    expect(nextEmbedStatus(EMBED_STATUS.connecting, gate("bogus"))).toBe(
      EMBED_STATUS.connected,
    );
  });

  it("keeps the no-key rules: a late hello revives a timed-out frame, nothing else moves", () => {
    expect(nextEmbedStatus(EMBED_STATUS.timeout, noKey)).toBe(
      EMBED_STATUS.connecting,
    );
    expect(nextEmbedStatus(EMBED_STATUS.diagnosing, noKey)).toBe(
      EMBED_STATUS.connecting,
    );
    for (const current of [
      EMBED_STATUS.connecting,
      EMBED_STATUS.connected,
      EMBED_STATUS.needsKey,
      EMBED_STATUS.refused,
    ]) {
      expect(nextEmbedStatus(current, noKey)).toBe(current);
    }
    expect(nextEmbedStatus(EMBED_STATUS.connecting, noVerdict)).toBe(
      EMBED_STATUS.needsKey,
    );
    expect(nextEmbedStatus(EMBED_STATUS.connected, noVerdict)).toBe(
      EMBED_STATUS.connected,
    );
  });
});

describe("gate status, a new document in the frame (RL 1275A)", () => {
  // The bridge reports a new document (the frame reloaded in place, or a
  // hello named a doc it had not seen): whatever the old one proved ends.
  const newDocument = { kind: GATE_EVENT.document };

  it("names the event", () => {
    expect(GATE_EVENT.document).toBe("document");
  });

  it("goes back to connecting from every status, in both modes", () => {
    for (const current of ALL) {
      expect(nextEmbedStatus(current, newDocument, gated)).toBe(
        EMBED_STATUS.connecting,
      );
      expect(nextEmbedStatus(current, newDocument)).toBe(
        EMBED_STATUS.connecting,
      );
    }
  });

  it("is connected again only on the new document's ready", () => {
    let status = EMBED_STATUS.connected;
    status = nextEmbedStatus(status, newDocument, gated);
    status = nextEmbedStatus(status, keySent, gated);
    expect(status).toBe(EMBED_STATUS.checking);
    status = nextEmbedStatus(status, noVerdict, gated);
    expect(status).toBe(EMBED_STATUS.needsKey);
    expect(nextEmbedStatus(status, gate("ready"), gated)).toBe(
      EMBED_STATUS.connected,
    );
  });
});

describe("EMBED_STATUS", () => {
  it("is one frozen set, the same from every import path", () => {
    expect(Object.isFrozen(EMBED_STATUS)).toBe(true);
    expect(REEXPORTED).toBe(EMBED_STATUS);
    expect(PUBLIC).toBe(EMBED_STATUS);
    expect(EMBED_STATUS.checking).toBe("checking");
  });
});
