import { describe, it, expect } from "vitest";
import {
  walkPaths,
  expandPaths,
  resolvePath,
  sortSpansForMapping,
} from "./rowPathWalker";

const sessionDetail = {
  name: "sess",
  _internal: "skip-me",
  traces: [
    {
      input: "hi",
      spans: [
        {
          name: "root",
          span_attributes: { llm: { model_name: "gpt", output_messages: [{ message: { content: "deep" } }] } },
        },
      ],
    },
  ],
};

describe("walkPaths", () => {
  it("emits collection-aware depth-4 paths (array indices ride free)", () => {
    const { paths } = walkPaths(sessionDetail);
    expect(paths).toContain("name");                        // depth 1
    expect(paths).toContain("traces.0.input");              // traces(1).input(2)
    expect(paths).toContain("traces.0.spans.0.name");       // traces(1).spans(2).name(3)
    // soft-flattened: span_attributes stripped, llm(3).model_name(4)
    expect(paths).toContain("traces.0.spans.0.llm.model_name");
  });

  it("truncates below maxDepth and records the boundary node", () => {
    const { paths, truncated } = walkPaths(sessionDetail);
    // llm.output_messages is depth 4 (traces.spans.llm.output_messages) → its
    // object children need level 5 → offered as a path but marked truncated
    expect(paths).toContain("traces.0.spans.0.llm.output_messages");
    expect(paths).not.toContain(
      "traces.0.spans.0.llm.output_messages.0.message",
    );
    expect(truncated.has("traces.0.spans.0.llm.output_messages.0")).toBe(true);
  });

  it("skips underscore keys and NO_RECURSE_KEYS children", () => {
    const { paths } = walkPaths({
      _spansLoaded: false,
      raw_log: { huge: { nested: 1 } },
    });
    expect(paths).toContain("raw_log");
    expect(paths).not.toContain("raw_log.huge");
    expect(paths.some((p) => p.startsWith("_"))).toBe(false);
  });

  it("dedupes soft-flattened collisions with top-level winning", () => {
    const { paths } = walkPaths({
      input: "top",
      span_attributes: { input: { value: "attr" } },
    });
    expect(paths.filter((p) => p === "input")).toHaveLength(1);
    expect(paths).toContain("input.value"); // stripped from span_attributes.input.value
  });

  it("returns empty result for null root", () => {
    const { paths, truncated } = walkPaths(null);
    expect(paths).toEqual([]);
    expect(truncated.size).toBe(0);
  });
});

describe("expandPaths", () => {
  const detail = {
    traces: [
      {
        spans: [
          {
            llm: {
              output_messages: [
                { message: { content: { text: "deep", meta: { a: 1 } } } },
              ],
            },
          },
        ],
      },
    ],
  };

  it("walks 4 more named levels below the prefix, absolute paths out", () => {
    const { paths } = expandPaths(
      detail,
      "traces.0.spans.0.llm.output_messages.0",
    );
    // below prefix: message(1).content(2).text(3)
    expect(paths).toContain(
      "traces.0.spans.0.llm.output_messages.0.message.content.text",
    );
    expect(paths).toContain(
      "traces.0.spans.0.llm.output_messages.0.message.content.meta.a",
    );
  });

  it("returns empty for a prefix that resolves to nothing", () => {
    const { paths } = expandPaths(detail, "traces.9.spans");
    expect(paths).toEqual([]);
  });

  it("resolves soft-flattened prefixes (stripped span_attributes)", () => {
    const d = {
      spans: [{ span_attributes: { llm: { deep: { deeper: { x: { y: 1 } } } } } }],
    };
    // user sees "spans.0.llm.deep" (stripped); expanding it must work
    const { paths } = expandPaths(d, "spans.0.llm.deep");
    expect(paths).toContain("spans.0.llm.deep.deeper.x.y");
  });
});

describe("resolvePath", () => {
  const detail = {
    input: "top-level",
    span_attributes: { llm: { model_name: "gpt" }, input: { value: "attr" } },
    traces: [
      { spans: [{ span_attributes: { cost: 3 } }] },
      { _spansLoaded: false, spans: [] },
    ],
  };

  it("resolves plain and soft-flattened paths", () => {
    expect(resolvePath(detail, "input")).toEqual({
      status: "resolved",
      value: "top-level",
    });
    expect(resolvePath(detail, "llm.model_name")).toEqual({
      status: "resolved",
      value: "gpt",
    });
    expect(resolvePath(detail, "traces.0.spans.0.cost")).toEqual({
      status: "resolved",
      value: 3,
    });
  });

  it("resolves legacy span_attributes-prefixed mappings", () => {
    expect(resolvePath(detail, "span_attributes.llm.model_name")).toEqual({
      status: "resolved",
      value: "gpt",
    });
  });

  it("returns missing for absent paths", () => {
    expect(resolvePath(detail, "nope.nothing").status).toBe("missing");
  });

  it("returns unknown when the walk crosses an unloaded spans collection", () => {
    expect(resolvePath(detail, "traces.1.spans.0.cost").status).toBe("unknown");
  });

  it("returns missing for empty inputs", () => {
    expect(resolvePath(null, "x").status).toBe("missing");
    expect(resolvePath(detail, "").status).toBe("missing");
  });
});

describe("sortSpansForMapping", () => {
  it("orders by start_time then id, nulls last — BE resolver parity", () => {
    const spans = [
      { id: "c", start_time: "2026-01-01T00:00:02Z" },
      { id: "b", start_time: null },
      { id: "a", start_time: "2026-01-01T00:00:01Z" },
      { id: "d", start_time: "2026-01-01T00:00:01Z" },
    ];
    expect(sortSpansForMapping(spans).map((s) => s.id)).toEqual([
      "a",
      "d",
      "c",
      "b",
    ]);
  });
});
