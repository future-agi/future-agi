import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import BuildEnvironmentDrawer from "../BuildEnvironmentDrawer";
import PhoneForm from "../forms/PhoneForm";

const create = vi.hoisted(() => vi.fn());
vi.mock("src/api/harness/harness", async (actual) => ({
  ...(await actual()),
  createHarnessJob: create,
}));

describe("Unavailable connectors", () => {
  it("opens only the supported Vapi platform without a simulated chat connector", () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <BuildEnvironmentDrawer open onClose={vi.fn()} />
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByRole("tab", { name: /Hosted platforms/ }));
    fireEvent.click(screen.getByRole("button", { name: "Connect Vapi" }));
    expect(screen.getByText("Vapi API key")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Chat$/ })).toBeNull();
    expect(create).not.toHaveBeenCalled();
  });

  it.each(["Outbound", "Steps"])(
    "keeps the %s phone setup unavailable",
    (mode) => {
      render(
        <QueryClientProvider client={new QueryClient()}>
          <PhoneForm />
        </QueryClientProvider>,
      );
      fireEvent.click(screen.getByRole("button", { name: new RegExp(mode) }));
      expect(
        screen.getByRole("button", { name: "Connect and build environment" }),
      ).toBeDisabled();
      expect(
        screen.queryByRole("button", { name: "Run preflight" }),
      ).toBeNull();
      expect(create).not.toHaveBeenCalled();
    },
  );
});
