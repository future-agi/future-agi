import { describe, expect, it } from "vitest";
import { rowKindOf } from "../itemModel";

const messages = [
  { role: "user", content: "Where is my order?" },
  { role: "assistant", content: "It arrives tomorrow." },
];

// get-row-data returns column-id keyed cells inside `current`, not row.kind
// or a synthetic `cells` map. The conversation can be in any saved column.
const getRowDataResponse = (value) => ({
  next: { row_id: [] },
  current: {
    row_id: "saved-row",
    order: 7,
    "column-1": {
      cell_id: "input-cell",
      cell_value: "Order status",
      metadata: { response_time_ms: null, token_count: null, annotation: null },
      status: null,
      value_infos: null,
      feedback_info: null,
    },
    "column-2": {
      cell_id: "saved-cell",
      cell_value: value,
      metadata: { response_time_ms: null, token_count: null, annotation: null },
      status: null,
      value_infos: null,
      feedback_info: null,
    },
  },
});

describe("rowKindOf saved get-row-data cells", () => {
  it.each([
    { name: "turns JSON text", value: JSON.stringify({ turns: messages }) },
    { name: "message-array JSON text", value: JSON.stringify(messages) },
    { name: "messages JSON text", value: JSON.stringify({ messages }) },
    { name: "turns object", value: { turns: messages } },
    { name: "message array", value: messages },
  ])("infers conversation from $name without kind or session metadata", ({ value }) => {
    const response = getRowDataResponse(value);

    expect(rowKindOf(response.current, "rows")).toBe("conversation");
  });

  it("infers conversation from messages when turns is numeric", () => {
    const { current } = getRowDataResponse(
      JSON.stringify({ turns: 2, messages: [{ role: "user", content: "Hi" }] }),
    );

    expect(() => rowKindOf(current, "rows")).not.toThrow();
    expect(rowKindOf(current, "rows")).toBe("conversation");
  });

  it.each(["row", "task", "trace", "conversation"])(
    "preserves explicit row kind %s over conversation cells",
    (kind) => {
      const { current } = getRowDataResponse(JSON.stringify(messages));

      expect(rowKindOf({ ...current, kind }, "rows")).toBe(kind);
    },
  );

  it.each(["row", "task", "trace", "conversation"])(
    "preserves explicit row_meta kind %s over conversation cells",
    (kind) => {
      const { current } = getRowDataResponse(JSON.stringify(messages));

      expect(rowKindOf({ ...current, row_meta: { kind } }, "rows")).toBe(kind);
    },
  );

  it("keeps row.kind ahead of row_meta.kind and the dataset kind", () => {
    const { current } = getRowDataResponse(JSON.stringify(messages));

    expect(
      rowKindOf(
        { ...current, kind: "trace", row_meta: { kind: "conversation" } },
        "conversations",
      ),
    ).toBe("trace");
  });

  it("preserves existing dataset-kind fallbacks", () => {
    const { current } = getRowDataResponse(JSON.stringify(messages));

    expect(rowKindOf(current, "task_suite")).toBe("task");
    expect(rowKindOf({}, "conversations")).toBe("conversation");
    expect(rowKindOf(current)).toBe("conversation");
    expect(rowKindOf(null, "rows")).toBe("row");
    expect(rowKindOf({}, "rows")).toBe("row");
  });

  it.each([
    { name: "missing cell value", value: undefined },
    { name: "null cell value", value: null },
    { name: "empty text", value: "" },
    { name: "whitespace", value: "   " },
    { name: "plain text", value: "Not a conversation" },
    { name: "malformed JSON", value: "[{" },
    { name: "JSON null", value: "null" },
    { name: "JSON scalar", value: "42" },
    { name: "empty array", value: "[]" },
    { name: "plain JSON", value: '{"answer":"ordinary data"}' },
    { name: "empty turns", value: '{"turns":[]}' },
    { name: "empty messages", value: '{"messages":[]}' },
    { name: "non-array turns", value: '{"turns":"ordinary data"}' },
    { name: "roleless objects", value: '[{"content":"ordinary data"}]' },
    { name: "primitive array", value: '["ordinary data"]' },
    { name: "mixed array", value: JSON.stringify([messages[0], null]) },
    {
      name: "system-only messages",
      value: JSON.stringify([{ role: "system", content: "Be helpful." }]),
    },
  ])("keeps $name as row", ({ value }) => {
    expect(rowKindOf(getRowDataResponse(value).current, "rows")).toBe("row");
  });

  it("does not treat metadata or cell annotations as saved conversations", () => {
    const { current } = getRowDataResponse("ordinary data");
    current.row_meta = { conversation: { turns: messages } };
    current["column-2"].value_infos = { messages };
    current["column-2"].feedback_info = { messages };

    expect(rowKindOf(current, "rows")).toBe("row");
  });
});
