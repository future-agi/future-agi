import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { render } from "src/utils/test-utils";
import {
  useEnvironmentsStore,
  resetEnvironmentsStore,
} from "../store/useEnvironmentsStore";

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

  it("renders only the three implemented options in order", () => {
    renderTab();
    const cards = screen.getAllByRole("button");
    expect(cards).toHaveLength(3);

    ["Source repository", "Code upload", "Hosted platform"].forEach(
      (title, i) => {
        expect(within(cards[i]).getByText(title)).toBeInTheDocument();
      },
    );
  });

  it("does not render the omitted options", () => {
    renderTab();
    expect(screen.queryByText("Running agent")).toBeNull();
    expect(screen.queryByText("Start from scratch")).toBeNull();
  });

  it("hides prebuilt, web, mcp, and local entirely", () => {
    renderTab();
    for (const title of [
      "Prebuilt Environments",
      "Web Environments",
      "MCP server",
      "Build locally",
    ]) {
      expect(screen.queryByText(title)).toBeNull();
    }
    expect(screen.queryAllByLabelText("Coming soon")).toHaveLength(0);
  });

  it("opens the source panel without offering unbuilt picks", async () => {
    const user = userEvent.setup();
    renderTab();

    await user.click(screen.getByText("Source repository"));
    // Title now appears on both the OptionCard and the SectionCard.
    expect(screen.getAllByText("Source repository")).toHaveLength(2);
    expect(useEnvironmentsStore.getState().choice).toBe("source");

    expect(screen.queryByText("MCP server")).toBeNull();
    expect(useEnvironmentsStore.getState().choice).toBe("source");
  });

  it("offers no navigation to the unbuilt Prebuilt hero", () => {
    renderTab();
    expect(screen.queryByText("Prebuilt Environments")).toBeNull();
    expect(navigate).not.toHaveBeenCalled();
  });
});
