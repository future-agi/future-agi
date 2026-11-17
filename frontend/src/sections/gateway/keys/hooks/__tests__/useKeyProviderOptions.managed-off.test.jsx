import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";

import { useKeyProviderOptions } from "../useKeyProviderOptions";

const state = vi.hoisted(() => ({
  enabled: false,
  available: true,
  catalog: vi.fn(),
}));
vi.mock("../../../providers/hooks/useGatewayConfig", () => ({
  useGatewayConfig: () => ({ data: { providers: { own: {} } } }),
}));
vi.mock("../../../providers/hooks/useOrgConfig", () => ({
  useOrgConfig: () => ({ data: null }),
}));
vi.mock("../../../models/hooks/useManagedModels", () => ({
  useManagedModelsStatus: () => ({
    data: { available: state.available, enabled: state.enabled },
  }),
  useManagedCatalog: (options) => {
    state.catalog(options);
    return { data: { models: [{ provider: "managed-provider" }] } };
  },
}));

beforeEach(() => {
  state.available = true;
  state.enabled = false;
  state.catalog.mockClear();
});

describe("F3 key provider organization enablement", () => {
  it.each([false, undefined])(
    "keeps own providers but not managed offers when enabled is %s, even with a cached catalog",
    (enabled) => {
      state.enabled = enabled;
      const { result } = renderHook(() => useKeyProviderOptions("default"));
      expect(result.current.options).toEqual(["own"]);
      expect(state.catalog).toHaveBeenLastCalledWith({ enabled: false });
    },
  );

  it("offers managed providers when the organization is enabled", () => {
    state.enabled = true;
    const { result } = renderHook(() => useKeyProviderOptions("default"));
    expect(result.current.options).toEqual(["own", "managed-provider"]);
    expect(result.current.labelFor("managed-provider")).toBe(
      "Future AGI credits (managed-provider)",
    );
    expect(state.catalog).toHaveBeenLastCalledWith({ enabled: true });
  });
});
