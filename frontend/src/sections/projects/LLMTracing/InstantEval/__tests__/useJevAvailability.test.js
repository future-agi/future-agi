import { renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const capability = { allowed: true, isLoading: false };
const keys = { data: [], isLoading: false };
const deployment = { isCloud: true, isSuccess: true, isLoading: false };

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  readQuery: vi.fn(),
  endpoints: { project: {}, develop: { eval: {}, apiKey: {} } },
}));
vi.mock("src/hooks/useCapabilities", () => ({
  CAPABILITY: { JEV_MODELS: "jev_models" },
  useFeatureAllowed: () => capability,
}));
vi.mock("src/api/model/api-keys", () => ({
  useApiKeysStatus: () => keys,
}));
vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => deployment,
}));

import { useJevAvailability } from "../useJevAvailability";

describe("useJevAvailability", () => {
  beforeEach(() => {
    capability.allowed = true;
    keys.data = [];
    deployment.isCloud = true;
  });

  it("is locked when the Cloud plan has no Jev", () => {
    capability.allowed = false;
    const { result } = renderHook(() => useJevAvailability());
    expect(result.current.available).toBe(false);
    expect(result.current.reason).toMatch(/isn't available in this workspace/);
    expect(result.current.settingsPath).toBeUndefined();
  });

  // Jev is in every self-hosted edition (Community included): only the key
  // counts there, even if the capability reads as denied.
  it("never shows the plan message when self-hosted", () => {
    deployment.isCloud = false;
    capability.allowed = false;
    const { result } = renderHook(() => useJevAvailability());
    expect(result.current.available).toBe(false);
    expect(result.current.reason).toMatch(/TypeSafe key/);
    expect(result.current.settingsPath).toBe(
      "/dashboard/settings/ai-providers",
    );

    keys.data = [{ provider: "typesafe", hasKey: true }];
    const { result: withKey } = renderHook(() => useJevAvailability());
    expect(withKey.current).toMatchObject({ available: true, reason: null });
  });

  it("asks for the organization's TypeSafe key", () => {
    keys.data = [{ provider: "openai", hasKey: true }];
    const { result } = renderHook(() => useJevAvailability());
    expect(result.current.available).toBe(false);
    expect(result.current.reason).toMatch(/TypeSafe key/);
    expect(result.current.settingsPath).toBe(
      "/dashboard/settings/ai-providers",
    );
  });

  it("is available with the capability and a saved key", () => {
    keys.data = [{ provider: "typesafe", hasKey: true }];
    const { result } = renderHook(() => useJevAvailability());
    expect(result.current).toMatchObject({ available: true, reason: null });
  });
});
