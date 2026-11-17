// @vitest-environment jsdom
import React, { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "src/utils/test-utils";
import axios, { endpoints } from "src/utils/axios";
import { CAPABILITIES_QUERY_KEY } from "src/hooks/useCapabilities";
import { setRealBackendForTests } from "../../realBackendCaps";
import { useDatasetListQuery } from "../../hooks/useDatasetListQuery";
import { useRealFolderInventory, FOLDERS_URL } from "../realFolderApi";
import DatasetStarToggle from "../DatasetStarToggle";

const h = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  del: vi.fn(),
  notify: vi.fn(),
}));
vi.mock("src/utils/axios", async (importOriginal) => {
  const original = await importOriginal();
  return {
    ...original,
    default: {
      ...original.default,
      get: h.get,
      post: h.post,
      delete: h.del,
    },
  };
});
vi.mock("src/contexts/OrganizationContext", async (importOriginal) => ({
  ...(await importOriginal()),
  useOrganization: () => ({ currentOrganizationId: "org-a" }),
}));
vi.mock("src/contexts/WorkspaceContext", async (importOriginal) => ({
  ...(await importOriginal()),
  useWorkspace: () => ({ currentWorkspaceId: "workspace-a" }),
}));
vi.mock("src/sections/common/ux/toast", async (importOriginal) => ({
  ...(await importOriginal()),
  notify: h.notify,
}));

let client;
let storedStar;
let allowed;
const capabilityAnswer = () => ({
  data: { features: { dataset_stars: { allowed } } },
});
const mount = (ui) =>
  render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
const dataset = { id: "dataset-a", name: "Golden", starred: false };
const starredDataset = { id: "dataset-b", name: "Silver", starred: true };

// A disabled MUI button takes no pointer events, so a real click lands on its
// wrapper. jsdom can still dispatch on the button itself; click both.
const clickDisabledStar = (name) => {
  const button = screen.getByRole("button", { name });
  expect(button).toBeDisabled();
  fireEvent.click(button.parentElement);
  fireEvent.click(button);
};

function ListHost() {
  const [onlyStars, setOnlyStars] = useState(false);
  const list = useDatasetListQuery({ starred: onlyStars });
  const inventory = useRealFolderInventory(true);
  return (
    <>
      <button type="button" onClick={() => setOnlyStars((only) => !only)}>
        Starred {inventory.data?.starred_count ?? 0}
      </button>
      {list.data?.items.map((row) => (
        <div key={row.id}>
          <span>{row.name}</span>
          <DatasetStarToggle dataset={row} />
        </div>
      ))}
    </>
  );
}

beforeEach(() => {
  setRealBackendForTests(true);
  storedStar = false;
  allowed = true;
  window.localStorage.clear();
  client = new QueryClient({
    defaultOptions: { queries: { retry: false, retryDelay: 0, gcTime: 0 } },
  });
  h.notify.mockReset();
  h.get.mockReset().mockImplementation(async (url, config) => {
    if (url === "/api/capabilities/") return capabilityAnswer();
    if (url === FOLDERS_URL)
      return {
        data: {
          result: {
            folders: [],
            all_count: 1,
            no_folder_count: 1,
            starred_count: Number(storedStar),
          },
        },
      };
    if (url === endpoints.develop.getDatasets()) {
      const rows = config?.params?.starred && !storedStar
        ? []
        : [{ ...dataset, starred: storedStar }];
      return { data: { result: { datasets: rows, total_count: rows.length } } };
    }
    throw new Error(`Unexpected read: ${url}`);
  });
  h.post.mockReset().mockImplementation(async () => {
    storedStar = true;
    return { data: { result: { dataset_id: dataset.id, starred: true } } };
  });
  h.del.mockReset().mockImplementation(async () => {
    storedStar = false;
    return { data: { result: { dataset_id: dataset.id, starred: false } } };
  });
});
afterEach(() => {
  cleanup();
  client.clear();
  setRealBackendForTests(null);
  vi.restoreAllMocks();
});

describe("W19 stars follow server support", () => {
  it("W19-01 disables the star on an unsupported server without a write or local pin", async () => {
    allowed = false;
    mount(<DatasetStarToggle dataset={dataset} />);
    const button = screen.getByRole("button", { name: "Star Golden" });
    expect(button).toBeDisabled();
    await waitFor(() => expect(h.get).toHaveBeenCalledWith("/api/capabilities/"));
    await waitFor(() => expect(client.getQueryState(CAPABILITIES_QUERY_KEY)?.status).toBe("success"));
    fireEvent.click(button);
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("aria-pressed", "false");
    expect(h.post).not.toHaveBeenCalled();
    expect(h.del).not.toHaveBeenCalled();
    expect(window.localStorage.length).toBe(0);
    fireEvent.mouseOver(button.parentElement);
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      "Stars are not available on this server.",
    );
  });

  it("W19-02 keeps the star disabled while support is loading or missing", async () => {
    h.get.mockImplementation(() => new Promise(() => {}));
    const mounted = mount(<DatasetStarToggle dataset={dataset} />);
    const button = screen.getByRole("button", { name: "Star Golden" });
    fireEvent.click(button);
    expect(button).toBeDisabled();
    expect(h.post).not.toHaveBeenCalled();
    mounted.unmount();
    client.clear();
    client.setQueryData(CAPABILITIES_QUERY_KEY, { data: { features: {} } });
    mount(<DatasetStarToggle dataset={dataset} />);
    expect(screen.getByRole("button", { name: "Star Golden" })).toBeDisabled();
  });

  it("W19-03 star and unstar refresh the real list and rail and survive remount", async () => {
    const mounted = mount(<ListHost />);
    const button = await screen.findByRole("button", { name: "Star Golden" });
    await waitFor(() => expect(button).toBeEnabled());
    fireEvent.click(button);
    expect(button).toHaveAttribute("aria-pressed", "true");
    await screen.findByRole("button", { name: "Starred 1" });
    expect(axios.post).toHaveBeenCalledWith(endpoints.develop.w9.datasetStar(dataset.id));
    fireEvent.click(screen.getByRole("button", { name: "Starred 1" }));
    await waitFor(() => expect(h.get).toHaveBeenCalledWith(
      endpoints.develop.getDatasets(),
      expect.objectContaining({ params: expect.objectContaining({ starred: true }) }),
    ));
    expect(await screen.findByText("Golden")).toBeVisible();
    mounted.unmount();
    client.clear();
    mount(<ListHost />);
    const restored = await screen.findByRole("button", { name: "Star Golden" });
    await waitFor(() => expect(restored).toBeEnabled());
    expect(restored).toHaveAttribute("aria-pressed", "true");
    await screen.findByRole("button", { name: "Starred 1" });
    fireEvent.click(restored);
    await screen.findByRole("button", { name: "Starred 0" });
    await waitFor(() => expect(restored).toHaveAttribute("aria-pressed", "false"));
    fireEvent.click(screen.getByRole("button", { name: "Starred 0" }));
    await waitFor(() => expect(screen.queryByText("Golden")).toBeNull());
    expect(axios.delete).toHaveBeenCalledWith(endpoints.develop.w9.datasetStar(dataset.id));
    expect(window.localStorage.length).toBe(0);
  });

  it("W19-05 keeps a failed support read distinct from a server denial", async () => {
    h.get.mockRejectedValue({ statusCode: 503, result: "Unavailable" });
    mount(<DatasetStarToggle dataset={dataset} />);
    await waitFor(() => expect(client.getQueryState(CAPABILITIES_QUERY_KEY)?.status).toBe("error"));
    const button = screen.getByRole("button", { name: "Star Golden" });
    expect(button).toBeDisabled();
    fireEvent.mouseOver(button.parentElement);
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      "Could not check star availability. Reload to try again.",
    );
    expect(h.post).not.toHaveBeenCalled();
  });

  it("W19-04 rolls back flattened server refusals without a false count", async () => {
    h.post.mockRejectedValue({ statusCode: 403, result: "You cannot star this dataset." });
    mount(<ListHost />);
    const button = await screen.findByRole("button", { name: "Star Golden" });
    await waitFor(() => expect(button).toBeEnabled());
    fireEvent.click(button);
    await waitFor(() => expect(button).toHaveAttribute("aria-pressed", "false"));
    expect(screen.getByRole("button", { name: "Starred 0" })).toBeVisible();
    expect(h.notify).toHaveBeenCalledWith("You cannot star this dataset.", {
      variant: "error",
      key: "dataset-star",
    });
  });
});

describe("W19 a disabled star click stays inside the star", () => {
  it("W19-08 clicking an unsupported star does not open its row or write", async () => {
    allowed = false;
    const onRow = vi.fn();
    mount(
      <>
        <div onClick={() => onRow(dataset.id)}>
          <DatasetStarToggle dataset={dataset} />
        </div>
        <div onClick={() => onRow(starredDataset.id)}>
          <DatasetStarToggle dataset={starredDataset} />
        </div>
      </>,
    );
    await waitFor(() => expect(client.getQueryState(CAPABILITIES_QUERY_KEY)?.status).toBe("success"));
    clickDisabledStar("Star Golden");
    clickDisabledStar("Star Silver");
    expect(onRow).not.toHaveBeenCalled();
    expect(h.post).not.toHaveBeenCalled();
    expect(h.del).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Star Golden" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByRole("button", { name: "Star Silver" })).toHaveAttribute("aria-pressed", "true");
  });

  it.each([
    {
      support: "still being checked",
      status: "pending",
      arrange: () => h.get.mockImplementation(() => new Promise(() => {})),
    },
    {
      support: "missing from the server answer",
      status: "success",
      arrange: () => client.setQueryData(CAPABILITIES_QUERY_KEY, { data: { features: {} } }),
    },
    {
      support: "unreadable",
      status: "error",
      arrange: () => h.get.mockRejectedValue({ statusCode: 503, result: "Unavailable" }),
    },
  ])("W19-09 clicking a star whose support is $support does not open its row or write", async ({ status, arrange }) => {
    arrange();
    const onRow = vi.fn();
    mount(
      <>
        <div onClick={() => onRow(dataset.id)}>
          <DatasetStarToggle dataset={dataset} />
        </div>
        <div onClick={() => onRow(starredDataset.id)}>
          <DatasetStarToggle dataset={starredDataset} />
        </div>
      </>,
    );
    await waitFor(() => expect(client.getQueryState(CAPABILITIES_QUERY_KEY)?.status).toBe(status));
    clickDisabledStar("Star Golden");
    clickDisabledStar("Star Silver");
    expect(onRow).not.toHaveBeenCalled();
    expect(h.post).not.toHaveBeenCalled();
    expect(h.del).not.toHaveBeenCalled();
  });

  it("W19-10 clicking a star while its write is in flight does not open its row or write again", async () => {
    let finishWrite;
    h.post.mockImplementation(() => new Promise((resolve) => {
      finishWrite = resolve;
    }));
    const onRow = vi.fn();
    mount(
      <div onClick={onRow}>
        <DatasetStarToggle dataset={dataset} />
      </div>,
    );
    const button = screen.getByRole("button", { name: "Star Golden" });
    await waitFor(() => expect(button).toBeEnabled());
    fireEvent.click(button);
    await waitFor(() => expect(h.post).toHaveBeenCalledTimes(1));
    clickDisabledStar("Star Golden");
    expect(onRow).not.toHaveBeenCalled();
    expect(h.post).toHaveBeenCalledTimes(1);
    expect(h.del).not.toHaveBeenCalled();
    finishWrite({ data: { result: { dataset_id: dataset.id, starred: true } } });
    await waitFor(() => expect(button).toBeEnabled());
    expect(button).toHaveAttribute("aria-pressed", "true");
    expect(onRow).not.toHaveBeenCalled();
  });
});
