import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, screen, within } from "@testing-library/react";
import { render } from "src/utils/test-utils";
import { EMPTY_ENV_FILTERS } from "src/api/simulate-environments/environmentListParams";
import { NOT_PROVIDED } from "../myEnvironments.constants";

vi.mock("src/api/agents/connectedAgents", () => ({
  useConnectedAgents: () => ({
    data: {
      items: [
        { id: "a1", name: "Billing" },
        { id: "a2", name: "Returns" },
      ],
      total: 2,
    },
  }),
}));

const { default: EnvironmentFilters } = await import(
  "../components/EnvironmentFilters"
);

const COUNTS = { building: 1, running: 0, completed: 30, failed: 6 };

const renderBar = ({
  value = EMPTY_ENV_FILTERS,
  statusCounts = COUNTS,
} = {}) => {
  const onChange = vi.fn();
  render(
    <EnvironmentFilters
      value={value}
      onChange={onChange}
      statusCounts={statusCounts}
    />,
  );
  return onChange;
};

const openMenu = (name) =>
  fireEvent.mouseDown(screen.getByRole("combobox", { name }));
const option = (name) =>
  within(screen.getByRole("listbox")).getByRole("option", {
    name: new RegExp(`^${name}`),
  });

describe("EnvironmentFilters", () => {
  it("shows search, status, agent type and agent, none disabled", () => {
    renderBar();
    const search = screen.getByPlaceholderText(
      "Search by name, agent or status",
    );
    expect(search).not.toBeDisabled();
    for (const name of ["Status", "Agent type", "Agent"]) {
      expect(screen.getByRole("combobox", { name })).not.toHaveAttribute(
        "aria-disabled",
        "true",
      );
    }
    expect(screen.getByRole("combobox", { name: "Status" })).toHaveTextContent(
      "All statuses",
    );
    expect(
      screen.getByRole("combobox", { name: "Agent type" }),
    ).toHaveTextContent("All agent types");
    expect(screen.getByRole("combobox", { name: "Agent" })).toHaveTextContent(
      "All agents",
    );
  });

  it("lists the statuses with their counts and a total", () => {
    renderBar();
    openMenu("Status");
    expect(option("All statuses")).toHaveTextContent("37");
    expect(option("Ready")).toHaveTextContent("30");
    for (const label of ["Building", "Running", "Failed"])
      expect(option(label)).toBeInTheDocument();
  });

  it("shows a placeholder for counts the API doesn't send", () => {
    renderBar({ statusCounts: NOT_PROVIDED });
    openMenu("Status");
    expect(
      within(screen.getByRole("listbox")).getAllByText("Count soon").length,
    ).toBe(5);
  });

  it("sends Ready as the completed status", () => {
    const onChange = renderBar();
    openMenu("Status");
    fireEvent.click(option("Ready"));
    expect(onChange).toHaveBeenCalledWith({
      ...EMPTY_ENV_FILTERS,
      status: "completed",
    });
  });

  it("picks one agent type", () => {
    const onChange = renderBar();
    openMenu("Agent type");
    fireEvent.click(option("Voice"));
    expect(onChange).toHaveBeenCalledWith({
      ...EMPTY_ENV_FILTERS,
      agentType: "voice",
    });
  });

  it("caps the menu height so a long agent list scrolls", () => {
    renderBar();
    openMenu("Agent");
    const paper = screen.getByRole("listbox").closest(".MuiPaper-root");
    expect(getComputedStyle(paper).maxHeight).toBe("320px");
  });

  it("picks one agent by id", () => {
    const onChange = renderBar();
    openMenu("Agent");
    fireEvent.click(option("Billing"));
    expect(onChange).toHaveBeenCalledWith({
      ...EMPTY_ENV_FILTERS,
      agentId: "a1",
    });
  });

  it("offers Clear only while something is set", () => {
    renderBar();
    expect(screen.queryByRole("button", { name: "Clear filters" })).toBeNull();
  });

  it("clears every filter at once", () => {
    const onChange = renderBar({
      value: { ...EMPTY_ENV_FILTERS, agentId: "a1", status: "failed" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(onChange).toHaveBeenCalledWith(EMPTY_ENV_FILTERS);
  });

  it("names an agent from the URL that isn't in the menu, instead of All agents", () => {
    renderBar({ value: { ...EMPTY_ENV_FILTERS, agentId: "gone" } });
    expect(screen.getByRole("combobox", { name: "Agent" })).toHaveTextContent(
      "Selected agent",
    );
  });

  it("doesn't offer Clear for a search of only spaces", () => {
    renderBar({ value: { ...EMPTY_ENV_FILTERS, search: "   " } });
    expect(screen.queryByRole("button", { name: "Clear filters" })).toBeNull();
  });

  describe("search", () => {
    beforeEach(() => vi.useFakeTimers());
    afterEach(() => vi.useRealTimers());

    it("sends the search once typing stops, not per keystroke", () => {
      const onChange = renderBar();
      const input = screen.getByPlaceholderText(
        "Search by name, agent or status",
      );
      for (const text of ["r", "re", "ref", "refund"]) {
        fireEvent.change(input, { target: { value: text } });
        act(() => vi.advanceTimersByTime(100));
      }
      expect(onChange).not.toHaveBeenCalled();
      act(() => vi.advanceTimersByTime(300));
      expect(onChange).toHaveBeenCalledTimes(1);
      expect(onChange).toHaveBeenCalledWith({
        ...EMPTY_ENV_FILTERS,
        search: "refund",
      });
    });
    it("drops half-typed text when Clear is clicked before it was sent", () => {
      const onChange = renderBar({
        value: { ...EMPTY_ENV_FILTERS, status: "failed" },
      });
      const input = screen.getByPlaceholderText(
        "Search by name, agent or status",
      );
      fireEvent.change(input, { target: { value: "ab" } });
      fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
      act(() => vi.advanceTimersByTime(400));
      expect(input).toHaveValue("");
      expect(onChange).toHaveBeenCalledWith(EMPTY_ENV_FILTERS);
      expect(onChange).not.toHaveBeenCalledWith(
        expect.objectContaining({ search: "ab" }),
      );
    });
  });
});
