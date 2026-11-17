/* eslint-disable react/prop-types */
// Where this build has no dataset access or saved sources route, the page
// does not read them, and it behaves exactly as when those reads fail
// today: the access gates stay open with manage withheld, the page is not
// held, and the Sources chip shows nothing.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, renderHook, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "src/utils/test-utils";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import useDatasetAccess from "../useDatasetAccess";
import DatasetAccessGate from "../DatasetAccessGate";
import SavedSourcesChip from "src/sections/common/AddData/SavedSourcesChip";

const api = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: api,
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));

const DATASET = "0b5b7b9e-1111-4c8f-9a8e-0e2f8d1d2c3e";
const OPEN = {
  view: true,
  download: true,
  edit: true,
  run: true,
  manage: false,
};
const notFound = () =>
  Promise.reject(
    Object.assign(new Error("Not Found"), {
      statusCode: 404,
      response: { status: 404, data: { detail: "Not found." } },
    }),
  );
const reads = (part) =>
  api.get.mock.calls
    .map(([url]) => String(url))
    .filter((url) => url.includes(`/datasets/${DATASET}/${part}/`));

const clients = [];
const newClient = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  clients.push(client);
  return client;
};
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/** What the page has: the access answer, and what the page shows. */
async function pageState() {
  const client = newClient();
  const wrapper = ({ children }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  const access = renderHook(() => useDatasetAccess(DATASET), { wrapper });
  render(
    <QueryClientProvider client={client}>
      <DatasetAccessGate datasetId={DATASET}>
        <span>Dataset page</span>
      </DatasetAccessGate>
      <SavedSourcesChip datasetId={DATASET} />
    </QueryClientProvider>,
  );
  await pause(50);
  await waitFor(() => expect(access.result.current.isLoading).toBe(false));
  return {
    can: access.result.current.can,
    view: access.result.current.view,
    page: Boolean(screen.queryByText("Dataset page")),
    denied: Boolean(screen.queryByTestId("dataset-access-denied")),
    sources: Boolean(screen.queryByTestId("import-sources-chip")),
  };
}

beforeEach(() => {
  api.get.mockImplementation(notFound);
});

afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  setLaunchOverrideForTest(null);
  vi.clearAllMocks();
});

describe("access and sources reads where this build has no route", () => {
  it("sends neither read", async () => {
    setLaunchOverrideForTest({ launch: true });
    await pageState();
    expect(reads("access")).toEqual([]);
    expect(reads("sources")).toEqual([]);
  });

  it("leaves the page as it is when those reads fail today", async () => {
    setLaunchOverrideForTest({ launch: true });
    const quiet = await pageState();
    cleanup();
    setLaunchOverrideForTest({ launch: false });
    const failing = await pageState();
    expect(reads("access").length).toBeGreaterThan(0);
    expect(reads("sources").length).toBeGreaterThan(0);
    expect(quiet).toEqual(failing);
    expect(quiet).toEqual({
      can: OPEN,
      view: null,
      page: true,
      denied: false,
      sources: false,
    });
  });
});
