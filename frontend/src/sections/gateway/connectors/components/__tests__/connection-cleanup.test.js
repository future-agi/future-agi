import { describe, expect, it } from "vitest";

import { cleanupNotice, disconnectNotice } from "../connection-cleanup";

// The vendor did not confirm revocation and Future AGI stopped asking. The
// notice shows while gateway eviction may still be pending, when the gateway
// may still hold a cached access token and tokens may be kept for a retry,
// so it says nothing about who holds the tokens.
const UNCONFIRMED =
  "The vendor did not confirm revocation. You can also revoke access in your vendor account settings.";
const GATEWAY_PENDING =
  " Gateway cleanup is still pending; retry disconnect before connecting again.";
const CUSTODY = /no longer holds|will not retry|old tokens/i;

describe("a vendor revocation that was not confirmed", () => {
  it("says what happened and where to revoke access once gateway cleanup is done", () => {
    expect(cleanupNotice("unconfirmed", false)).toBe(UNCONFIRMED);
  });

  it("claims nothing about who holds the tokens while gateway cleanup is pending", () => {
    const notice = cleanupNotice("unconfirmed", true);
    expect(notice).toBe(UNCONFIRMED + GATEWAY_PENDING);
    expect(notice).not.toMatch(CUSTODY);
  });

  it.each([
    [false, UNCONFIRMED],
    [true, UNCONFIRMED + GATEWAY_PENDING],
  ])(
    "after a disconnect the gateway synced, with cleanup pending %s, warns with the same notice",
    (pending, message) => {
      const notice = disconnectNotice("issues", {
        disconnected: true,
        gateway_synced: true,
        cleanup_pending: pending,
        upstream_revocation: "unconfirmed",
      });
      expect(notice).toEqual({ message, variant: "warning" });
      expect(notice.message).not.toMatch(CUSTODY);
    },
  );
});
