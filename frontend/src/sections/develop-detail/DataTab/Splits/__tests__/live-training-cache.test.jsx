import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  fireEvent,
  renderHook,
  screen,
  waitFor,
} from "@testing-library/react";
import { render } from "src/utils/test-utils";

const mocks = vi.hoisted(() => ({ fetchRows: vi.fn(), post: vi.fn() }));
vi.mock("src/utils/axios", () => ({
  default: { post: mocks.post },
  endpoints: {
    develop: { splits: { list: (id) => `/datasets/${id}/splits/` } },
  },
}));
vi.mock("src/sections/common/export/datasetExport", async (importOriginal) => ({
  ...(await importOriginal()),
  fetchDatasetRows: mocks.fetchRows,
}));
vi.mock("src/sections/common/ux/toast", () => ({ notify: vi.fn() }));

const { TrainingSetButton } = await import("../TrainingSetDialog");
const { useAssignSplits } = await import("src/api/develop/dataset-splits");
const key = ["training-set", "DS", "latest", "train-id"];
const columns = [
  {
    id: "input",
    name: "input",
    role: "input",
    data_type: "text",
    origin_type: "OTHERS",
  },
  {
    id: "answer",
    name: "answer",
    role: "expected",
    data_type: "text",
    origin_type: "OTHERS",
  },
];
const snapshot = (id) => ({
  columns,
  rows: id
    ? [
        {
          row_id: id,
          input: { cell_value: id },
          answer: { cell_value: "answer" },
        },
      ]
    : [],
  datasetName: "current rows",
});
let client;
beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  mocks.fetchRows.mockReset();
  mocks.post
    .mockReset()
    .mockResolvedValue({ data: { result: { updated: 1 } } });
});
afterEach(() => client.clear());

describe("live training export cache", () => {
  it("refetches a freshly cached snapshot before a reopened dialog can download", async () => {
    client.setQueryData(key, snapshot("removed-row"));
    let finish;
    mocks.fetchRows.mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    render(
      <QueryClientProvider client={client}>
        <TrainingSetButton
          datasetId="DS"
          splits={[{ id: "train-id", name: "train" }]}
        />
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByTestId("training-set-open"));
    await waitFor(() => expect(mocks.fetchRows).toHaveBeenCalledTimes(1));
    expect(screen.getByTestId("training-set-download")).toBeDisabled();
    await act(async () => finish(snapshot(null)));
    await screen.findByTestId("training-set-train-empty");
    expect(screen.getByTestId("training-set-download")).toBeDisabled();
  });

  it("invalidates this dataset's training snapshots after membership saves", async () => {
    const other = ["training-set", "other", "latest", "train-id"];
    client.setQueryData(key, snapshot("removed-row"));
    client.setQueryData(other, snapshot("other-row"));
    const wrapper = ({ children }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    const { result } = renderHook(() => useAssignSplits("DS"), { wrapper });
    await act(async () =>
      result.current.mutateAsync({
        row_ids: ["removed-row"],
        add: [],
        remove: ["train-id"],
      }),
    );
    expect(mocks.post).toHaveBeenCalledWith("/datasets/DS/splits/membership/", {
      row_ids: ["removed-row"],
      add: [],
      remove: ["train-id"],
    });
    expect(client.getQueryState(key).isInvalidated).toBe(true);
    expect(client.getQueryState(other).isInvalidated).toBe(false);
  });
});
