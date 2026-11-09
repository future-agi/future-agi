import React from "react";
import { MemoryRouter } from "react-router-dom";
import { render, screen } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { describe, expect, it } from "vitest";

import EmptyLayout from "src/components/EmptyLayout/EmptyLayout";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";

import GatewayEmptyState from "../../components/GatewayEmptyState";
import { ACCESS } from "../catalogRows";
import ModelApi from "./ModelApi";

const theme = createTheme({ palette: palette("light"), typography });

function renderThemed(ui) {
  return render(
    <ThemeProvider theme={theme}>
      <MemoryRouter>{ui}</MemoryRouter>
    </ThemeProvider>,
  );
}

// A model no route reaches: not sold on Future AGI credits and no provider
// of the organization serves it, so the API tab has no request to copy.
const unsoldRow = {
  id: "acme/unpriced-chat",
  type: "chat",
  access: ACCESS.NONE,
  ownRoutes: [],
  accessReason: "Not available on Future AGI credits: unpriced.",
};

describe("EmptyLayout title element", () => {
  it("keeps the title a div, not a heading, when no titleComponent is given", () => {
    renderThemed(
      <EmptyLayout title="Nothing here yet" description="Add one first." />,
    );
    expect(screen.getByText("Nothing here yet", { exact: true }).tagName).toBe(
      "DIV",
    );
    expect(screen.queryByRole("heading")).not.toBeInTheDocument();
  });

  it("keeps the gateway empty state's title a div by default", () => {
    renderThemed(<GatewayEmptyState title="No keys yet" />);
    expect(screen.getByText("No keys yet", { exact: true }).tagName).toBe(
      "DIV",
    );
    expect(screen.queryByRole("heading")).not.toBeInTheDocument();
  });

  it("changes only the element, never the title's typography, for an h2", () => {
    const { unmount } = renderThemed(<EmptyLayout title="Nothing here yet" />);
    const divClass = screen.getByText("Nothing here yet").className;
    unmount();
    renderThemed(<EmptyLayout title="Nothing here yet" titleComponent="h2" />);
    const heading = screen.getByRole("heading", {
      level: 2,
      name: "Nothing here yet",
    });
    expect(heading.tagName).toBe("H2");
    expect(heading.className).toBe(divClass);
  });
});

describe("model API tab: a model with no request to copy", () => {
  it("titles its empty state as a level-2 heading, like its sold sibling", () => {
    renderThemed(
      <ModelApi row={unsoldRow} baseUrl="http://localhost:28490/v1" />,
    );
    expect(
      screen.getByRole("heading", {
        level: 2,
        name: "No request to copy for this model",
      }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "Call this model" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Go to Providers" }),
    ).toBeInTheDocument();
  });
});
