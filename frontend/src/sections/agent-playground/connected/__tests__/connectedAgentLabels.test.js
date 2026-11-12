import { describe, it, expect } from "vitest";
import {
  agentNames,
  deleteBlockedReason,
  deleteConfirmText,
  deleteResultMessage,
  filtersToRows,
  modalityIcon,
  humanize,
  modalityLabel,
  rowsToFilters,
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

describe("deleteResultMessage", () => {
  it("says how many were deleted", () => {
    expect(deleteResultMessage(1)).toBe("1 agent deleted.");
    expect(deleteResultMessage(3)).toBe("3 agents deleted.");
  });
});

describe("agentNames", () => {
  const agents = (...names) => names.map((name) => ({ name }));

  it("lists up to three names and counts the rest", () => {
    expect(agentNames(agents("Clinic"))).toBe("Clinic");
    expect(agentNames(agents("Clinic", "Billing"))).toBe("Clinic and Billing");
    expect(agentNames(agents("Clinic", "Billing", "Returns"))).toBe(
      "Clinic, Billing and Returns",
    );
    expect(agentNames(agents("Clinic", "Billing", "Returns", "Refunds"))).toBe(
      "Clinic, Billing and 2 more",
    );
  });

  it("names the agents a delete removes", () => {
    expect(deleteConfirmText(agents("Clinic"))).toBe(
      "This deletes Clinic with all its versions.",
    );
    expect(deleteConfirmText(agents("Clinic", "Billing"))).toBe(
      "This deletes Clinic and Billing with all their versions.",
    );
  });

  it("names the agents that block a delete", () => {
    expect(deleteBlockedReason([{ name: "Clinic", environmentCount: 1 }])).toBe(
      "Clinic is still used in an environment. Delete those environments first.",
    );
    expect(
      deleteBlockedReason([
        { name: "Clinic", environmentCount: 1 },
        { name: "Billing", environmentCount: 2 },
      ]),
    ).toBe(
      "Clinic and Billing are still used in environments. Delete those environments first.",
    );
  });
});

describe("filter panel rows", () => {
  it("turns panel rows into the list's query filters", () => {
    expect(
      rowsToFilters([
        { field: "source", operator: "in", value: ["vapi", "retell"] },
        { field: "source", operator: "in", value: ["vapi", "bland"] },
        { field: "environments", operator: "in", value: "without" },
        { field: "observe", operator: "in", value: [] },
        { field: "owner", operator: "in", value: ["me"] },
      ]),
    ).toEqual({
      source: ["vapi", "retell", "bland"],
      environments: ["without"],
    });
    expect(rowsToFilters(null)).toBeNull();
    expect(rowsToFilters([{ field: "source", value: [""] }])).toBeNull();
  });

  it("shows the query filters back as panel rows", () => {
    expect(
      filtersToRows({ agent_type: ["voice"], source: [], observe: ["linked"] }),
    ).toEqual([
      {
        field: "agent_type",
        fieldType: "string",
        operator: "in",
        value: ["voice"],
      },
      {
        field: "observe",
        fieldType: "string",
        operator: "in",
        value: ["linked"],
      },
    ]);
    expect(filtersToRows(null)).toEqual([]);
  });
});
