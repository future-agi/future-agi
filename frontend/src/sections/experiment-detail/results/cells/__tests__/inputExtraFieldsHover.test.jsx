// The Input cell's `+N fields` shows, on hover, every field the cell does
// not show as `name: value`, each value cut to one short line, in every
// row density.
/* eslint-disable react/prop-types */
import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import { InputCell } from "../ExampleCells";
import {
  extraFieldCount,
  extraFieldNames,
  extraFieldsPreview,
  oneLineText,
} from "../cellCommon";

vi.mock("src/components/iconify", () => ({ default: () => null }));

const LONG = "Tokyo became the capital in 1868 and ".repeat(5).trim();
const INPUTS = { question: "Why Tokyo?", context: LONG, source: "wiki" };

async function hoverText(label) {
  fireEvent.mouseOver(screen.getByText(label));
  return (await screen.findByRole("tooltip")).textContent;
}

describe("Input cell `+N fields` hover", () => {
  it("lists the other fields as name: value, cut short", async () => {
    render(
      <InputCell
        data={{ example_id: "e1", inputs: INPUTS }}
        context={{ inputPreviewField: "question" }}
      />,
    );
    expect(screen.getByText("Why Tokyo?")).toBeInTheDocument();
    const text = await hoverText("+2 fields");
    expect(text).toContain(`context: ${LONG.slice(0, 79)}…`);
    expect(text).not.toContain(LONG);
    expect(text).toContain("source: wiki");
    expect(text).not.toContain("question:");
  });

  it("shows values, not only names, on compact rows", async () => {
    render(
      <InputCell
        data={{ example_id: "e2", inputs: INPUTS }}
        context={{ density: "compact", inputPreviewField: "question" }}
      />,
    );
    const text = await hoverText("+2 fields");
    expect(text).toContain("source: wiki");
    expect(text).toContain(`context: ${LONG.slice(0, 79)}…`);
    expect(text).not.toContain("Also:");
  });

  it("leaves out the field the cell shows, not the first one", async () => {
    render(
      <InputCell
        data={{ example_id: "e3", inputs: INPUTS }}
        context={{ density: "compact", inputPreviewField: "context" }}
      />,
    );
    const text = await hoverText("+2 fields");
    expect(text).toContain("question: Why Tokyo?");
    expect(text).toContain("source: wiki");
    expect(text).not.toContain("context:");
  });

  it("names how many more fields there are past the first eight", async () => {
    const many = Object.fromEntries(
      Array.from({ length: 11 }, (_, i) => [`field_${i}`, `value ${i}`]),
    );
    render(
      <InputCell data={{ example_id: "e4", inputs: many }} context={{}} />,
    );
    const text = await hoverText("+10 fields");
    expect(text).toContain("field_1: value 1");
    expect(text).toContain("field_8: value 8");
    expect(text).not.toContain("field_9:");
    expect(text).toContain("+2 more");
  });
});

describe("the `+N fields` hover lines", () => {
  it("cut a value to one line of at most the given length", () => {
    expect(oneLineText("a\n\n  b")).toBe("a b");
    expect(oneLineText("x".repeat(100), 10)).toBe(`${"x".repeat(9)}…`);
    expect(oneLineText({ k: [1, 2] })).toBe('{"k":[1,2]}');
    expect(oneLineText(0)).toBe("0");
  });

  it("list the same fields the `+N` counts", () => {
    expect(extraFieldNames(INPUTS)).toEqual(["context", "source"]);
    expect(extraFieldNames(INPUTS, "context")).toEqual(["question", "source"]);
    const noText = { a: { x: 1 }, b: [1], c: "" };
    expect(extraFieldNames(noText)).toEqual(["b"]);
    expect(extraFieldNames(noText)).toHaveLength(extraFieldCount(noText));
    expect(extraFieldsPreview(INPUTS, "question")).toEqual({
      fields: [
        { name: "context", text: `${LONG.slice(0, 79)}…` },
        { name: "source", text: "wiki" },
      ],
      more: 0,
    });
  });
});
