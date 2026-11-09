import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";
import EvalsListView from "../EvalsListView";

const h = vi.hoisted(() => ({ post: vi.fn(), snackbar: vi.fn() }));
vi.mock("src/utils/axios", () => ({
  default: { post: h.post },
  endpoints: {
    develop: {
      eval: {
        listEvalTemplates: "/fixture-list/",
        bulkDeleteEvalTemplates: "/fixture-delete/",
        getEvalNames: "/fixture-names/",
      },
    },
  },
}));
// Keep the real list query, cache keys, bulk mutation, and HTTP payload mapping.
vi.mock("../../hooks/useEvalsList", async (importOriginal) => ({
  ...(await importOriginal()),
  useEvalsListCharts: () => ({ data: null, isLoading: false }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("notistack", () => ({ enqueueSnackbar: h.snackbar }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/sections/project/VolumeBarChart", () => ({ default: () => null }));
vi.mock("../ErrorRateSparkline", () => ({ default: () => null }));
vi.mock("../DisplayOptionsPopover", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
}));

/* eslint-disable react/prop-types */
vi.mock("src/components/data-table", async (importOriginal) => ({
  ...(await importOriginal()),
  // Match DataTable's public contract: selection keys are page-local indexes,
  // not UUIDs. Deliberately do not auto-clear them when cached rows change.
  DataTable: ({ data, rowSelection, onRowSelectionChange, onSortingChange }) => (
    <section aria-label="Evaluation rows">
      {data.map((item, index) => (
        <label key={item.id}>
          <input
            type="checkbox"
            checked={Boolean(rowSelection[index])}
            onChange={(event) =>
              onRowSelectionChange({
                ...rowSelection,
                [index]: event.target.checked,
              })
            }
          />
          {item.name}
        </label>
      ))}
      <button
        type="button"
        onClick={() => onSortingChange([{ id: "name", desc: false }])}
      >
        Sort by name
      </button>
    </section>
  ),
}));
vi.mock("src/components/filter-panel/FilterPanel", () => ({
  default: ({ open, onApply, onClose }) =>
    open ? (
      <button
        type="button"
        onClick={() => {
          onApply({ eval_type: ["llm"] });
          onClose();
        }}
      >
        Apply evaluation filter
      </button>
    ) : null,
}));
/* eslint-enable react/prop-types */

const A = {
  id: "00000000-0000-4000-8000-00000000000a",
  name: "Evaluation A",
  owner: "user",
  eval_type: "llm",
  tags: ["SAFETY"],
};
const B = {
  ...A,
  id: "00000000-0000-4000-8000-00000000000b",
  name: "Evaluation B",
};
const C = {
  ...A,
  id: "00000000-0000-4000-8000-00000000000d",
  name: "Evaluation C",
};
const SYSTEM = {
  ...A,
  id: "00000000-0000-4000-8000-00000000000c",
  name: "System evaluation",
  owner: "system",
};

function listKey({
  page = 0,
  pageSize = 25,
  search = null,
  ownerFilter = "all",
  filters = null,
  sortBy = "updated_at",
  sortOrder = "desc",
} = {}) {
  return [
    "evals",
    "list",
    page,
    pageSize,
    search,
    ownerFilter,
    filters,
    sortBy,
    sortOrder,
  ];
}

let client;
function seed(params, items) {
  client.setQueryData(listKey(params), { items, total: 50 });
}

beforeEach(() => {
  localStorage.clear();
  h.post.mockReset();
  h.snackbar.mockReset();
  client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: Infinity },
      mutations: { retry: false },
    },
  });
  client.setQueryData(["evals", "all-names"], []);
  seed({}, [A, SYSTEM]);
  seed({ page: 1 }, [B]);
  seed({ sortBy: "name", sortOrder: "asc" }, [B, A, SYSTEM]);
  seed({ filters: { eval_type: ["llm"] } }, [B, A, SYSTEM]);
  seed({ filters: { tags: ["SAFETY"] } }, [B, A, SYSTEM]);
  seed({ search: "Evaluation" }, [B, A, SYSTEM]);
  seed({ pageSize: 50 }, [B, A, SYSTEM]);
  h.post.mockImplementation(async (url, body) => {
    if (url === "/fixture-delete/") return { data: { result: {} } };
    // The real mutation invalidates the list; its refetch reuses this fixture.
    if (url === "/fixture-list/") {
      const result = client.getQueryData(
        listKey({
          page: body.page,
          pageSize: body.page_size,
          search: body.search,
          ownerFilter: body.owner_filter,
          filters: body.filters,
          sortBy: body.sort_by,
          sortOrder: body.sort_order,
        }),
      );
      if (result) return { data: { result } };
    }
    throw new Error(`Unexpected fixture request: ${url}`);
  });
});
afterEach(() => {
  cleanup();
  client.clear();
});

function mount() {
  render(
    <QueryClientProvider client={client}>
      <EvalsListView />
    </QueryClientProvider>,
  );
}

const choose = (name) => fireEvent.click(screen.getByLabelText(name));
const deletePayloads = () =>
  h.post.mock.calls
    .filter(([url]) => url === "/fixture-delete/")
    .map(([, body]) => body.template_ids);

async function confirmOpenDialog() {
  fireEvent.click(
    within(screen.getByRole("dialog")).getByRole("button", { name: "Delete" }),
  );
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
}

async function deleteIfAvailable() {
  // Clearing a selection is also safe: no destructive action is then offered.
  const button = screen.queryByRole("button", { name: "Delete" });
  if (!button) return;
  fireEvent.click(button);
  await confirmOpenDialog();
}

describe("evaluation selection identity", () => {
  it("never submits an unselected UUID after returning to a prewarmed page", async () => {
    mount();
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(screen.getByLabelText(B.name)).not.toBeChecked();
    fireEvent.click(screen.getByRole("button", { name: "Previous page" }));
    choose(A.name);
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    // Both responses are already cached; there is no loading gap to clear state.
    expect(h.post).not.toHaveBeenCalled();
    await deleteIfAvailable();
    expect(deletePayloads().flat()).not.toContain(B.id);
    expect(deletePayloads()).toEqual([]);
    expect(screen.getByLabelText(B.name)).not.toBeChecked();

    // The replacement row becomes deletable only after its own explicit click.
    choose(B.name);
    await deleteIfAvailable();
    expect(deletePayloads()).toEqual([[B.id]]);
  });

  it.each(["sort", "filter", "search", "page size", "quick tag"])(
    "keeps the selected UUID after cached %s changes",
    async (transition) => {
      mount();
      choose(A.name);
      if (transition === "sort") {
        fireEvent.click(screen.getByRole("button", { name: "Sort by name" }));
      } else if (transition === "filter") {
        fireEvent.click(screen.getByRole("button", { name: "Filter" }));
        fireEvent.click(
          screen.getByRole("button", { name: "Apply evaluation filter" }),
        );
      } else if (transition === "search") {
        fireEvent.change(screen.getByPlaceholderText("Search"), {
          target: { value: "Evaluation" },
        });
      } else if (transition === "page size") {
        fireEvent.change(screen.getByRole("combobox", { name: "Rows per page" }), {
          target: { value: "50" },
        });
      } else {
        fireEvent.click(screen.getByRole("button", { name: "Safety" }));
      }
      await waitFor(() => expect(screen.getByLabelText(B.name)).toBeInTheDocument());
      expect(h.post).not.toHaveBeenCalled();
      await deleteIfAvailable();
      expect(deletePayloads()).toEqual([[A.id]]);
    },
  );

  it("keeps checkboxes bound to UUIDs during an in-place cache reorder", async () => {
    mount();
    choose(A.name);
    act(() => seed({}, [B, SYSTEM, A]));
    await waitFor(() => expect(screen.getByLabelText(B.name)).toBeInTheDocument());
    expect(screen.getByLabelText(A.name)).toBeChecked();
    expect(screen.getByLabelText(B.name)).not.toBeChecked();
    choose(A.name);
    expect(screen.queryByRole("button", { name: "Delete" })).not.toBeInTheDocument();
    expect(deletePayloads()).toEqual([]);
  });

  it("does not retarget an open confirmation when refreshed rows replace the selection", async () => {
    mount();
    choose(A.name);
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    act(() => seed({}, [B]));
    await waitFor(() =>
      expect(screen.queryByLabelText(A.name)).not.toBeInTheDocument(),
    );
    await confirmOpenDialog();
    expect(deletePayloads()).toEqual([]);
  });

  it("keeps an open confirmation bound to the selected UUID after a cache reorder", async () => {
    mount();
    choose(A.name);
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    act(() => seed({}, [B, SYSTEM, A]));
    await waitFor(() => expect(screen.getByLabelText(B.name)).toBeInTheDocument());
    await confirmOpenDialog();
    expect(deletePayloads()).toEqual([[A.id]]);
  });

  it("deletes exactly the selected user UUIDs after a multi-row cache reorder", async () => {
    seed({}, [A, B, C, SYSTEM]);
    mount();
    choose(A.name);
    choose(C.name);
    act(() => seed({}, [B, C, SYSTEM, A]));
    await waitFor(() =>
      expect(screen.getAllByRole("checkbox")[0]).toHaveAccessibleName(B.name),
    );
    await deleteIfAvailable();
    expect(deletePayloads()).toEqual([[C.id, A.id]]);
    expect(screen.getByLabelText(A.name)).not.toBeChecked();
    expect(screen.getByLabelText(C.name)).not.toBeChecked();
    expect(screen.getByLabelText(B.name)).not.toBeChecked();
    expect(screen.getByLabelText(SYSTEM.name)).not.toBeChecked();
  });

  it("deletes the explicitly selected same-page evaluation and clears selection", async () => {
    mount();
    choose(A.name);
    await deleteIfAvailable();
    expect(deletePayloads()).toEqual([[A.id]]);
    expect(screen.getByLabelText(A.name)).not.toBeChecked();
  });

  it("cancels confirmation without deleting or losing the explicit selection", async () => {
    mount();
    choose(A.name);
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel" }),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(deletePayloads()).toEqual([]);
    expect(screen.getByLabelText(A.name)).toBeChecked();
  });

  it("clears bulk selection without deleting", () => {
    mount();
    choose(A.name);
    fireEvent.click(screen.getByText("Cancel"));
    expect(screen.getByLabelText(A.name)).not.toBeChecked();
    expect(screen.queryByRole("button", { name: "Delete" })).not.toBeInTheDocument();
    expect(deletePayloads()).toEqual([]);
  });

  it("excludes system evaluations from mixed-selection payloads", async () => {
    mount();
    choose(A.name);
    choose(SYSTEM.name);
    await deleteIfAvailable();
    expect(deletePayloads()).toEqual([[A.id]]);
    expect(h.snackbar).toHaveBeenCalledWith(
      expect.stringContaining("skipped"),
      { variant: "warning" },
    );
  });

  it("does not send a delete request for a system-only selection", async () => {
    mount();
    choose(SYSTEM.name);
    await deleteIfAvailable();
    expect(deletePayloads()).toEqual([]);
    expect(h.snackbar).toHaveBeenCalledWith(
      "System evals cannot be deleted.",
      { variant: "warning" },
    );
    expect(screen.getByLabelText(SYSTEM.name)).not.toBeChecked();
  });
});
