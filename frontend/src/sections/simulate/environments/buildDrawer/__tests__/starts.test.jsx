import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import BuildEnvironmentDrawer from "../BuildEnvironmentDrawer";

vi.mock("src/pages/dashboard/harness/DesignForm", () => ({
  default: () => <div>Server environment design form</div>,
}));

describe("Build from scratch", () => {
  it("opens the real-design form without a sample build", () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <BuildEnvironmentDrawer open onClose={vi.fn()} />
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Build from scratch" }));
    expect(
      screen.getByText("Server environment design form"),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("This setup is unavailable."),
    ).not.toBeInTheDocument();
  });
});
