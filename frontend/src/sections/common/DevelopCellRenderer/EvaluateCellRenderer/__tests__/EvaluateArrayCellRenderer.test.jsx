import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render } from "@testing-library/react";
import EvaluateArrayCellRenderer from "../EvaluateArrayCellRenderer";
import EvaluateCellRendererWrapper from "../../CellRenderers/EvaluateCellRendererWrapper";

vi.mock("../../RenderMeta", () => ({ default: () => null }));
vi.mock("src/components/tooltip", () => ({
  default: ({ children }) => children,
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/sections/develop-detail/states", () => ({
  useCompositeEvalStore: () => vi.fn(),
}));
vi.mock("src/sections/develop-detail/DataTab/common", () => ({
  normalizeEvalResult: vi.fn(),
}));
afterEach(cleanup);

const labels = (props) => {
  const { container } = render(<EvaluateArrayCellRenderer {...props} />);
  return [...container.querySelectorAll(".MuiChip-label")].map(
    (node) => node.textContent,
  );
};

describe("producer blank semantics and Unicode scalar safety", () => {
  it.each([
    ["\ufeff", ["\ufeff"]],
    [String.raw`{'choice':'\ufeff','score':0.4}`, ["\ufeff"]],
    [String.raw`{'choices':['west','\ufeff'],'score':0.3}`, ["west", "\ufeff"]],
    [String.raw`["\ud83d\ude00"]`, ["😀"]],
    [String.raw`['\U0001f600']`, ["😀"]],
    ["😀", ["😀"]],
    [String.raw`['\\ud800']`, [String.raw`\ud800`]],
    ["\ufeff[west]", ["\ufeff[west]"]],
  ])("preserves the exact valid label: %s", (value, expected) => {
    expect(labels({ value })).toEqual(expected);
  });

  it.each([
    String.raw`["\ud800"]`,
    String.raw`["\udc00"]`,
    String.raw`['\ud800']`,
    String.raw`['\udc00']`,
    String.raw`['\ud83d\ude00']`,
    "\ud800",
    "\udc00",
    String.raw`['west','\x85']`,
    String.raw`['west','\x1c']`,
    String.raw`['west','\x1f']`,
  ])("rejects malformed surrogate/C1-blank labels: %s", (value) => {
    expect(labels({ value })).toEqual([]);
  });

  it("retains FEFF multi-choice through real parent metadata plumbing", () => {
    const result = ["west", "\ufeff"];
    const { container } = render(
      <EvaluateCellRendererWrapper
        value={String.raw`{'choices':['west','\ufeff'],'score':0.3}`}
        dataType="array"
        originType="evaluation"
        formattedValueReason={() => ""}
        cellData={{ value_infos: { output: "choices", data: { result } } }}
      />,
    );
    expect(
      [...container.querySelectorAll(".MuiChip-label")].map(
        (n) => n.textContent,
      ),
    ).toEqual(result);
  });

  it("rejects malformed Unicode even when metadata matches it exactly", () => {
    const value = "\ud800";
    expect(
      labels({
        value,
        valueInfos: { output: "choices", data: { result: value } },
      }),
    ).toEqual([]);
    cleanup();
    expect(labels({ value: ["west", value] })).toEqual([]);
  });
});

describe("actual evaluation choice cells", () => {
  it.each([
    ["catalog-west", ["catalog-west"]],
    ["['catalog-west', 'catalog-east']", ["catalog-west", "catalog-east"]],
    ["{'score': 0.2, 'choice': 'catalog-west'}", ["catalog-west"]],
    [
      "{'score': 0.5, 'choices': ['catalog-west', 'catalog-east']}",
      ["catalog-west", "catalog-east"],
    ],
    ['["catalog-west","catalog-east"]', ["catalog-west", "catalog-east"]],
    ['{"score":0.2,"choice":"catalog-west"}', ["catalog-west"]],
    [
      '{"score":0.5,"choices":["catalog-west","catalog-east"]}',
      ["catalog-west", "catalog-east"],
    ],
    ['"catalog-west"', ['"catalog-west"']],
    [`{'score': 0.4, 'choice': "O'Reilly"}`, ["O'Reilly"]],
    [JSON.stringify({ choice: "O'Reilly", score: 0.4 }), ["O'Reilly"]],
    [
      String.raw`{'choice': 'O\'Reilly says "雪" \\ path', 'score': 0.4}`,
      ['O\'Reilly says "雪" \\ path'],
    ],
    [
      ["west", "east"],
      ["west", "east"],
    ],
  ])("renders labels, never scores: %s", (value, expected) => {
    expect(labels({ value })).toEqual(expected);
  });

  it.each([
    "['unterminated]",
    "[globalThis.compromised = true]",
    "['west'] * 1000000000",
    "['west', 2]",
    "{'score': 0.2}",
    "{'choice': {'nested': 'west'}}",
    "{'choice': 'west', 'choices': ['east']}",
    "{'choice': 'west', 'unknown': 'east'}",
    '{"choice":"west","score":null}',
    "[".repeat(20) + "'west'" + "]".repeat(20),
    JSON.stringify(Array(257).fill("west")),
    "w".repeat(16385),
    '{"choice":"west","choice":"east"}',
    "{'choice': 'west', 'choice': 'east'}",
    "['we' 'st']",
    "{'choice': ['west']}",
    "{'choice': 'west', 'score': True}",
    "[b'west']",
    "[('west',)]",
    "[{'west'}]",
    "[x for x in []]",
    "['west' # comment\n]",
    '{"choice":"west","score":1e999}',
    "{'choice': 'west', **{}}",
    '{"choice":"west","__proto__":{"polluted":true}}',
  ])(
    "rejects malformed/bounded/ambiguous cells without invented labels: %s",
    (value) => {
      expect(labels({ value })).toEqual([]);
      expect(globalThis.compromised).toBeUndefined();
    },
  );

  it.each([undefined, null, "", "   "])(
    "does not invent an empty result from metadata: %s",
    (value) => {
      expect(
        labels({
          value,
          valueInfos: { output: "choices", data: { result: "metadata-only" } },
        }),
      ).toEqual([]);
    },
  );

  it.each([
    { output: "choices", data: { result: "wrong" } },
    { output: "choices", data: { result: ["wrong"] } },
    { output: "choices", data: { result: { choice: "wrong", score: 0.1 } } },
    { output: "score", data: { result: 0.2 } },
  ])("disagreeing metadata cannot replace the stored labels", (valueInfos) => {
    expect(
      labels({ value: "{'choice': 'west', 'score': 0.2}", valueInfos }),
    ).toEqual(["west"]);
  });

  it.each(["[west]", '{"choice":"this is a literal label"}', "O'Reilly"])(
    "uses exact structured evidence for a scalar label: %s",
    (value) => {
      expect(
        labels({
          value,
          valueInfos: { output: "choices", data: { result: value } },
        }),
      ).toEqual([value]);
    },
  );

  it("ignores timing metadata and does not use metadata to repair an unreadable cell", () => {
    expect(
      labels({
        value: "['broken",
        valueInfos: { output: "choices", data: { result: "west" } },
        meta: { data: { result: "west" } },
      }),
    ).toEqual([]);
  });

  it("uses matching structured multi-choice metadata without reordering or coercion", () => {
    const result = ["O'Reilly", "雪"];
    expect(
      labels({
        value: `{'choices': ["O'Reilly", '雪'], 'score': 0.3}`,
        valueInfos: { output: "choices", data: { result } },
      }),
    ).toEqual(result);
  });

  it("enforces bounds even on verified structured metadata", () => {
    const value = "w".repeat(16385);
    expect(
      labels({
        value,
        valueInfos: { output: "choices", data: { result: value } },
      }),
    ).toEqual([]);
  });
});

describe("actual parent metadata wiring", () => {
  it.each(["value_infos", "valueInfos"])(
    "forwards %s from the real wrapper through EvaluateCell",
    (field) => {
      const value = "[west]";
      const { container, rerender } = render(
        <EvaluateCellRendererWrapper
          value={value}
          dataType="array"
          originType="evaluation"
          formattedValueReason={() => ""}
          cellData={{
            [field]: JSON.stringify({
              output: "choices",
              data: { result: value },
            }),
          }}
        />,
      );
      expect(
        [...container.querySelectorAll(".MuiChip-label")].map(
          (n) => n.textContent,
        ),
      ).toEqual([value]);
      // Real parent memo plumbing must update when cell metadata changes.
      rerender(
        <EvaluateCellRendererWrapper
          value={value}
          dataType="array"
          originType="evaluation"
          formattedValueReason={() => ""}
          cellData={{
            [field]: { output: "choices", data: { result: "wrong" } },
          }}
        />,
      );
      expect(container.querySelectorAll(".MuiChip-label")).toHaveLength(0);
    },
  );
});

describe("exact parser parity fixtures", () => {
  it("matches independent special-label expectations used by the offline parity checker", () => {
    const cases = [
      { value: "O'Reilly", expected: ["O'Reilly"] },
      {
        value: String.raw`{'choice': 'O\'Reilly says "雪" \\ path', 'score': 0.4}`,
        expected: ['O\'Reilly says "雪" \\ path'],
      },
      {
        value: String.raw`['a\x00b', 'a\u96eab', 'a\U0001f600b', 'a\tb', 'a\nb']`,
        expected: ["a\x00b", "a雪b", "a😀b", "a\tb", "a\nb"],
      },
      {
        value: String.raw`{"choices":["雪","a\\b","a\"b","O'Reilly"],"score":0.5}`,
        expected: ["雪", "a\\b", 'a"b', "O'Reilly"],
      },
      { value: '"quoted literal"', expected: ['"quoted literal"'] },
      { value: '[" west ","east"]', expected: [" west ", "east"] },
      { value: "[west]", literal: true, expected: ["[west]"] },
      {
        value: '{"choice":"literal"}',
        literal: true,
        expected: ['{"choice":"literal"}'],
      },
      { value: "[west]", invalid: true },
      { value: "{'choice':['west']}", invalid: true },
      { value: '{"choice":"west","choice":"east"}', invalid: true },
      { value: "{'choice':'west','choice':'east'}", invalid: true },
      { value: "['we' 'st']", invalid: true },
      { value: '["west",2]', invalid: true },
      { value: '{"choice":"west","score":NaN}', invalid: true },
      {
        value: '{"choice":"west","score":' + "9".repeat(1000) + "}",
        invalid: true,
      },
      { value: "😀".repeat(8193), expected: ["😀".repeat(8193)] },
      { value: "w".repeat(16385), invalid: true },
    ];
    cases.forEach((item, index) => {
      const expected = item.invalid ? [] : item.expected;
      const valueInfos = item.literal
        ? { output: "choices", data: { result: item.value } }
        : undefined;
      expect(
        labels({ value: item.value, valueInfos }),
        `UI parity case ${index}`,
      ).toEqual(expected);
      cleanup();
    });
  });
});
