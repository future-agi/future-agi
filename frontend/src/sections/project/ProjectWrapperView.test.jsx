/* eslint-disable react/prop-types */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import {
  QueryClient,
  QueryClientProvider,
  focusManager,
} from "@tanstack/react-query";

const mocks = vi.hoisted(() => ({
  readPage: vi.fn(),
  get: vi.fn(),
  del: vi.fn(),
}));

vi.mock("src/api/project/observe-project-list", () => ({
  readObserveProjectPage: mocks.readPage,
}));

vi.mock("src/utils/axios", () => ({
  default: { get: mocks.get, delete: mocks.del },
  endpoints: {
    project: {
      projectExperimentList: "/model-hub/projects/",
      deleteObservePrototype: "/tracer/project/",
    },
  },
}));

vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  useSnackbar: () => ({ enqueueSnackbar: vi.fn(), closeSnackbar: vi.fn() }),
}));

vi.mock("react-helmet-async", async (importOriginal) => ({
  ...(await importOriginal()),
  Helmet: () => null,
}));

vi.mock("src/components/iconify", () => ({ default: () => null }));

vi.mock("./NewProject/NewObserve", () => ({
  default: () => <div>Setup instructions</div>,
}));
vi.mock("./NewProject/NewExperiment", () => ({
  default: () => <div>Prototype setup</div>,
}));

vi.mock("./ObserveListView", async () => {
  const { forwardRef } = await import("react");
  return {
    default: forwardRef(function ObserveListStub() {
      return <div>Project list</div>;
    }),
  };
});
vi.mock("./ExperimentListView", async () => {
  const { forwardRef } = await import("react");
  return {
    default: forwardRef(function ExperimentListStub() {
      return <div>Prototype list</div>;
    }),
  };
});

vi.mock("./context/ProjectObserveContextProvider", () => ({
  default: ({ children }) => <>{children}</>,
}));
vi.mock("./context/ProjectExperimentContextProvider", () => ({
  default: ({ children }) => <>{children}</>,
}));
vi.mock("./RightSection/ProjectRightSection", () => ({
  default: () => null,
}));
vi.mock("../projects/LLMTracing/TraceFilterPanel", () => ({
  default: () => null,
}));
vi.mock("./common", () => ({
  PROJECT_FILTER_PROPERTIES: [],
  PROJECT_FILTER_DEFAULT_OPERATORS: {},
  PROJECT_FILTER_DEFAULT_ROW: {},
  projectOperatorFilter: () => true,
}));

import ProjectWrapperView from "./ProjectWrapperView";

// Shape returned by readObserveProjectPage (the parsed list_projects page).
const page = (count) => {
  const rows = Array.from({ length: count }, (_, i) => ({
    id: `p${i + 1}`,
    name: `Project ${i + 1}`,
  }));
  return { rows, totalRows: count, totalPages: count > 0 ? 1 : 0 };
};

let client;

const tick = async (ms = 1) => {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
};

const renderObserve = () =>
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/dashboard/observe"]}>
        <ProjectWrapperView />
      </MemoryRouter>
    </QueryClientProvider>,
  );

beforeEach(() => {
  vi.useFakeTimers();
  mocks.readPage.mockReset();
  mocks.get.mockReset();
  mocks.del.mockReset();
  focusManager.setFocused(true);
  client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 5000 } },
  });
});

afterEach(() => {
  cleanup();
  client.clear();
  focusManager.setFocused(undefined);
  vi.useRealTimers();
});

describe("ProjectWrapperView first trace (OBS-SW-100)", () => {
  it("keeps checking while the Observe setup page is shown and switches to the project list when the first trace arrives", async () => {
    mocks.readPage
      .mockResolvedValueOnce(page(0))
      .mockResolvedValueOnce(page(1));

    renderObserve();
    await tick();

    expect(screen.getByText("Welcome to Observe")).toBeInTheDocument();
    expect(screen.queryByText("Project list")).toBeNull();
    expect(mocks.readPage).toHaveBeenCalledTimes(1);

    // The first trace creates a project; the setup page reads the list again
    // on its own, without a reload, focus change or tab switch.
    await tick(5100);

    expect(mocks.readPage).toHaveBeenCalledTimes(2);
    expect(screen.getByText("Project list")).toBeInTheDocument();
    expect(screen.queryByText("Welcome to Observe")).toBeNull();

    // Once a project exists the wrapper stops checking.
    await tick(15000);
    expect(mocks.readPage).toHaveBeenCalledTimes(2);
  });

  it("does not keep re-reading the list when the org already has projects", async () => {
    mocks.readPage.mockResolvedValue(page(1));

    renderObserve();
    await tick();

    expect(screen.getByText("Project list")).toBeInTheDocument();
    expect(mocks.readPage).toHaveBeenCalledTimes(1);

    await tick(15000);
    expect(mocks.readPage).toHaveBeenCalledTimes(1);
  });
});
