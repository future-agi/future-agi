import { describe, expect, it } from "vitest";

import { BUILD_TONES } from "../../../buildEnvironment/buildTones";
import { STATUS_META, runStateFor } from "../runs.constants";

describe("STATUS_META", () => {
  it("colours the header's mixed-result Completed green, same as a finished run", () => {
    expect(STATUS_META.completed.color).toBe(BUILD_TONES.green);
    expect(STATUS_META.completed.color).toBe(STATUS_META.finished.color);
  });

  it("keeps an all-failed run red", () => {
    expect(STATUS_META.failed.color).toBe(BUILD_TONES.red);
  });
});

describe("runStateFor", () => {
  it("shows a run whose calls are done but still scoring as Grading, not Running", () => {
    expect(runStateFor("evaluating")).toBe("grading");
    expect(STATUS_META.grading.label).toBe("Grading");
  });
});
