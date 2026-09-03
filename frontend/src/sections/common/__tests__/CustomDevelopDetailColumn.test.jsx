import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import CustomDevelopDetailColumn from "../CustomDevelopDetailColumn";

vi.mock("src/components/iconify", () => ({
  default: ({ icon, ...props }) => (
    <span data-testid="iconify" data-icon={icon} {...props} />
  ),
}));

vi.mock("src/components/svg-color", () => ({
  default: (props) => <span data-testid="svg-color" {...props} />,
}));

const baseProps = {
  displayName: "My Column",
  showColumnMenu: () => {},
  eGridHeader: { style: {} },
  api: null,
};

const colWithOrigin = (overrides = {}) => ({
  id: "col-1",
  originType: "evaluation",
  dataType: "text",
  ...overrides,
});

describe("CustomDevelopDetailColumn status indicator", () => {
  it("renders a running spinner when the column status is Running", () => {
    render(
      <CustomDevelopDetailColumn
        {...baseProps}
        col={colWithOrigin({ status: "Running" })}
      />,
    );
    expect(screen.getByTestId("column-status-running")).toBeInTheDocument();
    expect(
      screen.queryByTestId("column-status-queued"),
    ).not.toBeInTheDocument();
    expect(screen.queryByTestId("column-status-error")).not.toBeInTheDocument();
  });

  it("renders a running spinner for other computing statuses", () => {
    const { rerender } = render(
      <CustomDevelopDetailColumn
        {...baseProps}
        col={colWithOrigin({ status: "Editing" })}
      />,
    );
    expect(screen.getByTestId("column-status-running")).toBeInTheDocument();

    rerender(
      <CustomDevelopDetailColumn
        {...baseProps}
        col={colWithOrigin({ status: "PartialRun" })}
      />,
    );
    expect(screen.getByTestId("column-status-running")).toBeInTheDocument();

    rerender(
      <CustomDevelopDetailColumn
        {...baseProps}
        col={colWithOrigin({ status: "ExperimentEvaluation" })}
      />,
    );
    expect(screen.getByTestId("column-status-running")).toBeInTheDocument();
  });

  it("renders a queued clock icon when the column status is NotStarted", () => {
    render(
      <CustomDevelopDetailColumn
        {...baseProps}
        col={colWithOrigin({ status: "NotStarted" })}
      />,
    );
    expect(screen.getByTestId("column-status-queued")).toBeInTheDocument();
    expect(
      screen.queryByTestId("column-status-running"),
    ).not.toBeInTheDocument();
    expect(screen.queryByTestId("column-status-error")).not.toBeInTheDocument();
  });

  it("renders an error icon when the column status is error", () => {
    render(
      <CustomDevelopDetailColumn
        {...baseProps}
        col={colWithOrigin({ status: "error" })}
      />,
    );
    expect(screen.getByTestId("column-status-error")).toBeInTheDocument();
    expect(
      screen.queryByTestId("column-status-running"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("column-status-queued"),
    ).not.toBeInTheDocument();
  });

  it("renders no status indicator when the column is completed", () => {
    render(
      <CustomDevelopDetailColumn
        {...baseProps}
        col={colWithOrigin({ status: "completed" })}
      />,
    );
    expect(
      screen.queryByTestId("column-status-running"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("column-status-queued"),
    ).not.toBeInTheDocument();
    expect(screen.queryByTestId("column-status-error")).not.toBeInTheDocument();
  });

  it("renders no status indicator when the status is absent", () => {
    render(
      <CustomDevelopDetailColumn
        {...baseProps}
        col={colWithOrigin({ status: undefined })}
      />,
    );
    expect(
      screen.queryByTestId("column-status-running"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("column-status-queued"),
    ).not.toBeInTheDocument();
    expect(screen.queryByTestId("column-status-error")).not.toBeInTheDocument();
  });

  it("still renders the column display name regardless of status", () => {
    render(
      <CustomDevelopDetailColumn
        {...baseProps}
        col={colWithOrigin({ status: "Running" })}
      />,
    );
    expect(screen.getByText("My Column")).toBeInTheDocument();
  });
});
