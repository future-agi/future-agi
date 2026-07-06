import { describe, it, expect } from "vitest";
import {
  getEvalNonScoreStatus,
  getEvalNonScoreStatusFromValue,
  getEvalStatusLabel,
} from "../evalStatus";

// Every eval read surface now routes through this util to decide whether a cell
// shows a real score or a lifecycle marker, so pin the contract exhaustively.

describe("getEvalNonScoreStatus", () => {
  it.each([
    ["pending", "pending"],
    ["running", "running"],
    ["skipped", "skipped"],
    ["PENDING", "pending"], // case-insensitive
    ["Running", "running"],
    ["completed", null], // terminal score state — caller renders the score
    ["COMPLETED", null],
    ["errored", null], // errored is terminal, not a non-score marker
    ["", null],
    [null, null],
    [undefined, null],
  ])("maps status %o -> %o", (input, expected) => {
    expect(getEvalNonScoreStatus(input)).toBe(expected);
  });
});

describe("getEvalNonScoreStatusFromValue", () => {
  it.each([
    [{ status: "pending" }, "pending"],
    [{ status: "running" }, "running"],
    [{ status: "skipped" }, "skipped"],
    [{ eval_status: "pending" }, "pending"], // alternate producer key
    [{ status: "COMPLETED" }, null],
    [{ error: true }, null], // error marker carries no non-score status
    [0.82, null], // scalar score
    [1, null],
    ["0.82", null], // string scalar
    [["a", "b"], null], // array (CHOICES output) — never a marker
    [{}, null],
    [null, null],
    [undefined, null],
  ])("maps value %o -> %o", (input, expected) => {
    expect(getEvalNonScoreStatusFromValue(input)).toBe(expected);
  });
});

describe("getEvalStatusLabel", () => {
  it.each([
    ["pending", "Queued"],
    ["running", "Evaluating…"],
    ["skipped", "Skipped"],
    ["completed", ""], // terminal states have no lifecycle label
    ["errored", ""],
    [null, ""],
  ])("labels status %o -> %o", (input, expected) => {
    expect(getEvalStatusLabel(input)).toBe(expected);
  });
});
