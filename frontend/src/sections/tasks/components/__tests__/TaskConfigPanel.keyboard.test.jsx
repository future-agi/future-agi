import React from "react";
import PropTypes from "prop-types";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import axios from "src/utils/axios";
import TaskConfigPanel from "../TaskConfigPanel";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn() },
  endpoints: { project: { listProjects: () => "/projects/" } },
}));
vi.mock("src/api/project/project-detail", () => ({
  useGetProjectDetails: () => ({ data: { source: "sdk" }, isSuccess: true }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/utils/Mixpanel", () => ({
  Events: {},
  PropertyName: {},
  trackEvent: vi.fn(),
}));
vi.mock("src/sections/common/EvalPicker", () => ({
  EvalPickerDrawer: () => null,
  serializeEvalConfig: vi.fn(),
}));
vi.mock("src/components/snackbar", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/components/ModalWrapper/ModalWrapper", () => ({
  default: () => null,
}));
vi.mock("../TaskFilterBar", () => ({
  default: () => <button type="button">Add filter</button>,
}));
vi.mock("../TaskSchedulingSection", () => ({ default: () => null }));
vi.mock("../task_sampling_section", () => ({ default: () => null }));

let form;
function Harness({ locked = false }) {
  form = useForm({
    defaultValues: {
      name: "",
      project: locked ? "p1" : "",
      rowType: "spans",
      filters: [],
      evalsDetails: [],
    },
  });
  return (
    <form onSubmit={form.handleSubmit(vi.fn())}>
      <TaskConfigPanel
        mode={locked ? "edit" : "create"}
        control={form.control}
        getValues={form.getValues}
        setValue={form.setValue}
        projectLocked={locked}
        initialProjectName={locked ? "Alpha" : null}
      />
      <button type="submit">Save task</button>
    </form>
  );
}
Harness.propTypes = { locked: PropTypes.bool };
const show = (props) =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <Harness {...props} />
    </QueryClientProvider>,
  );
const focusProject = async (user) => {
  await user.tab();
  expect(screen.getByRole("textbox", { name: /Task Name/ })).toHaveFocus();
  await user.tab();
  expect(screen.getByLabelText(/Project/)).toHaveFocus();
  return screen.getByLabelText(/Project/);
};

beforeEach(() => {
  vi.clearAllMocks();
  axios.get.mockResolvedValue({
    data: {
      result: {
        projects: [
          { id: "p1", name: "Alpha" },
          { id: "p2", name: "Beta" },
        ],
      },
    },
  });
});

describe("Task Project keyboard path (real form and picker)", () => {
  it("opens with Enter, uses both arrows to highlight, and commits with Enter", async () => {
    const user = userEvent.setup();
    show();
    const input = await focusProject(user);
    await waitFor(() => expect(axios.get).toHaveBeenCalled());
    await user.keyboard("{Enter}{ArrowDown}{ArrowDown}{ArrowUp}{Enter}");
    expect(form.getValues("project")).toBe("p1");
    expect(input).toHaveValue("Alpha");
    expect(input).toHaveFocus();
    expect(input).toHaveAttribute("role", "combobox");
    expect(input).toHaveAttribute("aria-expanded", "false");
  });

  it("searches real options, exposes the active descendant, and Escape restores focus without committing", async () => {
    const user = userEvent.setup();
    show();
    const input = await focusProject(user);
    await user.type(input, "Bet");
    await user.keyboard("{ArrowDown}");
    const option = screen.getByRole("option", { name: "Beta" });
    expect(input).toHaveAttribute("aria-activedescendant", option.id);
    expect(screen.getByRole("listbox")).toContainElement(option);
    await user.keyboard("{Escape}");
    expect(input).toHaveFocus();
    expect(input).toHaveAttribute("aria-expanded", "false");
    expect(form.getValues("project")).toBe("");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it.each([false, true])(
    "Tab exits and closes without committing the highlighted option (shift=%s)",
    async (shift) => {
      const user = userEvent.setup();
      show();
      const input = await focusProject(user);
      await user.keyboard("{Enter}{ArrowDown}");
      await user.tab({ shift });
      expect(input).not.toHaveFocus();
      expect(input).toHaveAttribute("aria-expanded", "false");
      expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
      expect(form.getValues("project")).toBe("");
    },
  );

  it("keeps the edit-mode project immutable", () => {
    show({ locked: true });
    expect(screen.getByLabelText("Project")).toBeDisabled();
    expect(screen.getByLabelText("Project")).toHaveValue("Alpha");
    expect(axios.get).not.toHaveBeenCalled();
  });
});
