// W6 B1/B2/B4: the HTTP card parts and the agent source fields render what
// the models compute, and pick secrets from the one workspace list.
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("src/utils/axios", () => ({
  default: {
    get: vi.fn().mockResolvedValue({
      data: {
        results: [{ name: "FI_PROJECT_KEY" }, { name: "ANTHROPIC_API_KEY" }],
      },
    }),
    post: vi.fn(),
  },
  endpoints: { secrets: { list: "/model-hub/secrets/" } },
}));

const { default: SecretSelect } = await import("../SecretSelect");
const { default: RequestPreview } = await import("../RequestPreview");
const { default: ResponseTransform } = await import("../ResponseTransform");
const { A2aAuthField, A2aTimeline, McpEnvField } = await import(
  "../AgentSourceFields"
);
const { TestResultView } = await import("../EndpointTestPanel");

const wrap = (ui) =>
  render(
    <QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>,
  );
afterEach(cleanup);

const columns = [
  { id: "c1", name: "customer_message" },
  { id: "c2", name: "intent" },
];
const rows = [
  { row_id: "r1", order: 0, c1: "Where is A-7781?", c2: "status" },
  { row_id: "r12", order: 11, c1: "Refund my mixer", c2: "" },
];
const http = {
  url: "https://support-agent.acme.test/v2/reply",
  method: "POST",
  bodyTemplate: '{"message": "{{customer_message}}", "intent": "{{intent}}"}',
  headers: [{ name: "Authorization", secret: "FI_PROJECT_KEY" }],
};

describe("W6 B endpoint parts", () => {
  it("SecretSelect flags a name the workspace does not have", async () => {
    wrap(
      <SecretSelect
        value="FI_PROJECT_KYE"
        names={["FI_PROJECT_KEY"]}
        onChange={() => {}}
        testId="s"
      />,
    );
    expect(screen.getByRole("alert").textContent).toBe(
      "No workspace secret named FI_PROJECT_KYE. Pick one from the list.",
    );
  });

  it("RequestPreview shows the row's request and the empty variables", () => {
    wrap(
      <RequestPreview
        http={http}
        columns={columns}
        rows={rows}
        row={rows[1]}
        onRow={() => {}}
      />,
    );
    const root = screen.getByTestId("http-request-preview");
    expect(JSON.parse(root.getAttribute("data-body"))).toEqual({
      message: "Refund my mixer",
      intent: "",
    });
    expect(screen.getByTestId("http-preview-line").textContent).toBe(
      `POST ${http.url}`,
    );
    expect(screen.getByTestId("http-preview-headers").textContent).toBe(
      "Authorization: •••• (from secret FI_PROJECT_KEY)",
    );
    expect(screen.getByTestId("http-preview-empty").textContent).toBe(
      "{{intent}} is empty on 1 of 2 rows · Row 12",
    );
  });

  it("ResponseTransform writes the tool-call path", () => {
    const onChange = vi.fn();
    wrap(
      <ResponseTransform
        http={{ outputPath: "$.reply" }}
        onChange={onChange}
      />,
    );
    fireEvent.click(screen.getByTestId("http-transform-toggle"));
    fireEvent.change(screen.getByTestId("http-transform-tool-calls"), {
      target: { value: "$.actions" },
    });
    expect(onChange).toHaveBeenLastCalledWith({ tool_calls: "$.actions" });
    // What the fields feed today: the trace's tool steps; the try's tokens.
    expect(
      screen.getByText(
        "A list of {name, arguments} in the response: the trace shows each as a tool step.",
      ),
    ).toBeTruthy();
    expect(
      screen.getByTestId("http-transform-usage-prompt_tokens"),
    ).toBeTruthy();
    expect(
      screen.getByTestId("http-transform-usage-completion_tokens"),
    ).toBeTruthy();
    expect(screen.queryByTestId("http-transform-usage-cost_usd")).toBeNull();
    expect(screen.queryByText(/Cost \(USD\)/)).toBeNull();
  });

  it("A2aAuthField says OAuth 2 exchanges the secret for a token first", () => {
    wrap(
      <A2aAuthField
        auth={{ kind: "oauth2", secret: "FI_PROJECT_KEY" }}
        onChange={() => {}}
      />,
    );
    expect(screen.getByTestId("a2a-auth-oauth-note").textContent).toBe(
      "Before each task, Future AGI requests a token from the token URL (client credentials) and sends it as a Bearer token.",
    );
    expect(screen.getByTestId("a2a-auth-token_url")).toBeTruthy();
    expect(screen.getByTestId("a2a-auth-client_id")).toBeTruthy();
  });

  it("A2aTimeline shows the headers sent and the token step's status", () => {
    wrap(
      <A2aTimeline
        headers={{
          Authorization:
            "Bearer ••••1a2b (OAuth token from auth.globex.test, expires in 3600 s)",
        }}
        timeline={[
          {
            name: "OAuth token · POST auth.globex.test/oauth/token",
            state: null,
            method: null,
            http_status: 200,
            latency_ms: 120,
          },
          {
            name: "a",
            state: "submitted",
            method: "message/send",
            latency_ms: 80,
          },
        ]}
      />,
    );
    expect(screen.getByTestId("a2a-test-header").textContent).toBe(
      "Authorization: Bearer ••••1a2b (OAuth token from auth.globex.test, expires in 3600 s)",
    );
    expect(screen.getByTestId("a2a-test-timeline").textContent).toContain(
      "OAuth token · POST auth.globex.test/oauth/token · 200 · 120 ms",
    );
  });

  it("A2aAuthField asks for the header of an API key", () => {
    wrap(
      <A2aAuthField
        auth={{ kind: "api_key", secret: "FI_PROJECT_KEY" }}
        onChange={() => {}}
      />,
    );
    expect(screen.getByTestId("a2a-auth-header")).toBeTruthy();
    expect(screen.getByTestId("a2a-auth-summary").textContent).toBe(
      "API key header in X-Api-Key · secret FI_PROJECT_KEY",
    );
    expect(
      screen.getByText("Name the header the API key goes in, e.g. X-Api-Key."),
    ).toBeTruthy();
  });

  it("A2aTimeline lists the states; McpEnvField writes the env", () => {
    wrap(
      <A2aTimeline
        timeline={[
          {
            name: "a",
            state: "submitted",
            method: "message/send",
            latency_ms: 80,
          },
          { name: "b", state: "completed", latency_ms: 300, chunks: 0 },
        ]}
      />,
    );
    expect(screen.getByTestId("a2a-test-timeline").textContent).toContain(
      "message/send · submitted · 80 ms",
    );
    cleanup();
    const onChange = vi.fn();
    wrap(
      <McpEnvField
        server={{ env_secret: "ACME_SANDBOX_TOKEN" }}
        onChange={onChange}
      />,
    );
    fireEvent.change(screen.getByTestId("mcp-env-name"), {
      target: { value: "ACME_TOKEN" },
    });
    expect(onChange).toHaveBeenLastCalledWith([
      { name: "ACME_TOKEN", secret: "ACME_SANDBOX_TOKEN" },
    ]);
  });

  it("TestResultView shows the browser note and the echoed traceparent", () => {
    const tp = "00-1a2b3c4d5e6f4a1ba2c3d4e5f6a7b8c9-1a2b3c4d00000000-01";
    wrap(
      <TestResultView
        test={{
          simulated: false,
          status: 200,
          ms: 12,
          request: {
            method: "POST",
            url: "http://127.0.0.1:8787/reply",
            headers: {
              Authorization: "not sent (from secret FI_PROJECT_KEY)",
              traceparent: tp,
            },
          },
          note: "Your browser sent this without Authorization: secret headers are added when Future AGI calls your endpoint.",
          traceparent: tp,
          echoed: tp,
          output: "Thanks, I'm on it",
        }}
      />,
    );
    expect(screen.getByTestId("http-test-secret-note").textContent).toMatch(
      /^Your browser sent this without Authorization/,
    );
    expect(screen.getByTestId("http-test-traceparent").textContent).toBe(
      `traceparent ${tp} · your server echoed it`,
    );
    expect(screen.getByTestId("http-test-headers").textContent).toBe(
      "Authorization: not sent (from secret FI_PROJECT_KEY)",
    );
  });
});
