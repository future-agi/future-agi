import { describe, expect, it } from "vitest";

import { canStartEndToEndRun } from "./HarnessCreate";

describe("canStartEndToEndRun", () => {
  it("allows a sourced run without requiring a prior manual preflight", () => {
    expect(
      canStartEndToEndRun({
        hasSource: true,
        submitting: false,
        checking: false,
        uploadingSecretFile: false,
      }),
    ).toBe(true);
  });

  it.each([
    ["has no source", { hasSource: false }],
    ["is already submitting", { submitting: true }],
    ["is checking manually", { checking: true }],
    ["is uploading a credential", { uploadingSecretFile: true }],
  ])("blocks while the form %s", (_label, override) => {
    expect(
      canStartEndToEndRun({
        hasSource: true,
        submitting: false,
        checking: false,
        uploadingSecretFile: false,
        ...override,
      }),
    ).toBe(false);
  });
});
