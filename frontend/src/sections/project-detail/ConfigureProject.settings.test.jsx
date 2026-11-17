import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import { endpoints } from "src/utils/axios";

import ConfigureProject from "./ConfigureProject";

const h = vi.hoisted(() => ({
  post: vi.fn(),
  navigate: vi.fn(),
  enqueueSnackbar: vi.fn(),
  // One stable object: the dialog resets its form whenever projectDetail
  // changes identity, so a fresh object per render would loop.
  projectDetail: { id: "project-1", name: "FUTURE_AGI", sampling_rate: 0.5 },
}));

vi.mock("src/api/project/project-detail", async (importOriginal) => ({
  ...(await importOriginal()),
  useGetProjectDetails: () => ({ data: h.projectDetail }),
}));
vi.mock("react-router", async (importOriginal) => ({
  ...(await importOriginal()),
  useNavigate: () => h.navigate,
}));
vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: { post: h.post },
}));
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: h.enqueueSnackbar,
}));
vi.mock("src/components/iconify", () => ({
  // eslint-disable-next-line react/prop-types
  default: ({ icon }) => <span data-testid={icon} />,
}));
vi.mock("src/components/hook-form", async (importOriginal) => ({
  ...(await importOriginal()),
  RHFSlider: () => null,
}));
// The delete request itself belongs to DeleteProject. This stub confirms the
// delete the way DeleteProject does once the request succeeds: onSuccess()
// and then onClose().
vi.mock("./DeleteProject", () => ({
  // eslint-disable-next-line react/prop-types
  default: ({ open, onClose, onSuccess }) =>
    open ? (
      <button
        type="button"
        onClick={() => {
          onSuccess();
          onClose();
        }}
      >
        Confirm delete
      </button>
    ) : null,
}));

// Every cache that lists Observe projects by name: the Tracing list page
// (ObserveListView), the bulk-delete list (ProjectWrapperView) and the header
// project switcher (useProjectList), with and without a search term.
const OBSERVE_LIST_KEYS = [
  [
    "observe-projects",
    {
      search: "",
      page: 0,
      pageSize: 30,
      sortBy: "updated_at",
      sortOrder: "desc",
      apiFilters: null,
    },
  ],
  ["project-observe-list"],
  ["project-list"],
  ["project-list", "FUT"],
];
const DETAIL_KEY = ["project-detail", "project-1"];
const UNRELATED_KEY = ["unrelated-cache"];

const RENAME_WARNING =
  'Traces are matched by project name. Change the project name in your register() call to "Support bot" too, or new traces will create a new project named "FUTURE_AGI".';

let client;

beforeEach(() => {
  vi.clearAllMocks();
  h.post.mockReset().mockResolvedValue({ data: {} });
  client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: Infinity },
      mutations: { retry: false },
    },
  });
  // Cached pages with no observers, as after leaving the Tracing list for a
  // project: only an invalidation makes them refetch on the next mount.
  for (const key of [...OBSERVE_LIST_KEYS, DETAIL_KEY, UNRELATED_KEY]) {
    client.setQueryData(key, { rows: [] });
  }
});

afterEach(() => {
  client.clear();
});

const renderDialog = () => {
  const onClose = vi.fn();
  const refreshGrid = vi.fn();
  render(
    <QueryClientProvider client={client}>
      <ConfigureProject
        open
        id="project-1"
        module="observe"
        onClose={onClose}
        refreshGrid={refreshGrid}
      />
    </QueryClientProvider>,
  );
  return { onClose, refreshGrid };
};

const isInvalidated = (key) => client.getQueryState(key).isInvalidated;

describe("ConfigureProject (Observe settings)", () => {
  it("invalidates the Observe project lists once a delete from Settings succeeds", () => {
    renderDialog();

    fireEvent.click(screen.getByRole("button", { name: "delete-project" }));
    // Opening the confirm step must not count as the refresh: the project
    // still exists until the delete succeeds.
    for (const key of OBSERVE_LIST_KEYS) {
      expect(isInvalidated(key)).toBe(false);
    }

    fireEvent.click(screen.getByText("Confirm delete"));

    expect(h.navigate).toHaveBeenCalledWith("/dashboard/observe");
    for (const key of OBSERVE_LIST_KEYS) {
      expect(isInvalidated(key)).toBe(true);
    }
    expect(isInvalidated(UNRELATED_KEY)).toBe(false);
  });

  it("invalidates the Observe project lists after a rename from Settings", async () => {
    const { refreshGrid } = renderDialog();

    fireEvent.change(screen.getByPlaceholderText("Enter project name"), {
      target: { value: "Support bot" },
    });
    const update = screen.getByRole("button", { name: "update-project" });
    await waitFor(() => expect(update).toBeEnabled());
    fireEvent.click(update);

    await waitFor(() => expect(refreshGrid).toHaveBeenCalled());
    expect(h.post).toHaveBeenCalledWith(
      endpoints.project.updateProject,
      expect.objectContaining({ project_id: "project-1", name: "Support bot" }),
    );
    expect(isInvalidated(DETAIL_KEY)).toBe(true);
    for (const key of OBSERVE_LIST_KEYS) {
      expect(isInvalidated(key)).toBe(true);
    }
    expect(isInvalidated(UNRELATED_KEY)).toBe(false);
  });

  it("warns that traces are matched by name only while the name differs", async () => {
    renderDialog();
    const input = screen.getByPlaceholderText("Enter project name");
    expect(
      screen.queryByText(/Traces are matched by project name/),
    ).not.toBeInTheDocument();

    fireEvent.change(input, { target: { value: "Support bot" } });
    expect(await screen.findByText(RENAME_WARNING)).toBeInTheDocument();

    // Back to the saved name: nothing will split, so no warning.
    fireEvent.change(input, { target: { value: "FUTURE_AGI" } });
    await waitFor(() =>
      expect(
        screen.queryByText(/Traces are matched by project name/),
      ).not.toBeInTheDocument(),
    );
  });
});
