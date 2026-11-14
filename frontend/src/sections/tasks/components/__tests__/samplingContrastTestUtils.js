import { createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import { shadows } from "src/theme/shadows";
import { customShadows } from "src/theme/custom-shadows";
import { componentsOverrides } from "src/theme/overrides";

export function productionTheme(mode) {
  const theme = createTheme({
    palette: palette(mode),
    typography,
    shadows: shadows(mode),
    customShadows: customShadows(mode),
    shape: { borderRadius: 8 },
  });
  theme.components = componentsOverrides(theme);
  return theme;
}
export function rgb(color) {
  if (color === "white") return [255, 255, 255];
  if (color === "black") return [0, 0, 0];
  if (color.startsWith("#"))
    return color
      .slice(1)
      .match(/../g)
      .map((x) => parseInt(x, 16));
  if (color === "transparent") return [0, 0, 0, 0];
  return color.match(/[\d.]+/g).map(Number);
}
export function blend(foreground, background) {
  const alpha = foreground[3] ?? 1;
  return background
    .slice(0, 3)
    .map((c, i) => foreground[i] * alpha + c * (1 - alpha));
}
export function contrast(foreground, background) {
  const luminance = (color) =>
    color
      .slice(0, 3)
      .map((c) => c / 255)
      .map((c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4))
      .reduce((sum, c, i) => sum + c * [0.2126, 0.7152, 0.0722][i], 0);
  const a = luminance(foreground);
  const b = luminance(background);
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}
export function effectiveContrast(element, getStyle = getComputedStyle) {
  const over = (front, back) => {
    const a = front[3] ?? 1;
    const b = back[3] ?? 1;
    const opacity = a + b * (1 - a);
    return [
      ...front
        .slice(0, 3)
        .map((c, i) =>
          opacity ? (c * a + back[i] * b * (1 - a)) / opacity : 0,
        ),
      opacity,
    ];
  };
  const glyphStyle = getStyle(element);
  let ink = glyphStyle.color;
  if (element.matches("input, textarea")) {
    // InputBase can paint a different disabled value ink from CSS `color`.
    // The CSSOM adapter also resolves the placeholder's own color/fill/opacity.
    const fill =
      glyphStyle.webkitTextFillColor ||
      glyphStyle.WebkitTextFillColor ||
      glyphStyle.getPropertyValue?.("-webkit-text-fill-color");
    if (
      fill &&
      !["currentcolor", "inherit", "unset", "initial"].includes(
        fill.toLowerCase(),
      )
    )
      ink = fill;
  }
  let foreground = rgb(ink);
  let background = [0, 0, 0, 0];
  for (let node = element; node; node = node.parentElement) {
    const style = getStyle(node);
    const surface = rgb(style.backgroundColor);
    foreground = over(foreground, surface);
    background = over(background, surface);
    const opacity = style.opacity === "" ? 1 : Number(style.opacity);
    foreground[3] *= opacity;
    background[3] *= opacity;
  }
  return contrast(
    over(foreground, [255, 255, 255]),
    over(background, [255, 255, 255]),
  );
}
