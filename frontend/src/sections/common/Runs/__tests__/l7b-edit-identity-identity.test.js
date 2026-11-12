// options-truth L7b G2 (RC2): one run identity for the form and the mock.
import { describe, expect, it } from "vitest";
import {
  filledFrom,
  forCompare,
  kindChangeError,
  mappingAsChanged,
  runIdentityChanges,
  runIdentityOf,
  sameRunIdentity,
} from "../runIdentity";

const httpRun = {
  label: "#8A",
  letter: "A",
  target_type: "http",
  target: { url: "https://agent.acme.test/v2/reply", method: "POST" },
  config: {
    kind: "http",
    endpoint: {
      url: "https://agent.acme.test/v2/reply",
      method: "POST",
      headers: [],
      body_template: '{"message": "{{customer_message}}"}',
      output_path: "$.reply",
    },
    limits: { tries: 1, max_cost_usd: null },
  },
};
const httpEntry = {
  target_type: "http",
  run_id: "r8a",
  http: {
    url: "https://agent.acme.test/v2/reply",
    method: "POST",
    headers: [],
    body_template: '{"message": "{{customer_message}}"}',
    output_path: "$.reply",
  },
};

describe("runIdentityOf · equal / unequal per field", () => {
  it("an unchanged HTTP entry is the run it came from", () => {
    const a = runIdentityOf(httpRun, { trials: 1 });
    const b = filledFrom(runIdentityOf(httpEntry, { trials: 1 }), a);
    expect(sameRunIdentity(a, b)).toBe(true);
    expect(runIdentityChanges(a, b)).toEqual([]);
  });

  it.each([
    [
      "output path",
      { http: { ...httpEntry.http, output_path: "$.output.text" } },
      "output path $.reply → $.output.text",
    ],
    [
      "body",
      { http: { ...httpEntry.http, body_template: "{}" } },
      "body changed",
    ],
    [
      "method",
      { http: { ...httpEntry.http, method: "PUT" } },
      "method POST → PUT",
    ],
    [
      "header",
      {
        http: {
          ...httpEntry.http,
          headers: [{ name: "X-Tier", value: "gold" }],
        },
      },
      "+ header X-Tier",
    ],
    ["tries", { limits: { tries: 3 } }, "tries 1 → 3"],
    ["cost cap", { limits: { max_cost_usd: 0.5 } }, "cost cap – → $0.50"],
    [
      "mapping",
      { variable_mapping: { customer_message: "c-intent" } },
      "mapping – → customer_message ← c-intent",
    ],
  ])("%s changes the identity", (_, patch, words) => {
    const a = runIdentityOf(httpRun, { trials: 1 });
    const b = filledFrom(
      runIdentityOf({ ...httpEntry, ...patch }, { trials: 1 }),
      a,
    );
    expect(sameRunIdentity(a, b)).toBe(false);
    expect(runIdentityChanges(a, b)).toContain(words);
  });

  it("tries equal to the experiment's Trials are not a change", () => {
    const a = runIdentityOf(httpRun, { trials: 1 });
    const b = filledFrom(
      runIdentityOf({ ...httpEntry, limits: { tries: 1 } }, { trials: 1 }),
      a,
    );
    expect(sameRunIdentity(a, b)).toBe(true);
  });

  it("A2A: card URL, skill and parts", () => {
    const run = {
      label: "#9B",
      letter: "B",
      target_type: "http",
      target: {
        source: "a2a",
        url: "https://help.globex.test/a2a",
        skill: "refund_request",
      },
      config: {
        kind: "a2a",
        a2a: {
          card_url: "https://help.globex.test",
          skill_id: "refund_request",
          text_column: "c1",
          data_columns: ["c2"],
          context_column: null,
          auth: { name: "Authorization", secret: "" },
          agent: "Globex Help",
        },
      },
    };
    const entry = {
      target_type: "a2a",
      a2a: { ...run.config.a2a, agent: undefined, card: { name: "Globex" } },
    };
    const a = runIdentityOf(run, { trials: 1 });
    expect(a.kind).toBe("a2a");
    const same = filledFrom(runIdentityOf(entry, { trials: 1 }), a);
    expect(sameRunIdentity(a, same)).toBe(true);
    const other = filledFrom(
      runIdentityOf(
        { ...entry, a2a: { ...entry.a2a, skill_id: "order_status" } },
        { trials: 1 },
      ),
      a,
    );
    expect(runIdentityChanges(a, other)).toEqual([
      "skill refund_request → order_status",
    ]);
  });

  it("MCP tool: server incl. env, tool, arguments, side effects", () => {
    const server = {
      name: "acme-support-sandbox",
      transport: "stdio",
      command: "npx acme-support-mcp",
      url: "",
      env_secret: "ACME_KEY",
    };
    const run = {
      label: "#4A",
      letter: "A",
      target_type: "http",
      target: { source: "mcp_tool", server: server.name, tool: "lookup_order" },
      config: {
        kind: "mcp_tool",
        mcp_tool: {
          server,
          tool: "lookup_order",
          arguments: { order_id: { column: "c1" } },
          side_effects: "stubs",
        },
      },
    };
    const entry = {
      target_type: "mcp_tool",
      mcp_tool: { ...run.config.mcp_tool },
    };
    const a = runIdentityOf(run, { trials: 2 });
    expect(a.kind).toBe("mcp_tool");
    expect(a.limits.tries).toBe(2);
    expect(
      sameRunIdentity(a, filledFrom(runIdentityOf(entry, { trials: 2 }), a)),
    ).toBe(true);
    const env = filledFrom(
      runIdentityOf(
        {
          ...entry,
          mcp_tool: {
            ...entry.mcp_tool,
            server: { ...server, env_secret: "OTHER" },
          },
        },
        { trials: 2 },
      ),
      a,
    );
    expect(runIdentityChanges(a, env)).toEqual(["env secret ACME_KEY → OTHER"]);
    const fx = filledFrom(
      runIdentityOf(
        { ...entry, mcp_tool: { ...entry.mcp_tool, side_effects: "real" } },
        { trials: 2 },
      ),
      a,
    );
    expect(runIdentityChanges(a, fx)).toEqual(["side effects stubs → real"]);
  });

  it("prompt: version, model, params, tools, max tool turns", () => {
    const run = {
      label: "#1A",
      letter: "A",
      target_type: "prompt",
      target: {
        prompt_id: "p1",
        prompt_version_id: "v3",
        model: "gpt-4o",
        model_params: { temperature: 0.3 },
      },
      config: {
        kind: "prompt",
        tools: [{ name: "lookup_order", mode: "mock" }],
        max_tool_turns: 8,
      },
    };
    const entry = {
      target_type: "prompt",
      prompt_id: "p1",
      prompt_version: "v3",
      model: "gpt-4o",
      model_params: { temperature: 0.3, providers: "openai", logoUrl: "x" },
      tools: [{ name: "lookup_order", mode: "mock" }],
      max_tool_turns: 8,
      on_unrecorded: "mock",
    };
    const a = runIdentityOf(run, { trials: 1 });
    expect(
      sameRunIdentity(a, filledFrom(runIdentityOf(entry, { trials: 1 }), a)),
    ).toBe(true);
    // an entry that does not carry its tools keeps the run's
    const { tools: _t, max_tool_turns: _m, on_unrecorded: _o, ...bare } = entry;
    expect(
      sameRunIdentity(a, filledFrom(runIdentityOf(bare, { trials: 1 }), a)),
    ).toBe(true);
    const turns = filledFrom(
      runIdentityOf({ ...entry, max_tool_turns: 3 }, { trials: 1 }),
      a,
    );
    expect(runIdentityChanges(a, turns)).toEqual(["max tool turns 8 → 3"]);
    const temp = filledFrom(
      runIdentityOf(
        { ...entry, model_params: { temperature: 0.7 } },
        { trials: 1 },
      ),
      a,
    );
    expect(runIdentityChanges(a, temp)).toEqual([
      "params temperature 0.3 → 0.7",
    ]);
    const ver = filledFrom(
      runIdentityOf({ ...entry, prompt_version: "v4" }, { trials: 1 }),
      a,
    );
    expect(sameRunIdentity(a, ver)).toBe(false);
  });

  it("agent: graph version", () => {
    const run = {
      label: "#4A",
      letter: "A",
      target_type: "agent",
      target: { graph_id: "g1", version_id: "gv3" },
      config: { kind: "agent" },
    };
    const a = runIdentityOf(run, { trials: 1 });
    const same = filledFrom(
      runIdentityOf(
        { target_type: "agent", agent_id: "g1", agent_version: "gv3" },
        { trials: 1 },
      ),
      a,
    );
    expect(sameRunIdentity(a, same)).toBe(true);
    const other = filledFrom(
      runIdentityOf(
        { target_type: "agent", agent_id: "g1", agent_version: "gv1" },
        { trials: 1 },
      ),
      a,
    );
    expect(runIdentityChanges(a, other)).toEqual(["version gv3 → gv1"]);
  });

  it("an entry of another kind is refused, never converted", () => {
    const a2a = runIdentityOf(
      {
        label: "#9B",
        letter: "B",
        target_type: "http",
        target: { source: "a2a" },
        config: { kind: "a2a", a2a: {} },
      },
      { trials: 1 },
    );
    expect(kindChangeError("#9B", a2a, { kind: "http" })).toBe(
      "#9B is an A2A run; edit it as an A2A card",
    );
    expect(kindChangeError("#9B", a2a, { kind: "a2a" })).toBeNull();
    const mcp = { kind: "mcp_tool" };
    expect(kindChangeError("#4A", mcp, { kind: "http" })).toBe(
      "#4A is an MCP tool run; edit it as an MCP tool card",
    );
  });

  it("names a removed remap while retaining the other explicit bindings", () => {
    const fixed = { kind: "constant", value: "retained" };
    const before = runIdentityOf({
      ...httpEntry,
      variable_mapping: { context: "c-context::$.summary", fixed },
    });
    const after = runIdentityOf({
      ...httpEntry,
      variable_mapping: { context: "c-context", fixed },
    });
    const [was, now] = forCompare(before, after, {
      columns: [{ id: "c-context", name: "context" }],
    });
    expect(now.variable_mapping).toEqual({ fixed });
    expect(sameRunIdentity(was, now)).toBe(false);
    expect(runIdentityChanges(was, now)).toEqual([
      'mapping context ← c-context::$.summary, fixed = "retained" → fixed = "retained"',
    ]);
    expect(filledFrom(now, was).variable_mapping).toEqual({ fixed });
    expect(filledFrom(runIdentityOf(httpEntry), before).variable_mapping)
      .toEqual(before.variable_mapping);
    expect(filledFrom({ variable_mapping: {} }, before).variable_mapping)
      .toEqual({});
  });

  it("a by-name mapping is the same as none (the form maps by itself)", () => {
    const columns = [
      { id: "c1", name: "customer_message" },
      { id: "c7", name: "customer_tier" },
      { id: "c9", name: "intent" },
    ];
    expect(
      mappingAsChanged(
        { customer_message: "c1", customer_tier: "c7", note: "" },
        columns,
      ),
    ).toBeNull();
    expect(mappingAsChanged({ customer_message: "c9" }, columns)).toEqual({
      customer_message: "c9",
    });
    const a = runIdentityOf(httpRun, { trials: 1 });
    const byName = runIdentityOf(
      { ...httpEntry, variable_mapping: { customer_message: "c1" } },
      { trials: 1 },
    );
    expect(sameRunIdentity(...forCompare(a, byName, { columns }))).toBe(true);
    const moved = runIdentityOf(
      { ...httpEntry, variable_mapping: { customer_message: "c9" } },
      { trials: 1 },
    );
    const [b2, a2] = forCompare(a, moved, { columns });
    expect(sameRunIdentity(b2, a2)).toBe(false);
    expect(runIdentityChanges(b2, a2)).toEqual([
      "mapping – → customer_message ← c9",
    ]);
  });
});
