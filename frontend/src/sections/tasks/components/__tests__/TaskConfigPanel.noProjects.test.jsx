/* eslint-disable react/prop-types */
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { useForm } from "react-hook-form";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import TaskConfigPanel from "../TaskConfigPanel";

const axiosGetMock = vi.hoisted(() => vi.fn());

vi.mock("src/utils/axios", () => ({
  default: { get: axiosGetMock, post: vi.fn(), patch: vi.fn() },
  endpoints: {
    project: {
      listProjects: () => "/projects",
      createEvalTaskConfig: () => "/eval-task-config",
      updateEvalTaskConfig: (id) => `/eval-task-config/${id}`,
    },
  },
}));

vi.mock("src/api/project/project-detail", () => ({
  useGetProjectDetails: () => ({ data: undefined, isSuccess: false }),
}));

// The picker's own menu is covered by FormSelectMenus tests; here only the
// options and the empty-list text the panel hands it matter.
vi.mock("src/components/FromSearchSelectField", () => ({
  FormSearchSelectFieldControl: ({ label, options, noOptions }) => (
    <div data-testid="project-select">
      <span>{label}</span>
      {options.map((option) => (
        <span key={option.value}>{option.label}</span>
      ))}
      {options.length === 0 && (
        <span data-testid="project-no-options">{noOptions}</span>
      )}
    </div>
  ),
}));

vi.mock("src/components/FormTextField/FormTextFieldV2", () => ({
  default: () => null,
}));
vi.mock("../TaskFilterBar", () => ({ default: () => null }));
vi.mock("../TaskSchedulingSection", () => ({ default: () => null }));
vi.mock("src/sections/common/EvalPicker", () => ({
  EvalPickerDrawer: () => null,
  serializeEvalConfig: vi.fn(),
}));
vi.mock("src/components/ModalWrapper/ModalWrapper", () => ({
  default: () => null,
}));
vi.mock("src/components/tabs/tabs", () => ({
  SegmentedTabs: ({ children }) => <div>{children}</div>,
}));
vi.mock("src/components/ComplexFilter/FilterErrorBoundary", () => ({
  default: ({ children }) => <>{children}</>,
}));
vi.mock("src/components/snackbar", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/components/iconify", () => ({ default: () => null }));

const NO_PROJECTS_COPY =
  "No Observe projects yet: send your first trace to create one";

const projectList = (projects) => ({ data: { result: { projects } } });

const Harness = ({ projectLocked = false }) => {
  const { control, setValue, getValues } = useForm({
    defaultValues: {
      name: "",
      project: "",
      rowType: "spans",
      filters: [],
      evalsDetails: [],
    },
  });
  return (
    <TaskConfigPanel
      mode={projectLocked ? "edit" : "create"}
      control={control}
      setValue={setValue}
      getValues={getValues}
      projectLocked={projectLocked}
      initialProjectName={projectLocked ? "Checkout agent" : null}
    />
  );
};

const renderPanel = (props = {}) => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const rendered = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <Harness {...props} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...rendered, queryClient };
};

describe("TaskConfigPanel with no Observe projects", () => {
  beforeEach(() => {
    axiosGetMock.mockReset();
  });

  it("explains how to get a project and links to Observe setup", async () => {
    axiosGetMock.mockResolvedValue(projectList([]));

    renderPanel();

    const link = await screen.findByRole("link", { name: "Set up Observe" });
    expect(link).toHaveAttribute("href", "/dashboard/observe");
    expect(
      screen.getByText(`${NO_PROJECTS_COPY}.`, { exact: false }),
    ).toBeInTheDocument();
    expect(screen.getByTestId("project-no-options")).toHaveTextContent(
      NO_PROJECTS_COPY,
    );
    expect(screen.queryByText("No projects available")).not.toBeInTheDocument();
    expect(axiosGetMock).toHaveBeenCalledWith("/projects", {
      params: { project_type: "observe" },
    });
  });

  it("keeps the plain project picker when Observe projects exist", async () => {
    axiosGetMock.mockResolvedValue(
      projectList([{ id: "project-a", name: "Checkout agent" }]),
    );

    const { queryClient } = renderPanel();

    expect(await screen.findByText("Checkout agent")).toBeInTheDocument();
    await waitFor(() =>
      expect(queryClient.getQueryState(["project-list"])?.status).toBe(
        "success",
      ),
    );
    expect(
      screen.queryByRole("link", { name: "Set up Observe" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText(NO_PROJECTS_COPY, { exact: false }),
    ).not.toBeInTheDocument();
  });

  it("does not claim there are no projects when the list fails to load", async () => {
    axiosGetMock.mockRejectedValue(new Error("Network Error"));

    const { queryClient } = renderPanel();

    await waitFor(() =>
      expect(queryClient.getQueryState(["project-list"])?.status).toBe("error"),
    );
    expect(
      screen.queryByRole("link", { name: "Set up Observe" }),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("project-no-options")).toHaveTextContent(
      "No projects available",
    );
  });

  it("shows no setup link on an existing task, where the project is fixed", () => {
    renderPanel({ projectLocked: true });

    expect(screen.getByDisplayValue("Checkout agent")).toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "Set up Observe" }),
    ).not.toBeInTheDocument();
    expect(axiosGetMock).not.toHaveBeenCalled();
  });
});
