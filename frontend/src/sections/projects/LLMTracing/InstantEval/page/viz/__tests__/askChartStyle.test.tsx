import React from "react";
import {
  afterAll,
  afterEach,
  beforeAll,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import { render, screen } from "@testing-library/react";
import {
  ThemeProvider,
  createTheme,
  getContrastRatio,
} from "@mui/material/styles";
import type { Theme, ThemeOptions } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import { AskHeatmap, AskPatterns, AskTimeline } from "../index";
import * as vizParts from "../vizParts";
import { QUESTIONS, RESOLVED, syntheticRun } from "./fixtures/syntheticRun";

// Count the contrast checks the charts make, still working them out.
vi.mock("@mui/material/styles", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@mui/material/styles")>();
  return { ...actual, getContrastRatio: vi.fn(actual.getContrastRatio) };
});
vi.mock("src/components/iconify", () => ({ default: () => null }));

beforeAll(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date(2026, 9, 15, 18, 30));
});
afterAll(() => vi.useRealTimers());
afterEach(() => vi.restoreAllMocks());

// The theme's own roles and type sizes beyond MUI's, as the charts read them.
type AppTheme = Theme & {
  palette: Theme["palette"] & { border: { selected: string } };
  typography: Theme["typography"] & { s2: { fontSize: string } };
};
const theme = createTheme({
  palette: palette("light"),
  typography,
} as ThemeOptions) as AppTheme;
const contrastRatio = vi.mocked(getContrastRatio);
const run = syntheticRun();

function renderViz(Component: React.ElementType, props = {}) {
  return render(
    <ThemeProvider theme={theme}>
      <Component
        records={run.records}
        questions={QUESTIONS}
        focus={{ configId: RESOLVED, answer: null }}
        window={run.window}
        running={false}
        onFocus={vi.fn()}
        onSelect={vi.fn()}
        onOpen={vi.fn()}
        {...props}
      />
    </ThemeProvider>,
  );
}

// Lay the charts out at `width` instead of jsdom's 640px fallback.
const atWidth = (width: number) =>
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockReturnValue({
    width,
    height: 0,
    top: 0,
    left: 0,
    right: 0,
  } as DOMRect);

it("keeps the ink helpers out of the chart component module", () => {
  expect(vizParts).not.toHaveProperty("inkOn");
  expect(vizParts).not.toHaveProperty("shownOn");
});

describe("heatmap cell text", () => {
  it("works out no ink when the cells are too small for numbers", () => {
    contrastRatio.mockClear();
    const { container } = renderViz(AskHeatmap);
    expect(
      screen.getByRole("img", { name: /^Share of “No” by day and hour/ }),
    ).toBeInTheDocument();
    // 640px leaves 15px cells: no counts, no totals, only the labels.
    const numbers = [
      ...container.querySelectorAll("svg[role='img'] text"),
    ].filter((text) => /^\d+$/.test(text.textContent ?? ""));
    expect(numbers).toHaveLength(0);
    expect(contrastRatio).not.toHaveBeenCalled();
  });

  it("works out one ink per ramp step for the cells and totals that show a number", () => {
    atWidth(1200);
    contrastRatio.mockClear();
    const { container } = renderViz(AskHeatmap);
    const svg = screen.getByRole("img", {
      name: /^Share of “No” by day and hour/,
    });
    const inks = new Set([
      theme.palette.text.primary,
      theme.palette.background.paper,
    ]);
    const numbers = [...container.querySelectorAll("svg[role='img'] text")]
      .filter((text) => /^\d+$/.test(text.textContent ?? ""))
      .filter((text) => inks.has(text.getAttribute("fill") ?? ""));
    // Counts in the cells and shares in the totals row, far more than five.
    expect(numbers.length).toBeGreaterThan(50);
    expect(svg).toHaveAccessibleName(/by day and hour/);
    // Two ratios per fill, five fills in the ramp, however many cells.
    expect(contrastRatio.mock.calls.length).toBeLessThanOrEqual(2 * 5);
  });
});

describe("timeline", () => {
  // Most of the run in one 4-hour column, so it is cut with "+N" on top.
  const burst = run.records.map((record, i) =>
    i % 3
      ? record
      : { ...record, time: new Date(2026, 9, 12, 9).toISOString() },
  );

  it("counts a cut column's hidden rows in 11px figures, centred in the badge", () => {
    const { container } = renderViz(AskTimeline, { records: burst });
    const badge = [...container.querySelectorAll("svg[role='img'] text")].find(
      (text) => /^\+\d+$/.test(text.textContent ?? ""),
    );
    if (!badge) throw new Error("no +N badge on the cut column");
    expect(badge.getAttribute("font-size")).toBe(
      String(theme.typography.s2.fontSize),
    );
    const pill = badge.previousElementSibling;
    if (!pill) throw new Error("no pill behind the +N badge");
    const top = Number(pill.getAttribute("y"));
    const height = Number(pill.getAttribute("height"));
    // Room for 11px glyphs, with the text on the pill's middle.
    expect(height).toBeGreaterThanOrEqual(14);
    expect(badge.getAttribute("dominant-baseline")).toBe("central");
    expect(Number(badge.getAttribute("y"))).toBe(top + height / 2);
  });

  it("shows a pressed answer as a neutral selection, not a purple outline", () => {
    renderViz(AskTimeline, { focus: { configId: RESOLVED, answer: "No" } });
    const pressed = screen.getByRole("button", { name: /^No \d+% · \d+$/ });
    expect(pressed).toHaveAttribute("aria-pressed", "true");
    const style = getComputedStyle(pressed);
    expect(style.borderTopColor).not.toBe(
      getComputedStyleOf(theme.palette.border.selected),
    );
    expect(style.borderTopColor).toBe(
      getComputedStyleOf(theme.palette.text.secondary),
    );
    expect(style.backgroundColor).toBe(
      getComputedStyleOf(theme.palette.action.selected),
    );
    const other = screen.getByRole("button", { name: /^Yes \d+% · \d+$/ });
    expect(other).toHaveAttribute("aria-pressed", "false");
    expect(getComputedStyle(other).borderTopColor).toBe(
      getComputedStyleOf("transparent"),
    );
  });
});

describe("the patterns' answer switch", () => {
  it("shows the pressed answer as a neutral selection, not a purple border", () => {
    renderViz(AskPatterns, { focus: { configId: RESOLVED, answer: "No" } });
    const pressed = screen.getByRole("button", { name: "No", pressed: true });
    const style = getComputedStyle(pressed);
    expect(style.borderTopColor).not.toBe(
      getComputedStyleOf(theme.palette.border.selected),
    );
    expect(style.borderTopColor).toBe(
      getComputedStyleOf(theme.palette.text.secondary),
    );
    expect(style.backgroundColor).toBe(
      getComputedStyleOf(theme.palette.action.selected),
    );
  });
});

// A colour as jsdom reports it in a computed style.
function getComputedStyleOf(color: string) {
  const probe = document.createElement("span");
  probe.style.color = color;
  document.body.appendChild(probe);
  const value = getComputedStyle(probe).color;
  probe.remove();
  return value;
}
