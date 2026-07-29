import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  OSS_SETUP_SEEN_KEY,
  OSS_SETUP_TABS,
  shouldClearOssSetupHint,
} from "./constants";
import { useOssSetupModal } from "./useOssSetupModal";

const deploymentMode = vi.hoisted(() => ({
  current: {
    isLoading: true,
    isOSS: true,
    isSuccess: false,
  },
}));

vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => deploymentMode.current,
}));

const storage = new Map();
const localStorageMock = {
  clear: () => storage.clear(),
  getItem: (key) => storage.get(key) ?? null,
  setItem: (key, value) => storage.set(key, String(value)),
};

Object.defineProperty(globalThis, "localStorage", {
  configurable: true,
  value: localStorageMock,
});

describe("shouldClearOssSetupHint", () => {
  it("keeps a valid hint until its requested tab is open", () => {
    expect(shouldClearOssSetupHint(OSS_SETUP_TABS.RESET, false, null)).toBe(
      false,
    );
    expect(
      shouldClearOssSetupHint(
        OSS_SETUP_TABS.RESET,
        true,
        OSS_SETUP_TABS.CREATE,
      ),
    ).toBe(false);
    expect(
      shouldClearOssSetupHint(OSS_SETUP_TABS.RESET, true, OSS_SETUP_TABS.RESET),
    ).toBe(true);
  });

  it("clears invalid one-shot hints without waiting for the modal", () => {
    expect(shouldClearOssSetupHint("unknown", false, null)).toBe(true);
    expect(shouldClearOssSetupHint(null, false, null)).toBe(false);
  });
});

describe("useOssSetupModal", () => {
  beforeEach(() => {
    window.localStorage.clear();
    deploymentMode.current = {
      isLoading: true,
      isOSS: true,
      isSuccess: false,
    };
  });

  it("opens an explicit reset hint after delayed OSS detection even when setup was seen", async () => {
    window.localStorage.setItem(OSS_SETUP_SEEN_KEY, "1");

    const { result, rerender } = renderHook(() =>
      useOssSetupModal({ autoOpenTab: OSS_SETUP_TABS.RESET }),
    );

    expect(result.current.open).toBe(false);

    act(() => {
      deploymentMode.current = {
        isLoading: false,
        isOSS: true,
        isSuccess: true,
      };
      rerender();
    });

    await waitFor(() => expect(result.current.open).toBe(true));
    expect(result.current.activeTab).toBe(OSS_SETUP_TABS.RESET);
  });

  it("does not auto-open without a hint after setup was seen", () => {
    window.localStorage.setItem(OSS_SETUP_SEEN_KEY, "1");
    deploymentMode.current = {
      isLoading: false,
      isOSS: true,
      isSuccess: true,
    };

    const { result } = renderHook(() => useOssSetupModal());

    expect(result.current.open).toBe(false);
  });
});
