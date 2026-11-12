/* eslint-env node */
import assert from "node:assert/strict";
import {
  dsPalette,
  dsTypography,
  dsSize,
  dsShape,
} from "../src/theme/ds-tokens.js";

// WCAG 2 relative luminance; translucent semantic fills are composited on the
// actual page/overlay surface before contrast is measured. Disabled UI is exempt.
function rgba(value) {
  if (value.startsWith("#"))
    return [
      ...value
        .slice(1)
        .match(/../g)
        .map((n) => parseInt(n, 16)),
      1,
    ];
  const numbers = value.match(/[\d.]+/g).map(Number);
  return numbers.length === 3 ? [...numbers, 1] : numbers;
}
function composite(fg, bg) {
  const a = fg[3];
  return fg.slice(0, 3).map((v, i) => v * a + bg[i] * (1 - a));
}
function luminance(rgb) {
  const linear = rgb.slice(0, 3).map((c) => {
    const s = c / 255;
    return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  });
  return linear[0] * 0.2126 + linear[1] * 0.7152 + linear[2] * 0.0722;
}
const rows = [];
function check(mode, name, fg, bg, surface, minimum = 4.5) {
  const back = composite(rgba(bg), rgba(surface));
  const front = composite(rgba(fg), back);
  const l1 = luminance(front);
  const l2 = luminance(back);
  const ratio = (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
  rows.push({
    mode,
    name,
    ratio: Number(ratio.toFixed(3)),
    minimum,
    pass: ratio >= minimum,
  });
}
for (const mode of ["light", "dark"]) {
  const p = dsPalette(mode);
  for (const surface of ["default", "paper", "subtle", "overlay"]) {
    for (const text of ["primary", "secondary", "tertiary", "link"])
      check(
        mode,
        `${text}/${surface}`,
        p.text[text],
        p.background[surface],
        p.background.default,
      );
    for (const [role, colors] of Object.entries(p.status))
      check(
        mode,
        `${role}/${surface}`,
        colors.fg,
        colors.bg,
        p.background[surface],
      );
    check(
      mode,
      `focus/${surface}`,
      p.border.focus,
      p.background[surface],
      p.background.default,
      3,
    );
  }
  for (const [role, colors] of Object.entries(p.status))
    check(
      mode,
      `${role}/solid`,
      colors.onSolid,
      colors.solid,
      p.background.paper,
    );
  check(
    mode,
    "primary action",
    p.primary.contrastText,
    p.primary.main,
    p.background.paper,
  );
}
assert.equal(dsSize.row.default, 32);
assert.equal(dsSize.row.compact, 28);
assert.equal(dsSize.row.twoLine, 44);
assert.equal(dsShape.borderRadius, 6);
assert.equal(dsTypography.body_sm.fontSize, "0.75rem");
assert.equal(dsTypography.body_sm.lineHeight, "18px");
for (const [role, value] of Object.entries(dsTypography)) {
  if (typeof value !== "object") continue;
  assert.ok(parseFloat(value.fontSize) * 16 >= 11, `${role}: text below 11px`);
  assert.ok(value.fontWeight <= 600, `${role}: weight above 600`);
}
console.log(
  JSON.stringify(
    { checks: rows.length, failures: rows.filter((r) => !r.pass), rows },
    null,
    2,
  ),
);
assert.equal(
  rows.filter((r) => !r.pass).length,
  0,
  "Pinned DS contrast check failed; report upstream, never hand-edit the generated values",
);
