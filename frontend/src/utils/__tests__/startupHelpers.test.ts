import { afterEach, describe, expect, it, vi } from "vitest";
import { copyToClipboard } from "../copyToClipboard";
import { formatISOCustom } from "../formatISOCustom";
import { getRandomId } from "../getRandomId";
import * as utils from "../utils";

afterEach(() => vi.restoreAllMocks());

describe("standalone startup helpers", () => {
  it("preserves the shared utility exports", () => {
    expect(utils.copyToClipboard).toBe(copyToClipboard);
    expect(utils.formatISOCustom).toBe(formatISOCustom);
    expect(utils.getRandomId).toBe(getRandomId);
  });

  it("preserves random ID generation and its length limit", () => {
    const random = vi.spyOn(Math, "random").mockReturnValue(0.123456789);
    expect(getRandomId()).toBe((0.123456789).toString(36).substr(2, 9));
    expect(random).toHaveBeenCalledTimes(1);
  });

  it("preserves date conversion for strings, numbers, and Date objects", () => {
    const iso = "2026-01-15T10:30:00.000Z";
    expect(formatISOCustom(iso)).toBe(iso);
    expect(formatISOCustom(new Date(iso))).toBe(iso);
    expect(formatISOCustom(Date.parse(iso))).toBe(iso);
    expect(() => formatISOCustom("invalid")).toThrow(RangeError);
  });
});
