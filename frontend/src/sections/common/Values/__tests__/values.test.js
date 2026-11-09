import { describe, expect, it } from "vitest";
import { sniffValue, normalizeChat, messageText } from "../sniff";
import { copyAs, shellQuote } from "../copyAs";
import {
  coveragePercent,
  coverageText,
  inferColumns,
  inferPaths,
  kindsMixText,
} from "../inferSchema";
import {
  groupIssues,
  schemaProblems,
  validateRow,
  validateValue,
} from "../schemaValidate";
import { presetById, schemaFromInferred } from "../presets";

describe("sniffValue", () => {
  it("detects scalar kinds and declared coercion", () => {
    expect(sniffValue(null).kind).toBe("empty");
    expect(sniffValue("  ").kind).toBe("empty");
    expect(sniffValue(3).kind).toBe("number");
    expect(sniffValue(true).kind).toBe("boolean");
    expect(sniffValue("0.83", { declaredType: "float" })).toMatchObject({
      kind: "number",
      normalized: 0.83,
    });
    const bad = sniffValue('"0.8"', { declaredType: "float" });
    expect(bad.kind).toBe("text");
    expect(bad.meta.mismatch).toBe('Text in a number column: "0.8"');
    expect(sniffValue("true", { declaredType: "boolean" }).normalized).toBe(
      true,
    );
    expect(sniffValue("2026-10-10T08:00:00.000Z").kind).toBe("datetime");
    expect(sniffValue("1760083200000", { declaredType: "datetime" }).kind).toBe(
      "datetime",
    );
    expect(sniffValue("qa", { declaredType: "enum" }).kind).toBe("enum");
  });

  it("detects text, markdown and YAML text (not transcripts)", () => {
    expect(sniffValue("Where is my parcel?").kind).toBe("text");
    expect(sniffValue("# Title\n\n- one").kind).toBe("markdown");
    expect(sniffValue("owner: meera\nchecks:\n  - pii\n  - tone").kind).toBe(
      "yaml_text",
    );
    expect(
      sniffValue("Customer: Hi there\nAgent: Hello, how can I help?").kind,
    ).toBe("text");
    expect(sniffValue('{"a": 1}').kind).toBe("object");
  });

  it("detects media by URL and data URI", () => {
    expect(sniffValue("/mock/multimodal/screen-1.png").kind).toBe("image");
    expect(sniffValue("data:image/png;base64,AAAA").meta.dataUri).toBe(true);
    expect(sniffValue("https://x.io/call.wav").kind).toBe("audio");
    expect(sniffValue("https://x.io/clip.mp4").kind).toBe("video");
    expect(sniffValue("https://x.io/contract.pdf").meta.fileName).toBe(
      "contract.pdf",
    );
    expect(
      sniffValue('["/a.png","/b.png"]', { declaredType: "images" }),
    ).toMatchObject({
      kind: "images",
    });
    expect(
      sniffValue({ type: "image_url", image_url: { url: "/a.png" } }).kind,
    ).toBe("image");
    expect(
      sniffValue({
        type: "input_audio",
        input_audio: { data: "AA", format: "wav" },
      }).kind,
    ).toBe("audio");
  });

  it("detects chat in OpenAI, Anthropic and LangChain shapes", () => {
    const openai = [
      { role: "user", content: "hi" },
      {
        role: "assistant",
        content: null,
        tool_calls: [
          {
            id: "c1",
            type: "function",
            function: { name: "search", arguments: "{}" },
          },
        ],
      },
      { role: "tool", tool_call_id: "c1", content: "[]" },
    ];
    const s = sniffValue(JSON.stringify(openai), { declaredType: "json" });
    expect(s.kind).toBe("chat");
    expect(s.meta.turns).toBe(3);
    const anthropic = [
      {
        role: "user",
        content: [
          { type: "text", text: "see" },
          {
            type: "image",
            source: { type: "base64", media_type: "image/png", data: "AA" },
          },
        ],
      },
    ];
    const a = sniffValue(anthropic);
    expect(a.meta.normalizedFrom).toBe("Anthropic");
    expect(a.normalized[0].content[1].image_url.url).toBe(
      "data:image/png;base64,AA",
    );
    expect(messageText(a.normalized[0])).toBe("see [image]");
    const lc = normalizeChat([
      { type: "human", content: "hi" },
      { type: "ai", content: "hello" },
    ]);
    expect(lc.from).toBe("LangChain");
    expect(lc.messages[1].role).toBe("assistant");
    expect(
      sniffValue("[]", { declaredType: "json", fieldName: "conversation" })
        .kind,
    ).toBe("chat");
  });

  it("detects embeddings and plain arrays", () => {
    expect(sniffValue(Array.from({ length: 64 }, (_, i) => i / 64)).kind).toBe(
      "embedding",
    );
    expect(sniffValue([1, 2, 3]).kind).toBe("array");
    expect(sniffValue("{bad", { declaredType: "json" }).meta.invalidJson).toBe(
      true,
    );
  });
});

describe("copyAs", () => {
  const v = { answer: "ok", citations: [{ doc: "a.pdf", page: 3 }] };
  it("copies in every format", () => {
    expect(copyAs(v, "json")).toBe(JSON.stringify(v, null, 2));
    expect(copyAs(JSON.stringify(v), "yaml")).toBe(
      "answer: ok\ncitations:\n  - doc: a.pdf\n    page: 3",
    );
    expect(copyAs("plain text", "yaml")).toBe("plain text");
    expect(copyAs(v, "path", { path: "expected.citations[0].page" })).toBe(
      "expected.citations[0].page",
    );
    expect(copyAs("it's", "curl")).toBe("'it'\\''s'");
    expect(shellQuote("a")).toBe("'a'");
    expect(copyAs(null, "value")).toBe("");
  });
});

describe("inferPaths / inferColumns", () => {
  const values = [
    JSON.stringify({ customer: { tier: "enterprise" }, list: [1, 2] }),
    JSON.stringify({ customer: { tier: "pro" }, list: [3] }),
    JSON.stringify({ customer: { tier: "enterprise" } }),
    null,
  ];
  it("lists typed paths with coverage and edge indexes", () => {
    const paths = inferPaths(values, { column: "metadata" });
    const tier = paths.find((p) => p.path === "metadata.customer.tier");
    expect(tier.coverage).toEqual({ present: 3, total: 4 });
    expect(tier.kinds).toEqual({ string: 0.75, null: 0.25 });
    expect(tier.rel).toBe("customer.tier");
    const last = paths.find((p) => p.path === "metadata.list[-1]");
    expect(last.example).toBe(2);
    expect(last.min).toBe(2);
    expect(last.max).toBe(3);
    expect(paths.find((p) => p.path === "metadata.list").array_max).toBe(2);
  });
  it("infers top-level column kinds", () => {
    const cols = [{ id: "c1", name: "score", data_type: "float" }];
    const rows = [{ c1: "0.5" }, { c1: '"0.8"' }, { c1: "0.9" }, { c1: "0.7" }];
    const [entry] = inferColumns(cols, rows);
    expect(kindsMixText(entry.kinds)).toBe("number 75% · string 25%");
    expect(entry.min).toBe(0.5);
  });
  it("formats coverage", () => {
    expect(coverageText({ present: 34, total: 40 })).toBe("34 of 40");
    expect(coverageText({ present: 40, total: 40 })).toBe("all 40");
    expect(coveragePercent({ present: 34, total: 40 })).toBe("85%");
    expect(coveragePercent({ present: 40, total: 40 })).toBe("all rows");
  });
});

describe("schema validation", () => {
  const chat = presetById("chat").schema;
  const columns = [
    { name: "conversation", json: true },
    { name: "expected", json: true },
  ];
  it("validates rows against the Chat preset", () => {
    const ok = validateRow(
      {
        conversation: JSON.stringify([{ role: "user", content: "hi" }]),
        expected: "{}",
      },
      chat,
      { columns },
    );
    expect(ok.ok).toBe(true);
    const bad = validateRow(
      {
        conversation: JSON.stringify([
          { role: "system", content: "" },
          { role: "user", content: "hi" },
          { role: "bot", content: "yo" },
        ]),
        expected: '{"answer": "missing brace"',
      },
      chat,
      { columns },
    );
    expect(bad.issues.map((i) => i.message)).toEqual([
      "conversation[2].role must be one of system, user, assistant, tool",
      "expected is not valid JSON",
    ]);
  });
  it("covers type, required, minItems and additionalProperties", () => {
    const s = {
      type: "object",
      required: ["a"],
      additionalProperties: false,
      properties: { a: { type: "array", minItems: 2 } },
    };
    expect(
      validateValue({ a: [1], b: 2 }, s, [{ type: "key", key: "x" }]).map(
        (i) => i.message,
      ),
    ).toEqual(["x.a must have at least 2 items", "x.b is not allowed"]);
    expect(validateValue({}, s, [{ type: "key", key: "x" }])[0].message).toBe(
      "x.a is required",
    );
    expect(
      validateValue(3, { type: "string" }, [{ type: "key", key: "y" }])[0]
        .message,
    ).toBe("y must be text");
  });
  it("groups issues and checks schemas", () => {
    const groups = groupIssues([
      { rowId: "r9", issues: [{ path: "p", message: "m1" }] },
      { rowId: "r21", issues: [{ path: "q", message: "m2" }] },
      { rowId: "r26", issues: [{ path: "p", message: "m1" }] },
    ]);
    expect(groups.map((g) => [g.message, g.rows.length])).toEqual([
      ["m1", 2],
      ["m2", 1],
    ]);
    expect(schemaProblems({ type: "objekt" })).toEqual([
      'schema: unknown type "objekt"',
    ]);
    expect(schemaProblems(chat)).toEqual([]);
  });
  it("declares a schema from inferred columns", () => {
    const s = schemaFromInferred([
      {
        path: "task",
        kinds: { string: 1 },
        enum_values: ["qa"],
        coverage: { present: 4, total: 4 },
      },
      {
        path: "score",
        kinds: { number: 0.9, string: 0.1 },
        coverage: { present: 3, total: 4 },
      },
    ]);
    expect(s.properties.task).toEqual({ type: "string", enum: ["qa"] });
    expect(s.properties.score.type).toEqual(["number", "string"]);
    expect(s.required).toEqual(["task"]);
  });
});

describe("YAML editing (FieldEditor)", () => {
  it("parses YAML back to compact JSON and reports errors with the line", async () => {
    const { validateYamlEdit, toYamlText } = await import(
      "src/sections/develop-detail/DataTab/DatapointDrawerV2/editValue"
    );
    expect(toYamlText('{"citations":[{"page":3}]}')).toBe(
      "citations:\n  - page: 3\n",
    );
    expect(
      validateYamlEdit(
        "citations:\n  - page: 4\nusage:\n  total_tokens: 812\n",
      ),
    ).toEqual({
      ok: true,
      value: '{"citations":[{"page":4}],"usage":{"total_tokens":812}}',
      commentsDropped: false,
    });
    expect(validateYamlEdit("a: 1 # note").commentsDropped).toBe(true);
    const bad = validateYamlEdit("a: [1\nb: 2");
    expect(bad.ok).toBe(false);
    expect(bad.error).toMatch(/^Invalid YAML: .* \(line 2\)$/);
  });
});
