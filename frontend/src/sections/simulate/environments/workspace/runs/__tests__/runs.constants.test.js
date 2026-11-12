import { describe, expect, it } from "vitest";

import { STATUS_META, runStateFor } from "../runs.constants";

describe("STATUS_META", () => {
  it("gives the header's mixed-result Completed the same role as a finished run", () => {
    expect(STATUS_META.completed.role).toBe(STATUS_META.finished.role);
  });

  it("keeps an all-failed run in the danger role", () => {
    expect(STATUS_META.failed.role).toBe("danger");
  });
});

describe("runStateFor", () => {
  it("shows a run whose calls are done but still scoring as Grading, not Running", () => {
    expect(runStateFor("evaluating")).toBe("grading");
    expect(STATUS_META.grading.label).toBe("Grading");
  });
});
