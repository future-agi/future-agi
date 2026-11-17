import React from "react";
import PropTypes from "prop-types";
import { MemoryRouter, useLocation } from "react-router-dom";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { afterEach, describe, expect, it, vi } from "vitest";

import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";

import ProductSwitcher from "../ProductSwitcher";
import { resetHostPathForTests, rememberHostPath } from "../products";

// The capabilities answer admits no Omega product: this line has no Console.
vi.mock("src/hooks/useCapabilities", () => ({
  useCapabilities: () => ({
    data: { features: {} },
    isLoading: false,
    isError: false,
  }),
}));

function LocationProbe({ into }) {
  into.pathname = useLocation().pathname;
  return null;
}
LocationProbe.propTypes = { into: PropTypes.object.isRequired };

function renderSwitcher(path, mode = "light") {
  const location = { pathname: null };
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ThemeProvider
        theme={createTheme({ palette: palette(mode), typography })}
      >
        <MemoryRouter initialEntries={[path]}>
          <ProductSwitcher />
          <LocationProbe into={location} />
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
  return location;
}

describe("product switcher with the AI Gateway", () => {
  afterEach(() => resetHostPathForTests());

  it("is a menu even without the Omega Console", async () => {
    renderSwitcher("/dashboard/develop");
    const trigger = screen.getByRole("button", {
      name: "Switch product (current: Future AGI)",
    });
    await userEvent.click(trigger);
    const menu = screen.getByRole("menu", { name: "Products" });
    const options = within(menu).getAllByRole("menuitem");
    expect(options.map((o) => o.textContent)).toEqual([
      expect.stringContaining("Future AGI"),
      expect.stringContaining("AI Gateway"),
    ]);
    expect(within(menu).queryByText("Omega Console")).not.toBeInTheDocument();
  });

  it("opens the gateway overview when AI Gateway is chosen", async () => {
    const location = renderSwitcher("/dashboard/develop");
    await userEvent.click(
      screen.getByRole("button", { name: /Switch product/ }),
    );
    await userEvent.click(screen.getByRole("menuitem", { name: /AI Gateway/ }));
    expect(location.pathname).toBe("/dashboard/gateway");
  });

  it("names the AI Gateway as current on a gateway page and goes back", async () => {
    rememberHostPath("/dashboard/observe");
    const location = renderSwitcher("/dashboard/gateway/logs");
    const trigger = screen.getByRole("button", {
      name: "Switch product (current: AI Gateway)",
    });
    expect(within(trigger).getByText("AI Gateway")).toBeInTheDocument();
    await userEvent.click(trigger);
    await userEvent.click(screen.getByRole("menuitem", { name: /Future AGI/ }));
    expect(location.pathname).toBe("/dashboard/observe");
  });

  it("renders in dark mode", () => {
    renderSwitcher("/dashboard/gateway", "dark");
    expect(
      screen.getByRole("button", { name: /current: AI Gateway/ }),
    ).toBeInTheDocument();
  });
});
