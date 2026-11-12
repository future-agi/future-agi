import React from "react";
import { createTheme, ThemeProvider } from "@mui/material/styles";
import { describe, expect, it } from "vitest";
import { render } from "src/utils/test-utils";
import { palette } from "src/theme/palette";
import CustomTraceRenderer from "../Renderers/CustomTraceRenderer";
import StatusChip from "src/components/custom-status-chip/CustomStatusChip";
import { CELL_TYPES } from "../Renderers/common";
import traceFilterPanelSource from "../TraceFilterPanel.jsx?raw";
import ts from "typescript";

// TH-4088 — span-list-only neutral status readability (PRD r1.1, R1/R3/R4/AC-07..AC-12).
//
// The Status column of the Observe span list rendered neutral (UNSET) text with the
// global `text.disabled` token, measured at 3.465:1 dark / 2.545:1 light against the
// composited chip background. The repair is scoped to the span list through the existing
// `context.entityType === "span"` seam in CustomTraceRenderer; shared STATUS_CONFIG,
// the trace list and other consumers use the shared default. The target design-system
// contract uses text.secondary for neutral labels and status.success/danger for
// OK/ERROR. Keep the opt-in seam, exact labels, null handling and disabled behavior.
//
// Token source: src/theme/palette, palette(mode).text.secondary; do not assume
// status.neutral.fg is an alias. Contract evidence: the pinned dev 13b60b8954
// version of this test, lines 18-23, 90-93 and 182-184. The absorb dispatch's
// decisions.DS_version selects dev's DS v0.2.3; assembled-foundation execution
// remains a separate check because its implementation is not bound here.
// These are jsdom token-resolution tests, not rendered contrast measurements.
// Status role colors remain asserted on both tinted badge roots and their labels.
// Rendered-grid contrast remains separate evidence (AC-01..AC-03), not proved here.

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
// The label declares `color: inherit` and the badge root sets the token
// (StatusBadge). jsdom returns the keyword itself, so follow an explicit
// `inherit` up to the element that declares the colour.
const declaredColor = (el) => {
  let node = el;
  let color = window.getComputedStyle(node).color;
  while (color === "inherit" && node.parentElement) {
    node = node.parentElement;
    color = window.getComputedStyle(node).color;
  }
  return color;
};
const chipColor = (container) => declaredColor(chipRoot(container));

// Raw source keeps the duplicate-binding regression runnable even when the JSX
// module cannot be parsed; only its parameter list is parsed, never executed.
describe("FE-OBSERVE absorb placeholder regressions", () => {
  it("declares aiPlaceholder once in a parseable parameter list", () => {
    const parameters = traceFilterPanelSource.match(
      /const TraceFilterPanel = \(\{([\s\S]*?)\}\) => \{/,
    );
    expect(parameters).not.toBeNull();
    expect(
      parameters[1].match(/^\s*aiPlaceholder(?:\s*=|\s*,)/gm),
    ).toHaveLength(1);
    const arrow = `({${parameters[1]}}) => {};`;
    const { diagnostics } = ts.transpileModule(arrow, {
      reportDiagnostics: true,
      fileName: "TraceFilterPanelParameters.jsx",
      compilerOptions: { jsx: ts.JsxEmit.Preserve },
    });
    expect(diagnostics ?? []).toHaveLength(0);
    const parsed = ts.createSourceFile(
      "TraceFilterPanelParameters.jsx",
      arrow,
      ts.ScriptTarget.Latest,
      true,
      ts.ScriptKind.JSX,
    );
    const bindings =
      parsed.statements[0].expression.parameters[0].name.elements;
    const names = bindings.map((element) => element.name.getText(parsed));
    expect(new Set(names).size).toBe(names.length);
  });

  it("declares aiPlaceholder PropTypes once without changing its type", () => {
    const propTypes = traceFilterPanelSource.match(
      /TraceFilterPanel\.propTypes = \{([\s\S]*?)\n\};/,
    );
    expect(propTypes).not.toBeNull();
    expect(propTypes[1].match(/^\s*aiPlaceholder:/gm)).toHaveLength(1);
    expect(propTypes[1]).toContain("aiPlaceholder: PropTypes.string,");
  });
});

describe("TH-4088 span-list neutral status readability", () => {
  describe.each(["dark", "light"])("%s mode", (mode) => {
    it("neutral labels use text.secondary even when the neutral role differs", () => {
      const theme = createTheme(themeFor(mode), {
        palette: { status: { neutral: { fg: "#123456" } } },
      });
      expect(theme.palette.status.neutral.fg).not.toBe(
        theme.palette.text.secondary,
      );
      const trace = render(
        <CustomTraceRenderer {...statusParams("UNSET", "trace")} />,
        { theme },
      );
      const direct = render(<StatusChip status="UNSET" label="UNSET" />, {
        theme,
      });
      expect(chipColor(trace.container)).toBe(
        hexToRgb(theme.palette.text.secondary),
      );
      expect(chipColor(direct.container)).toBe(
        hexToRgb(theme.palette.text.secondary),
      );
      expect(chipColor(trace.container)).not.toBe(
        hexToRgb(theme.palette.status.neutral.fg),
      );
      expect(chipColor(direct.container)).not.toBe(
        hexToRgb(theme.palette.status.neutral.fg),
      );
    });

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

    it("UNSET on the trace list uses text.secondary and never gets text.disabled (sibling isolation, AC-12)", () => {
      const { container } = render(
        <CustomTraceRenderer {...statusParams("UNSET", "trace")} />,
        {
          theme: themeFor(mode),
        },
      );
      // The shared neutral label uses the DS badge default, not its role color.
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
    expect(chipColor(ok.container)).toBe(
      hexToRgb(theme.palette.status.success.fg),
    );
    expect(chipColor(err.container)).toBe(
      hexToRgb(theme.palette.status.danger.fg),
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

  it.each(["light", "dark"])(
    "a caller override is isolated to neutral chips in %s",
    (mode) => {
      const theme = themeFor(mode);
      const neutral = render(
        <StatusChip
          status="UNSET"
          label="UNSET"
          neutralTextColor="text.primary"
        />,
        { theme },
      );
      const success = render(
        <StatusChip
          status="pass"
          label="Passed"
          neutralTextColor="text.primary"
        />,
        { theme },
      );
      expect(chipColor(neutral.container)).toBe(
        hexToRgb(theme.palette.text.primary),
      );
      expect(chipColor(neutral.container)).not.toBe(tokens(mode).secondary);
      expect(chipColor(success.container)).toBe(
        hexToRgb(theme.palette.status.success.fg),
      );
    },
  );

  it("a direct StatusChip without an override uses text.secondary", () => {
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
