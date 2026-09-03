import { describe, it, expect } from "vitest";
import {
  CHANGE_STATUS,
  buildAgentDefinitionFileName,
  buildDefinitionDocument,
  classifySaveChanges,
  countLineChanges,
  definitionToJson,
  flattenGraphVersions,
  pairNodesByName,
  pickBaselineVersion,
} from "../saveAgentDiff";
import { buildVersionPayload } from "../versionPayloadUtils";
import { NODE_TYPES, VERSION_STATUS } from "../constants";

function node(id, name, extra = {}) {
  return {
    id,
    name,
    type: extra.type || "atomic",
    prompt_template: extra.prompt_template || {
      model: "gpt-4",
      messages: [{ role: "system", content: extra.prompt || "hello" }],
    },
  };
}

function snapshot(nodes, connections = []) {
  return { nodes, connections };
}

describe("pairNodesByName", () => {
  it("pairs remapped UUIDs by name", () => {
    const { pairs, created, deleted } = pairNodesByName(
      [node("old-1", "Prompt node")],
      [node("new-99", "Prompt node")],
    );
    expect(pairs).toHaveLength(1);
    expect(pairs[0].previous.id).toBe("old-1");
    expect(pairs[0].current.id).toBe("new-99");
    expect(created).toHaveLength(0);
    expect(deleted).toHaveLength(0);
  });
});

describe("classifySaveChanges", () => {
  it("marks created, updated, deleted, rerouted, and unchanged", () => {
    const previous = snapshot(
      [
        node("a", "Prompt node", { prompt: "old instructions" }),
        node("b", "Research Agent"),
        node("c", "Conditional node"),
        node("d", "Output node"),
        node("idle", "Idle node"),
      ],
      [
        { source_node_id: "a", target_node_id: "b" },
        { source_node_id: "b", target_node_id: "d" },
      ],
    );
    const current = snapshot(
      [
        node("a2", "Prompt node", { prompt: "new instructions" }),
        node("b2", "Research Agent"),
        node("d2", "Output node"),
        node("e", "Custom code"),
        node("idle2", "Idle node"),
      ],
      [
        { source_node_id: "a2", target_node_id: "e" },
        { source_node_id: "e", target_node_id: "d2" },
      ],
    );

    const result = classifySaveChanges({
      previousSnapshot: previous,
      currentSnapshot: current,
    });
    const byName = Object.fromEntries(
      result.entries.map((entry) => [entry.name, entry]),
    );

    expect(byName["Prompt node"].status).toBe(CHANGE_STATUS.UPDATED);
    expect(byName["Prompt node"].description).toMatch(/prompt/i);
    expect(byName["Research Agent"].status).toBe(CHANGE_STATUS.REROUTED);
    expect(byName["Conditional node"].status).toBe(CHANGE_STATUS.DELETED);
    expect(byName["Custom code"].status).toBe(CHANGE_STATUS.CREATED);
    expect(byName["Output node"].status).toBe(CHANGE_STATUS.REROUTED);
    expect(byName["Idle node"].status).toBe(CHANGE_STATUS.UNCHANGED);
    expect(result.entries.map((entry) => entry.name)).toContain("Idle node");
  });

  it("treats first save with no previous version as all created", () => {
    const result = classifySaveChanges({
      previousSnapshot: snapshot([]),
      currentSnapshot: snapshot([node("n1", "Prompt node")]),
    });
    expect(result.hasBaseline).toBe(false);
    expect(result.entries).toHaveLength(1);
    expect(result.entries[0].status).toBe(CHANGE_STATUS.CREATED);
    expect(result.previousJson).toBeTruthy();
    expect(result.currentJson).toContain("Prompt node");
  });

  it("does not throw when both sides are empty", () => {
    expect(() =>
      classifySaveChanges({
        previousSnapshot: snapshot([]),
        currentSnapshot: snapshot([]),
      }),
    ).not.toThrow();
  });

  it("ignores position-only moves", () => {
    const previous = snapshot([
      {
        id: "a",
        name: "Prompt node",
        type: "atomic",
        position: { x: 0, y: 0 },
      },
    ]);
    const current = snapshot([
      {
        id: "b",
        name: "Prompt node",
        type: "atomic",
        position: { x: 400, y: 20 },
      },
    ]);
    const result = classifySaveChanges({
      previousSnapshot: previous,
      currentSnapshot: current,
    });
    expect(result.entries[0].status).toBe(CHANGE_STATUS.UNCHANGED);
  });

  it("treats an unedited prompt node as unchanged across GET vs payload shapes", () => {
    const previous = snapshot(
      [
        {
          id: "old-prompt",
          name: "Prompt node",
          type: "atomic",
          node_template_id: "tpl-prompt",
          prompt_template: {
            model: "gpt-4",
            messages: [
              { role: "system", content: "You are a helpful assistant" },
            ],
            template_format: "f-string",
            variable_names: [],
            metadata: {},
            is_draft: false,
            template_version: 1,
            response_schema: null,
            response_format: "text",
          },
        },
      ],
      [],
    );
    const currentPayload = buildVersionPayload(
      [
        {
          id: "new-prompt",
          type: NODE_TYPES.LLM_PROMPT,
          position: { x: 0, y: 0 },
          data: {
            label: "Prompt node",
            node_template_id: "tpl-prompt",
            config: {
              modelConfig: {
                model: "gpt-4",
                modelDetail: {
                  modelName: "GPT-4",
                  logoUrl: "",
                  providers: "openai",
                  isAvailable: true,
                },
                responseFormat: "text",
                toolChoice: "auto",
                tools: [],
              },
              messages: [
                {
                  id: "msg-0",
                  role: "system",
                  content: [
                    { type: "text", text: "You are a helpful assistant" },
                  ],
                },
              ],
            },
          },
        },
      ],
      [],
    );
    const result = classifySaveChanges({
      previousSnapshot: previous,
      currentSnapshot: {
        nodes: currentPayload.nodes,
        connections: currentPayload.node_connections,
      },
    });
    expect(result.entries).toHaveLength(1);
    expect(result.entries[0].status).toBe(CHANGE_STATUS.UNCHANGED);
  });

  it("computes line totals and per-node breakdown", () => {
    const previous = snapshot([node("a", "Prompt node", { prompt: "old" })]);
    const current = snapshot([
      node("a2", "Prompt node", { prompt: "brand new prompt text" }),
      node("b", "Research Agent"),
    ]);
    const result = classifySaveChanges({
      previousSnapshot: previous,
      currentSnapshot: current,
    });
    expect(result.totals.added).toBeGreaterThan(0);
    const research = result.perNode.find(
      (row) => row.name === "Research Agent",
    );
    expect(research.added).toBeGreaterThan(0);
    expect(research.removed).toBe(0);
  });
});

describe("pickBaselineVersion", () => {
  const versions = [
    { id: "draft-1", status: VERSION_STATUS.DRAFT, created_at: "2026-09-04" },
    { id: "active-1", status: VERSION_STATUS.ACTIVE, created_at: "2026-09-03" },
    { id: "old-1", status: VERSION_STATUS.INACTIVE, created_at: "2026-09-01" },
  ];

  it("uses the latest non-draft when the current version is a draft", () => {
    const baseline = pickBaselineVersion(versions, {
      version_id: "draft-1",
      is_draft: true,
    });
    expect(baseline.id).toBe("active-1");
  });

  it("uses the current version when it is already saved", () => {
    const baseline = pickBaselineVersion(versions, {
      version_id: "active-1",
      is_draft: false,
    });
    expect(baseline.id).toBe("active-1");
  });

  it("returns null for a first-ever draft", () => {
    const baseline = pickBaselineVersion(
      [{ id: "draft-1", status: VERSION_STATUS.DRAFT }],
      { version_id: "draft-1", is_draft: true },
    );
    expect(baseline).toBeNull();
  });
});

describe("flattenGraphVersions", () => {
  it("flattens infinite-query pages", () => {
    const flat = flattenGraphVersions({
      pages: [
        { data: { result: { versions: [{ id: "v1" }] } } },
        { data: { result: { versions: [{ id: "v2" }] } } },
      ],
    });
    expect(flat.map((v) => v.id)).toEqual(["v1", "v2"]);
  });
});

describe("definition document", () => {
  it("uses names instead of remapped ids in connections", () => {
    const doc = buildDefinitionDocument(
      snapshot(
        [node("uuid-1", "Prompt node"), node("uuid-2", "Research Agent")],
        [{ source_node_id: "uuid-1", target_node_id: "uuid-2" }],
      ),
    );
    expect(doc.connections).toEqual([
      { from: "Prompt node", to: "Research Agent" },
    ]);
    expect(definitionToJson(doc)).toContain("Prompt node");
    expect(definitionToJson(doc)).not.toContain("uuid-1");
  });
});

describe("buildAgentDefinitionFileName", () => {
  it("slugifies the agent name", () => {
    expect(buildAgentDefinitionFileName("Untitled_1")).toBe(
      "untitled-1-agent.json",
    );
  });

  it("falls back to untitled", () => {
    expect(buildAgentDefinitionFileName("")).toBe("untitled-agent.json");
  });
});

describe("countLineChanges", () => {
  it("counts added and removed lines", () => {
    const { added, removed } = countLineChanges("a\nb\n", "a\nc\n");
    expect(added).toBe(1);
    expect(removed).toBe(1);
  });
});
