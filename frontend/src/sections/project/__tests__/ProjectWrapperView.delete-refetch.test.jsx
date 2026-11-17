import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  renderWithRouter,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";
import ProjectWrapperView from "../ProjectWrapperView";

const { getProjects, deleteProjects } = vi.hoisted(() => ({
  getProjects: vi.fn(),
  deleteProjects: vi.fn(),
}));

vi.mock("src/utils/axios", () => ({
  default: { get: getProjects, delete: deleteProjects },
  endpoints: {
    project: {
      projectExperimentList: "/projects",
      deleteObservePrototype: "/projects/delete",
    },
  },
}));
vi.mock("notistack", () => ({
  useSnackbar: () => ({ enqueueSnackbar: vi.fn() }),
}));
vi.mock("src/utils/Mixpanel", () => ({
  Events: {},
  PropertyName: {},
  trackEvent: vi.fn(),
}));
vi.mock("src/sections/projects/UsersView/common", () => ({
  formatNumberWithCommas: String,
}));

/* eslint-disable react/prop-types */
// Keep the wrapper, child list, shared selection adapter, query cache and
// ProjectFtux real. Only visual grids and unrelated child panels are replaced.
vi.mock("@mui/x-data-grid", () => ({
  DataGrid: ({ rows, getRowId, rowSelectionModel, onRowSelectionModelChange }) => (
    <section aria-label="Projects">
      {rows.map((row) => (
        <label key={getRowId(row)}>
          <input
            type="checkbox"
            checked={rowSelectionModel.includes(getRowId(row))}
            onChange={(event) =>
              onRowSelectionModelChange(
                event.target.checked
                  ? [...rowSelectionModel, getRowId(row)]
                  : rowSelectionModel.filter((id) => id !== getRowId(row)),
              )
            }
          />
          {row.name}
        </label>
      ))}
    </section>
  ),
}));
vi.mock("src/components/data-table", async () => ({
  DataTable: (await import("src/components/data-table/DataTable")).default,
  DataTablePagination: (await import("src/components/data-table/DataTablePagination")).default,
}));
vi.mock("react-helmet-async", () => ({ Helmet: () => null }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("../ObserveListView", () => ({ default: () => null }));
vi.mock("../RightSection/ProjectRightSection", () => ({ default: () => null }));
vi.mock("../NewProject/NewObserve", () => ({ default: () => null }));
vi.mock("../NewProject/NewExperiment", () => ({ default: () => null }));
vi.mock("../context/ProjectObserveContextProvider", () => ({
  default: ({ children }) => children,
}));
vi.mock("../context/ProjectExperimentContextProvider", () => ({
  default: ({ children }) => children,
}));
vi.mock("src/sections/projects/LLMTracing/TraceFilterPanel", () => ({
  default: () => null,
}));
/* eslint-enable react/prop-types */

let client;
let projects;
let previousPath;

beforeEach(() => {
  vi.clearAllMocks();
  previousPath = `${window.location.pathname}${window.location.search}${window.location.hash}`;
  projects = [{ id: "final-project", name: "Final project" }];
  getProjects.mockImplementation(async () => ({
    data: { result: { projects: [...projects], total_count: projects.length } },
  }));
  deleteProjects.mockImplementation(async () => {
    projects = [];
    return { data: {} };
  });
  client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
});

afterEach(() => {
  cleanup();
  client.clear();
  window.history.replaceState({}, "", previousPath);
});

describe("ProjectWrapperView deletion refresh", () => {
  it("refetches the trailing-slash wrapper and shows ProjectFtux after deleting its final project", async () => {
    const invalidateQueries = vi.spyOn(client, "invalidateQueries");
    renderWithRouter(
      <QueryClientProvider client={client}>
        <ProjectWrapperView />
      </QueryClientProvider>,
      { route: "/dashboard/observe/" },
    );

    fireEvent.click(await screen.findByRole("checkbox", { name: "Final project" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete", exact: true }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("Are you sure you want to delete this project?")).toBeInTheDocument();
    expect(client.getQueryData(["project--list"]).data.result.projects).toHaveLength(1);
    getProjects.mockClear();

    fireEvent.click(within(dialog).getByRole("button", { name: "Delete", exact: true }));

    await waitFor(() =>
      expect(deleteProjects).toHaveBeenCalledWith("/projects/delete", {
        data: { project_ids: ["final-project"], project_type: "experiment" },
      }),
    );
    expect(deleteProjects).toHaveBeenCalledTimes(1);
    await screen.findByText("Welcome to Observe");
    expect(screen.queryByRole("region", { name: "Projects" })).not.toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ["project--list"] });
    expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ["experiment-projects"] });
    // The wrapper GET has no page_number; a child-list refetch alone cannot
    // satisfy this assertion or replace the table with ProjectFtux.
    expect(getProjects).toHaveBeenCalledWith(
      "/projects",
      expect.objectContaining({ params: { project_type: "experiment" } }),
    );
    expect(client.getQueryData(["project--list"]).data.result.projects).toEqual([]);
  });
});
