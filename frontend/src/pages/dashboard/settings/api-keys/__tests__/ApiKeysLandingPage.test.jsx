// Settings > API keys at phone width: the header row (description, search,
// Add API Key) wraps instead of forcing a sideways-scrolling box. The verifier
// walk on 007d90498 measured "Add API Key" 50 px past a 390 px viewport; the
// layout itself is proven by the browser walk, this pins the CSS that makes it.
import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";

import ApiKeysLandingPage from "../ApiKeysLandingPage";

vi.mock("ag-grid-react", () => ({
  AgGridReact: () => <div data-testid="keys-grid" />,
}));
vi.mock("src/hooks/use-ag-theme", () => ({ useAgThemeWith: () => ({}) }));
vi.mock("src/theme/ag-theme", () => ({
  AG_THEME_OVERRIDES: { withColumnBorder: {} },
}));
vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(() => new Promise(() => {})) },
  endpoints: { keys: { getKeys: "/keys/" } },
}));
vi.mock("../CreateApiKey", () => ({ default: () => null }));
vi.mock("../CellRenderer", () => ({ default: () => null }));
vi.mock("src/components/image", () => ({ default: () => null }));
vi.mock("src/components/iconify", () => ({ default: () => null }));

describe("Settings > API keys header at phone width", () => {
  it("wraps the description and the controls instead of overflowing", () => {
    render(<ApiKeysLandingPage />);
    const header = screen.getByTestId("api-keys-header");
    expect(getComputedStyle(header).flexWrap).toBe("wrap");

    const controls = screen.getByTestId("api-keys-controls");
    expect(header).toContainElement(controls);
    expect(getComputedStyle(controls).flexWrap).toBe("wrap");
  });

  it("lets the search field shrink and keeps Add API Key whole", () => {
    render(<ApiKeysLandingPage />);
    const search = screen
      .getByPlaceholderText("Search")
      .closest(".MuiTextField-root");
    // A 250 px floor next to the button is what pushed it off a phone screen.
    expect(getComputedStyle(search).minWidth).not.toBe("250px");
    expect(getComputedStyle(search).flexShrink).toBe("1");

    const add = screen.getByRole("button", { name: "Add API Key" });
    expect(getComputedStyle(add).flexShrink).toBe("0");
    expect(getComputedStyle(add).whiteSpace).toBe("nowrap");
  });
});
