import { describe, expect, it, vi, beforeEach } from "vitest";

const axiosMocks = vi.hoisted(() => ({
  post: vi.fn(),
  verifyApiKey: "/simulate/agent-definitions/verify-api-key/",
}));

vi.mock("src/utils/axios", () => ({
  default: {
    post: axiosMocks.post,
  },
  endpoints: {
    agentDefinitions: {
      verifyApiKey: axiosMocks.verifyApiKey,
    },
  },
}));

const baseAgent = {
  agentType: "voice",
  agentName: "Outbound WebRTC agent",
  languages: ["en"],
  description: "An outbound LiveKit agent",
  commitMessage: "initial",
  inbound: false,
};

const liveKitFields = {
  provider: "livekit",
  livekitUrl: "wss://example.livekit.cloud",
  livekitApiKey: "APIxxxxxxxx",
  livekitApiSecret: "secretxxxxxxxx",
  livekitAgentName: "outbound-agent",
};

function errorPaths(result) {
  return result.error.issues.map((issue) => issue.path.join("."));
}

describe("createAgentDefinitionSchema — outbound LiveKit (TH-7507)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    axiosMocks.post.mockResolvedValue({ data: {} });
  });

  it("accepts an outbound LiveKit agent with no API key or Assistant ID", async () => {
    const schema = createAgentDefinitionSchema({ keysRequired: true });

    const result = await schema.safeParseAsync({
      ...baseAgent,
      ...liveKitFields,
    });

    expect(result.success ? [] : errorPaths(result)).toEqual([]);
  });

  it("still requires API key and Assistant ID for an outbound non-LiveKit agent", async () => {
    const schema = createAgentDefinitionSchema({ keysRequired: true });

    const result = await schema.safeParseAsync({
      ...baseAgent,
      provider: "vapi",
      authenticationMethod: "api_key",
    });

    expect(result.success).toBe(false);
    expect(errorPaths(result)).toEqual(
      expect.arrayContaining(["apiKey", "assistantId"]),
    );
  });

  it("still requires keys for the 'others' provider, which has no branch of its own", async () => {
    const schema = createAgentDefinitionSchema({ keysRequired: true });

    const result = await schema.safeParseAsync({
      ...baseAgent,
      provider: "others",
    });

    expect(result.success).toBe(false);
    expect(errorPaths(result)).toEqual(
      expect.arrayContaining(["apiKey", "assistantId"]),
    );
  });

  it("still requires keys for a non-voice agent, which the voice branches skip", async () => {
    const schema = createAgentDefinitionSchema({ keysRequired: true });

    const result = await schema.safeParseAsync({
      ...baseAgent,
      agentType: "text",
      provider: "vapi",
    });

    expect(result.success).toBe(false);
    expect(errorPaths(result)).toEqual(
      expect.arrayContaining(["apiKey", "assistantId"]),
    );
  });

  it("leaves an inbound LiveKit agent's own credential rules intact", async () => {
    const schema = createAgentDefinitionSchema({ keysRequired: true });

    const result = await schema.safeParseAsync({
      ...baseAgent,
      inbound: true,
      provider: "livekit",
      livekitUrl: "",
      livekitApiKey: "",
      livekitApiSecret: "",
      livekitAgentName: "",
    });

    expect(result.success).toBe(false);
    expect(errorPaths(result)).toEqual(
      expect.arrayContaining([
        "livekitUrl",
        "livekitApiKey",
        "livekitApiSecret",
        "livekitAgentName",
      ]),
    );
  });
});

describe("createAgentDefinitionSchema — voice transport", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    axiosMocks.post.mockResolvedValue({ data: {} });
  });

  // inbound: true keeps the provider-key rules out of the way so these
  // assertions are about the contact number alone.
  const voiceAgent = {
    ...baseAgent,
    inbound: true,
    provider: "vapi",
  };

  it("requires a contact number when the transport is telephony", async () => {
    const schema = createAgentDefinitionSchema();

    const result = await schema.safeParseAsync({
      ...voiceAgent,
      voiceTransport: "telephony",
    });

    expect(result.success).toBe(false);
    expect(errorPaths(result)).toEqual(
      expect.arrayContaining(["countryCode", "contactNumber"]),
    );
  });

  it("accepts a telephony agent with a full number", async () => {
    const schema = createAgentDefinitionSchema();

    const result = await schema.safeParseAsync({
      ...voiceAgent,
      voiceTransport: "telephony",
      countryCode: "1",
      contactNumber: "4155551234",
    });

    expect(result.success ? [] : errorPaths(result)).toEqual([]);
  });

  it("asks for no number at all when the transport is webrtc", async () => {
    const schema = createAgentDefinitionSchema();

    const result = await schema.safeParseAsync({
      ...voiceAgent,
      voiceTransport: "webrtc",
    });

    expect(result.success ? [] : errorPaths(result)).toEqual([]);
  });

  it("ignores a number left behind by a switch back to webrtc", async () => {
    const schema = createAgentDefinitionSchema();

    const result = await schema.safeParseAsync({
      ...voiceAgent,
      voiceTransport: "webrtc",
      countryCode: "1",
      contactNumber: "415",
    });

    expect(result.success ? [] : errorPaths(result)).toEqual([]);
  });

  it("still rejects a malformed number in telephony mode", async () => {
    const schema = createAgentDefinitionSchema();

    const result = await schema.safeParseAsync({
      ...voiceAgent,
      voiceTransport: "telephony",
      countryCode: "1",
      contactNumber: "abc",
    });

    expect(result.success).toBe(false);
    expect(errorPaths(result)).toEqual(
      expect.arrayContaining(["contactNumber"]),
    );
  });

  it("defaults to webrtc when no transport is supplied", async () => {
    const schema = createAgentDefinitionSchema();

    const result = await schema.safeParseAsync(voiceAgent);

    expect(result.success ? [] : errorPaths(result)).toEqual([]);
  });
});

import { createAgentDefinitionSchema } from "../helper";
