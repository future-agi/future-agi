import { createTheme, getContrastRatio } from "@mui/material/styles";
import { expect } from "vitest";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import { shadows } from "src/theme/shadows";
import { customShadows } from "src/theme/custom-shadows";
import { componentsOverrides } from "src/theme/overrides";
import { dsShape } from "src/theme/ds-tokens";

export function evalPresentationTheme(mode) {
  const theme = createTheme({
    palette: palette(mode),
    typography,
    shadows: shadows(mode),
    customShadows: customShadows(mode),
    shape: dsShape,
  });
  theme.components = componentsOverrides(theme);
  return theme;
}

export function expectReadableSegmentedTab(tab, theme) {
  expect(tab).toHaveAttribute("aria-selected", "false");
  // jsdom resolves competing Emotion rules by insertion order, not CSS
  // specificity. Assert the actual emitted, matching selector instead of its
  // computed color; this selector beats MuiTab's :not(.Mui-selected) rule in
  // a browser regardless of which component/test inserts its styles first.
  const rules = Array.from(document.styleSheets).flatMap((sheet) =>
    Array.from(sheet.cssRules),
  );
  const rule = rules.find(
    (candidate) =>
      candidate.selectorText?.endsWith(" .MuiTab-root:not(.Mui-selected)") &&
      tab.matches(candidate.selectorText),
  );
  expect(rule).toBeDefined();
  const probe = document.createElement("span");
  probe.style.color = rule.style.color;
  const actual = probe.style.color;
  probe.style.color = theme.palette.text.secondary;
  expect(actual).toBe(probe.style.color);
  expect(
    getContrastRatio(actual, theme.palette.background.muted),
  ).toBeGreaterThanOrEqual(4.5);
}

export function expectReadableTab(tab, background, allowedTokens) {
  const actual = getComputedStyle(tab).color;
  // Normalize theme hex/rgb spellings using the same DOM implementation.
  const probe = document.createElement("span");
  document.body.appendChild(probe);
  const allowed = allowedTokens.map((token) => {
    probe.style.color = token;
    return getComputedStyle(probe).color;
  });
  probe.remove();
  expect(allowed).toContain(actual);
  expect(getContrastRatio(actual, background)).toBeGreaterThanOrEqual(4.5);
}
