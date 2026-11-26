import React from "react";
import PropTypes from "prop-types";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const axiosMocks = vi.hoisted(() => ({
  post: vi.fn(),
  createVersion: vi.fn((id) => `/simulate/agent-definitions/${id}/versions/`),
}));

vi.mock("src/utils/axios", () => ({
  default: { post: axiosMocks.post },
  endpoints: {
    agentDefinitions: { createVersion: axiosMocks.createVersion },
  },
}));

vi.mock("src/utils/Mixpanel", () => ({
  trackEvent: vi.fn(),
  Events: {},
  PropertyName: {},
}));

function createWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });

  function Wrapper({ children }) {
    return (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
  }

  Wrapper.propTypes = { children: PropTypes.node };
  return Wrapper;
}

function renderSubmit() {
  return renderHook(
    () =>
      useAgentSubmit({
        agentDefinitionId: "agent-1",
        reset: vi.fn(),
        queryClient: { invalidateQueries: vi.fn() },
        enqueueSnackbar: vi.fn(),
        setError: vi.fn(),
        setSelectedVersion: vi.fn(),
      }),
    { wrapper: createWrapper() },
  );
}

const voiceAgent = {
  agentType: "voice",
  agentName: "Support agent",
  languages: ["en"],
  provider: "vapi",
  apiKey: "sk-test",
  assistantId: "asst_1",
  description: "desc",
  commitMessage: "msg",
  inbound: true,
  observabilityEnabled: false,
  headers: [],
};

describe("useAgentSubmit — voice transport payload", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    axiosMocks.post.mockResolvedValue({ data: { version: { id: "v1" } } });
  });

  it("sends the full number when the transport is telephony", async () => {
    const { result } = renderSubmit();

    await result.current.onSubmit({
      ...voiceAgent,
      voiceTransport: "telephony",
      countryCode: "1",
      contactNumber: "4155551234",
    });

    const [, payload] = axiosMocks.post.mock.calls[0];
    expect(payload.contact_number).toBe("+14155551234");
  });

  it("preserves the full number for a LiveKit telephony version", async () => {
    const { result } = renderSubmit();

    await result.current.onSubmit({
      ...voiceAgent,
      provider: "livekit",
      voiceTransport: "telephony",
      countryCode: "1",
      contactNumber: "4155551234",
      livekitUrl: "https://livekit.example.com",
      livekitApiKey: "API-test",
      livekitApiSecret: "secret",
      livekitAgentName: "test-agent",
      livekitConfigJson: {},
      livekitMaxConcurrency: 5,
    });

    const [, payload] = axiosMocks.post.mock.calls[0];
    expect(payload.contact_number).toBe("+14155551234");
  });

  it("clears a saved number when switching to webrtc", async () => {
    const { result } = renderSubmit();

    await result.current.onSubmit({
      ...voiceAgent,
      voiceTransport: "webrtc",
      countryCode: "1",
      contactNumber: "4155551234",
    });

    const [, payload] = axiosMocks.post.mock.calls[0];
    // The version endpoint only updates fields present in the request, so an
    // omitted contact_number would leave the old number on the agent.
    expect(payload).toHaveProperty("contact_number");
    expect(payload.contact_number).toBe("");
  });
});

import { useAgentSubmit } from "../useAgentConfigForm";
