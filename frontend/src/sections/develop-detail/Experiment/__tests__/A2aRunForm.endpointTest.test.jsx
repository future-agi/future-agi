/* eslint-disable react/prop-types */
// The A2A agent card discovers the card and tests one row through the
// core's endpoint test route (the routes it called before do not exist on
// the server), shows the agent's real reply, and never labels a real reply
// "Simulated".
import React, { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";

const { post, get } = vi.hoisted(() => ({ post: vi.fn(), get: vi.fn() }));
vi.mock("src/utils/axios", () => ({ default: { post, get } }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("../RunCardShell", () => ({
  default: ({ title, error, children }) => (
    <section>
      <h3>{title}</h3>
      {error ? <p data-testid="card-error">{error}</p> : null}
      {children}
    </section>
  ),
}));
vi.mock("../ExperimentFormContext", () => ({
  useExperimentForm: () => ({
    datasetId: "8d3c1f7e-2b6a-4c11-9d0e-5f6a7b8c9d01",
    mappingColumns: [{ id: "c1", name: "customer_message", role: "input" }],
    sampleRow: {
      row_id: "4f1e0c52-1a2b-4c3d-8e9f-0a1b2c3d4e51",
      c1: "Where is A-7781?",
    },
  }),
}));
vi.mock("../cardWatch-g2", () => ({
  useCardValue: (control, _prefix, key) => control?.[key],
}));
vi.mock("../experimentFormValues", () => ({ newRunCard: () => ({}) }));
vi.mock("../endpointCards-g2", () => ({
  a2aRunName: (card, skill) =>
    card ? `${card.name} · ${skill}` : "A2A agent card",
  isAutoA2aName: () => false,
}));
vi.mock("src/sections/common/RunSources/endpoint/httpCardParts", () => ({
  A2aAuthField: () => null,
  A2aTimeline: ({ timeline, headers }) => (
    <div data-testid="a2a-test-timeline">
      {Object.entries(headers ?? {})
        .map(([k, v]) => `${k}: ${v}`)
        .join("\n")}
      {(timeline ?? [])
        .map((s) => `${s.method} · ${s.state} · ${s.http_status}`)
        .join("\n")}
    </div>
  ),
}));
vi.mock("src/sections/common/RunSources/endpoint/StandInTag", () => ({
  default: ({ testId }) => <span data-testid={testId}>Simulated</span>,
}));

const { default: A2aRunForm } = await import("../A2aRunForm");

const CARD = {
  name: "Support agent",
  version: "1.0.0",
  url: "https://support.acme.test/a2a",
  // A sparse but valid card: skills without tags or input modes.
  skills: [{ id: "answer", name: "Answer" }],
};
const ROUTE = "/model-hub/r4/endpoints/test/";
const answer = (result) => ({ data: { status: true, result } });
const upstream = (status, response, extra = {}) =>
  answer({
    simulated: false,
    provenance: "upstream",
    host: "support.acme.test",
    status,
    latency_ms: 120.4,
    request: {
      headers: { Authorization: "Bearer •••• (from secret ACME_TOKEN)" },
    },
    response,
    response_is_json: response != null && typeof response === "object",
    error:
      status >= 200 && status < 300
        ? null
        : `Endpoint returned HTTP ${status}. Check the URL, method and header settings, then retry.`,
    ...extra,
  });

function Harness({ initial }) {
  const [card, setCard] = useState(initial);
  const setValue = (name, value) =>
    setCard((c) => ({ ...c, [name.split(".").pop()]: value }));
  return (
    <A2aRunForm
      control={card}
      setValue={setValue}
      fieldPrefix="promptConfig.0"
    />
  );
}

beforeEach(() => {
  post.mockReset();
  get.mockReset();
});
afterEach(cleanup);

describe("A2A agent card on the real server", () => {
  it("Discover reads the card through the endpoint test route and shows its skills", async () => {
    post.mockResolvedValueOnce(upstream(200, CARD));
    render(
      <Harness initial={{ a2a: { card_url: "https://support.acme.test" } }} />,
    );
    fireEvent.click(screen.getByTestId("a2a-discover"));
    await screen.findByTestId("a2a-card");
    expect(get).not.toHaveBeenCalled();
    expect(post).toHaveBeenCalledTimes(1);
    expect(post).toHaveBeenCalledWith(ROUTE, {
      prepared_request: {
        url: "https://support.acme.test/.well-known/agent-card.json",
        method: "GET",
        headers: [],
        body: "",
      },
    });
    expect(screen.getByText("Support agent 1.0.0")).toBeTruthy();
    expect(screen.getByTestId("a2a-skill-answer")).toBeTruthy();
  });

  it("Discover tries the older card path, then says plainly when there is no card", async () => {
    post
      .mockResolvedValueOnce(upstream(404, { detail: "Not Found" }))
      .mockResolvedValueOnce(upstream(404, { detail: "Not Found" }));
    render(
      <Harness initial={{ a2a: { card_url: "https://support.acme.test" } }} />,
    );
    fireEvent.click(screen.getByTestId("a2a-discover"));
    const error = await screen.findByTestId("a2a-error");
    expect(
      post.mock.calls.map(([, body]) => body.prepared_request.url),
    ).toEqual([
      "https://support.acme.test/.well-known/agent-card.json",
      "https://support.acme.test/.well-known/agent.json",
    ]);
    expect(error.textContent).toBe(
      "No agent card at https://support.acme.test/.well-known/agent-card.json or https://support.acme.test/.well-known/agent.json. Enter the agent card URL, or a base URL that serves /.well-known/agent-card.json.",
    );
  });

  it("Discover shows the core's reason when the host cannot be called", async () => {
    post.mockResolvedValueOnce(
      answer({
        simulated: false,
        provenance: "upstream",
        host: "10.0.0.5",
        status: null,
        latency_ms: 2,
        request: {},
        response: null,
        response_is_json: false,
        error:
          "Use a public HTTP(S) endpoint. Private and local hosts cannot be called from Future AGI.",
      }),
    );
    render(<Harness initial={{ a2a: { card_url: "https://10.0.0.5" } }} />);
    fireEvent.click(screen.getByTestId("a2a-discover"));
    expect((await screen.findByTestId("a2a-error")).textContent).toBe(
      "Use a public HTTP(S) endpoint. Private and local hosts cannot be called from Future AGI.",
    );
    expect(post).toHaveBeenCalledTimes(1);
  });

  it("Test on one row sends message/send for the sample row and shows the agent's reply", async () => {
    post.mockResolvedValueOnce(
      upstream(200, {
        jsonrpc: "2.0",
        id: "x",
        result: {
          kind: "message",
          role: "agent",
          parts: [{ kind: "text", text: "A-7781 ships today." }],
        },
      }),
    );
    render(
      <Harness
        initial={{
          a2a: {
            card_url: "https://support.acme.test",
            card: CARD,
            skill_id: "answer",
            text_column: "c1",
            data_columns: [],
            auth: { kind: "bearer", secret: "ACME_TOKEN" },
          },
        }}
      />,
    );
    expect(
      screen.getByText("Sends message/send for the sample row."),
    ).toBeTruthy();
    fireEvent.click(screen.getByTestId("a2a-test"));
    const reply = await screen.findByTestId("a2a-test-reply");
    expect(reply.textContent).toBe("A-7781 ships today.");
    const [route, body] = post.mock.calls[0];
    expect(route).toBe(ROUTE);
    expect(body.prepared_request.url).toBe("https://support.acme.test/a2a");
    expect(body.prepared_request.method).toBe("POST");
    expect(body.prepared_request.headers).toEqual([
      { name: "Authorization", secret_ref: "ACME_TOKEN", scheme: "bearer" },
    ]);
    const sent = JSON.parse(body.prepared_request.body);
    expect(sent.method).toBe("message/send");
    expect(sent.params.message.parts).toEqual([
      { kind: "text", text: "Where is A-7781?" },
    ]);
    // A real agent answered: no "Simulated" tag.
    expect(screen.queryByTestId("a2a-test-simulated")).toBeNull();
    expect(screen.getByTestId("a2a-test-timeline").textContent).toContain(
      "Authorization: Bearer •••• (from secret ACME_TOKEN)",
    );
  });

  it("an unfinished task is shown as the plain reason, not as a reply", async () => {
    post.mockResolvedValueOnce(
      upstream(200, {
        jsonrpc: "2.0",
        id: "x",
        result: { kind: "task", id: "t-9", status: { state: "working" } },
      }),
    );
    render(
      <Harness
        initial={{
          a2a: {
            card_url: "https://support.acme.test",
            card: CARD,
            skill_id: "answer",
            text_column: "c1",
          },
        }}
      />,
    );
    fireEvent.click(screen.getByTestId("a2a-test"));
    await waitFor(() =>
      expect(screen.getByTestId("a2a-test-reply").textContent).toBe(
        "The agent's task is still working. Future AGI sends one message per row and does not wait. Make the agent finish before it answers, then Retry this row.",
      ),
    );
  });

  it("says which host each row's message and the auth secret go to", () => {
    render(
      <Harness
        initial={{
          a2a: {
            card_url: "https://cards.acme.test",
            // The card is read from one host and names another for messages.
            fetched_from: "https://cards.acme.test/.well-known/agent-card.json",
            card: CARD,
            skill_id: "answer",
            text_column: "c1",
            auth: { kind: "bearer", secret: "ACME_TOKEN" },
          },
        }}
      />,
    );
    expect(screen.getByTestId("a2a-send-host").textContent).toBe(
      "Each row's message and the auth secret go to support.acme.test. The card was read from cards.acme.test.",
    );
    cleanup();
    render(
      <Harness
        initial={{
          a2a: {
            card_url: "https://support.acme.test",
            fetched_from:
              "https://support.acme.test/.well-known/agent-card.json",
            card: CARD,
            skill_id: "answer",
            text_column: "c1",
          },
        }}
      />,
    );
    expect(screen.getByTestId("a2a-send-host").textContent).toBe(
      "Each row's message goes to support.acme.test.",
    );
  });

  it("Basic sign-in is refused before anything is sent", async () => {
    render(
      <Harness
        initial={{
          a2a: {
            card_url: "https://support.acme.test",
            card: CARD,
            skill_id: "answer",
            text_column: "c1",
            auth: { kind: "basic", username: "bot", secret: "S" },
          },
        }}
      />,
    );
    fireEvent.click(screen.getByTestId("a2a-test"));
    expect((await screen.findByTestId("a2a-error")).textContent).toBe(
      "Basic and OAuth 2 sign-in for A2A agents aren't available in this release. Choose Bearer token, API key header or None.",
    );
    expect(post).not.toHaveBeenCalled();
  });
});
