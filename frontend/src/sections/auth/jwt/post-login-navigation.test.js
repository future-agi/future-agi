import { describe, expect, it, vi } from "vitest";

import { paths } from "src/routes/paths";

import {
  isSafeAuthReturnTo,
  navigateAfterAuthSuccess,
  prepareAuthSuccessPostLoginResolution,
  resolveAuthSuccessRoute,
} from "./post-login-navigation";

const memoryStorage = (initial = {}) => {
  const values = new Map(Object.entries(initial));
  return {
    getItem: (key) => values.get(key) ?? null,
    removeItem: vi.fn((key) => values.delete(key)),
    setItem: vi.fn((key, value) => values.set(key, String(value))),
  };
};

describe("post-login navigation", () => {
  it("accepts product deep links and rejects unsafe or legacy fallback targets", () => {
    expect(isSafeAuthReturnTo("/dashboard/observe?project=1")).toBe(true);
    expect(isSafeAuthReturnTo("https://example.com/dashboard")).toBe(false);
    expect(isSafeAuthReturnTo("//example.com/dashboard")).toBe(false);
    expect(isSafeAuthReturnTo(paths.auth.jwt.login)).toBe(false);
    expect(isSafeAuthReturnTo(paths.dashboard.falconAI)).toBe(false);
    expect(isSafeAuthReturnTo(paths.dashboard.getstarted)).toBe(false);
  });

  it("routes legacy fallback return targets through the normal post-login fallback", () => {
    expect(
      resolveAuthSuccessRoute({
        returnTo: paths.dashboard.falconAI,
        fallbackPath: paths.dashboard.home,
      }),
    ).toBe(paths.dashboard.home);
    expect(
      resolveAuthSuccessRoute({
        returnTo: "/dashboard/observe?project=1",
        fallbackPath: paths.dashboard.home,
      }),
    ).toBe("/dashboard/observe?project=1");
  });

  it("removes stale initial-render state so AuthGuard resolves the destination", () => {
    const storage = memoryStorage({
      "initial-render": "done",
      redirectUrl: "/dashboard/observe?project=1",
    });

    prepareAuthSuccessPostLoginResolution({
      returnTo: "/dashboard/observe?project=1",
      storage,
    });

    expect(storage.removeItem).toHaveBeenCalledWith("initial-render");
    expect(storage.removeItem).not.toHaveBeenCalledWith("redirectUrl");
  });

  it("clears stale stored return targets when login has no safe return target", () => {
    const storage = memoryStorage({
      "initial-render": "done",
      redirectUrl: paths.dashboard.falconAI,
    });

    prepareAuthSuccessPostLoginResolution({
      returnTo: paths.dashboard.falconAI,
      storage,
    });

    expect(storage.removeItem).toHaveBeenCalledWith("initial-render");
    expect(storage.removeItem).toHaveBeenCalledWith("redirectUrl");
  });

  it("can preserve the stored return target for 2FA handoff", () => {
    const storage = memoryStorage({
      "initial-render": "done",
      redirectUrl: "/dashboard/observe?project=1",
    });

    prepareAuthSuccessPostLoginResolution({
      preserveStoredReturnTo: true,
      storage,
    });

    expect(storage.removeItem).toHaveBeenCalledWith("initial-render");
    expect(storage.removeItem).not.toHaveBeenCalledWith("redirectUrl");
  });

  it("pushes the resolved route without marking initial render done", () => {
    const storage = memoryStorage({ "initial-render": "done" });
    const router = { push: vi.fn() };

    const targetRoute = navigateAfterAuthSuccess({
      router,
      returnTo: paths.dashboard.getstarted,
      fallbackPath: paths.dashboard.home,
      storage,
    });

    expect(targetRoute).toBe(paths.dashboard.home);
    expect(router.push).toHaveBeenCalledWith(paths.dashboard.home);
    expect(storage.setItem).not.toHaveBeenCalledWith("initial-render", "done");
  });
});
