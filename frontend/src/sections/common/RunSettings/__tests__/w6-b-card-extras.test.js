// W6 B2/B4/B5: the create-body fields an endpoint card adds (FE seam slot
// cardExtras/endpoint, `extrasOf(card)`), and Review's cells for endpoint
// runs (runUses/endpoint, `usesOf(card, {kind})`), through the
// composers (cardExtras.js / runUses.js).
import { describe, expect, it } from "vitest";
import endpointExtrasSlot from "../cardExtras/endpoint";
import endpointUsesSlot from "../runUses/endpoint";
import { cardExtrasOf } from "../cardExtras";
import { runUsesOf } from "../runUses";

const endpointCardExtras = endpointExtrasSlot.extrasOf;
const endpointUses = endpointUsesSlot.usesOf;

describe("cardExtras/endpoint", () => {
  it("adds the transform only when one is set", () => {
    expect(endpointCardExtras({ http: { outputPath: "$.reply" } })).toEqual({});
    expect(
      endpointCardExtras({
        http: { outputPath: "$.reply", transform: { tool_calls: "$.actions" } },
      }),
    ).toEqual({
      endpoint: { transform: { output: "$.reply", tool_calls: "$.actions" } },
    });
  });

  it("adds a complete context source", () => {
    expect(
      endpointCardExtras({
        contextSource: {
          variable: "context",
          kb_id: "kb1",
          top_k: "3",
          query_column: "c1",
        },
      }),
    ).toEqual({
      context_source: {
        variable: "context",
        kb_id: "kb1",
        top_k: 3,
        query_column: "c1",
      },
    });
    expect(
      endpointCardExtras({ contextSource: { variable: "context" } }),
    ).toEqual({});
  });

  it("reaches the create entry through the composer", () => {
    expect(
      cardExtrasOf({
        targetType: "http",
        http: { outputPath: "$.reply", transform: { tool_calls: "$.actions" } },
      }),
    ).toEqual({
      endpoint: { transform: { output: "$.reply", tool_calls: "$.actions" } },
    });
  });
});

describe("runUses/endpoint", () => {
  it("names the host, transform and secrets of an HTTP card", () => {
    const card = {
      targetType: "http",
      http: {
        url: "https://support-agent.acme.test/v2/reply",
        outputPath: "$.reply",
        headers: [{ name: "Authorization", secret: "FI_PROJECT_KEY" }],
        transform: { tool_calls: "$.actions" },
      },
    };
    expect(endpointUses(card, { kind: "http" })).toEqual({
      model:
        "HTTP · support-agent.acme.test · Output $.reply · tool calls $.actions",
      tools: "from the response ($.actions)",
      permissions: "secrets FI_PROJECT_KEY",
    });
    expect(runUsesOf(card, { kind: "http" })).toEqual(
      endpointUses(card, { kind: "http" }),
    );
    expect(endpointUses({ targetType: "prompt" })).toBeNull();
  });

  it("leaves out cells it has nothing for", () => {
    expect(
      endpointUses(
        { http: { url: "https://agent.acme.test/reply", headers: [] } },
        { kind: "http" },
      ),
    ).toEqual({ model: "HTTP · agent.acme.test" });
  });

  it("names an A2A card's auth and an MCP tool's env secret", () => {
    expect(
      endpointUses(
        {
          a2a: {
            card: { name: "Acme support" },
            skill_id: "refunds",
            auth: { kind: "bearer", secret: "acme-key" },
          },
        },
        { kind: "a2a" },
      ),
    ).toEqual({
      model: "A2A · Acme support · skill refunds",
      permissions: "Bearer token · secret acme-key",
    });
    expect(
      endpointUses(
        {
          mcp_tool: {
            tool: "lookup_order",
            server: {
              name: "acme-orders",
              env: [{ name: "ACME_TOKEN", secret: "acme-mcp" }],
            },
          },
        },
        { kind: "mcp_tool" },
      ),
    ).toEqual({
      tools: "lookup_order",
      mcp: "acme-orders · env ACME_TOKEN ← secret acme-mcp",
    });
  });
});
