import React from "react";
import { act, render, renderHook, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { usePostLoginPath } from "src/hooks/useDeploymentMode";

import PostLoginLanding from "../PostLoginLanding";
import {
  CONSOLE_CHAT_PATH,
  OMEGA_LANDING_PATH,
  defaultLandingPath,
  landingTarget,
} from "../post-login-landing";
import { OMEGA_PRODUCT, PRODUCT_STATUS } from "../rollout";
import { rolloutQueryClient } from "./rollout-fixtures";
import { ensureWebStorage } from "./test-providers";

// DEMO-FIX-2 item 8: a workspace admitted to the Omega Console lands on
// Console Chat after signing in; everyone else lands where they always did.

vi.mock("src/components/loading-screen", () => ({
  LoadingScreen: () => <div data-testid="loading" />,
  SplashScreen: () => <div data-testid="loading" />,
}));
// The capabilities request of the "still loading" case never answers.
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    default: { ...actual.default, get: vi.fn(() => new Promise(() => {})) },
  };
});

ensureWebStorage();

const withMode = (client, mode) => {
  client.setQueryData(["deployment-info"], { data: { result: { mode } } });
  return client;
};

function Where() {
  const location = useLocation();
  return <div data-testid="where">{location.pathname}</div>;
}

function land(client) {
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[OMEGA_LANDING_PATH]}>
        <Where />
        <Routes>
          <Route path={OMEGA_LANDING_PATH} element={<PostLoginLanding />} />
          <Route path="*" element={<div data-testid="landed" />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  localStorage.clear();
  vi.stubEnv("VITE_OMEGA_CONSOLE", "1");
});
afterEach(() => vi.unstubAllEnvs());

describe("where the landing route goes", () => {
  it("Console Chat only when the Console is admitted; waits while loading; otherwise Future AGI's own page", () => {
    expect(CONSOLE_CHAT_PATH).toBe("/dashboard/console/chat");
    expect(OMEGA_LANDING_PATH).toBe("/dashboard/home");
    expect(landingTarget(PRODUCT_STATUS.ADMITTED, false)).toBe(
      CONSOLE_CHAT_PATH,
    );
    expect(landingTarget(PRODUCT_STATUS.LOADING, false)).toBeNull();
    for (const status of [
      PRODUCT_STATUS.PAUSED,
      PRODUCT_STATUS.HIDDEN,
      PRODUCT_STATUS.UNAVAILABLE,
    ]) {
      expect(landingTarget(status, true)).toBe("/dashboard/get-started");
      expect(landingTarget(status, false)).toBe("/dashboard/falcon-ai");
    }
    expect(defaultLandingPath(true)).toBe("/dashboard/get-started");
  });

  it("sign-in goes to the landing route only with the Omega switch on, and a returnTo always wins", () => {
    const client = withMode(new QueryClient(), "oss");
    const wrapper = ({ children }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    expect(
      renderHook(() => usePostLoginPath(), { wrapper }).result.current,
    ).toBe("/dashboard/home");
    localStorage.setItem(
      "redirectUrl",
      "/dashboard/deployments/dep-0123456789abcdef",
    );
    expect(
      renderHook(() => usePostLoginPath(), { wrapper }).result.current,
    ).toBe("/dashboard/deployments/dep-0123456789abcdef");
    localStorage.removeItem("redirectUrl");
    vi.stubEnv("VITE_OMEGA_CONSOLE", "");
    expect(
      renderHook(() => usePostLoginPath(), { wrapper }).result.current,
    ).toBe("/dashboard/get-started");
  });
});

describe("the landing route", () => {
  it("moves an admitted workspace on to Console Chat, marking the first render done before it does", async () => {
    land(withMode(rolloutQueryClient(), "oss"));
    expect(await screen.findByTestId("landed")).toBeInTheDocument();
    expect(screen.getByTestId("where")).toHaveTextContent(
      "/dashboard/console/chat",
    );
    // The Console route's own AuthGuard must not send the person back here.
    expect(localStorage.getItem("initial-render")).toBe("done");
  });

  it("sends a workspace the Console does not admit, or has paused, to Future AGI's usual page", async () => {
    const hidden = land(
      withMode(
        rolloutQueryClient({
          admitted: [OMEGA_PRODUCT.SANDBOXES, OMEGA_PRODUCT.DEPLOYMENTS],
        }),
        "ee",
      ),
    );
    await screen.findByTestId("landed");
    expect(screen.getByTestId("where")).toHaveTextContent(
      "/dashboard/falcon-ai",
    );
    hidden.unmount();
    land(
      withMode(rolloutQueryClient({ paused: [OMEGA_PRODUCT.CONSOLE] }), "oss"),
    );
    await screen.findByTestId("landed");
    expect(screen.getByTestId("where")).toHaveTextContent(
      "/dashboard/get-started",
    );
  });

  it("waits on the loading screen while the workspace's capabilities have not answered", async () => {
    const client = withMode(
      new QueryClient({ defaultOptions: { queries: { retry: false } } }),
      "oss",
    );
    land(client);
    await act(() => new Promise((resolve) => setTimeout(resolve, 30)));
    expect(screen.getByTestId("loading")).toBeInTheDocument();
    expect(screen.getByTestId("where")).toHaveTextContent("/dashboard/home");
    expect(localStorage.getItem("initial-render")).toBeNull();
  });
});
