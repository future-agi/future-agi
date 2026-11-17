// W5-1 A3 · the Data grid dialog's placeholder comes from the dataset's own
// examples, so it never names a column the dataset does not have.
import { describe, expect, it } from "vitest";
import {
  GRID_PLACEHOLDER,
  gridExamples,
  gridPlaceholder,
} from "../gridDoorModel";

const COLS = [
  { id: "c1", name: "question", role: "input", data_type: "text" },
  { id: "c2", name: "topic", role: "metadata", data_type: "text" },
];
const ROWS = [
  {
    row_id: "r1",
    c1: { cell_value: "How do I reset it?" },
    c2: { cell_value: "account" },
  },
  {
    row_id: "r2",
    c1: { cell_value: "Where is my parcel today?" },
    c2: { cell_value: "shipping" },
  },
  {
    row_id: "r3",
    c1: { cell_value: "Refund please" },
    c2: { cell_value: "billing" },
  },
];

describe("gridPlaceholder", () => {
  it("is the dataset's first example, over its own columns", () => {
    const examples = gridExamples(COLS, ROWS);
    const text = gridPlaceholder(examples);
    expect(text).toBe(examples[0].expr);
    expect(text).not.toMatch(/expected_reply|language/);
    expect(text).toMatch(/question|topic/);
  });

  it("falls back to plain words when the dataset gives no example", () => {
    expect(gridPlaceholder([])).toBe(GRID_PLACEHOLDER);
    expect(GRID_PLACEHOLDER).not.toMatch(/[()"=<>]/);
  });
});
