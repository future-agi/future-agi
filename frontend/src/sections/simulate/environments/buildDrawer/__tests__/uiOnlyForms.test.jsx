import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import BuildEnvironmentDrawer from "../BuildEnvironmentDrawer";

const create = vi.hoisted(() => vi.fn());
vi.mock("src/api/harness/harness", async (actual) => ({
  ...(await actual()),
  createHarnessJob: create,
}));

describe("Unavailable connectors", () => {
  it("marks Vapi chat unavailable without preflight or a sample build", () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <BuildEnvironmentDrawer open onClose={vi.fn()} />
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByRole("tab", { name: /Hosted platforms/ }));
    fireEvent.click(screen.getByRole("button", { name: "Connect Vapi" }));
    fireEvent.click(screen.getByRole("button", { name: /Chat/ }));
    expect(
      screen.getByRole("button", { name: "Connect and build environment" }),
    ).toBeDisabled();
    expect(
      screen.queryByRole("button", { name: "Run preflight" }),
    ).not.toBeInTheDocument();
    expect(create).not.toHaveBeenCalled();
  });
});
