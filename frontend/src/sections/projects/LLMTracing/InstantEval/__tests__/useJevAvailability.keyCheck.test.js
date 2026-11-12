import { renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// The key-status read as useApiKeysStatus returns it: `data` stays
// undefined when the first read fails.
const keys = { data: undefined, isLoading: false, isError: false };

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  readQuery: vi.fn(),
  endpoints: { project: {}, develop: { eval: {}, apiKey: {} } },
}));
vi.mock("src/hooks/useCapabilities", () => ({
  CAPABILITY: { JEV_MODELS: "jev_models" },
  useFeatureAllowed: () => ({ allowed: true, isLoading: false }),
}));
vi.mock("src/api/model/api-keys", () => ({
  useApiKeysStatus: () => keys,
}));
vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({
    isCloud: true,
    isSuccess: true,
    isLoading: false,
  }),
}));

import { useJevAvailability } from "../useJevAvailability";

describe("useJevAvailability when the key status can't be read", () => {
  beforeEach(() => {
    keys.data = undefined;
    keys.isLoading = false;
    keys.isError = false;
  });

  // A failed read is not a missing key: the user isn't told to add a key
  // they may have saved, and Ask isn't blocked. The server refuses a
  // start without the key (JEV_KEY_MISSING) and the page links to AI
  // Providers then.
  it("doesn't say the TypeSafe key is missing, and lets Ask run", () => {
    keys.isError = true;
    const { result } = renderHook(() => useJevAvailability());
    expect(result.current.available).toBe(true);
    expect(result.current.reason).toBeNull();
    expect(result.current.settingsPath).toBeUndefined();
  });

  it("keeps the last read's answer when a later read fails", () => {
    keys.isError = true;
    keys.data = [{ provider: "typesafe", hasKey: true }];
    const { result } = renderHook(() => useJevAvailability());
    expect(result.current).toMatchObject({ available: true, reason: null });
  });

  it("still asks for the key when a read said there is none", () => {
    keys.isError = true;
    keys.data = [];
    const { result } = renderHook(() => useJevAvailability());
    expect(result.current.available).toBe(false);
    expect(result.current.reason).toMatch(/TypeSafe key/);
    expect(result.current.settingsPath).toBe(
      "/dashboard/settings/ai-providers",
    );
  });
});
