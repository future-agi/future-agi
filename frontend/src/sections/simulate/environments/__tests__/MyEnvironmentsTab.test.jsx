import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, useLocation, useNavigationType } from "react-router-dom";

vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

const useMyEnvironments = vi.fn();
vi.mock("src/api/simulate-environments/environments", () => ({
  useMyEnvironments: (...args) => useMyEnvironments(...args),
  useDeleteEnvironment: () => ({ mutate: vi.fn() }),
}));

vi.mock("src/api/agents/connectedAgents", () => ({
  useConnectedAgents: () => ({ data: { items: [{ id: "u1", name: "Billing" }], total: 1 } }),
}));

// The tab imports useEnvironmentsTab for the empty-state CTA; a light stub keeps
// the render focused on the list/empty/error branch.
vi.mock("../hooks/useEnvironmentsTab", () => ({
  default: () => ({ setTab: vi.fn() }),
}));

const { default: MyEnvironmentsTab } = await import("../MyEnvironmentsTab");

let location = null;
let navigationType = null;
function LocationProbe() {
  location = useLocation();
  navigationType = useNavigationType();
  return null;
}

const renderTab = (route = "/dashboard/simulate/environments") =>
  render(
    <MemoryRouter initialEntries={[route]}>
      <MyEnvironmentsTab />
      <LocationProbe />
    </MemoryRouter>,
  );

const listResult = (over = {}) => ({
  data: { rows: [], total: 0, statusCounts: { building: 0, running: 0, completed: 2, failed: 1 } },
  isLoading: false,
  isPending: false,
  isError: false,
  ...over,
});

const ROWS = Array.from({ length: 25 }, (_, i) => ({
  id: `env-${i}`,
  name: `Env ${i}`,
  status: "ready",
  agentType: "voice",
}));

const lastCall = () => useMyEnvironments.mock.calls.at(-1)[0];
const openMenu = (name) => fireEvent.mouseDown(screen.getByRole("combobox", { name }));
const pick = (name) =>
  fireEvent.click(within(screen.getByRole("listbox")).getByRole("option", { name: new RegExp(`^${name}`) }));

describe("MyEnvironmentsTab", () => {
  beforeEach(() => useMyEnvironments.mockReset());

  it("shows an error state, not the empty state, when the list request fails", () => {
    useMyEnvironments.mockReturnValue({
      data: undefined,
      isLoading: false,
      isPending: false,
      isError: true,
    });
    renderTab();
    expect(screen.getByText(/Couldn't load your environments/i)).toBeInTheDocument();
    expect(screen.queryByText(/create your first environment/i)).toBeNull();
  });

  it("shows the empty state when the list is genuinely empty", () => {
    useMyEnvironments.mockReturnValue({
      data: { rows: [], total: 0 },
      isLoading: false,
      isPending: false,
      isError: false,
    });
    renderTab();
    expect(screen.getByText(/create your first environment/i)).toBeInTheDocument();
    expect(screen.queryByText(/Couldn't load your environments/i)).toBeNull();
  });

  it("reads the filters from the page URL and sends them", () => {
    useMyEnvironments.mockReturnValue(listResult());
    renderTab("/dashboard/simulate/environments?tab=my-environments&status=failed&agent_id=u1");
    expect(lastCall().filters).toEqual({ search: "", status: "failed", agentType: "", agentId: "u1" });
    expect(screen.getByRole("combobox", { name: "Status" })).toHaveTextContent("Failed");
    expect(screen.getByRole("combobox", { name: "Agent" })).toHaveTextContent("Billing");
  });

  it("writes a filter to the URL in place, and drops it when set back to All", () => {
    useMyEnvironments.mockReturnValue(listResult({ data: { rows: ROWS, total: 25 } }));
    renderTab("/dashboard/simulate/environments?tab=my-environments");
    openMenu("Agent type");
    pick("Voice");
    expect(new URLSearchParams(location.search).get("agent_type")).toBe("voice");
    expect(navigationType).toBe("REPLACE");
    openMenu("Agent type");
    pick("All agent types");
    expect(new URLSearchParams(location.search).has("agent_type")).toBe(false);
    expect(new URLSearchParams(location.search).get("tab")).toBe("my-environments");
  });

  it("goes back to the first page when a filter changes", () => {
    useMyEnvironments.mockReturnValue(listResult({ data: { rows: ROWS, total: 60 } }));
    renderTab();
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(lastCall().page).toBe(1);
    const before = useMyEnvironments.mock.calls.length;
    openMenu("Agent type");
    pick("Chat");
    const after = useMyEnvironments.mock.calls.slice(before).map(([args]) => args);
    // No render may ask for the old page with the new filter (a 404 on a shorter result).
    expect(after.filter((a) => a.filters.agentType === "chat").every((a) => a.page === 0)).toBe(true);
    expect(lastCall().page).toBe(0);
    expect(lastCall().filters.agentType).toBe("chat");
  });

  it("says no match, not the first-run state, when filters empty the list", () => {
    useMyEnvironments.mockReturnValue(listResult());
    renderTab("/dashboard/simulate/environments?tab=my-environments&status=failed&agent_id=u1");
    expect(screen.getByText("No environments match these filters")).toBeInTheDocument();
    expect(screen.queryByText(/create your first environment/i)).toBeNull();
    fireEvent.click(screen.getAllByRole("button", { name: "Clear filters" })[0]);
    expect(location.search).toBe("?tab=my-environments");
  });

  describe("loader timing", () => {
    beforeEach(() => vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] }));
    afterEach(() => vi.useRealTimers());

    it("shows nothing, not the empty message, while a first load is still quick", () => {
      useMyEnvironments.mockReturnValue(listResult({ data: undefined, isLoading: true, isPending: true }));
      renderTab();
      expect(screen.queryByTestId("data-table-skeleton")).toBeNull();
      expect(screen.queryByText("No environments yet")).toBeNull();
      expect(screen.queryByPlaceholderText("Search by name, agent or status")).toBeNull();
    });

    it("shows skeleton rows once the first load passes the delay", () => {
      useMyEnvironments.mockReturnValue(listResult({ data: undefined, isLoading: true, isPending: true }));
      renderTab();
      act(() => vi.advanceTimersByTime(200));
      expect(screen.getByTestId("data-table-skeleton")).toBeInTheDocument();
    });

    it("keeps the current rows, without a flash, while a new filter's results are quick", () => {
      useMyEnvironments.mockReturnValue(
        listResult({ data: { rows: ROWS, total: 25 }, isPlaceholderData: true }),
      );
      renderTab("/dashboard/simulate/environments?tab=my-environments&status=failed");
      expect(screen.queryByTestId("data-table-skeleton")).toBeNull();
      act(() => vi.advanceTimersByTime(200));
      expect(screen.getByTestId("data-table-skeleton")).toBeInTheDocument();
    });
  });

  it("treats a search of only spaces as no filter", () => {
    useMyEnvironments.mockReturnValue(listResult());
    renderTab("/dashboard/simulate/environments?tab=my-environments&search=%20%20%20");
    expect(screen.getByText(/create your first environment/i)).toBeInTheDocument();
    expect(screen.queryByText("No environments match these filters")).toBeNull();
  });

  it("offers one Clear when nothing matches", () => {
    useMyEnvironments.mockReturnValue(listResult());
    renderTab("/dashboard/simulate/environments?tab=my-environments&status=failed");
    expect(screen.getByText("No environments match these filters")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Clear filters" })).toHaveLength(1);
  });
});
