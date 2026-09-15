import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { render } from "src/utils/test-utils";

const enqueueSnackbar = vi.fn();
const navigate = vi.fn();

vi.mock("notistack", () => ({ enqueueSnackbar: (...a) => enqueueSnackbar(...a) }));

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useNavigate: () => navigate };
});

const { default: PrebuiltEnvironmentsBrowse } = await import(
  "../PrebuiltEnvironmentsBrowse"
);

const renderBrowse = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <PrebuiltEnvironmentsBrowse />
    </QueryClientProvider>,
  );
};

const tile = (name) => screen.getByRole("button", { name: new RegExp(name) });

describe("PrebuiltEnvironmentsBrowse", () => {
  beforeEach(() => {
    enqueueSnackbar.mockReset();
    navigate.mockReset();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders agent-type category headers and tiles from the fixture", async () => {
    renderBrowse();

    expect(await screen.findByText("Customer Support Line")).toBeInTheDocument();
    ["Voice & chat", "Computer use", "Code"].forEach((group) => {
      expect(screen.getByText(group)).toBeInTheDocument();
    });
    ["Retail Banking Assistant", "Browser", "Coding", "Verilog"].forEach(
      (name) => expect(screen.getByText(name)).toBeInTheDocument(),
    );
  });

  it("reports the exact scenario count on a tile", async () => {
    renderBrowse();
    // Customer Support Line: 8 tools, 6 rules, 4 data-trap tables, Starter depth 3.
    expect(await screen.findByText(/68 scenarios/)).toBeInTheDocument();
  });

  it("filters tiles by name as the user searches", async () => {
    const user = userEvent.setup();
    renderBrowse();
    await screen.findByText("Customer Support Line");

    await user.type(
      screen.getByPlaceholderText("Search templates…"),
      "Verilog",
    );

    await waitFor(() =>
      expect(screen.queryByText("Customer Support Line")).not.toBeInTheDocument(),
    );
    expect(screen.getByText("Verilog")).toBeInTheDocument();
    expect(screen.queryByText("Voice & chat")).not.toBeInTheDocument();
    expect(screen.getByText("Code")).toBeInTheDocument();
  });

  it("stubs the adopt flow with a snackbar and does not navigate", async () => {
    const user = userEvent.setup();
    renderBrowse();
    await screen.findByText("Customer Support Line");

    await user.click(tile("Customer Support Line"));

    expect(enqueueSnackbar).toHaveBeenCalledWith(
      "Opening a prebuilt environment lands in a later phase.",
      { variant: "info" },
    );
    expect(navigate).not.toHaveBeenCalled();
  });

  it("activates a tile from the keyboard (Enter)", async () => {
    renderBrowse();
    await screen.findByText("Customer Support Line");

    const target = tile("Customer Support Line");
    expect(target).toHaveAttribute("tabindex", "0");
    fireEvent.keyDown(target, { key: "Enter" });

    expect(enqueueSnackbar).toHaveBeenCalledWith(
      "Opening a prebuilt environment lands in a later phase.",
      { variant: "info" },
    );
  });

  it("returns to the environments home from the back button", async () => {
    const user = userEvent.setup();
    renderBrowse();
    await screen.findByText("Customer Support Line");

    await user.click(
      screen.getByRole("button", { name: /Back to how you want to start/ }),
    );

    expect(navigate).toHaveBeenCalledWith(
      "/dashboard/simulate/environments",
    );
  });
});
