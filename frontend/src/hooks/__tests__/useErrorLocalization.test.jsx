import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook } from "@testing-library/react";

import { useErrorLocalizationAvailable } from "src/hooks/useErrorLocalization";

const { deployment } = vi.hoisted(() => ({
  deployment: { mode: "oss", isCloud: false, isOSS: true, isEE: false },
}));
vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => deployment,
}));

describe("useErrorLocalizationAvailable (TH-7177)", () => {
  beforeEach(() => {
    Object.assign(deployment, {
      mode: "oss",
      isCloud: false,
      isOSS: true,
      isEE: false,
    });
  });

  it("is available on cloud", () => {
    Object.assign(deployment, { mode: "cloud", isCloud: true, isOSS: false });
    const { result } = renderHook(() => useErrorLocalizationAvailable());
    expect(result.current).toBe(true);
  });

  it("is unavailable on OSS", () => {
    const { result } = renderHook(() => useErrorLocalizationAvailable());
    expect(result.current).toBe(false);
  });

  it("is available on licensed self-hosted EE", () => {
    Object.assign(deployment, {
      mode: "ee",
      isCloud: false,
      isOSS: false,
      isEE: true,
    });
    const { result } = renderHook(() => useErrorLocalizationAvailable());
    expect(result.current).toBe(true);
  });

  it("fails closed while deployment info is still loading (no mode confirmed)", () => {
    Object.assign(deployment, {
      mode: "oss",
      isCloud: undefined,
      isLoading: true,
    });
    const { result } = renderHook(() => useErrorLocalizationAvailable());
    expect(result.current).toBeFalsy();
  });
});
