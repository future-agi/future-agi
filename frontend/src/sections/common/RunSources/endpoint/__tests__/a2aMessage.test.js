// The A2A card's requests match what the server sends for each row, and a
// reply reads the same way the server stores it.
import { describe, expect, it } from "vitest";
import {
  ENDPOINT_TEST,
  a2aAuthProblem,
  a2aMessageBody,
  a2aReplyOf,
  a2aRequestHeaders,
  agentCardUrls,
  agentJsonRpcUrl,
  isAgentCard,
  normalizedCard,
} from "../a2aMessage";

describe("A2A agent card requests", () => {
  it("goes through the core's endpoint test route", () => {
    expect(ENDPOINT_TEST).toBe("/model-hub/r4/endpoints/test/");
  });

  it("looks for the card on the well-known paths of a base URL", () => {
    expect(agentCardUrls("https://support.acme.test/")).toEqual([
      "https://support.acme.test/.well-known/agent-card.json",
      "https://support.acme.test/.well-known/agent.json",
    ]);
    expect(agentCardUrls("https://acme.test/agents/help")).toEqual([
      "https://acme.test/agents/help/.well-known/agent-card.json",
      "https://acme.test/agents/help/.well-known/agent.json",
    ]);
    expect(agentCardUrls("https://acme.test/cards/help.json")).toEqual([
      "https://acme.test/cards/help.json",
    ]);
    expect(agentCardUrls("support.acme.test")).toEqual([]);
    expect(agentCardUrls("")).toEqual([]);
  });

  it("fills the fields the card view reads, so a sparse card still shows", () => {
    const card = normalizedCard({
      name: "Help",
      url: "https://acme.test/a2a",
      defaultInputModes: ["text/plain"],
      skills: [{ id: "answer" }, { name: "no id" }, null],
    });
    expect(card.version).toBe("");
    expect(card.skills).toEqual([
      {
        id: "answer",
        name: "answer",
        description: "",
        tags: [],
        inputModes: ["text/plain"],
      },
    ]);
    expect(isAgentCard(card)).toBe(true);
    expect(isAgentCard({ error: "not found" })).toBe(false);
    expect(isAgentCard([])).toBe(false);
  });

  it("sends to the card's JSON-RPC URL", () => {
    expect(agentJsonRpcUrl({ url: "https://acme.test/a2a" })).toBe(
      "https://acme.test/a2a",
    );
    expect(
      agentJsonRpcUrl({
        preferredTransport: "GRPC",
        url: "https://acme.test/grpc",
        additionalInterfaces: [
          { transport: "JSONRPC", url: "https://acme.test/jsonrpc" },
        ],
      }),
    ).toBe("https://acme.test/jsonrpc");
    expect(
      agentJsonRpcUrl({ preferredTransport: "GRPC", url: "https://x.test" }),
    ).toBeNull();
  });

  it("builds the same message/send body the server sends per row", () => {
    expect(JSON.parse(a2aMessageBody('Where is "A-7781"?', "r1"))).toEqual({
      jsonrpc: "2.0",
      id: "r1",
      method: "message/send",
      params: {
        message: {
          kind: "message",
          role: "user",
          messageId: "r1",
          parts: [{ kind: "text", text: 'Where is "A-7781"?' }],
        },
        configuration: { blocking: true },
      },
    });
  });

  it("sends auth as secret references only", () => {
    expect(a2aRequestHeaders({ name: "Authorization", secret: "" })).toEqual(
      [],
    );
    expect(a2aRequestHeaders({ kind: "bearer", secret: "ACME_TOKEN" })).toEqual(
      [{ name: "Authorization", secret_ref: "ACME_TOKEN", scheme: "bearer" }],
    );
    expect(
      a2aRequestHeaders({ kind: "api_key", header: "X-Key", secret: "K" }),
    ).toEqual([{ name: "X-Key", secret_ref: "K" }]);
    expect(a2aRequestHeaders({ kind: "basic", secret: "S" })).toBeNull();
    expect(a2aAuthProblem({ kind: "oauth2", secret: "S" })).toBe(
      "Basic and OAuth 2 sign-in for A2A agents aren't available in this release. Choose Bearer token, API key header or None.",
    );
    expect(a2aAuthProblem({ kind: "bearer", secret: "" })).toBe(
      "Pick the workspace secret this auth sends.",
    );
  });

  it("reads a Message, a finished Task, and says why an unfinished one is an error", () => {
    expect(
      a2aReplyOf({
        result: { kind: "message", parts: [{ kind: "text", text: "Hi" }] },
      }).text,
    ).toBe("Hi");
    expect(
      a2aReplyOf({
        result: {
          kind: "task",
          id: "t-1",
          status: { state: "completed" },
          artifacts: [{ parts: [{ kind: "text", text: "Refund sent" }] }],
        },
      }),
    ).toEqual({
      text: "Refund sent",
      error: null,
      state: "completed",
      taskId: "t-1",
    });
    expect(
      a2aReplyOf({
        result: {
          id: "t-2",
          status: {
            state: "completed",
            message: [{ type: "text", text: "Old shape" }],
          },
        },
      }).text,
    ).toBe("Old shape");
    expect(
      a2aReplyOf({ result: { kind: "task", status: { state: "working" } } })
        .error,
    ).toBe(
      "The agent's task is still working. Future AGI sends one message per row and does not wait. Make the agent finish before it answers, then Retry this row.",
    );
    expect(a2aReplyOf({ error: { code: -32601, message: "x" } }).error).toBe(
      "The agent answered with A2A error -32601. Check the agent and its settings, then Retry this row.",
    );
    // Artifacts that are not a list are a malformed reply, as on the server.
    for (const artifacts of [7, true, "text"])
      expect(
        a2aReplyOf({
          result: { kind: "task", status: { state: "completed" }, artifacts },
        }).error,
      ).toBe(
        "The agent's reply is not an A2A message/send result. Check the agent URL, then retry.",
      );
    expect(a2aReplyOf("nope").error).toBe(
      "The agent's reply is not an A2A message/send result. Check the agent URL, then retry.",
    );
  });
});
