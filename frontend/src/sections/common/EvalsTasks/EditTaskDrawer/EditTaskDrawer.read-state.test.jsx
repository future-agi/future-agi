import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import EditTaskDrawer from "./EditTaskDrawer";
import EditTaskDrawerV2 from "./EditTaskDrawerV2";
import { useGetTaskData } from "../common";

vi.mock("src/utils/axios", () => ({
  default: {},
  endpoints: {},
}));
vi.mock("src/components/FormTextField/FormTextFieldV2", () => ({
  default: () => null,
}));
vi.mock("src/components/FormSelectField", () => ({
  FormSelectField: () => null,
}));

vi.mock("../common", () => ({
  getDefaultTaskValues: vi.fn(() => ({})),
  useGetTaskData: vi.fn(),
}));

vi.mock("./DetailsEdit", () => ({
  default: ({ taskDetails }) => <div>Task editor: {taskDetails.name}</div>,
}));

const failedRead = (overrides = {}) => ({
  data: undefined,
  isLoading: false,
  isFetching: false,
  isError: true,
  error: new Error("task lookup failed"),
  refetch: vi.fn(),
  ...overrides,
});

describe("task edit drawer read states", () => {
  beforeEach(() => useGetTaskData.mockReset());

  it("offers retry instead of a blank legacy drawer", () => {
    const query = failedRead();
    useGetTaskData.mockReturnValue(query);

    render(
      <EditTaskDrawer
        open
        onClose={vi.fn()}
        selectedRow={{ id: "task-1", name: "Task one" }}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(query.refetch).toHaveBeenCalledOnce();
  });

  it("keeps prior task details visible after a legacy refresh failure", () => {
    const query = failedRead({ data: { name: "Task one", project_id: "p-1" } });
    useGetTaskData.mockReturnValue(query);

    render(
      <EditTaskDrawer
        open
        onClose={vi.fn()}
        selectedRow={{ id: "task-1", name: "Task one" }}
      />,
    );

    expect(screen.getByText("Task editor: Task one")).toBeInTheDocument();
    expect(
      screen.getByText(/Existing task details are still shown/i),
    ).toBeInTheDocument();
  });

  it("offers retry instead of a blank v2 drawer", () => {
    const query = failedRead();
    useGetTaskData.mockReturnValue(query);

    render(
      <EditTaskDrawerV2
        open
        onClose={vi.fn()}
        selectedRow={{ id: "task-1", name: "Task one" }}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(query.refetch).toHaveBeenCalledOnce();
  });
});
