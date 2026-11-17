// W6: the experiment Paths tab read
// GET /model-hub/r4/runs/<run>/paths/, a route the backend does not have, and
// showed "Couldn't load this run's paths. Retry". A launch build now sends no
// paths request and says, in one calm line, "Not available in this release".
// The tab and its toolbar stay. Prototype and unit-test builds still read
// paths (their mock server answers that route).
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
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
const PATHS_URL = `/model-hub/r4/runs/${RUN}/paths/`;

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

describe("W6 Paths tab in a launch build", () => {
  it("shows one calm line, with no failed load, no Retry and no paths request", async () => {
    setLaunchOverrideForTest({
      launch: true,
      experimentsBasic: true,
      madeReal: madeRealSurfaceIds(),
    });
    renderPaths();
    await settle();

    const line = screen.getByTestId("paths-not-in-release");
    expect(line).toHaveTextContent(/^Not available in this release$/);
    expect(line).toBeVisible();
    expect(screen.queryByText(/Couldn.t load this run.s paths/)).toBeNull();
    expect(screen.queryByRole("button", { name: /Retry/ })).toBeNull();
    expect(screen.queryByTestId("paths-loading")).toBeNull();
    // The tab itself stays, with its toolbar.
    expect(screen.getByTestId("paths-view")).toBeInTheDocument();
    expect(screen.getByTestId("paths-run")).toBeInTheDocument();
    expect(screen.getByTestId("paths-compare")).toBeInTheDocument();
    // No request to the missing route.
    expect(requestedUrls().filter((url) => url.includes("/paths/"))).toEqual(
      [],
    );
  });
});

describe("W6 Paths tab outside a launch build", () => {
  it("still reads the run's paths (the prototype mock server has the route)", async () => {
    setLaunchOverrideForTest({
      launch: false,
      experimentsBasic: true,
      madeReal: madeRealSurfaceIds(),
    });
    renderPaths();
    await waitFor(() => expect(requestedUrls()).toContain(PATHS_URL));
    expect(screen.queryByTestId("paths-not-in-release")).toBeNull();
  });
});
