import React from "react";
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";

import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";

import { AccessCell, ModelCell, VendorCell } from "./cells";

const renderCell = (cell) =>
  render(
    <ThemeProvider
      theme={createTheme({ palette: palette("light"), typography })}
    >
      <MemoryRouter>{cell}</MemoryRouter>
    </ThemeProvider>,
  );

const ARGON = {
  id: "google/gemini-4-argon:batch",
  name: "Gemini 4 Argon (batch)",
  offered: true,
  onCredits: false,
};

// On a row with the "Not available on credits"
// badge the name shrank to "G..." beside it, and no name or id said in full
// what it was once cut to an ellipsis.
describe("catalog model cell (layout)", () => {
  it("keeps the name off the badge's line and still shows the badge", () => {
    renderCell(<ModelCell data={ARGON} />);
    const name = screen.getByRole("link", { name: ARGON.name });
    const badge = screen.getByText("Not available on credits");
    expect(badge).toBeVisible();
    expect(name.parentElement).not.toContainElement(badge);
  });

  it("gives the name and the id their full text as a title", () => {
    renderCell(<ModelCell data={ARGON} />);
    expect(screen.getByRole("link", { name: ARGON.name })).toHaveAttribute(
      "title",
      ARGON.name,
    );
    expect(screen.getByText(ARGON.id)).toHaveAttribute("title", ARGON.id);
  });

  it("titles the name when the model has no page to link to", () => {
    renderCell(<ModelCell data={{ ...ARGON, id: "no-provider-prefix" }} />);
    expect(screen.getByText(ARGON.name)).toHaveAttribute("title", ARGON.name);
  });

  it("puts a retirement note on the badge's line, not beside the name", () => {
    renderCell(
      <ModelCell data={{ ...ARGON, retirement: { date: "2026-12-01" } }} />,
    );
    const name = screen.getByRole("link", { name: ARGON.name });
    const retire = screen.getByText("Retires 2026-12-01");
    expect(name.parentElement).not.toContainElement(retire);
    expect(retire.parentElement).toContainElement(
      screen.getByText("Not available on credits"),
    );
  });

  it("titles a provider name the column cuts", () => {
    renderCell(<VendorCell data={{ vendorName: "Anthropic" }} />);
    expect(screen.getByText("Anthropic")).toHaveAttribute("title", "Anthropic");
  });
});

// "Future AGI credits · off" was cut to "Future AGI
// credits..." at every width; the label wraps to a second line instead.
describe("catalog access cell (layout)", () => {
  it("wraps the access label instead of cutting it", () => {
    renderCell(
      <AccessCell
        data={{
          access: "managed",
          managedOn: false,
          accessLabel: "Future AGI credits",
          ownRoutes: [],
        }}
      />,
    );
    const label = screen.getByText("Future AGI credits · off");
    expect(window.getComputedStyle(label).whiteSpace).toBe("normal");
  });
});
