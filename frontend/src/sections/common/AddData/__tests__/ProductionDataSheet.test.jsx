import React from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  close: vi.fn(),
  navigate: vi.fn(),
  dirty: vi.fn(),
}));
vi.mock("src/utils/axios", () => ({
  default: { get: mocks.get, post: mocks.post },
  endpoints: { project: { listProjects: () => "/projects/" } },
}));
vi.mock("react-router-dom", () => ({ useNavigate: () => mocks.navigate }));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/api/develop/develop-detail", () => ({
  useDevelopDatasetList: () => ({ data: [] }),
  useGetDatasetColumns: () => ({ data: [{ name: "input" }] }),
}));
import ProductionDataSheet from "../ProductionDataSheet";
let client;
beforeEach(() => {
  vi.clearAllMocks();
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  mocks.get.mockImplementation(async (path) => ({
    data: {
      result:
        path === "/projects/"
          ? { projects: [{ id: "project", name: "Project" }] }
          : {
              metadata: {
                total_rows: mocks.post.mock.calls.some(([url]) =>
                  url.includes("add_to_"),
                )
                  ? 1
                  : 0,
              },
            },
    },
  }));
  mocks.post.mockImplementation(async (path) => ({
    data: {
      result: path.endsWith("preview_sources/")
        ? { items: [{ span_id: "span", name: "operation" }] }
        : { dataset_id: "created", status: "processing" },
    },
  }));
});
afterEach(() => {
  cleanup();
  client.clear();
});
/** @param {{ new: { name: string } } | { dataset_id: string }} [target] */
async function setup(
  target = { new: { name: "Example" } },
  source = { kind: "traces" },
) {
  render(
    <QueryClientProvider client={client}>
      <ProductionDataSheet
        initial={{ source, target }}
        onClose={mocks.close}
        setDirty={mocks.dirty}
      />
    </QueryClientProvider>,
  );
  fireEvent.click(await screen.findByLabelText("operation · span"));
}
it("treats select-all IDs as exclusions rather than importing them", async () => {
  mocks.post.mockResolvedValueOnce({
    data: {
      result: {
        items: [
          {
            span_id: "excluded",
            trace_id: "excluded-trace",
            name: "excluded operation",
          },
          { span_id: "span", trace_id: "kept-trace", name: "operation" },
        ],
      },
    },
  });
  await setup(undefined, {
    kind: "traces",
    select_all: true,
    trace_ids: ["excluded-trace"],
  });
  expect(
    screen.queryByLabelText("excluded operation · excluded"),
  ).not.toBeInTheDocument();
  expect(mocks.post).toHaveBeenCalledWith("/tracer/dataset/preview_sources/", {
    project: "project",
    source_kind: "traces",
    failures_only: false,
  });
});
it("uses scoped tracer actions and never stages prototype pending operations", async () => {
  await setup();
  fireEvent.click(
    screen.getByRole("button", { name: "Create dataset with 1 row" }),
  );
  await waitFor(() =>
    expect(mocks.navigate).toHaveBeenCalledWith(
      "/dashboard/develop/created?tab=data",
    ),
  );
  expect(mocks.post).toHaveBeenLastCalledWith(
    "/tracer/dataset/add_to_new_dataset/",
    expect.objectContaining({
      project: "project",
      span_ids: ["span"],
      new_dataset_name: "Example",
    }),
  );
  expect(mocks.post.mock.calls.some(([path]) => path.includes("imports"))).toBe(
    false,
  );
  expect(screen.queryByText("Save version")).not.toBeInTheDocument();
  expect(screen.queryByText("Keep as source")).not.toBeInTheDocument();
});
it("splits mappings between existing and new destination columns", async () => {
  await setup({ dataset_id: "existing" });
  fireEvent.click(screen.getByRole("button", { name: "Add 1 row" }));
  await waitFor(() =>
    expect(mocks.post).toHaveBeenLastCalledWith(
      "/tracer/dataset/add_to_existing_dataset/",
      expect.objectContaining({
        dataset_id: "existing",
        mapping_config: [
          { col_name: "input", span_field: "input", data_type: "json" },
        ],
        new_mapping_config: [
          { col_name: "output", span_field: "output", data_type: "json" },
        ],
      }),
    ),
  );
});
it("keeps the sheet and selection after a refused write", async () => {
  await setup();
  mocks.post.mockRejectedValueOnce(new Error("Source unavailable"));
  fireEvent.click(
    screen.getByRole("button", { name: "Create dataset with 1 row" }),
  );
  expect(await screen.findByRole("alert")).toBeVisible();
  expect(mocks.close).not.toHaveBeenCalled();
  expect(mocks.navigate).not.toHaveBeenCalled();
  expect(screen.getByLabelText("operation · span")).toBeChecked();
});
it("checks an accepted import again without submitting its rows twice", async () => {
  await setup();
  mocks.get.mockRejectedValueOnce(new Error("Status temporarily unavailable"));
  fireEvent.click(
    screen.getByRole("button", { name: "Create dataset with 1 row" }),
  );
  expect(await screen.findByRole("alert")).toHaveTextContent("Import accepted");
  expect(mocks.close).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Check import" }));
  await waitFor(() => expect(mocks.navigate).toHaveBeenCalled());
  expect(
    mocks.post.mock.calls.filter(([path]) => path.includes("add_to_")),
  ).toHaveLength(1);
});
