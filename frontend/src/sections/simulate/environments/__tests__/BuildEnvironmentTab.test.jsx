import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { render } from "src/utils/test-utils";
import { useEnvironmentsStore, resetEnvironmentsStore } from "../store/useEnvironmentsStore";

const navigate = vi.fn();
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useNavigate: () => navigate };
});

const { default: BuildEnvironmentTab } = await import("../BuildEnvironmentTab");

const renderTab = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <BuildEnvironmentTab />
    </QueryClientProvider>,
  );
};

describe("BuildEnvironmentTab", () => {
  beforeAll(() => {
    Element.prototype.scrollIntoView = vi.fn();
  });

  beforeEach(() => {
    resetEnvironmentsStore();
    navigate.mockReset();
  });

  it("renders the two hero cards and five option cards in order", () => {
    renderTab();
    const cards = screen.getAllByRole("button");
    expect(cards).toHaveLength(7);

    [
      "Prebuilt Environments",
      "Web Environments",
      "Source repository",
      "Code upload",
      "Hosted platform",
      "MCP server",
      "Build locally",
    ].forEach((title, i) => {
      expect(within(cards[i]).getByText(title)).toBeInTheDocument();
    });
  });

  it("does not render the omitted options", () => {
    renderTab();
    expect(screen.queryByText("Running agent")).toBeNull();
    expect(screen.queryByText("Start from scratch")).toBeNull();
  });

  it("marks prebuilt, web, mcp, and local as coming soon", () => {
    renderTab();
    expect(screen.getAllByLabelText("Coming soon")).toHaveLength(4);
  });

  it("opens the source panel and ignores coming-soon picks", async () => {
    const user = userEvent.setup();
    renderTab();

    await user.click(screen.getByText("Source repository"));
    // Title now appears on both the OptionCard and the SectionCard.
    expect(screen.getAllByText("Source repository")).toHaveLength(2);
    expect(useEnvironmentsStore.getState().choice).toBe("source");

    await user.click(screen.getByText("MCP server"));
    expect(useEnvironmentsStore.getState().choice).toBe("source");
    // MCP has no panel, so its title stays only on the OptionCard.
    expect(screen.getAllByText("MCP server")).toHaveLength(1);
  });

  it("does not navigate from the Prebuilt hero while it is coming soon", async () => {
    const user = userEvent.setup();
    renderTab();

    await user.click(screen.getByText("Prebuilt Environments"));
    expect(navigate).not.toHaveBeenCalled();
  });
});
