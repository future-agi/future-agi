import { describe, expect, it } from "vitest";

import { DEMO_MCP } from "../testing/renderMcp";
import {
  accessSummary,
  toolCandidates,
  formFromEntry,
  globMatch,
  mcpEndpoint,
  notEnforced,
  slugFromTitle,
  slugError,
  virtualServerPatch,
  virtualServerRows,
  virtualServerStatus,
} from "./virtualServers";

const TOOLS = [
  {
    name: "crm-tools__find_account",
    server_id: "crm-tools",
    upstream_name: "find_account",
  },
  {
    name: "crm-tools__log_activity",
    server_id: "crm-tools",
    upstream_name: "log_activity",
  },
  {
    name: "support-tools__create_ticket",
    server_id: "support-tools",
    upstream_name: "create_ticket",
  },
  {
    name: "support-tools__delete_customer_data",
    server_id: "support-tools",
    upstream_name: "delete_customer_data",
  },
];

describe("virtual server helpers", () => {
  it("builds the MCP endpoint from the gateway's OpenAI base URL", () => {
    expect(mcpEndpoint("http://localhost:28490/v1", "support-desk")).toBe(
      "http://localhost:28490/mcp/v/support-desk",
    );
    expect(mcpEndpoint("https://gw.example.com/v1/", "")).toBe(
      "https://gw.example.com/mcp",
    );
    expect(mcpEndpoint("", "x")).toBeNull();
  });

  it("matches the gateway's globs: * spans any run, / included", () => {
    expect(globMatch("support-tools/*", "support-tools/create_ticket")).toBe(
      true,
    );
    expect(globMatch("*/delete_*", "support-tools/delete_customer_data")).toBe(
      true,
    );
    expect(globMatch("crm-tools/find_account", "crm-tools/find_account")).toBe(
      true,
    );
    expect(globMatch("crm-tools/find", "crm-tools/find_account")).toBe(false);
  });

  it("lists filtered upstream candidates without claiming composed names", () => {
    const entry = {
      members: ["support-tools", "crm-tools"],
      naming: "prefixed",
      tools: { exclude: ["*/delete_*"] },
      overrides: { "crm-tools/log_activity": { hidden: true } },
    };
    expect(toolCandidates(entry, TOOLS).map((t) => t.ref)).toEqual([
      "crm-tools/find_account",
      "support-tools/create_ticket",
    ]);
    expect(
      toolCandidates({ members: ["crm-tools"], naming: "flat" }, TOOLS).map(
        (t) => t.ref,
      ),
    ).toEqual(["crm-tools/find_account", "crm-tools/log_activity"]);
    expect(
      toolCandidates(
        {
          members: ["crm-tools", "support-tools"],
          tools: { include: ["support-tools/*"] },
        },
        TOOLS,
      ),
    ).toHaveLength(2);
  });

  it.each([undefined, null])("does not invent a zero count for unknown inventory (%s)", (tools) => {
    expect(virtualServerRows(DEMO_MCP, tools, "")[0].candidateCount).toBeNull();
    expect(virtualServerRows(DEMO_MCP, [], "")[0].candidateCount).toBe(0);
  });

  it("lists every virtual server with its endpoint, members and tools", () => {
    const rows = virtualServerRows(
      DEMO_MCP,
      TOOLS,
      "http://localhost:28490/v1",
    );
    expect(rows).toEqual([
      expect.objectContaining({
        slug: "support-desk",
        id: "vs_01M5CA9CSQ7WV0A7Z7W8T71E58",
        title: "Support desk",
        members: ["support-tools", "crm-tools"],
        candidateCount: 4,
        endpoint: "http://localhost:28490/mcp/v/support-desk",
      }),
    ]);
  });

  it("names what a virtual server stores but the gateway does not enforce", () => {
    expect(notEnforced(DEMO_MCP.virtual_servers["support-desk"])).toEqual([]);
    expect(
      notEnforced({
        mode: "search",
        budgets: { rpm_per_key: 10, max_tool_usd_per_run: 1 },
        step_profiles: { act: { include: ["*"] } },
        approval: { require: "none" },
      }).map((item) => item.key),
    ).toEqual(["budgets"]);
  });

  it("never calls search mode or step profiles unenforced: the gateway applies both", () => {
    // hook_list_toolset.go and hook_metatool.go (M30): a search-mode virtual
    // server lists search_tools, describe_tool and call_tool in place of its
    // tools, and a step profile narrows what a step is offered.
    expect(notEnforced({ mode: "search" })).toEqual([]);
    expect(notEnforced({ step_profiles: { act: { include: ["*"] } } })).toEqual(
      [],
    );
  });

  it("says who may use a virtual server", () => {
    expect(accessSummary(undefined)).toBe("Every key in the organization");
    expect(accessSummary({ default_for_org: true })).toBe(
      "Every key (organization default)",
    );
    expect(accessSummary({ keys: ["k1", "k2"], agents: ["a1"] })).toBe(
      "2 keys, 1 agent",
    );
  });

  it("derives and checks slugs the way the API does", () => {
    expect(slugFromTitle("Billing desk (EU)")).toBe("billing-desk-eu");
    expect(slugError("billing-desk", [])).toBe("");
    expect(slugError("Billing", [])).toMatch(/lowercase/);
    expect(slugError("support-desk", ["support-desk"])).toMatch(/already/);
    expect(slugError("", [])).toMatch(/required/i);
  });

  it("creates a virtual server without an id and with only enforced fields", () => {
    const form = {
      ...formFromEntry(null),
      title: "Billing desk",
      slug: "billing-desk",
      members: ["crm-tools"],
    };
    expect(virtualServerPatch(form, null)).toEqual({
      virtual_servers: {
        "billing-desk": {
          title: "Billing desk",
          description: null,
          members: ["crm-tools"],
          naming: "prefixed",
          tools: null,
          access: null,
        },
      },
    });
  });

  it("edits a stored one as a merge patch that leaves the rest as stored", () => {
    const stored = DEMO_MCP.virtual_servers["support-desk"];
    const form = {
      ...formFromEntry(stored, "support-desk"),
      title: "Support desk (EU)",
      keys: ["key_1"],
    };
    const patch = virtualServerPatch(form, stored);
    const entry = patch.virtual_servers["support-desk"];
    expect(entry).toEqual({
      title: "Support desk (EU)",
      description: "Tools the support bot may call",
      members: ["support-tools", "crm-tools"],
      naming: "prefixed",
      tools: null,
      access: { keys: ["key_1"], agents: null, default_for_org: null },
    });
    // The id, the mode and the stored approval are not sent: the merge keeps them.
    expect(entry).not.toHaveProperty("id");
    expect(entry).not.toHaveProperty("approval");
    expect(entry).not.toHaveProperty("mode");
  });

  it("reports MCP status without claiming candidates are served", () => {
    expect(virtualServerStatus({ kind: "on" }, 2)).toEqual({
      tone: "healthy",
      label: "MCP on",
    });
    expect(virtualServerStatus({ kind: "on" }, 0)).toEqual({
      tone: "neutral",
      label: "No candidates",
    });
    // No servers at all: nothing to serve.
    expect(virtualServerStatus({ kind: "empty" }, 0)).toEqual({
      tone: "neutral",
      label: "No candidates",
    });
    ["gateway_off", "org_off"].forEach((kind) => {
      expect(virtualServerStatus({ kind }, 2)).toEqual({
        tone: "neutral",
        label: "MCP off",
      });
      expect(virtualServerStatus({ kind }, 0)).toEqual({
        tone: "neutral",
        label: "MCP off",
      });
    });
    // Never green before the gateway has answered, or when it cannot.
    ["loading", "status_error", "unreachable", "not_reported"].forEach(
      (kind) => {
        expect(virtualServerStatus({ kind }, 2)).toEqual({
          tone: "neutral",
          label: "Status unknown",
        });
      },
    );
    expect(virtualServerStatus(undefined, 2)).toEqual({
      tone: "neutral",
      label: "Status unknown",
    });
  });
});

// With MCP on, a row read "No tools" while the tool list was
// still loading, and for good when that read failed.
describe("virtualServerStatus before the tool list is known", () => {
  it("says Status unknown while the tools load or when their read failed", () => {
    for (const toolsKnown of [false]) {
      expect(virtualServerStatus({ kind: "on" }, 0, { toolsKnown })).toEqual({
        tone: "neutral",
        label: "Status unknown",
      });
      expect(virtualServerStatus({ kind: "on" }, 2, { toolsKnown })).toEqual({
        tone: "neutral",
        label: "Status unknown",
      });
    }
  });

  it("still says MCP off when the gateway says so, tools or not", () => {
    expect(
      virtualServerStatus({ kind: "gateway_off" }, 0, { toolsKnown: false }),
    ).toEqual({ tone: "neutral", label: "MCP off" });
  });

  it("reads the tool count once the tools are known", () => {
    expect(
      virtualServerStatus({ kind: "on" }, 0, { toolsKnown: true }),
    ).toEqual({ tone: "neutral", label: "No candidates" });
  });
});
