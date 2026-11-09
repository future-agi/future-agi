import { themeQuartz, iconSetMaterial } from "ag-grid-enterprise";
import { dsPalette, dsSize, dsShape, dsTypography } from "./ds-tokens";
import { remToPx } from "./typography";

// Adapted from Vel's 05a5f9f77a theme; density comes from product-design v0.2.
export function agThemeParams(mode) {
  const p = dsPalette(mode);
  return {
    accentColor: p.border.focus,
    browserColorScheme: p.mode,
    fontFamily: dsTypography.fontFamily,
    fontSize: remToPx(dsTypography.body_sm.fontSize),
    foregroundColor: p.text.primary,
    backgroundColor: p.background.paper,
    cellTextColor: p.text.secondary,
    borderColor: p.border.default,
    borderRadius: dsShape.borderRadius,
    wrapperBorderRadius: 0,
    wrapperBorder: false,
    rowBorder: { width: 1, color: p.border.default },
    headerRowBorder: { width: 1, color: p.border.default },
    columnBorder: false,
    headerColumnBorder: false,
    headerBackgroundColor: p.background.paper,
    headerTextColor: p.text.tertiary,
    headerFontSize: remToPx(dsTypography.label_md.fontSize),
    headerFontWeight: dsTypography.label_md.fontWeight,
    headerHeight: dsSize.tableHeader.default,
    rowHeight: dsSize.row.default,
    selectedRowBackgroundColor: p.action.selected,
    rowHoverColor: p.action.hover,
    checkboxUncheckedBorderColor: p.border.strong,
    checkboxBorderWidth: 1,
    checkboxCheckedBackgroundColor: p.control.checked,
    checkboxCheckedBorderColor: p.control.checked,
    checkboxCheckedShapeColor: p.control.onChecked,
    scrollBarColor: p.border.strong,
    chromeBackgroundColor: p.background.overlay,
    advancedFilterBuilderColumnPillColor: p.background.muted,
  };
}

// Keep these public preset names and per-grid override APIs. Explicit screen
// withParams/rowHeight values still win; they belong to the deferred screen pass.
export const AG_THEME_OVERRIDES = {
  noHeaderBorder: { headerColumnBorder: { width: 0 } },
  borderless: {
    columnBorder: false,
    headerColumnBorder: false,
    wrapperBorder: false,
    wrapperBorderRadius: 0,
  },
  withColumnBorder: { columnBorder: true },
  dataGrid: { columnBorder: true, headerHeight: dsSize.tableHeader.default },
  dataGridPadded: { columnBorder: true, rowHeight: dsSize.row.twoLine },
};
export function getAgTheme(mode) {
  return themeQuartz.withPart(iconSetMaterial).withParams(agThemeParams(mode));
}
export function getAgThemeWithoutGrid(mode) {
  return getAgTheme(mode).withParams({
    rowBorder: false,
    headerRowBorder: false,
    borderColor: "transparent",
  });
}
export function getAgThemePrompt(mode) {
  return themeQuartz.withParams({
    ...agThemeParams(mode),
    columnBorder: true,
    headerColumnBorder: true,
    rowHoverColor: "transparent",
  });
}
