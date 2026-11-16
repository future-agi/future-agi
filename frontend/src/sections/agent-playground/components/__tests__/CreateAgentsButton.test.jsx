import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import CreateAgentsButton from "../CreateAgentsButton";
import { CONNECT_AGENT_PATH } from "../../agentsTabs";

const mockNavigate = vi.fn();
const mockCreateGraph = vi.fn();

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useNavigate: () => mockNavigate };
});

vi.mock("src/api/agent-playground/agent-playground", () => ({
  useCreateGraph: () => ({ mutate: mockCreateGraph, isPending: false }),
}));

vi.mock("src/components/svg-color", () => ({
  default: () => <span data-testid="svg-color" />,
}));

const renderButton = (props = {}) =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>
        <CreateAgentsButton {...props} />
      </MemoryRouter>
    </QueryClientProvider>,
  );

const openMenu = () =>
  fireEvent.click(screen.getByRole("button", { name: /create agents/i }));

describe("CreateAgentsButton", () => {
  beforeEach(() => {
    mockNavigate.mockReset();
    mockCreateGraph.mockReset();
  });

  it("offers Playground and Connect agents with their descriptions", () => {
    renderButton();
    openMenu();
    expect(screen.getByText("Playground")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Build and run multi-step AI agent workflows on a drag-and-drop canvas",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("Connect agents")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Connect an agent that already runs, to test it in simulations",
      ),
    ).toBeInTheDocument();
  });

  it("sends Connect agents to the environments build page", () => {
    renderButton();
    openMenu();
    fireEvent.click(screen.getByText("Connect agents"));
    expect(CONNECT_AGENT_PATH).toBe(
      "/dashboard/simulate/environments?tab=build",
    );
    expect(mockNavigate).toHaveBeenCalledWith(
      "/dashboard/simulate/environments?tab=build",
    );
    expect(mockCreateGraph).not.toHaveBeenCalled();
  });

  it("creates a playground agent from Playground", () => {
    renderButton();
    openMenu();
    fireEvent.click(screen.getByText("Playground"));
    expect(mockCreateGraph).toHaveBeenCalledTimes(1);
    expect(mockNavigate).not.toHaveBeenCalled();
  });

  it("disables only Playground when the user can't create playground agents", () => {
    renderButton({ canCreatePlayground: false });
    openMenu();
    expect(screen.getByText("Playground").closest("li")).toHaveAttribute(
      "aria-disabled",
      "true",
    );
    expect(
      screen.getByText("Connect agents").closest("li"),
    ).not.toHaveAttribute("aria-disabled", "true");
  });
});
