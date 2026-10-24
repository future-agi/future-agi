import { themeQuartz, iconSetMaterial } from "ag-grid-enterprise";
import { dsPalette } from "./ds-tokens";

// ----------------------------------------------------------------------
// AG Grid params, derived from the Future AGI Design System palette so grids
// match MUI tables: no outer box, 13px cells, 12px/500 headers in fg.tertiary
// with no fill, border.default row lines, bg.hover / bg.selected rows,
// control.checked boxes.
// ----------------------------------------------------------------------

const FONT = "Inter, ui-sans-serif, system-ui, sans-serif";

function core(mode) {
  const p = dsPalette(mode);
  const dark = mode === "dark";
  return {
    p,
    base: {
      accentColor: p.border.focus,
      browserColorScheme: dark ? "dark" : "light",
      fontFamily: FONT,
      fontSize: "13px",
      foregroundColor: p.text.primary,
      backgroundColor: p.background.paper,
      cellTextColor: p.text.secondary,
      borderColor: p.border.subtle,
      headerBackgroundColor: p.background.paper,
      headerTextColor: p.text.subtitle,
      headerFontSize: "12px",
      headerFontWeight: 500,
      selectedRowBackgroundColor: p.action.selected,
      rowHoverColor: p.action.hover,
      checkboxUncheckedBorderColor: p.border.strong,
      checkboxBorderWidth: 1,
      checkboxCheckedBackgroundColor: p.control.checked,
      checkboxCheckedBorderColor: p.control.checked,
      checkboxCheckedShapeColor: p.control.onChecked,
      scrollBarColor: dark ? "rgba(255, 255, 255, 0.24)" : "#D4D4D8",
      columnBorder: false,
    },
  };
}

function gridParams(mode) {
  const { p, base } = core(mode);
  return {
    ...base,
    advancedFilterBuilderColumnPillColor: p.background.muted,
    borderRadius: "6px",
    wrapperBorderRadius: 0,
    rowBorder: { width: "1px", color: p.border.default },
    headerRowBorder: { width: "1px", color: p.border.default },
    headerColumnBorder: false,
    wrapperBorder: false,
    chromeBackgroundColor: p.background.paper,
    headerHeight: 32,
    rowHeight: 36,
  };
}

function withoutGridParams(mode) {
  const { p, base } = core(mode);
  return {
    ...base,
    borderColor: "transparent",
    rowBorder: false,
    headerRowBorder: false,
    headerColumnBorder: false,
    wrapperBorder: false,
    selectCellBorder: false,
  };
}

function promptParams(mode) {
  const { p, base } = core(mode);
  return {
    ...base,
    accentColor: p.text.subtitle,
    borderColor: p.divider,
    columnBorder: true,
    headerColumnBorder: true,
    headerFontSize: 12,
    headerFontWeight: 500,
    wrapperBorderRadius: 0,
    rowHoverColor: "transparent",
    fontSize: 13,
  };
}

// ----------------------------------------------------------------------
// Reusable theme override presets (stable references for useMemo deps)
// ----------------------------------------------------------------------

export const AG_THEME_OVERRIDES = {
  noHeaderBorder: { headerColumnBorder: { width: "0px" } },
  borderless: {
    columnBorder: false,
    headerColumnBorder: { width: 0 },
    wrapperBorder: { width: 0 },
    wrapperBorderRadius: 0,
  },
  withColumnBorder: { columnBorder: true, headerColumnBorder: true },
  dataGrid: { columnBorder: true, headerColumnBorder: true, headerHeight: 32 },
  dataGridPadded: {
    columnBorder: true,
    headerColumnBorder: true,
    rowVerticalPaddingScale: 2.6,
  },
};

// ----------------------------------------------------------------------
// Mode-aware factory functions
// ----------------------------------------------------------------------

export function getAgTheme(mode) {
  return themeQuartz.withPart(iconSetMaterial).withParams(gridParams(mode));
}

export function getAgThemeWithoutGrid(mode) {
  return themeQuartz
    .withPart(iconSetMaterial)
    .withParams(withoutGridParams(mode));
}

export function getAgThemePrompt(mode) {
  return themeQuartz.withParams(promptParams(mode));
}
