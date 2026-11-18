import { describe, it, expect } from "vitest";
import {
  modalityIcon,
  humanize,
  modalityLabel,
  sourceLabel,
  usedInLabel,
  versionsParts,
} from "../connectedAgentLabels";

describe("humanize", () => {
  it("title-cases snake and kebab values", () => {
    expect(humanize("video_call")).toBe("Video Call");
    expect(humanize("vonage-sip")).toBe("Vonage Sip");
    expect(humanize("voice")).toBe("Voice");
  });
});

describe("modalityLabel", () => {
  it("adds the direction when there is one", () => {
    expect(modalityLabel({ modality: "voice", direction: "inbound" })).toBe(
      "Voice · Inbound",
    );
    expect(modalityLabel({ modality: "voice", direction: "outbound" })).toBe(
      "Voice · Outbound",
    );
  });

  it("calls a text agent Chat, as the ticket does", () => {
    expect(modalityLabel({ modality: "text", direction: null })).toBe("Chat");
  });

  it("shows the modality alone without a direction", () => {
    expect(modalityLabel({ modality: "voice" })).toBe("Voice");
  });

  it("shows a modality it has never seen", () => {
    expect(modalityLabel({ modality: "video_call" })).toBe("Video Call");
  });

  it("falls back to a dash with no modality", () => {
    expect(modalityLabel({ modality: null })).toBe("—");
  });
});

describe("modalityIcon", () => {
  it("uses the old Agent Definition page's icons", () => {
    expect(modalityIcon({ modality: "voice", direction: "inbound" })).toBe(
      "/assets/icons/ic_phone_call.svg",
    );
    expect(modalityIcon({ modality: "voice", direction: "outbound" })).toBe(
      "/assets/icons/ic_phone_call.svg",
    );
    expect(modalityIcon({ modality: "text", direction: null })).toBe(
      "/assets/icons/ic_chat_single.svg",
    );
  });

  it("has no icon when the modality is unknown or missing", () => {
    expect(modalityIcon({ modality: null })).toBeNull();
    expect(modalityIcon({ modality: "video" })).toBeNull();
  });
});

describe("sourceLabel", () => {
  it("spells known providers and code sources properly", () => {
    expect(sourceLabel("github")).toBe("GitHub");
    expect(sourceLabel("archive")).toBe("Upload");
    expect(sourceLabel("livekit")).toBe("LiveKit");
    expect(sourceLabel("vapi")).toBe("Vapi");
    expect(sourceLabel("retell")).toBe("Retell");
  });

  it("humanises a source it has never seen", () => {
    expect(sourceLabel("vonage_sip")).toBe("Vonage Sip");
  });

  it("falls back to a dash with no source", () => {
    expect(sourceLabel(null)).toBe("—");
    expect(sourceLabel(undefined)).toBe("—");
  });
});

describe("usedInLabel", () => {
  it("says so when no environment uses it", () => {
    expect(usedInLabel({ environments: [], environmentCount: 0 })).toEqual({
      text: "No environments yet",
      empty: true,
    });
  });

  it("names a single environment", () => {
    expect(
      usedInLabel({
        environments: [{ id: "e1", name: "Support line" }],
        environmentCount: 1,
      }),
    ).toEqual({ text: "Support line", empty: false });
  });

  it("counts a single environment whose name didn't come back", () => {
    expect(usedInLabel({ environments: [], environmentCount: 1 })).toEqual({
      text: "1 environment",
      empty: false,
    });
  });

  it("uses the true total, not the capped list of five", () => {
    expect(
      usedInLabel({
        environments: [
          { id: "e1", name: "A" },
          { id: "e2", name: "B" },
        ],
        environmentCount: 7,
      }),
    ).toEqual({ text: "7 environments", empty: false });
  });
});

describe("versionsParts", () => {
  it("splits the latest version from the total", () => {
    expect(versionsParts({ latestLabel: "v3", versionCount: 5 })).toEqual({
      version: "v3",
      rest: " · 5 total",
    });
  });

  it("shows a dash for an agent with no versions", () => {
    expect(versionsParts({ latestLabel: null, versionCount: 0 })).toEqual({
      version: "",
      rest: "—",
    });
  });
});
