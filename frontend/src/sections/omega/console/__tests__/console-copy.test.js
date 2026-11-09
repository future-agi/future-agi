import { describe, expect, it } from "vitest";

import { DIAGNOSIS } from "src/sections/omega/embed";

import { consoleGateCopy } from "../console-copy";
import { MINT_STATE } from "../session-assertion";

describe("consoleGateCopy", () => {
  it("names each mint failure, and falls back to a plain reconnect", () => {
    expect(consoleGateCopy(MINT_STATE.REFUSED).needsKey.title).toBe(
      "Omega Console isn't available for your role",
    );
    expect(consoleGateCopy(MINT_STATE.SIGNED_OUT).needsKey.title).toBe(
      "Your Future AGI session has ended",
    );
    expect(consoleGateCopy(MINT_STATE.UNAVAILABLE).needsKey.title).toBe(
      "Omega Console can't sign you in right now",
    );
    expect(consoleGateCopy(null).needsKey.title).toBe(
      "Omega Console needs to reconnect",
    );
  });

  it("keeps the shared wording for causes that belong to the browser", () => {
    const { diagnosis } = consoleGateCopy(null);
    // undefined hands the gate back to the shared, cause-specific copy.
    for (const cause of [
      DIAGNOSIS.localNetworkDenied,
      DIAGNOSIS.safari,
      DIAGNOSIS.brave,
    ]) {
      expect(diagnosis(cause)).toBeUndefined();
    }
  });

  it("says 'not available' for every other reason the Console did not answer", () => {
    const { diagnosis } = consoleGateCopy(null);
    for (const cause of [
      DIAGNOSIS.framingBlocked,
      DIAGNOSIS.notRunning,
      DIAGNOSIS.noResponse,
      undefined,
    ]) {
      expect(diagnosis(cause)).toEqual({
        title: "Omega Console isn't available right now",
        body: expect.stringContaining("didn't respond"),
      });
    }
  });
});
