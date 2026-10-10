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
// in each context. The actual ≥4.5:1 contrast on the rendered grid background is proved
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

const chipRoot = (container) => container.querySelector(".MuiChip-root");
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

    it("UNSET on the trace list keeps text.disabled (sibling isolation, AC-12)", () => {
      const { container } = render(
        <CustomTraceRenderer {...statusParams("UNSET", "trace")} />,
        {
          theme: themeFor(mode),
        },
      );
      expect(chipColor(container)).toBe(tokens(mode).disabled);
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
    expect(chipColor(ok.container)).toBe(hexToRgb(theme.palette.green[500]));
    expect(chipColor(err.container)).toBe(hexToRgb(theme.palette.red[500]));
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
          neutralTextColor="text.secondary"
        />
      </ThemeProvider>,
    );
    expect(chipColor(container)).toBe(tokens("dark").disabled);
  });

  it("a direct StatusChip without the opt-in prop is unchanged (R4 default)", () => {
    const theme = themeFor("light");
    const { container } = render(
      <ThemeProvider theme={theme}>
        <StatusChip status="UNSET" label="UNSET" />
      </ThemeProvider>,
    );
    expect(chipColor(container)).toBe(tokens("light").disabled);
  });
});
