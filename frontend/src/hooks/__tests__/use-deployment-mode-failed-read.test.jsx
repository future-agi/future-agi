/* eslint-disable react/prop-types */
import React, { useEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

const mocks = vi.hoisted(() => ({ get: vi.fn() }));

// Mock only the axios default instance; keep the real `endpoints`.
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { get: mocks.get } };
});

const { useDeploymentMode } = await import("../useDeploymentMode");
const { default: OssSetupGuard } = await import(
  "src/auth/guard/oss-setup-guard"
);

// A page under the guard reads the deployment mode too, as the app's
// layouts and pages do. Each time the guard swaps it for the splash and
// back, it mounts again.
let pageMounts = 0;
function Page() {
  const { mode, isConfirmed } = useDeploymentMode();
  useEffect(() => {
    pageMounts += 1;
  }, []);
  return (
    <p data-testid="page">
      {mode} {isConfirmed ? "confirmed" : "unconfirmed"}
    </p>
  );
}

function renderApp() {
  const client = new QueryClient();
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <OssSetupGuard>
          <Page />
        </OssSetupGuard>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const settle = (ms) =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });

beforeEach(() => {
  vi.useFakeTimers();
  pageMounts = 0;
  mocks.get.mockReset();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("The deployment mode when its read fails", () => {
  it("asks once and retries once, then shows the app without asking again", async () => {
    mocks.get.mockRejectedValue({ statusCode: 503 });
    renderApp();
    await settle(30000);

    // One try and its one retry, not a new try on every mount.
    expect(mocks.get).toHaveBeenCalledTimes(2);
    // The page mounted once and stays: the splash did not come back.
    expect(screen.getByTestId("page")).toHaveTextContent("oss unconfirmed");
    expect(pageMounts).toBe(1);
  });

  it("still reads the mode once when the answer arrives", async () => {
    mocks.get.mockResolvedValue({ data: { result: { mode: "cloud" } } });
    renderApp();
    await settle(5000);

    expect(mocks.get).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("page")).toHaveTextContent("cloud confirmed");
    expect(pageMounts).toBe(1);
  });
});
