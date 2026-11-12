import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createTheme } from "@mui/material/styles";
import { act, render, screen, waitFor } from "src/utils/test-utils";

const axiosGetMock = vi.hoisted(() => vi.fn());

vi.mock("src/utils/axios", () => ({
  default: { get: axiosGetMock },
  endpoints: {
    project: {
      projectObserveList: "/tracer/project/list_projects/",
      projectExperimentDetail: (projectId) => `/tracer/project/${projectId}/`,
    },
  },
}));

vi.mock("./TagEditor", () => ({ default: () => <div>tags</div> }));

import ObserveListView from "./ObserveListView";

const theme = createTheme();

const LIST_URL = "/tracer/project/list_projects/";
const projectUrl = (projectId) => `/tracer/project/${projectId}/`;

const QUICKSTART_TITLE = "Send your first trace";

// Every new workspace is given this sample project at sign-up, already
// holding traces. The project record marks it with source "demo"; projects
// the user creates default to "prototype".
const demoProject = {
  id: "demo-observe",
  name: "Demo SQL Agent (Observe)",
  source: "demo",
  issues: 0,
  daily_volume: [],
  activity_query_complete: true,
};

const ownProject = (overrides) => ({
  id: "own-1",
  name: "checkout-agent",
  source: "prototype",
  issues: 0,
  daily_volume: [],
  activity_query_complete: true,
  ...overrides,
});

// list_projects rows carry no source; only the project record does.
const listRow = ({ source: _source, ...row }) => row;

const listPage = (projects) => ({
  data: {
    status: true,
    result: {
      table: projects.map(listRow),
      metadata: {
        total_rows: projects.length,
        total_pages: projects.length > 0 ? 1 : 0,
        page_number: 0,
        page_size: 25,
      },
    },
  },
});

// Answers each list read with the next of `pages` (the last one repeats) and
// each project read with that project's record. Reads of `failingIds` fail.
const serve = (pages, { failingIds = [] } = {}) => {
  const records = new Map();
  pages.flat().forEach((project) => records.set(project.id, project));
  let listReads = 0;
  axiosGetMock.mockImplementation(async (url) => {
    if (url === LIST_URL) {
      const page = pages[Math.min(listReads, pages.length - 1)];
      listReads += 1;
      return listPage(page);
    }
    const project = [...records.values()].find(
      (candidate) => projectUrl(candidate.id) === url,
    );
    if (!project || failingIds.includes(project.id)) {
      throw new Error(`Request failed: ${url}`);
    }
    return { data: { status: true, result: project } };
  });
};

const listReadCount = () =>
  axiosGetMock.mock.calls.filter(([url]) => url === LIST_URL).length;
const projectReads = () =>
  axiosGetMock.mock.calls.map(([url]) => url).filter((url) => url !== LIST_URL);

// Lets reads started by the list answer and the screen update before a check
// that something is not shown.
const settle = () =>
  act(() => new Promise((resolve) => setTimeout(resolve, 200)));

const renderList = (props = {}) => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ObserveListView {...props} />
    </QueryClientProvider>,
    { theme },
  );
};

describe("ObserveListView with the sign-up demo project", () => {
  beforeEach(() => {
    axiosGetMock.mockReset();
  });

  it("shows the first-trace code when the demo project is the only project, and keeps the demo project in the list", async () => {
    serve([[demoProject]]);

    renderList();

    await waitFor(() =>
      expect(screen.getByText(QUICKSTART_TITLE)).toBeInTheDocument(),
    );
    // The sample project above has traces, so the page asks for the
    // user's own first trace instead of saying there are none or waiting.
    // The title appears once; the code card under it is the quickstart.
    expect(screen.getAllByText(QUICKSTART_TITLE)).toHaveLength(1);
    expect(screen.getByText("Quickstart")).toBeInTheDocument();
    expect(screen.queryByText("No traces yet")).not.toBeInTheDocument();
    expect(
      screen.queryByText("Waiting for the first trace…"),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText(
        /^Demo SQL Agent \(Observe\) above is a sample project\./,
      ),
    ).toBeInTheDocument();
    // The demo row says it is a sample (the grid applies the new column on
    // its next render, so wait for it).
    expect(await screen.findByText("Sample")).toBeInTheDocument();
    const code = screen.getByText(/project_name="my-project"/);
    expect(code.textContent).toContain("tracer_provider.shutdown()");
    expect(
      screen.getByRole("button", { name: "Copy code" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Demo SQL Agent (Observe)")).toBeInTheDocument();
  });

  it("recognises a renamed demo project by its source, not its name", async () => {
    serve([
      [{ ...demoProject, id: "demo-renamed", name: "Text-to-SQL sample" }],
    ]);

    renderList();

    await waitFor(() =>
      expect(screen.getByText(QUICKSTART_TITLE)).toBeInTheDocument(),
    );
    expect(screen.getByText("Text-to-SQL sample")).toBeInTheDocument();
    expect(
      screen.getByText(/^Text-to-SQL sample above is a sample project\./),
    ).toBeInTheDocument();
  });

  it("shows only the list for a project of the user's own that carries the demo project's name", async () => {
    const lookalike = ownProject({
      id: "own-lookalike",
      name: "Demo SQL Agent (Observe)",
    });
    serve([[lookalike]]);

    renderList();

    await waitFor(() =>
      expect(screen.getByText("Demo SQL Agent (Observe)")).toBeInTheDocument(),
    );
    await settle();
    expect(screen.queryByText(QUICKSTART_TITLE)).not.toBeInTheDocument();
    expect(screen.queryByText("No traces yet")).not.toBeInTheDocument();
  });

  it("shows only the list when the workspace has a project of its own", async () => {
    serve([[ownProject()]]);

    renderList();

    await waitFor(() =>
      expect(screen.getByText("checkout-agent")).toBeInTheDocument(),
    );
    await settle();
    expect(screen.queryByText(QUICKSTART_TITLE)).not.toBeInTheDocument();
    expect(screen.queryByText("No traces yet")).not.toBeInTheDocument();
  });

  it("shows only the list when a project of the user's own sits beside the demo project", async () => {
    serve([[ownProject(), demoProject]]);

    renderList();

    await waitFor(() =>
      expect(screen.getByText("checkout-agent")).toBeInTheDocument(),
    );
    expect(screen.getByText("Demo SQL Agent (Observe)")).toBeInTheDocument();
    await settle();
    expect(screen.queryByText(QUICKSTART_TITLE)).not.toBeInTheDocument();
    // The Sample tag belongs to the sample-only state.
    expect(screen.queryByText("Sample")).not.toBeInTheDocument();
  });

  it("shows only the list while a search matches just the demo project", async () => {
    serve([[demoProject]]);

    renderList({ searchQuery: "Demo" });

    await waitFor(() =>
      expect(screen.getByText("Demo SQL Agent (Observe)")).toBeInTheDocument(),
    );
    await settle();
    expect(screen.queryByText(QUICKSTART_TITLE)).not.toBeInTheDocument();
    expect(projectReads()).toEqual([]);
  });

  it("shows only the list when the demo project's record cannot be read", async () => {
    serve([[demoProject]], { failingIds: [demoProject.id] });

    renderList();

    await waitFor(() =>
      expect(screen.getByText("Demo SQL Agent (Observe)")).toBeInTheDocument(),
    );
    await settle();
    expect(screen.queryByText(QUICKSTART_TITLE)).not.toBeInTheDocument();
    expect(screen.queryByText(/Request failed/)).not.toBeInTheDocument();
  });

  it("keeps checking while only the demo project is listed and drops the code once the first trace makes a project", async () => {
    serve([
      [demoProject],
      [ownProject({ id: "own-first", name: "my-project" }), demoProject],
    ]);

    renderList();

    await waitFor(() =>
      expect(screen.getByText(QUICKSTART_TITLE)).toBeInTheDocument(),
    );
    expect(listReadCount()).toBe(1);

    // No reload, focus change or search: the list reads itself again.
    await waitFor(
      () => expect(screen.getByText("my-project")).toBeInTheDocument(),
      { timeout: 8000 },
    );
    expect(listReadCount()).toBe(2);
    await settle();
    expect(screen.queryByText(QUICKSTART_TITLE)).not.toBeInTheDocument();
    expect(screen.getByText("Demo SQL Agent (Observe)")).toBeInTheDocument();
  });
});
