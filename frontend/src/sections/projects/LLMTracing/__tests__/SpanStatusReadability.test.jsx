import React from "react";
import { createTheme, ThemeProvider } from "@mui/material/styles";
import { describe, expect, it } from "vitest";
import { render } from "src/utils/test-utils";
import { palette } from "src/theme/palette";
import CustomTraceRenderer from "../Renderers/CustomTraceRenderer";
import StatusChip from "src/components/custom-status-chip/CustomStatusChip";
import { CELL_TYPES } from "../Renderers/common";

// TH-4088 — span-list-only neutral status readability (PRD r1.1, R1/R3/R4/AC-07..AC-12).
//
// The Status column of the Observe span list rendered neutral (UNSET) text with the
// global `text.disabled` token, measured at 3.465:1 dark / 2.545:1 light against the
// composited chip background. The repair is scoped to the span list through the existing
// `context.entityType === "span"` seam in CustomTraceRenderer; shared STATUS_CONFIG,
// the trace list and every other StatusChip consumer keep their prior styling.
//
// These are jsdom token-resolution tests: they prove WHICH palette token reaches the chip
// in each context. The status is drawn by the shared design-system StatusBadge
// (a neutral state is a dot + label whose text is text.secondary by default; OK and
// ERROR are tinted badges whose text takes the status role colour), so the label
// element carries the colour, not a MUI Chip root. The actual ≥4.5:1 contrast on the rendered grid background is proved
// separately by the Chrome harness recorded on the ticket (AC-01..AC-03), not here.

const hexToRgb = (hex) => {
  const h = hex.replace("#", "");
  const n = parseInt(
    h.length === 3
      ? h
          .split("")
          .map((c) => c + c)
          .join("")
      : h,
    16,
  );
  return `rgb(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255})`;
};

const themeFor = (mode) => createTheme({ palette: palette(mode) });

const tokens = (mode) => {
  const p = palette(mode);
  return {
    disabled: hexToRgb(p.text.disabled),
    secondary: hexToRgb(p.text.secondary),
  };
};

const statusParams = (value, entityType) => ({
  colDef: {
    context: { sourceColumn: { id: CELL_TYPES.STATUS, name: "Status" } },
  },
  value,
  data: { project_id: "p1", trace_id: "t1", span_id: "s1" },
  context: { entityType, canEditTags: false },
});

// The status badge's label element: the innermost element holding the status text.
const chipRoot = (container) => {
  const labels = [...container.querySelectorAll("span")].filter(
    (el) => el.children.length === 0 && el.textContent.trim() !== "",
  );
  return labels[0] || null;
};
const chipColor = (container) =>
  window.getComputedStyle(chipRoot(container)).color;

describe("TH-4088 span-list neutral status readability", () => {
  describe.each(["dark", "light"])("%s mode", (mode) => {
    it("UNSET on the span list uses text.secondary instead of text.disabled", () => {
      const { container } = render(
        <CustomTraceRenderer {...statusParams("UNSET", "span")} />,
        {
          theme: themeFor(mode),
        },
      );
      const chip = chipRoot(container);
      expect(chip).toBeTruthy();
      expect(chip.textContent).toBe("UNSET");
      expect(chipColor(container)).toBe(tokens(mode).secondary);
    });

    it("UNSET on the trace list never gets text.disabled (sibling isolation, AC-12)", () => {
      const { container } = render(
        <CustomTraceRenderer {...statusParams("UNSET", "trace")} />,
        {
          theme: themeFor(mode),
        },
      );
      // The shared badge no longer draws neutral text with text.disabled at all, so the
      // trace list keeps the badge's own default (text.secondary), not the disabled token.
      expect(chipColor(container)).toBe(tokens(mode).secondary);
      expect(chipColor(container)).not.toBe(tokens(mode).disabled);
    });
  });

  it("OK and ERROR on the span list keep their status colors (AC-08)", () => {
    const theme = themeFor("dark");
    const ok = render(<CustomTraceRenderer {...statusParams("OK", "span")} />, {
      theme,
    });
    const err = render(
      <CustomTraceRenderer {...statusParams("ERROR", "span")} />,
      { theme },
    );
    // Tinted badge: the text colour is set on the badge root and inherited by the label.
    const badgeColor = (c) =>
      window.getComputedStyle(chipRoot(c).parentElement).color;
    expect(badgeColor(ok.container)).toBe(
      hexToRgb(theme.palette.status.success.fg),
    );
    expect(badgeColor(err.container)).toBe(
      hexToRgb(theme.palette.status.danger.fg),
    );
    expect(chipRoot(ok.container).textContent).toBe("OK");
    expect(chipRoot(err.container).textContent).toBe("ERROR");
  });

  it("a non-canonical neutral-classified value keeps its exact label and gets the readable token (AC-09)", () => {
    const theme = themeFor("dark");
    const { container } = render(
      <CustomTraceRenderer {...statusParams("MYSTERY", "span")} />,
      {
        theme,
      },
    );
    expect(chipRoot(container).textContent).toBe("MYSTERY");
    expect(chipColor(container)).toBe(tokens("dark").secondary);
  });

  it("null and empty status render no chip (AC-07)", () => {
    const theme = themeFor("dark");
    const nul = render(
      <CustomTraceRenderer {...statusParams(null, "span")} />,
      { theme },
    );
    const empty = render(
      <CustomTraceRenderer {...statusParams("", "span")} />,
      { theme },
    );
    expect(chipRoot(nul.container)).toBeNull();
    expect(chipRoot(empty.container)).toBeNull();
  });

  it("the opt-in token never applies to a disabled chip (AC-11)", () => {
    const theme = themeFor("dark");
    const { container } = render(
      <ThemeProvider theme={theme}>
        <StatusChip
          status="UNSET"
          label="UNSET"
          disabled
          // A token distinct from the badge default, so a leak would show.
          neutralTextColor="error.main"
        />
      </ThemeProvider>,
    );
    // A disabled chip is dimmed (opacity) and keeps the badge's own neutral colour.
    expect(chipColor(container)).toBe(tokens("dark").secondary);
    expect(chipColor(container)).not.toBe(hexToRgb(theme.palette.error.main));

    // Control: the same token does apply when the chip is not disabled.
    const live = render(
      <ThemeProvider theme={theme}>
        <StatusChip
          status="UNSET"
          label="UNSET"
          neutralTextColor="error.main"
        />
      </ThemeProvider>,
    );
    expect(chipColor(live.container)).toBe(hexToRgb(theme.palette.error.main));
  });

  it("a direct StatusChip without the opt-in prop is unchanged (R4 default)", () => {
    const theme = themeFor("light");
    const { container } = render(
      <ThemeProvider theme={theme}>
        <StatusChip status="UNSET" label="UNSET" />
      </ThemeProvider>,
    );
    // Without the opt-in the neutral chip keeps the badge default and never text.disabled.
    expect(chipColor(container)).toBe(tokens("light").secondary);
    expect(chipColor(container)).not.toBe(tokens("light").disabled);
  });
});
