import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import CssBaseline from "@mui/material/CssBaseline";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { palette } from "src/theme/palette";
import { resetEnvironmentsStore } from "../store/useEnvironmentsStore";

vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

const { default: EnvironmentsHome } = await import("../EnvironmentsHome");

const theme = createTheme({
  palette: palette("light"),
  spacing: (factor) => `${0.25 * factor}rem`,
});

let lastSearch = "";
function LocationProbe() {
  lastSearch = useLocation().search;
  return null;
}

const renderHome = (route) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <MemoryRouter initialEntries={[route]}>
      <QueryClientProvider client={client}>
        <ThemeProvider theme={theme}>
          <CssBaseline />
          <EnvironmentsHome />
          <LocationProbe />
        </ThemeProvider>
      </QueryClientProvider>
    </MemoryRouter>,
  );
};

describe("EnvironmentsHome", () => {
  beforeAll(() => {
    Element.prototype.scrollIntoView = vi.fn();
  });

  beforeEach(() => {
    resetEnvironmentsStore();
    lastSearch = "";
  });

  it("defaults to the Build environment tab with the matrix", () => {
    renderHome("/dashboard/simulate/environments");

    expect(
      screen.getByRole("tab", { name: "Build environment" }),
    ).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("Source repository")).toBeInTheDocument();
    // A table-only fixture name proves the My Environments list is absent.
    expect(screen.queryByText("Billing Chat Agent")).toBeNull();
  });

  it("shows the build matrix when ?tab=build", () => {
    renderHome("/dashboard/simulate/environments?tab=build");

    expect(
      screen.getByRole("tab", { name: "Build environment" }),
    ).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("Source repository")).toBeInTheDocument();
    // 2 hero + 5 option cards; tabs are role="tab", not button.
    expect(screen.getAllByRole("button")).toHaveLength(7);
    // "Customer Support Line" doubles as a Prebuilt hero chip, so assert a
    // table-only fixture name to prove the My Environments list is absent.
    expect(screen.queryByText("Billing Chat Agent")).toBeNull();
  });

  it("switches tabs and writes the choice to the url", async () => {
    const user = userEvent.setup();
    renderHome("/dashboard/simulate/environments");

    await user.click(screen.getByRole("tab", { name: "My Environments" }));
    expect(lastSearch).toContain("tab=my");
    expect(await screen.findByText("Billing Chat Agent")).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "Build environment" }));
    expect(lastSearch).toContain("tab=build");
    expect(screen.getByText("Source repository")).toBeInTheDocument();
  });

  it("renders the header and no scratch button", () => {
    renderHome("/dashboard/simulate/environments?tab=build");

    expect(screen.getByText("Environments")).toBeInTheDocument();
    expect(
      screen.getByText(
        "An environment is the world your agent runs in — seeded state, tools, and rules. Pick how you want to bring your agent in and we take care of the rest.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText("Start from scratch")).toBeNull();
    expect(screen.queryByLabelText("Back to environments")).toBeNull();
  });
});
