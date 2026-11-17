// The experiment Paths tab as the link serves it: a launch build with
// unreleased parts shown. Its route is absent, so the tab keeps its toolbar
// and says exactly "Not available in this release" in one calm line, with
// no failed load, no Retry and no paths request. This pins the line as it is.
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { madeRealSurfaceIds } from "src/launch/madeReal";

// Every read stays pending: the test is about which requests are sent.
const http = vi.hoisted(() => ({
  get: vi.fn(() => new Promise(() => {})),
  post: vi.fn(() => new Promise(() => {})),
}));
vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: http,
}));

import PathsView from "../PathsView";

const RUN = "run-a";

const renderPaths = () =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <MemoryRouter initialEntries={["/dashboard/develop/experiment/e1/paths"]}>
        <PathsView datasetId="d1" experimentId="e1" runIds={[RUN]} />
      </MemoryRouter>
    </QueryClientProvider>,
  );

const requestedUrls = () => http.get.mock.calls.map(([url]) => String(url));

// Let any request the page would send leave first.
const settle = () =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

afterEach(() => {
  cleanup();
  setLaunchOverrideForTest(null);
  vi.clearAllMocks();
});

describe("the Paths tab on the link", () => {
  it('says exactly "Not available in this release", with its toolbar and no paths request', async () => {
    setLaunchOverrideForTest({
      launch: true,
      experimentsBasic: true,
      madeReal: madeRealSurfaceIds(),
      showUnreleased: true,
    });
    renderPaths();
    await settle();

    const line = screen.getByTestId("paths-not-in-release");
    expect(line.textContent).toBe("Not available in this release");
    expect(line.closest("[role='alert']")).toBeNull();
    expect(screen.queryByRole("button", { name: /Retry/ })).toBeNull();
    expect(screen.getByTestId("paths-view")).toBeInTheDocument();
    expect(screen.getByTestId("paths-run")).toBeInTheDocument();
    expect(screen.getByTestId("paths-compare")).toBeInTheDocument();
    expect(requestedUrls().filter((url) => url.includes("/paths/"))).toEqual(
      [],
    );
  });
});
