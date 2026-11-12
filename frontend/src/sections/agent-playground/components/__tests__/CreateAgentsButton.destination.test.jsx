import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import CreateAgentsButton from "../CreateAgentsButton";

const mockNavigate = vi.fn();

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useNavigate: () => mockNavigate };
});

vi.mock("src/api/agent-playground/agent-playground", () => ({
  useCreateGraph: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock("src/components/svg-color", () => ({
  default: () => <span data-testid="svg-color" />,
}));

const openMenu = () => {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>
        <CreateAgentsButton />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: /create agents/i }));
};

// Connecting an agent is not on the Agents page: the choice leaves for
// Simulate > Environment, so it has to say so before the click.
describe("CreateAgentsButton names where Connect agents goes", () => {
  beforeEach(() => mockNavigate.mockReset());

  it("says Connect agents opens Simulate > Environment", () => {
    openMenu();
    const connect = screen.getByText("Connect agents").closest("li");
    expect(
      within(connect).getByText("Opens Simulate > Environment"),
    ).toBeInTheDocument();
  });

  it("names no other place for Playground, which stays on Agents", () => {
    openMenu();
    const playground = screen.getByText("Playground").closest("li");
    expect(within(playground).queryByText(/^Opens /)).toBeNull();
  });

  it("goes to the place it names", () => {
    openMenu();
    fireEvent.click(screen.getByText("Opens Simulate > Environment"));
    expect(mockNavigate).toHaveBeenCalledWith(
      "/dashboard/simulate/environments?tab=build",
    );
  });
});
