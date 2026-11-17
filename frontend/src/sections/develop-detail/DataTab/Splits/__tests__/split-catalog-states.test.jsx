import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  cleanup,
  fireEvent,
  screen,
  waitFor,
} from "@testing-library/react";
import { render } from "src/utils/test-utils";

// Keep the real useDatasetSplits observer and transport contract. Only the
// unit-test transport is mocked; the walk must obtain a genuine scoped denial.
const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  setSplitIds: vi.fn(),
  refreshGrid: vi.fn(),
}));
vi.mock("src/utils/axios", () => ({
  default: { get: mocks.get, post: mocks.post },
  endpoints: {
    develop: {
      splits: { list: (id) => `/model-hub/datasets/${id}/splits/` },
    },
  },
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/sections/common/ux/toast", () => ({ notify: vi.fn() }));
vi.mock("src/launch/launchSwitch", async (importOriginal) => ({
  ...(await importOriginal()),
  isSurfaceShown: (id) => id === "dataset.splits",
}));
vi.mock("../../../Context/DevelopDetailContext", () => ({
  useDevelopDetailContext: () => ({ refreshGrid: mocks.refreshGrid }),
}));
vi.mock("../../DataGrid/useDataGridView", () => ({
  useDataGridView: () => ({ splitIds: [], setSplitIds: mocks.setSplitIds }),
  useDataGridViewStore: { getState: () => ({}) },
}));

const { default: SplitFilterButton } = await import("../SplitFilterButton");
const { default: ManageSplitsDialog } = await import("../ManageSplitsDialog");
const { default: AssignSplitsMenu } = await import("../AssignSplitsMenu");
const { useManageSplitsStore } = await import("../splitStores");
const { datasetSplitsQueryKey } = await import(
  "src/api/develop/dataset-splits"
);

const ENDPOINT = "/model-hub/datasets/DS/splits/";
const SPLIT = {
  id: "S-train",
  name: "train",
  color: "secondary",
  description: "",
  row_count: 1,
};
const response = (splits = [SPLIT]) => ({
  data: { result: { splits, no_split_count: 2 } },
});
const denial = (status) => ({
  response: {
    status,
    data: {
      detail: status === 403 ? "Workspace access required." : "Not found.",
    },
  },
});
const deferred = () => {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
};

let client;
let anchor;
beforeEach(() => {
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  mocks.get.mockReset();
  mocks.post
    .mockReset()
    .mockResolvedValue({ data: { result: { updated: 1 } } });
  mocks.setSplitIds.mockReset();
  mocks.refreshGrid.mockReset();
  useManageSplitsStore.setState({ open: false });
  anchor = document.createElement("button");
  document.body.appendChild(anchor);
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
    x: 0,
    y: 0,
    top: 0,
    left: 0,
    right: 100,
    bottom: 30,
    width: 100,
    height: 30,
    toJSON: () => ({}),
  });
});
afterEach(() => {
  cleanup();
  client.clear();
  anchor.remove();
  useManageSplitsStore.setState({ open: false });
  vi.restoreAllMocks();
});

const input = (surface) =>
  screen.getByPlaceholderText(
    {
      filter: "Find split…",
      manage: "New split name, e.g. edge-cases",
      assign: "Find or create a split…",
    }[surface],
  );
const ready = (surface) =>
  surface === "manage"
    ? screen.findByDisplayValue("train")
    : screen.findByText("train");
const absentReady = (surface) => {
  expect(
    surface === "manage"
      ? screen.queryByDisplayValue("train")
      : screen.queryByText("train"),
  ).toBeNull();
  expect(screen.queryByText(/No splits yet/)).toBeNull();
  expect(screen.queryByText("No split")).toBeNull();
};
const open = (surface) => {
  useManageSplitsStore.setState({ open: surface === "manage" });
  render(
    <QueryClientProvider client={client}>
      {surface === "filter" && <SplitFilterButton datasetId="DS" />}
      {surface === "manage" && <ManageSplitsDialog datasetId="DS" />}
      {surface === "assign" && (
        <AssignSplitsMenu
          datasetId="DS"
          anchorEl={anchor}
          onClose={vi.fn()}
          rowIds={["row-1"]}
          rowSplits={[[]]}
        />
      )}
    </QueryClientProvider>,
  );
  if (surface === "filter")
    fireEvent.click(screen.getByRole("button", { name: "All splits" }));
};

// Red-first: all these cases import only pre-existing components and API hooks.
// On the pre-fix head the required status/alert is absent, not an import failure.
describe.each(["filter", "manage", "assign"])(
  "%s catalog states",
  (surface) => {
    it("distinguishes a pending catalog from empty and resumes the ready UI", async () => {
      const read = deferred();
      mocks.get.mockReturnValue(read.promise);
      open(surface);
      expect(screen.getByRole("status")).toHaveTextContent("Loading splits…");
      expect(
        screen.getByRole("progressbar", { name: "Loading splits" }),
      ).toBeVisible();
      expect(input(surface)).toBeDisabled();
      absentReady(surface);
      await waitFor(() => expect(mocks.get).toHaveBeenCalledWith(ENDPOINT));
      expect(mocks.post).not.toHaveBeenCalled();
      await act(async () => read.resolve(response()));
      await ready(surface);
      expect(screen.queryByRole("status")).toBeNull();
      expect(screen.queryByRole("alert")).toBeNull();
      expect(input(surface)).toBeEnabled();
      if (surface === "filter") {
        fireEvent.click(screen.getByText("train"));
        expect(mocks.setSplitIds).toHaveBeenCalledWith([SPLIT.id]);
      } else if (surface === "assign") {
        fireEvent.click(screen.getByText("train"));
        await waitFor(() =>
          expect(mocks.post).toHaveBeenCalledWith(`${ENDPOINT}membership/`, {
            row_ids: ["row-1"],
            add: [SPLIT.id],
            remove: [],
          }),
        );
      } else {
        expect(
          screen.getByRole("button", { name: "Delete train" }),
        ).toBeEnabled();
      }
    });

    it.each([403, 404])(
      "renders a %i read failure and retries the same endpoint",
      async (status) => {
        const retry = deferred();
        mocks.get
          .mockRejectedValueOnce(denial(status))
          .mockReturnValue(retry.promise);
        open(surface);
        expect(await screen.findByRole("alert")).toHaveTextContent(
          "Couldn't load splits.",
        );
        absentReady(surface);
        expect(input(surface)).toBeDisabled();
        expect(mocks.get).toHaveBeenCalledTimes(1);
        expect(mocks.get).toHaveBeenLastCalledWith(ENDPOINT);
        fireEvent.click(screen.getByRole("button", { name: "Try again" }));
        await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(2));
        expect(mocks.get).toHaveBeenLastCalledWith(ENDPOINT);
        expect(await screen.findByRole("status")).toHaveTextContent(
          "Loading splits…",
        );
        expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
        expect(mocks.post).not.toHaveBeenCalled();
        await act(async () => retry.resolve(response()));
        await ready(surface);
        expect(screen.queryByRole("alert")).toBeNull();
        expect(input(surface)).toBeEnabled();
      },
    );

    it("does not present cached choices after a denied refetch, and recovers", async () => {
      client.setQueryData(datasetSplitsQueryKey("DS"), response());
      mocks.get.mockRejectedValue(denial(403));
      open(surface);
      await ready(surface);
      const field = input(surface);
      fireEvent.change(field, { target: { value: "fresh-split" } });
      await act(async () =>
        client.invalidateQueries({ queryKey: datasetSplitsQueryKey("DS") }),
      );
      expect(await screen.findByRole("alert")).toHaveTextContent(
        "Couldn't load splits.",
      );
      absentReady(surface);
      expect(input(surface)).toBeDisabled();
      fireEvent.keyDown(input(surface), { key: "Enter" });
      expect(mocks.post).not.toHaveBeenCalled();
      if (surface === "manage") {
        expect(
          screen.getByRole("button", { name: "Create split" }),
        ).toBeDisabled();
        expect(screen.queryByTestId("training-set-open")).toBeNull();
      }
      if (surface === "assign") {
        expect(screen.queryByText("Create split “fresh-split”")).toBeNull();
        expect(screen.queryByText(/Use lowercase letters/)).toBeNull();
      }
      const retry = deferred();
      mocks.get.mockReturnValue(retry.promise);
      fireEvent.click(screen.getByRole("button", { name: "Try again" }));
      expect(
        await screen.findByRole("button", { name: "Retrying…" }),
      ).toBeDisabled();
      expect(screen.getByRole("alert")).toHaveTextContent(
        "Couldn't load splits.",
      );
      await act(async () => retry.resolve(response()));
      await waitFor(() => expect(input(surface)).toBeEnabled());
      fireEvent.change(input(surface), { target: { value: "" } });
      await ready(surface);
      expect(screen.queryByRole("alert")).toBeNull();
    });
  },
);
