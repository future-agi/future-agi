import { describe, expect, it } from "vitest";

import { BUILD_TONES } from "../../../buildEnvironment/buildTones";
import { STATUS_META } from "../runs.constants";

describe("STATUS_META", () => {
  it("colours the header's mixed-result Completed green, same as a finished run", () => {
    expect(STATUS_META.completed.color).toBe(BUILD_TONES.green);
    expect(STATUS_META.completed.color).toBe(STATUS_META.finished.color);
  });

  it("keeps an all-failed run red", () => {
    expect(STATUS_META.failed.color).toBe(BUILD_TONES.red);
  });
});
