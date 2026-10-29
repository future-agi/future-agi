import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "src/utils/test-utils";

const { get, patch, enqueueSnackbar } = vi.hoisted(() => ({
  get: vi.fn(),
  patch: vi.fn(),
  enqueueSnackbar: vi.fn(),
}));

vi.mock("src/utils/axios", () => ({
  default: { get, patch },
  endpoints: {
    project: { projectObserveList: "/tracer/project/list_projects/" },
  },
}));
vi.mock("notistack", () => ({ enqueueSnackbar }));

import ObserveListView from "./ObserveListView";
import TagEditor from "./TagEditor";

const listUrl = "/tracer/project/list_projects/";
const detailUrl = "/tracer/project/p1/";
const project = (id, tags) => ({
  id,
  name: `Project ${id}`,
  tags,
  daily_volume: [],
});
const projects = [project("p1", ["existing"]), project("p2", [])];
const clients = [];

function pageResponse(rows, pageSize = 25) {
  return {
    data: {
      status: true,
      result: {
        table: rows,
        metadata: {
          total_rows: rows.length,
          total_pages: Math.ceil(rows.length / pageSize),
          page_number: 0,
          page_size: pageSize,
        },
      },
    },
  };
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function renderEditor(
  ui = <ObserveListView />,
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  }),
) {
  clients.push(client);
  return {
    ...render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>),
    client,
  };
}

const tagsCell = (container, id = "p1") =>
  container.querySelector(`[data-id="${id}"] [data-field="tags"]`);

async function openEditor(container) {
  await screen.findByText("Project p1");
  fireEvent.click(tagsCell(container).firstElementChild);
  await screen.findByPlaceholderText("Type new tag and press Enter");
}

function addTag(tag) {
  const input = screen.getByPlaceholderText("Type new tag and press Enter");
  fireEvent.change(input, { target: { value: tag } });
  fireEvent.keyDown(input, { key: "Enter" });
}

function closeEditor() {
  fireEvent.keyDown(screen.getByPlaceholderText("Search tags"), {
    key: "Escape",
  });
}

describe("project tags", () => {
  beforeEach(() => {
    get.mockReset();
    patch.mockReset();
    enqueueSnackbar.mockReset();
    get.mockImplementation((url, options) =>
      Promise.resolve(
        url === listUrl
          ? pageResponse(projects, options.params.page_size)
          : { data: { result: { tags: ["detail-only"] } } },
      ),
    );
  });

  afterEach(() => {
    cleanup();
    clients.splice(0).forEach((client) => client.clear());
  });

  it("renders populated and empty grid tags with only the project-list request", async () => {
    const { container } = renderEditor();
    await screen.findByText("Project p1");

    expect(get.mock.calls.map(([url]) => url)).toEqual([listUrl]);
    expect(tagsCell(container)).toHaveTextContent("existing");
    expect(tagsCell(container, "p2")).toHaveTextContent("");
  });

  it("uses every fresh list response even when the project-tag cache disagrees", async () => {
    const client = new QueryClient();
    client.setQueryData(["project-tags", "p1"], ["stale-cache"]);
    const { container } = renderEditor(<ObserveListView />, client);
    await screen.findByText("Project p1");
    expect(tagsCell(container)).toHaveTextContent("existing");

    for (const tags of [["fresh-list"], ["existing"]]) {
      get.mockResolvedValue(pageResponse([project("p1", tags)]));
      await act(() =>
        client.refetchQueries({ queryKey: ["observe-projects"] }),
      );
      await waitFor(() =>
        expect(tagsCell(container)).toHaveTextContent(tags[0]),
      );
    }
    expect(get.mock.calls.every(([url]) => url === listUrl)).toBe(true);
  });

  it("still fetches standalone header tags without list data", async () => {
    renderEditor(<TagEditor projectId="p1" variant="header" />);
    await screen.findByText("detail-only");
    expect(get.mock.calls.map(([url]) => url)).toEqual([detailUrl]);
  });

  it("aborts an older list read so it cannot overwrite an optimistic edit", async () => {
    const staleRead = deferred();
    patch.mockReturnValue(deferred().promise);
    const { container, client } = renderEditor();
    await screen.findByText("Project p1");
    get.mockImplementation((_url, options) =>
      options.params.page_size === 25
        ? staleRead.promise
        : Promise.resolve(pageResponse(projects, 100)),
    );
    let refreshing;
    act(() => {
      refreshing = client.refetchQueries({ queryKey: ["observe-projects"] });
    });
    const signal = get.mock.calls.at(-1)[1].signal;
    expect(signal.aborted).toBe(false);

    await openEditor(container);
    addTag("new-tag");
    await waitFor(() => expect(patch).toHaveBeenCalledTimes(1));
    expect(signal.aborted).toBe(true);
    await act(async () => {
      staleRead.resolve(pageResponse([project("p1", ["stale-response"])]));
      await refreshing;
    });
    expect(tagsCell(container)).toHaveTextContent("new-tag");
    expect(tagsCell(container)).not.toHaveTextContent("stale-response");
  });

  it("keeps the saved server tags after an optimistic edit closes before the reply", async () => {
    const save = deferred();
    const refresh = deferred();
    patch.mockReturnValue(save.promise);
    const { container } = renderEditor();
    await openEditor(container);
    get.mockImplementation((_url, options) =>
      options.params.page_size === 25
        ? refresh.promise
        : Promise.resolve(pageResponse(projects, 100)),
    );

    addTag("new-tag");
    await waitFor(() =>
      expect(tagsCell(container)).toHaveTextContent("new-tag"),
    );
    expect(patch).toHaveBeenCalledWith("/tracer/project/p1/tags/", {
      tags: ["existing", "new-tag"],
    });
    closeEditor();
    await act(() =>
      save.resolve({ data: { result: { tags: ["saved-tag"] } } }),
    );
    await waitFor(() =>
      expect(tagsCell(container)).toHaveTextContent("saved-tag"),
    );
    expect(
      screen.queryByPlaceholderText("Search tags"),
    ).not.toBeInTheDocument();
    expect(get.mock.calls.every(([url]) => url === listUrl)).toBe(true);
  });

  it("rolls back only the failed project's tags, preserving other refreshed fields", async () => {
    const save = deferred();
    const refresh = deferred();
    patch.mockReturnValue(save.promise);
    const { container, client } = renderEditor();
    await openEditor(container);
    get.mockImplementation((_url, options) =>
      options.params.page_size === 25
        ? refresh.promise
        : Promise.resolve(pageResponse(projects, 100)),
    );

    addTag("new-tag");
    await waitFor(() =>
      expect(tagsCell(container)).toHaveTextContent("new-tag"),
    );
    act(() =>
      client.setQueriesData({ queryKey: ["observe-projects"] }, (page) => ({
        ...page,
        rows: page.rows.map((row) =>
          row.id === "p1"
            ? { ...row, name: "Refreshed project" }
            : { ...row, tags: ["other-project-update"] },
        ),
      })),
    );
    closeEditor();
    await act(() => save.reject(new Error("Save failed")));

    await waitFor(() =>
      expect(tagsCell(container)).not.toHaveTextContent("new-tag"),
    );
    expect(tagsCell(container)).toHaveTextContent("existing");
    expect(screen.getByText("Refreshed project")).toBeInTheDocument();
    expect(tagsCell(container, "p2")).toHaveTextContent("other-project-update");
    expect(enqueueSnackbar).toHaveBeenCalledWith("Failed to update tags", {
      variant: "error",
    });
  });

  it("removes the last tag and retains the saved empty array when closed", async () => {
    const save = deferred();
    const refresh = deferred();
    patch.mockReturnValue(save.promise);
    const { container } = renderEditor();
    await openEditor(container);
    get.mockImplementation((_url, options) =>
      options.params.page_size === 25
        ? refresh.promise
        : Promise.resolve(pageResponse(projects, 100)),
    );

    fireEvent.click(screen.getByRole("checkbox", { checked: true }));
    await waitFor(() => expect(tagsCell(container)).toHaveTextContent(""));
    expect(patch).toHaveBeenCalledWith("/tracer/project/p1/tags/", {
      tags: [],
    });
    closeEditor();
    await act(() => save.resolve({ data: { result: { tags: [] } } }));
    expect(tagsCell(container)).toHaveTextContent("");
    expect(get.mock.calls.every(([url]) => url === listUrl)).toBe(true);
  });

  it("keeps current tags editable when suggestions fail", async () => {
    const { container } = renderEditor();
    await screen.findByText("Project p1");
    get.mockRejectedValue(new Error("Suggestions failed"));
    patch.mockResolvedValue({
      data: { result: { tags: ["existing", "new-tag"] } },
    });
    await openEditor(container);
    await screen.findByRole("alert");
    expect(screen.getByRole("checkbox", { checked: true })).toBeInTheDocument();

    addTag("new-tag");
    await waitFor(() =>
      expect(tagsCell(container)).toHaveTextContent("new-tag"),
    );
    expect(get.mock.calls.every(([url]) => url === listUrl)).toBe(true);
  });
});
