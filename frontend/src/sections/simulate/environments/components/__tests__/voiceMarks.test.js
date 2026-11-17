// Every mark the hosted voice list shows has a recorded source, licence and
// difference from that source, and a sha256 that fails here when the bundled
// file changes (the same change detector as the run-source marks').
import { createHash } from "node:crypto";
import { describe, expect, it } from "vitest";
import { PLATFORM_LOGOS } from "../platformLogos";
import { VOICE_MARKS } from "../voiceMarks";

const sha256 = (text) =>
  createHash("sha256").update(text, "utf8").digest("hex");

const VOICE_IDS = [
  "bland",
  "elevenlabs",
  "livekit",
  "retell",
  "retell_chat",
  "vapi",
];

describe("voice platform marks", () => {
  it("records a source, licence and difference for every mark the voice list shows", () => {
    expect(Object.keys(PLATFORM_LOGOS).sort()).toEqual(VOICE_IDS);
    // One row per mark, no orphans.
    expect(Object.keys(VOICE_MARKS).sort()).toEqual(VOICE_IDS);
    VOICE_IDS.forEach((id) => {
      const row = VOICE_MARKS[id];
      if (id === "bland") {
        // Bland shows the team's generic icon, not a vendor mark.
        expect(row.source, id).toMatch(/^the team's file, not a vendor mark/);
      } else {
        expect(row.source, id).toMatch(/^https:\/\//);
      }
      expect(row.licence, id).toBeTruthy();
      expect(row.differs, id).toBeTruthy();
    });
    // Chat's Retell AI shows the same file as voice's.
    expect(PLATFORM_LOGOS.retell_chat.svg).toBe(PLATFORM_LOGOS.retell.svg);
    expect(VOICE_MARKS.retell_chat).toEqual(VOICE_MARKS.retell);
  });

  it("detects any change to a bundled file (sha256 change detector)", () => {
    // A swapped or edited file fails here until a new hash is recorded,
    // which is when its row in voiceMarks.js must be checked again.
    VOICE_IDS.forEach((id) => {
      const row = VOICE_MARKS[id];
      expect(row.sha256, id).toMatch(/^[0-9a-f]{64}$/);
      expect(sha256(PLATFORM_LOGOS[id].svg), id).toBe(row.sha256);
    });
  });
});
