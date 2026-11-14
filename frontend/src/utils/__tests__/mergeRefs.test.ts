import { createRef } from "react";
import { describe, expect, it, vi } from "vitest";
import { mergeRefs } from "../mergeRefs";
import { mergeRefs as legacyMergeRefs } from "../utils";

describe("mergeRefs", () => {
  it("keeps the shared utility export compatible", () => {
    expect(legacyMergeRefs).toBe(mergeRefs);
  });

  it("sets callback and object refs and clears them on unmount", () => {
    const callback = vi.fn();
    const objectRef = createRef<HTMLSpanElement>();
    const setRef = mergeRefs(callback, objectRef, null, undefined);
    const node = document.createElement("span");

    setRef(node);
    expect(callback).toHaveBeenLastCalledWith(node);
    expect(objectRef.current).toBe(node);

    setRef(null);
    expect(callback).toHaveBeenLastCalledWith(null);
    expect(objectRef.current).toBeNull();
  });

  it("accepts an empty list of refs", () => {
    expect(() => mergeRefs()(null)).not.toThrow();
  });
});
