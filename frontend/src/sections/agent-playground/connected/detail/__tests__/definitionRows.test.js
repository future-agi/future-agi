import { describe, it, expect } from "vitest";
import {
  HOSTED_AGENT_DETAIL,
  HOSTED_VERSION_DETAIL,
  STORED_AGENT,
} from "src/api/agents/_fixtures/agentDetail";
import { mapAgentDetail, mapVersionDetail } from "src/api/agents/agentDetail";
import {
  factsOf,
  hostedConfigurationRows,
  identityRows,
  reachRows,
  sourceRows,
  storedAgentRows,
} from "../definitionRows";

const byLabel = (rows) => Object.fromEntries(rows.map((r) => [r.label, r]));

const agent = mapAgentDetail(HOSTED_AGENT_DETAIL);
const version = mapVersionDetail(HOSTED_VERSION_DETAIL);

describe("factsOf", () => {
  it("reads the version's own snapshot", () => {
    const facts = factsOf(
      { ...agent, name: "Renamed later" },
      { ...version, config: { ...version.config, agent_name: "Northwind" } },
    );
    expect(facts.name).toBe("Northwind");
  });

  it("never fills an older version's gaps with the agent's current values", () => {
    const facts = factsOf(agent, { ...version, config: { provider: "vapi" } });
    expect(facts.name).toBeNull();
    expect(facts.description).toBeNull();
    expect(facts.contactNumber).toBeNull();
    expect(facts.inbound).toBeNull();
    expect(facts.languages).toEqual([]);
  });

  it("takes where the agent lives from the version, else the agent", () => {
    expect(
      factsOf({ source: "github" }, { source: { kind: "archive" } }).provider,
    ).toBe("archive");
    expect(factsOf({ source: "github" }, {}).provider).toBe("github");
  });
});

describe("identityRows", () => {
  it("names the agent, its type and languages", () => {
    const rows = byLabel(identityRows(factsOf(agent, version)));
    expect(rows.Name.value).toBe("Northwind Pharmacy");
    expect(rows.Type.value).toBe("Voice");
    expect(rows.Languages.items).toEqual(["en", "es"]);
  });

  it("leaves the prompt to Configuration, so it shows once", () => {
    const facts = factsOf(agent, version);
    expect(byLabel(identityRows(facts)).Description).toBeUndefined();
    expect(byLabel(hostedConfigurationRows(facts)).Prompt.value).toBe(
      "Callers ring you to refill a prescription.",
    );
  });

  it("leaves out what wasn't set", () => {
    const rows = byLabel(
      identityRows(
        factsOf({}, { config: { agent_name: "Bare", agent_type: "text" } }),
      ),
    );
    expect(Object.keys(rows)).toEqual(["Name", "Type"]);
    expect(rows.Type.value).toBe("Chat");
  });
});

describe("sourceRows", () => {
  it("reads where a hosted agent lives and what we read it with", () => {
    const rows = byLabel(
      sourceRows(factsOf(agent, version), { isCode: false }),
    );
    expect(rows["Lives in"].value).toBe("Vapi");
    expect(rows["Assistant ID"].value).toBe(
      "8ea1e33d-c4b7-4288-8e3c-50fa2cb539fa",
    );
    expect(rows["API key"].value).toBe("sk-a...9f2c");
  });

  it("says the key isn't saved when the server sends null", () => {
    const rows = byLabel(
      sourceRows(factsOf(agent, { ...version, apiKey: null }), {
        isCode: false,
      }),
    );
    expect(rows["API key"].value).toBe("Not saved");
    expect(rows["API key"].mono).toBe(false);
  });

  it("names the repository, branch and commit a code version points at", () => {
    const rows = byLabel(
      sourceRows(
        factsOf(
          { source: "github" },
          {
            source: {
              kind: "github",
              repository: "https://github.com/acme/uber-voice-agent",
              ref: "main",
              subfolder: "agents/voice",
              commit_sha: "3f9a2c1d8e7b6a5f4c3d2e1f0a9b8c7d6e5f4a3b",
            },
          },
        ),
        { isCode: true },
      ),
    );
    expect(Object.keys(rows)).toEqual([
      "Lives in",
      "Repository",
      "Branch",
      "Folder",
      "Commit",
    ]);
    expect(rows["Lives in"].value).toBe("GitHub");
    expect(rows.Repository.value).toBe("github.com/acme/uber-voice-agent");
    expect(rows.Branch.value).toBe("main");
    expect(rows.Folder.value).toBe("agents/voice");
    expect(rows.Commit.value).toBe("3f9a2c1");
  });

  it("has no assistant, key or repository for an upload", () => {
    const rows = byLabel(
      sourceRows(
        factsOf({ source: "archive" }, { source: { kind: "archive" } }),
        {
          isCode: true,
        },
      ),
    );
    expect(Object.keys(rows)).toEqual(["Lives in"]);
    expect(rows["Lives in"].value).toBe("Upload");
  });

  it("adds the LiveKit fields for a LiveKit agent", () => {
    const rows = byLabel(
      sourceRows(
        factsOf(
          {},
          {
            config: {
              provider: "livekit",
              livekit_url: "wss://lk.example.com",
              livekit_agent_name: "front-desk",
              livekit_api_key: "APIa...7f",
            },
          },
        ),
        { isCode: false },
      ),
    );
    expect(rows["Lives in"].value).toBe("LiveKit");
    expect(rows["LiveKit URL"].value).toBe("wss://lk.example.com");
    expect(rows["LiveKit agent"].value).toBe("front-desk");
    expect(rows["LiveKit API key"].value).toBe("APIa...7f");
  });
});

describe("reachRows", () => {
  it("describes a voice agent's call and number", () => {
    const rows = byLabel(reachRows(factsOf(agent, version)));
    expect(rows.Voice.value).toBe("Phone call · inbound · caller speaks first");
    expect(rows["Phone number"].value).toBe("+14155550100");
    expect(rows["Phone number"].mono).toBe(true);
  });

  it("reads a code agent's numbers from the stored Agent's telephony", () => {
    const rows = byLabel(
      reachRows(
        factsOf(
          {},
          { config: { agent_type: "voice", inbound: true } },
          STORED_AGENT.agent,
        ),
      ),
    );
    expect(rows.Voice.value).toBe("Phone call · inbound");
    expect(rows["Phone number"].value).toBe("+14155550199");
  });

  it("lists every number when the stored Agent has several", () => {
    const rows = byLabel(
      reachRows(
        factsOf(
          {},
          { config: { agent_type: "voice" } },
          {
            ...STORED_AGENT.agent,
            telephony: [
              { number: "+14155550199", provider: "twilio" },
              { number: "+14155550200", provider: "twilio" },
            ],
          },
        ),
      ),
    );
    expect(rows["Phone numbers"].items).toEqual([
      "+14155550199",
      "+14155550200",
    ]);
  });

  it("calls it a web call when there is no number", () => {
    const rows = byLabel(
      reachRows(
        factsOf(
          {},
          {
            config: {
              agent_type: "voice",
              inbound: false,
              target_speaks_first: true,
            },
          },
        ),
      ),
    );
    expect(rows.Voice.value).toBe("Web call · outbound · agent speaks first");
    expect(rows["Phone number"]).toBeUndefined();
  });

  it("describes a chat agent and how many conversations at once", () => {
    const rows = byLabel(
      reachRows(
        factsOf(
          {},
          {
            config: {
              agent_type: "text",
              inbound: true,
              target_speaks_first: true,
              livekit_max_concurrency: 2,
            },
          },
        ),
      ),
    );
    expect(rows.Chat.value).toBe("Inbound · agent writes first");
    expect(rows["At once"].value).toBe("Up to 2 conversations");
  });
});

describe("hostedConfigurationRows", () => {
  it("reads the model and prompt", () => {
    const rows = byLabel(hostedConfigurationRows(factsOf(agent, version)));
    expect(rows.Model.value).toBe("gpt-4o");
    expect(rows.Prompt.value).toBe(
      "Callers ring you to refill a prescription.",
    );
    expect(rows.Prompt.multiline).toBe(true);
  });

  it("is empty when neither was recorded", () => {
    expect(hostedConfigurationRows(factsOf({}, {}))).toEqual([]);
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

  it("leaves out what the code doesn't describe", () => {
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
    expect(rows.Model).toBeUndefined();
    expect(rows.Voice).toBeUndefined();
    expect(rows.Telephony).toBeUndefined();
    expect(rows.Gaps.value).toBe("None");
    expect(rows["Tracing library"].value).toBe("traceAI");
  });
});
