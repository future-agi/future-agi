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

  it("hides the build panel until a template is selected", async () => {
    renderBrowse();
    await screen.findByText("Customer Support Line");

    expect(screen.queryByRole("tab", { name: /Build here/ })).not.toBeInTheDocument();
  });

  it("opens the inline build panel for the clicked row without navigating", async () => {
    const user = userEvent.setup();
    renderBrowse();
    await screen.findByText("Customer Support Line");

    await user.click(tile("Customer Support Line"));

    expect(screen.getByRole("tab", { name: /Build here/ })).toBeInTheDocument();
    expect(screen.getByText("Build in the cloud")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Build environment/ }),
    ).toBeInTheDocument();
    expect(navigate).not.toHaveBeenCalled();
    expect(enqueueSnackbar).not.toHaveBeenCalled();
  });

  it("selects a row from the keyboard (Enter)", async () => {
    renderBrowse();
    await screen.findByText("Customer Support Line");

    const target = tile("Customer Support Line");
    expect(target).toHaveAttribute("tabindex", "0");
    fireEvent.keyDown(target, { key: "Enter" });

    expect(await screen.findByRole("tab", { name: /Build here/ })).toBeInTheDocument();
    expect(navigate).not.toHaveBeenCalled();
  });

  it("collapses the selection when search filters it out", async () => {
    const user = userEvent.setup();
    renderBrowse();
    await screen.findByText("Customer Support Line");

    await user.click(tile("Customer Support Line"));
    expect(screen.getByRole("tab", { name: /Build here/ })).toBeInTheDocument();

    await user.type(
      screen.getByPlaceholderText("Search templates…"),
      "Verilog",
    );

    await waitFor(() =>
      expect(screen.queryByRole("tab", { name: /Build here/ })).not.toBeInTheDocument(),
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
