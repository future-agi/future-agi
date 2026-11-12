import { describe, it, expect, vi } from "vitest";
import {
  createTheme,
  decomposeColor,
  getContrastRatio,
} from "@mui/material/styles";
import { render, screen } from "src/utils/test-utils";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import { customShadows } from "src/theme/custom-shadows";
import { dsShape } from "src/theme/ds-tokens";
import { componentsOverrides } from "src/theme/overrides";
import TemplateHeroCard from "../cards/TemplateHeroCard";
import WebEnvironmentsHeroCard from "../cards/WebEnvironmentsHeroCard";

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

// What the person sees: the text's colour faded by every ancestor up to the
// card, laid over the card's own background.
const seen = (el, card, background) => {
  let alpha = 1;
  for (let node = el; node && node !== card.parentElement; ) {
    const opacity = parseFloat(getComputedStyle(node).opacity);
    if (!Number.isNaN(opacity)) alpha *= opacity;
    node = node.parentElement;
  }
  const fg = decomposeColor(getComputedStyle(el).color).values;
  const bg = decomposeColor(background).values;
  const mixed = [0, 1, 2].map((i) =>
    Math.round(alpha * fg[i] + (1 - alpha) * bg[i]),
  );
  return { alpha, color: `rgb(${mixed.join(", ")})` };
};

const HEROES = [
  ["Prebuilt Environments", TemplateHeroCard, ["· Fastest", "+ 10 more"]],
  [
    "Web Environments",
    WebEnvironmentsHeroCard,
    ["· Live SaaS sandboxes", "+ 8 more"],
  ],
];

describe.each(HEROES)("%s hero", (_title, Hero, subLabels) => {
  it.each(["light", "dark"])(
    "keeps its sub-labels at 4.5:1 or better in %s while the card stays dimmed and inert",
    (mode) => {
      const theme = appTheme(mode);
      const { paper } = theme.palette.background;

      render(<Hero onClick={vi.fn()} />, { theme });

      const card = screen.getByRole("button");
      expect(card).toHaveAttribute("aria-disabled", "true");
      subLabels.forEach((text) => {
        const { alpha, color } = seen(screen.getByText(text), card, paper);
        // Still faded with the rest of the unreleased card.
        expect(alpha).toBeLessThan(1);
        // text.subtitle under the card's dim was about 2.8:1 (light) and
        // 3.5:1 (dark).
        expect(getContrastRatio(color, paper)).toBeGreaterThanOrEqual(4.5);
      });
    },
  );
});
