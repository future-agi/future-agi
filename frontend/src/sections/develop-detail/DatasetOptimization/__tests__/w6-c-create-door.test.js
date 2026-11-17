// W6 C0 → C6: the Optimization create door. C0 hid it in prototype mode while
// POST /model-hub/dataset-optimization/ was unmocked; C6 serves the path
// (handlers/w6-runs.js), so the door is offered in both modes.
import { describe, expect, it } from "vitest";
import { optimizationCreateOffered } from "../createDoor";

describe("optimizationCreateOffered", () => {
  it("offers the door in prototype mode now that the mock world answers it", () => {
    expect(optimizationCreateOffered(true)).toBe(true);
  });
  it("offers it where the backend serves the path", () => {
    expect(optimizationCreateOffered(false)).toBe(true);
  });
});
