import { describe, it, expect, vi } from "vitest";
import {
  createTheme,
  decomposeColor,
  getContrastRatio,
  recomposeColor,
} from "@mui/material/styles";
import { render, screen, within } from "src/utils/test-utils";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import { customShadows } from "src/theme/custom-shadows";
import { dsShape } from "src/theme/ds-tokens";
import { componentsOverrides } from "src/theme/overrides";
import AnnotatorPicker from "../annotator-picker";

vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({
    currentOrganizationId: "org-1",
  }),
}));

vi.mock("src/api/annotation-queues/annotation-queues", () => ({
  useOrgMembersInfinite: () => ({
    data: [
      { id: "user-1", name: "Alice", email: "alice@example.com" },
      { id: "user-2", name: "Bob", email: "bob@example.com" },
    ],
    fetchNextPage: vi.fn(),
    hasNextPage: false,
    isFetchingNextPage: false,
  }),
}));

vi.mock("src/components/iconify", () => ({
  default: ({ icon, ...props }) => (
    <span data-testid="iconify" data-icon={icon} {...props} />
  ),
}));

// The app's theme, built as theme/index.jsx builds it.
const appTheme = (mode) => {
  const theme = createTheme({
    palette: palette(mode),
    typography,
    customShadows: customShadows(mode),
    shape: dsShape,
  });
  theme.components = componentsOverrides(theme);
  return theme;
};

const normalize = (color) => recomposeColor(decomposeColor(color));

// Every style rule in the document whose selector matches the element.
const rulesMatching = (el) => {
  const found = [];
  const visit = (rules) => {
    for (const rule of Array.from(rules ?? [])) {
      if (rule.cssRules) visit(rule.cssRules);
      if (!rule.selectorText || !rule.style) continue;
      try {
        if (el.matches(rule.selectorText)) found.push(rule);
      } catch {
        // A selector jsdom cannot parse is not the rule under test.
      }
    }
  };
  for (const sheet of Array.from(document.styleSheets)) visit(sheet.cssRules);
  return found;
};

// Enough for the class, attribute and child selectors MUI and emotion write.
const specificity = (selector) => {
  const ids = (selector.match(/#[\w-]+/g) ?? []).length;
  const classes = (
    selector.match(/\.[\w-]+|\[[^\]]*\]|:(?!not\()[\w-]+/g) ?? []
  ).length;
  const types = (
    selector
      .replace(/\[[^\]]*\]|[#.:][\w-]+|[()]/g, " ")
      .match(/[a-z][\w-]*/gi) ?? []
  ).length;
  return ids * 10000 + classes * 100 + types;
};

// jsdom applies style rules in sheet order and ignores selector specificity.
// MUI fades a disabled label with `.x .MuiFormControlLabel-label.Mui-disabled`,
// which outranks the row's own `.x .MuiFormControlLabel-label` in a browser,
// so the colour is picked from the matching rules the way a browser picks it.
const cascadedColor = (el) => {
  let best = null;
  rulesMatching(el).forEach((rule) => {
    const color = rule.style.getPropertyValue("color");
    if (!color) return;
    const weight = Math.max(
      ...rule.selectorText
        .split(",")
        .filter((selector) => {
          try {
            return el.matches(selector);
          } catch {
            return false;
          }
        })
        .map(specificity),
    );
    if (!best || weight >= best.weight) best = { weight, color };
  });
  return best && normalize(best.color);
};

describe("AnnotatorPicker creator row", () => {
  it.each(["light", "dark"])(
    "shows (creator) and the locked Manager role at 4.5:1 or better in %s, with Manager still disabled",
    (mode) => {
      const theme = appTheme(mode);
      const { secondary } = theme.palette.text;
      expect(
        getContrastRatio(secondary, theme.palette.background.paper),
      ).toBeGreaterThanOrEqual(4.5);

      render(
        <AnnotatorPicker
          value={[]}
          onChange={vi.fn()}
          creatorId="user-1"
          isManager
        />,
        { theme },
      );

      const creatorRow = within(screen.getByTestId("annotator-row-user-1"));
      // text.disabled took both to about 2.6:1.
      expect(cascadedColor(creatorRow.getByText("(creator)"))).toBe(
        normalize(secondary),
      );
      expect(cascadedColor(creatorRow.getByText("Manager"))).toBe(
        normalize(secondary),
      );
      // Still locked: the creator always manages the queue.
      expect(creatorRow.getByLabelText("Manager")).toBeChecked();
      expect(creatorRow.getByLabelText("Manager")).toBeDisabled();
      // And it looks locked. The theme draws a checked box in its checked
      // colour even when it is disabled, the same as the editable boxes.
      const manager = creatorRow.getByLabelText("Manager");
      expect(cascadedColor(manager.closest(".MuiCheckbox-root"))).toBe(
        normalize(theme.palette.action.disabled),
      );
    },
  );
});
