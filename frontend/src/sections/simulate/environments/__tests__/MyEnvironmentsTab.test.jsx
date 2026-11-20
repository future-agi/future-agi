import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

const useMyEnvironments = vi.fn();
vi.mock("src/api/simulate-environments/environments", () => ({
  useMyEnvironments: (...args) => useMyEnvironments(...args),
  useDeleteEnvironment: () => ({ mutate: vi.fn() }),
}));

// The tab imports useEnvironmentsTab for the empty-state CTA; a light stub keeps
// the render focused on the list/empty/error branch.
vi.mock("../hooks/useEnvironmentsTab", () => ({
  default: () => ({ setTab: vi.fn() }),
}));

const { default: MyEnvironmentsTab } = await import("../MyEnvironmentsTab");

const renderTab = () =>
  render(
    <MemoryRouter>
      <MyEnvironmentsTab />
    </MemoryRouter>,
  );

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
});
