import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";

import SelectProject from "../SelectProject";

// OBS-SW-120: in an org with no Observe projects, Alerts > Create alert
// opened "Choose a project" whose only option was "No option provided",
// with Next disabled and Cancel the only way out. The dialog now says an
// alert needs an Observe project and links to Observe setup instead.

const NO_PROJECTS =
  "Alerts watch an Observe project. There are no Observe projects yet: send your first trace to create one.";

const h = vi.hoisted(() => ({
  projects: { data: [], isLoading: false },
}));

vi.mock("src/sections/projects/LLMTracing/common", async (importOriginal) => ({
  ...(await importOriginal()),
  useProjectList: () => h.projects,
}));

const renderDialog = () => {
  const props = {
    open: true,
    value: null,
    onChange: vi.fn(),
    onClose: vi.fn(),
    onAction: vi.fn(),
  };
  render(<SelectProject {...props} />);
  return props;
};

// The dialog is portalled to <body>.
const projectPicker = () =>
  document.querySelector('[data-alert-field="project"]');

beforeEach(() => {
  h.projects = { data: [], isLoading: false };
  window.history.pushState({}, "", "/dashboard/alerts");
});

describe("Choosing a project for a new alert", () => {
  it("with no Observe projects, links to Observe setup", async () => {
    const user = userEvent.setup();
    const props = renderDialog();

    expect(screen.getByText(NO_PROJECTS)).toBeInTheDocument();
    expect(projectPicker()).toBeNull();
    expect(screen.queryByRole("button", { name: "Next" })).toBeNull();

    const setUp = screen.getByRole("link", { name: "Set up Observe" });
    expect(setUp).toHaveAttribute("href", "/dashboard/observe");

    await user.click(setUp);
    expect(props.onClose).toHaveBeenCalledTimes(1);
    expect(props.onChange).toHaveBeenCalledWith(null);
    expect(window.location.pathname).toBe("/dashboard/observe");
  });

  it("with projects, keeps the picker and Next", () => {
    h.projects = {
      data: [{ id: "project-1", name: "checkout-bot" }],
      isLoading: false,
    };
    renderDialog();

    expect(projectPicker()).not.toBeNull();
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
    expect(screen.queryByText(NO_PROJECTS)).toBeNull();
    expect(screen.queryByRole("link", { name: "Set up Observe" })).toBeNull();
  });

  it("while the project list loads, does not say there are none", () => {
    h.projects = { data: undefined, isLoading: true };
    renderDialog();

    expect(projectPicker()).not.toBeNull();
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
    expect(screen.queryByText(NO_PROJECTS)).toBeNull();
    expect(screen.queryByRole("link", { name: "Set up Observe" })).toBeNull();
  });
});
