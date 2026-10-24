import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "src/utils/test-utils";

import RateLimitingConfigTab from "./RateLimitingConfigTab";

// The tab says exactly what each field limits, and whether this gateway
// applies them, from GET /-/config's org_sections_enforced read through
// useGatewayConfig.

const gatewayConfig = { data: undefined, isLoading: false };

vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "gw-1" }),
}));

vi.mock("../providers/hooks/useGatewayConfig", () => ({
  useGatewayConfig: vi.fn(() => ({
    data: gatewayConfig.data,
    isLoading: gatewayConfig.isLoading,
  })),
}));

const enabled = {
  enabled: true,
  global_rpm: 60,
  global_tpm: 0,
  per_key_rpm: 0,
  per_key_tpm: 0,
  per_model_rpm: 0,
  per_user_rpm: 0,
};

describe("RateLimitingConfigTab", () => {
  beforeEach(() => {
    gatewayConfig.data = { org_sections_enforced: ["rate_limiting"] };
    gatewayConfig.isLoading = false;
  });

  it("says which requests the section counts and which it does not", () => {
    render(<RateLimitingConfigTab rateLimiting={enabled} onChange={vi.fn()} />);

    // Only the routes whose handler stamps the org's limits and runs the
    // pipeline are counted (limits_routes_test.go proves each).
    expect(
      screen.getByText(
        /These limits count chat completions, completions, Responses, Anthropic Messages and Gemini \(\/v1beta\) requests/,
      ),
    ).toBeInTheDocument();
    // The relayed Deepgram and Cartesia speech sessions are counted, one
    // request each and no tokens (limits_speech_test.go); the audio API
    // under /v1/audio is not.
    expect(
      screen.getByText(
        /Each relayed Deepgram or Cartesia speech session counts as one request and adds no tokens/,
      ),
    ).toBeInTheDocument();
    // A session is counted per model only by a model its URL names, and a
    // token limit other requests have spent refuses it
    // (limits_speech_test.go).
    expect(
      screen.getByText(
        /A session counts per model only when its URL names the model, and a new session is refused while a token limit that holds it is spent for the minute/,
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        /Embeddings \(\/v1\/embeddings\), images, audio \(\/v1\/audio\), rerank, search, OCR, Assistants, video, realtime and scheduled requests are not counted or limited by this section/,
      ),
    ).toBeInTheDocument();
    // No sentence claims every request of the organization.
    expect(
      screen.queryByText(/All of this organization's requests/),
    ).toBeNull();
    expect(
      screen.queryByText(/of all of this organization's requests/),
    ).toBeNull();
  });

  it("says what each field limits, on the routes it covers", () => {
    render(<RateLimitingConfigTab rateLimiting={enabled} onChange={vi.fn()} />);

    expect(
      screen.getByText(
        /This organization's chat, completions, Responses, Messages and Gemini requests, from every key, counted together/,
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        /Tokens \(prompt and completion\) of this organization's chat, completions, Responses, Messages and Gemini requests, counted together/,
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        /Each API key's chat, completions, Responses, Messages and Gemini requests, counted separately/,
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/A limit set on the key itself is used instead/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        /Each API key's tokens on chat, completions, Responses, Messages and Gemini requests, counted separately/,
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        /Each model's chat, completions, Responses, Messages and Gemini requests from every key of the organization, counted separately by the model name the request sends/,
      ),
    ).toBeInTheDocument();
  });

  it("says which user per_user_rpm counts on each route and which requests it does not limit", () => {
    render(<RateLimitingConfigTab rateLimiting={enabled} onChange={vi.fn()} />);

    expect(
      screen.getByText(
        /Each end user's requests from every key of the organization, counted separately/,
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        /On chat completions the user is x-agentcc-user-id \(or metadata\.agent\.user_id\), else the body's user field/,
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        /on completions and Responses it is the body's user field/,
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        /Anthropic Messages and Gemini requests, and a request that names no user, are not limited by this field/,
      ),
    ).toBeInTheDocument();
    // No helper names only the header, or the header on every route.
    expect(screen.queryByText("Per x-agentcc-user-id limit")).toBeNull();
    expect(screen.queryByText(/else the request body's user field/)).toBeNull();
  });

  it("says how tokens are counted and how a refusal reads", () => {
    render(<RateLimitingConfigTab rateLimiting={enabled} onChange={vi.fn()} />);

    expect(
      screen.getByText(
        /A request is let through while this minute's tokens are under the limit/,
      ),
    ).toBeInTheDocument();
    // The refusal's shape depends on the route's API
    // (limits_routes_test.go checks both).
    expect(
      screen.getByText(
        /on chat completions, completions and Responses it is rate_limit_exceeded naming the field that refused it, with Retry-After/,
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        /on Anthropic Messages and Gemini it is that API's own error, whose message names the field and the seconds to wait/,
      ),
    ).toBeInTheDocument();
  });

  it("says a cached answer is neither counted nor refused", () => {
    render(<RateLimitingConfigTab rateLimiting={enabled} onChange={vi.fn()} />);

    expect(
      screen.getByText(
        /An answer served from the response cache is not counted against, or refused by, any rate limit/,
      ),
    ).toBeInTheDocument();
  });

  it("says the gateway applies the limits when it reports rate_limiting", () => {
    render(<RateLimitingConfigTab rateLimiting={enabled} onChange={vi.fn()} />);
    expect(
      screen.getByText(/This gateway enforces these limits/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Not enforced on this gateway/)).toBeNull();
    expect(screen.queryByText(/Enforcement unknown/)).toBeNull();
  });

  it("says Not enforced on this gateway when it reports sections without rate_limiting", () => {
    gatewayConfig.data = { org_sections_enforced: ["budgets", "cache"] };
    render(<RateLimitingConfigTab rateLimiting={enabled} onChange={vi.fn()} />);
    expect(
      screen.getByText(/Not enforced on this gateway/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/This gateway enforces these limits/)).toBeNull();
  });

  it("says Enforcement unknown when the gateway does not report its sections", () => {
    gatewayConfig.data = { server: {} };
    render(<RateLimitingConfigTab rateLimiting={enabled} onChange={vi.fn()} />);
    expect(screen.getByText(/Enforcement unknown/)).toBeInTheDocument();
    expect(screen.queryByText(/This gateway enforces these limits/)).toBeNull();
    expect(screen.queryByText(/Not enforced on this gateway/)).toBeNull();
  });

  it("shows the enforcement state with the switch off too", () => {
    gatewayConfig.data = { org_sections_enforced: [] };
    render(
      <RateLimitingConfigTab
        rateLimiting={{ enabled: false }}
        onChange={vi.fn()}
      />,
    );
    expect(
      screen.getByText(/Not enforced on this gateway/),
    ).toBeInTheDocument();
  });

  it("still saves every field", () => {
    const onChange = vi.fn();
    render(
      <RateLimitingConfigTab rateLimiting={enabled} onChange={onChange} />,
    );

    fireEvent.change(screen.getByLabelText("Tokens per Minute (TPM)"), {
      target: { value: "5000" },
    });
    expect(onChange).toHaveBeenLastCalledWith({ ...enabled, global_tpm: 5000 });

    fireEvent.change(screen.getByLabelText("RPM per User"), {
      target: { value: "3" },
    });
    expect(onChange).toHaveBeenLastCalledWith({ ...enabled, per_user_rpm: 3 });

    fireEvent.change(screen.getByLabelText("TPM per Key"), {
      target: { value: "700" },
    });
    expect(onChange).toHaveBeenLastCalledWith({ ...enabled, per_key_tpm: 700 });

    fireEvent.change(screen.getByLabelText("RPM per Model"), {
      target: { value: "9" },
    });
    expect(onChange).toHaveBeenLastCalledWith({ ...enabled, per_model_rpm: 9 });
  });
});
