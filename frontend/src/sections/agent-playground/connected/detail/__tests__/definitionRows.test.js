import { describe, it, expect } from "vitest";
import {
  HOSTED_VERSION_DETAIL,
  STORED_AGENT,
} from "src/api/agents/_fixtures/agentDetail";
import { mapVersionDetail } from "src/api/agents/agentDetail";
import { hostedRows, storedAgentRows } from "../definitionRows";

const byLabel = (rows) => Object.fromEntries(rows.map((r) => [r.label, r]));

describe("hostedRows", () => {
  const v = mapVersionDetail(HOSTED_VERSION_DETAIL);

  it("reads what the hosted agent was set up with", () => {
    const rows = byLabel(hostedRows(v));
    expect(rows.Provider.value).toBe("Vapi");
    expect(rows["Assistant ID"].value).toBe(
      "8ea1e33d-c4b7-4288-8e3c-50fa2cb539fa",
    );
    expect(rows["API key"].value).toBe("sk-a...9f2c");
    expect(rows["Contact number"].value).toBe("+14155550100");
    expect(rows.Direction.value).toBe("Inbound · caller speaks first");
    expect(rows.Languages.items).toEqual(["en", "es"]);
    expect(rows.Prompt.value).toBe(
      "Callers ring you to refill a prescription.",
    );
    expect(rows.Model.value).toBe("gpt-4o");
  });

  it("says the key isn't saved when the server sends null", () => {
    const rows = byLabel(hostedRows({ ...v, apiKey: null }));
    expect(rows["API key"].value).toBe("Not saved");
  });

  it("leaves out what wasn't set", () => {
    const rows = byLabel(
      hostedRows({ ...v, config: { provider: "retell", inbound: false } }),
    );
    expect(Object.keys(rows)).toEqual(["Provider", "API key", "Direction"]);
    expect(rows.Direction.value).toBe("Outbound");
  });

  it("adds the LiveKit fields for a LiveKit agent", () => {
    const rows = byLabel(
      hostedRows({
        ...v,
        config: {
          provider: "livekit",
          livekit_url: "wss://lk.example.com",
          livekit_agent_name: "front-desk",
          livekit_api_key: "APIa...7f",
          livekit_max_concurrency: 2,
        },
      }),
    );
    expect(rows.Provider.value).toBe("LiveKit");
    expect(rows["LiveKit URL"].value).toBe("wss://lk.example.com");
    expect(rows["LiveKit agent"].value).toBe("front-desk");
    expect(rows["LiveKit API key"].value).toBe("APIa...7f");
    expect(rows["Max concurrent calls"].value).toBe("2");
  });
});

describe("storedAgentRows", () => {
  const details = {
    agent: STORED_AGENT.agent,
    sdkVersion: "0.2.3",
    storedAt: "2026-10-14T11:50:22.212003Z",
    setupStale: false,
  };

  it("reads what the SDK stored", () => {
    const rows = byLabel(storedAgentRows(details));
    expect(rows.Kind.value).toBe("Source");
    expect(rows.Type.value).toBe("Voice");
    expect(rows.Model.value).toBe(
      "gpt-4.1 · temperature 0.3 · max 1024 tokens",
    );
    expect(rows.Voice.value).toBe("sonic-en · cartesia · en · speed 1");
    expect(rows.Prompts.value).toBe(
      "# ROLE\nYou are the Uber phone-booking assistant.",
    );
    expect(rows["Tools · 2"].items).toEqual([
      "create_guest_rider",
      "get_saved_places",
    ]);
    expect(rows.Telephony.items).toEqual(["+14155550199 · twilio"]);
    expect(rows["Tracing library"].value).toBe("None");
    expect(rows.Gaps.items).toEqual(["Flow not described"]);
  });

  it("has no provider block for a code agent", () => {
    expect(byLabel(storedAgentRows(details)).Provider).toBeUndefined();
  });

  it("shows the provider when the stored Agent points at one", () => {
    const rows = byLabel(
      storedAgentRows({
        ...details,
        agent: {
          ...details.agent,
          provider_ref: { provider: "vapi", id: "asst_1", version: null },
        },
      }),
    );
    expect(rows.Provider.value).toBe("Vapi · asst_1");
  });

  it("shows a dash for what the code doesn't describe", () => {
    const rows = byLabel(
      storedAgentRows({
        ...details,
        agent: {
          ...details.agent,
          model: null,
          voice: null,
          telephony: [],
          gaps: [],
          instrumentation: "traceai",
        },
      }),
    );
    expect(rows.Model.value).toBe("—");
    expect(rows.Voice.value).toBe("—");
    expect(rows.Telephony.value).toBe("—");
    expect(rows.Gaps.value).toBe("None");
    expect(rows["Tracing library"].value).toBe("traceAI");
  });
});
