import React from "react";
import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";

import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";

import { DEMO_CATALOG, DEMO_PROVIDERS } from "../test-fixtures/demoCatalog";
import { buildCatalogRows } from "../catalogRows";
import { AccessCell, ModelCell, VendorCell } from "./cells";

const rowFor = (id) =>
  buildCatalogRows(DEMO_CATALOG.models, {
    providers: DEMO_PROVIDERS.providers,
    managedStatus: { available: true, enabled: true, markup_percent: 5 },
  }).find((r) => r.id === id);

const renderCell = (cell) =>
  render(
    <ThemeProvider
      theme={createTheme({ palette: palette("light"), typography })}
    >
      <MemoryRouter>{cell}</MemoryRouter>
    </ThemeProvider>,
  );

describe("catalog cells", () => {
  it("S032 describes the off state without an unavailable activation instruction", async () => {
    const row = buildCatalogRows(DEMO_CATALOG.models, {
      providers: [],
      managedStatus: { available: true, enabled: false, markup_percent: 5 },
    }).find((item) => item.id === "openai/gpt-4o");
    renderCell(<AccessCell data={row} />);
    fireEvent.mouseOver(screen.getByText("Future AGI credits · off"));
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      /^Future AGI credits are off for your organization\.$/,
    );
  });

  // A keyboard user tabs to the name and presses Enter; anyone can open the
  // model page in a new tab.
  it("makes the model name a link to its model page", () => {
    renderCell(<ModelCell data={rowFor("cohere/north-mini-code:free")} />);
    expect(
      screen.getByRole("link", { name: /North Mini Code/ }),
    ).toHaveAttribute(
      "href",
      "/dashboard/gateway/models/cohere/north-mini-code%3Afree",
    );
  });

  // One cell type size: only the model name (the row's title) is larger.
  it("sets the access cell in the table's 12 px text, like the other cells", () => {
    renderCell(<AccessCell data={rowFor("openai/gpt-4o")} />);
    expect(screen.getByText("Future AGI credits + own key")).toHaveStyle({
      fontSize: "0.75rem",
    });
  });
});

describe("catalog cells: access and provider", () => {
  // "Not callable" is a real value, not a disabled control.
  it("writes Not callable in secondary text, not disabled grey", () => {
    renderCell(<AccessCell data={rowFor("black-forest-labs/flux-3-image")} />);
    expect(screen.getByText("Not callable")).toHaveStyle({
      color: palette("light").text.secondary,
    });
  });

  const LOGO =
    "https://fi-image-assets.s3.ap-south-1.amazonaws.com/provider-logos/claude-ai-icon.png";

  it("draws the provider's logo before its name", () => {
    const { container } = renderCell(
      <VendorCell
        data={{
          ...rowFor("anthropic/claude-haiku-5.5"),
          vendorLogo: LOGO,
        }}
      />,
    );
    expect(screen.getByText("Anthropic")).toBeInTheDocument();
    const img = container.querySelector("img");
    expect(img).toHaveAttribute("src", LOGO);
    expect(img).toHaveAttribute("width", "16");
  });

  it("falls back to the provider's initial when there is no logo, or it fails to load", () => {
    const { container, rerender } = renderCell(
      <VendorCell data={rowFor("cohere/north-mini-code:free")} />,
    );
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText("C")).toBeInTheDocument();
    rerender(
      <ThemeProvider
        theme={createTheme({ palette: palette("light"), typography })}
      >
        <MemoryRouter>
          <VendorCell
            data={{
              ...rowFor("anthropic/claude-haiku-5.5"),
              vendorLogo: LOGO,
            }}
          />
        </MemoryRouter>
      </ThemeProvider>,
    );
    fireEvent.error(container.querySelector("img"));
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText("A")).toBeInTheDocument();
  });
});
