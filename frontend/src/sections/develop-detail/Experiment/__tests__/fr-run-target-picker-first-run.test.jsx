import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import RunTargetPicker from "../RunTargetPicker";

// FR-EXPERIMENTS-01: a brand-new workspace has no saved prompt or agent, and
// every first-run door into New experiment goes through this picker. The two
// catalogue reads are the only seams; the popover, placement, debounce and
// copy are the real component.
const { reads } = vi.hoisted(() => ({
  reads: { prompts: null, agents: null },
}));
vi.mock("src/api/develop/prompt", () => ({
  usePromptExecutions: () => reads.prompts,
}));
vi.mock("src/api/experiment/use-get-agents", () => ({
  useGetListOfAgents: () => reads.agents,
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));

// A read that has not settled (also what a disabled read reports).
const pendingRead = () => ({
  isPending: true,
  isError: false,
  data: undefined,
  refetch: vi.fn(),
});
const settledRead = (pages, refetch = vi.fn()) => ({
  isPending: false,
  isError: false,
  data: { pages },
  refetch,
});

let anchor;
const picker = (type, handlers) => (
  <RunTargetPicker
    anchorEl={anchor}
    type={type}
    onPick={handlers.onPick}
    onClose={handlers.onClose}
  />
);
const openPicker = (type = "prompt") => {
  const handlers = { onPick: vi.fn(), onClose: vi.fn() };
  const view = render(picker(type, handlers));
  return {
    ...handlers,
    rerender: () => view.rerender(picker(type, handlers)),
  };
};

beforeEach(() => {
  anchor = document.createElement("button");
  anchor.textContent = "Prompt";
  document.body.appendChild(anchor);
  reads.prompts = pendingRead();
  reads.agents = pendingRead();
});
afterEach(() => {
  anchor.remove();
  vi.clearAllMocks();
});

describe("RunTargetPicker first run (FR-EXPERIMENTS-01)", () => {
  it("FR-EXP-01-1: no saved prompts offers Create a prompt (new tab) and Refresh list", () => {
    const refetch = vi.fn();
    reads.prompts = settledRead([{ results: [] }], refetch);
    openPicker("prompt");

    // The old dead end was this line of text with nothing to click.
    expect(screen.queryByText(/create one in prompts/i)).toBeNull();
    expect(screen.getByText("No saved prompts")).toBeInTheDocument();
    const create = screen.getByRole("link", { name: /create a prompt/i });
    expect(create.getAttribute("href")).toMatch(/^\/dashboard\/workbench/);
    expect(create).toHaveAttribute("target", "_blank");
    expect(create.getAttribute("rel")).toMatch(/noopener/);

    fireEvent.click(screen.getByRole("button", { name: /refresh/i }));
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it("FR-EXP-01-1b: after Refresh list the new prompt is picked without leaving the drawer", () => {
    const refetch = vi.fn();
    reads.prompts = settledRead([{ results: [] }], refetch);
    const { onPick, onClose, rerender } = openPicker("prompt");

    fireEvent.click(screen.getByRole("button", { name: /refresh list/i }));
    expect(refetch).toHaveBeenCalledTimes(1);

    // While the list is read again the action says so and cannot be repeated.
    reads.prompts = {
      ...settledRead([{ results: [] }], refetch),
      isFetching: true,
    };
    rerender();
    expect(screen.getByRole("button", { name: /refreshing/i })).toBeDisabled();

    const made = { id: "p-new", name: "Support triage", model: "gpt-4o-mini" };
    reads.prompts = settledRead([{ results: [made] }], refetch);
    rerender();
    expect(screen.queryByRole("link", { name: /create a prompt/i })).toBeNull();
    fireEvent.click(screen.getByRole("menuitem", { name: /support triage/i }));
    expect(onPick).toHaveBeenCalledWith(made);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("FR-EXP-01-2: a failed prompt read shows an error with Retry, never the empty-catalogue text", () => {
    const refetch = vi.fn();
    reads.prompts = {
      isPending: false,
      isError: true,
      error: { message: "Network Error" },
      data: undefined,
      refetch,
    };
    openPicker("prompt");

    expect(screen.queryByText(/no saved prompts/i)).toBeNull();
    expect(screen.queryByRole("link", { name: /create a prompt/i })).toBeNull();
    expect(screen.getByRole("alert")).toHaveTextContent(
      /couldn.t load your prompts/i,
    );
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it("FR-EXP-01-2b: a failed agent read shows an error with Retry, never the empty-catalogue text", () => {
    const refetch = vi.fn();
    reads.agents = {
      isPending: false,
      isError: true,
      error: { message: "Request failed with status code 500" },
      data: undefined,
      refetch,
    };
    openPicker("agent");

    expect(screen.queryByText(/no agents|no saved agents/i)).toBeNull();
    expect(screen.getByRole("alert")).toHaveTextContent(
      /couldn.t load your agents/i,
    );
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it("FR-EXP-01-3: no agents offers an Agent Playground link (new tab) and Refresh list", () => {
    const refetch = vi.fn();
    reads.agents = settledRead([{ graphs: [] }], refetch);
    openPicker("agent");

    expect(screen.queryByText(/create one in agent playground/i)).toBeNull();
    expect(screen.getByText("No saved agents")).toBeInTheDocument();
    const create = screen.getByRole("link", { name: /create an agent/i });
    expect(create).toHaveAttribute("href", "/dashboard/agents/playground");
    expect(create).toHaveAttribute("target", "_blank");
    fireEvent.click(screen.getByRole("button", { name: /refresh/i }));
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it("FR-EXP-01-4: a populated list still picks one prompt, then closes", () => {
    const item = { id: "p1", name: "Support triage", model: "gpt-4o-mini" };
    reads.prompts = settledRead([{ results: [item] }]);
    const { onPick, onClose } = openPicker("prompt");

    expect(screen.getByText("Prompt · gpt-4o-mini")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("menuitem", { name: /support triage/i }));
    expect(onPick).toHaveBeenCalledWith(item);
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onPick.mock.invocationCallOrder[0]).toBeLessThan(
      onClose.mock.invocationCallOrder[0],
    );
  });

  it("FR-EXP-01-5: a search with no results still says No matches", () => {
    reads.prompts = settledRead([{ results: [] }]);
    openPicker("prompt");

    fireEvent.change(screen.getByPlaceholderText("Search prompts"), {
      target: { value: "nothing like this" },
    });
    expect(screen.getByText("No matches")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /create a prompt/i })).toBeNull();
  });

  it("FR-EXP-01-6: a pending read shows the skeleton, not the empty or error copy", () => {
    openPicker("prompt");

    const paper = screen.getByTestId("run-target-picker");
    expect(paper.querySelectorAll(".MuiSkeleton-root")).toHaveLength(3);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.queryByText("No matches")).toBeNull();
  });
});
