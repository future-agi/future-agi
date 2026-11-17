// Saved URLs are bookmarks, never credentials. The child has no stored key
// whose refusal could justify removing a saved connection.
import { describe, expect, it } from "vitest";

import { EMBED_STATUS } from "src/sections/omega/embed";

import { STORAGE_VERDICT, storageVerdict } from "../use-studio-connection";

const CONNECTIONS = ["link", "pasted", "saved", "restored"].map((source) => ({
  id: 1,
  url: "http://127.0.0.1:4191",
  source,
}));

describe("storageVerdict", () => {
  it("remembers a server only once Studio said ready", () => {
    for (const connection of CONNECTIONS) {
      expect(storageVerdict(EMBED_STATUS.connected, connection)).toBe(
        STORAGE_VERDICT.remember,
      );
      for (const status of Object.values(EMBED_STATUS)) {
        if (status === EMBED_STATUS.connected) continue;
        expect(storageVerdict(status, connection)).toBe(STORAGE_VERDICT.keep);
      }
    }
  });

  it("never lets a refused or missing key delete a saved URL", () => {
    for (const connection of CONNECTIONS) {
      for (const status of [EMBED_STATUS.refused, EMBED_STATUS.needsKey]) {
        expect(storageVerdict(status, connection)).toBe(STORAGE_VERDICT.keep);
      }
    }
    expect(storageVerdict(EMBED_STATUS.connected, null)).toBe(
      STORAGE_VERDICT.keep,
    );
  });
});
