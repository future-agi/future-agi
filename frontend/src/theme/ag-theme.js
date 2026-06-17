import { themeQuartz, iconSetMaterial } from "ag-grid-enterprise";
import { darkSpace, darkText, darkBorder } from "./palette";

// ----------------------------------------------------------------------
// Light mode params
// ----------------------------------------------------------------------

const agThemeLight = {
  accentColor: "#7857FC",
  advancedFilterBuilderColumnPillColor: "#E1DFEC",
  backgroundColor: "#FFFFFF",
  borderColor: "#E6E6E6",
  borderRadius: "4px",
  rowBorder: { width: "1px" },
  headerRowBorder: { width: "1px" },
  headerColumnBorder: { width: "1px" },
  wrapperBorder: { width: "1px" },
  browserColorScheme: "light",
  cellTextColor: "#1F1F1F",
  checkboxUncheckedBorderColor: "#D1D1D1",
  checkboxBorderWidth: 1.5,
  checkboxCheckedBackgroundColor: "#9575CD",
  checkboxCheckedBorderColor: "#9575CD",
  chromeBackgroundColor: {
    ref: "foregroundColor",
    mix: 0.07,
    onto: "backgroundColor",
  },
  fontFamily: "Inter, sans-serif",
  fontSize: "12px",
  foregroundColor: "#000000",
  headerBackgroundColor: "#FBFBFB",
  headerFontSize: "14px",
  headerFontWeight: 500,
  headerTextColor: "#343434",
  columnBorder: false,
  selectedRowBackgroundColor: "#F2F2F2",
  rowHoverColor: "#F8F8F8",
  scrollBarColor: "#d1d5db",
};

const agThemeDark = {
  accentColor: darkText.starBright,
  advancedFilterBuilderColumnPillColor: darkBorder.hover,
  backgroundColor: darkSpace.void,
  borderColor: darkBorder.default,
  borderRadius: "4px",
  rowBorder: { width: "1px" },
  headerRowBorder: { width: "1px" },
  headerColumnBorder: { width: "1px" },
  wrapperBorder: { width: "1px" },
  browserColorScheme: "dark",
  cellTextColor: darkText.starBright,
  checkboxUncheckedBorderColor: "#555555",
  checkboxBorderWidth: 1,
  checkboxCheckedBackgroundColor: "#9575CD",
  checkboxCheckedBorderColor: "#9575CD",
  chromeBackgroundColor: {
    ref: "foregroundColor",
    mix: 0.07,
    onto: "backgroundColor",
  },
  fontFamily: "Inter, sans-serif",
  fontSize: "12px",
  foregroundColor: darkText.starBright,
  headerBackgroundColor: darkSpace.nebulaDark,
  headerFontSize: "14px",
  headerFontWeight: 500,
  headerTextColor: darkText.moonlight,
  columnBorder: false,
  selectedRowBackgroundColor: darkSpace.asteroid,
  rowHoverColor: darkSpace.dust,
  scrollBarColor: "rgba(255, 255, 255, 0.3)",
};

// ----------------------------------------------------------------------
// Without-grid light / dark params
// ----------------------------------------------------------------------

const agThemeWithoutGridLight = {
  accentColor: "#7857FC",
  borderColor: "transparent",
  cellTextColor: "#1F1F1F",
  checkboxUncheckedBorderColor: "#D1D1D1",
  checkboxBorderWidth: 1.5,
  checkboxCheckedBackgroundColor: "#9575CD",
  checkboxCheckedBorderColor: "#9575CD",
  fontFamily: "Inter, sans-serif",
  fontSize: "14px",
  headerBackgroundColor: "#F8F8F8",
  headerTextColor: "#343434",
  columnBorder: false,
  rowBorder: false,
  headerRowBorder: false,
  headerColumnBorder: false,
  wrapperBorder: false,
  selectCellBorder: false,
  selectedRowBackgroundColor: "#F2F2F2",
  scrollBarColor: "#d1d5db",
};

const agThemeWithoutGridDark = {
  accentColor: darkText.starBright,
  backgroundColor: darkSpace.void,
  borderColor: "transparent",
  cellTextColor: darkText.starBright,
  checkboxUncheckedBorderColor: "#555555",
  checkboxCheckedBackgroundColor: "#9575CD",
  checkboxCheckedBorderColor: "#9575CD",
  fontFamily: "Inter, sans-serif",
  fontSize: "14px",
  headerBackgroundColor: darkSpace.nebulaDark,
  headerTextColor: darkText.moonlight,
  columnBorder: false,
  rowBorder: false,
  headerRowBorder: false,
  headerColumnBorder: false,
  wrapperBorder: false,
  selectCellBorder: false,
  selectedRowBackgroundColor: darkSpace.asteroid,
  scrollBarColor: "rgba(255, 255, 255, 0.3)",
};

// ----------------------------------------------------------------------
// Prompt theme light / dark params
// ----------------------------------------------------------------------

const agThemePromptLight = {
  accentColor: "#666666",
  borderColor: "#E6E6E6",
  browserColorScheme: "light",
  cellTextColor: "#1A1A1A",
  columnBorder: true,
  fontFamily: "Inter, sans-serif",
  headerBackgroundColor: "#F8F8F8",
  headerColumnBorder: true,
  headerFontSize: 14,
  headerFontWeight: 400,
  wrapperBorderRadius: 0,
  rowHoverColor: "transparent",
  fontSize: 14,
  scrollBarColor: "#d1d5db",
};

const agThemePromptDark = {
  accentColor: darkText.faintStar,
  backgroundColor: darkSpace.nebulaDark,
  borderColor: darkBorder.default,
  browserColorScheme: "dark",
  cellTextColor: darkText.starBright,
  columnBorder: true,
  fontFamily: "Inter, sans-serif",
  foregroundColor: darkText.starBright,
  headerBackgroundColor: darkSpace.nebulaDark,
  headerColumnBorder: true,
  headerFontSize: 14,
  headerFontWeight: 400,
  headerTextColor: darkText.moonlight,
  wrapperBorderRadius: 0,
  rowHoverColor: "transparent",
  fontSize: 14,
  scrollBarColor: "rgba(255, 255, 255, 0.3)",
};

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
  withColumnBorder: { columnBorder: true },
  dataGrid: { columnBorder: true, headerHeight: "39px" },
  dataGridPadded: { columnBorder: true, rowVerticalPaddingScale: 2.6 },
};

// ----------------------------------------------------------------------
// Mode-aware factory functions
// ----------------------------------------------------------------------

export function getAgTheme(mode) {
  const params = mode === "dark" ? agThemeDark : agThemeLight;
  return themeQuartz.withPart(iconSetMaterial).withParams(params);
}

export function getAgThemeWithoutGrid(mode) {
  const params =
    mode === "dark" ? agThemeWithoutGridDark : agThemeWithoutGridLight;
  return themeQuartz.withPart(iconSetMaterial).withParams(params);
}

export function getAgThemePrompt(mode) {
  const params = mode === "dark" ? agThemePromptDark : agThemePromptLight;
  return themeQuartz.withParams(params);
}
