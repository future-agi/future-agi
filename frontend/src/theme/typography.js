import { dsTypography } from "./ds-tokens";

// ----------------------------------------------------------------------

export function remToPx(value) {
  return Math.round(parseFloat(value) * 16);
}

export function pxToRem(value) {
  return `${value / 16}rem`;
}

export function responsiveFontSizes({ sm, md, lg }) {
  return {
    "@media (min-width:600px)": {
      fontSize: pxToRem(sm),
    },
    "@media (min-width:900px)": {
      fontSize: pxToRem(md),
    },
    "@media (min-width:1200px)": {
      fontSize: pxToRem(lg),
    },
  };
}

export const primaryFont =
  'Inter, ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif';
export const secondaryFont = "Inter, sans-serif";

// ----------------------------------------------------------------------

// Type comes from the Future AGI Design System (ds-tokens.js): 13px UI default,
// 14px body, 12px smallest text (11px only for the uppercase overline,
// `subheader`), weights 400/500/600 — fontWeightBold is 600, so
// anything asking for bold renders semibold. The legacy custom variants below
// are snapped onto the same scale so existing screens keep their meaning.
export const typography = {
  ...dsTypography,
  fontFamily: primaryFont,
  fontSecondaryFamily: secondaryFont,
  subheader: {
    fontSize: pxToRem(11),
    lineHeight: "16px",
    fontWeight: 500,
    letterSpacing: "0.04em",
  },
  // Interface text is 13px; 14px is only for long reading text (s1_2).
  s1: { fontSize: pxToRem(13), lineHeight: "20px" },
  s1_2: { fontSize: pxToRem(14), lineHeight: "22px" },
  s2: { fontSize: pxToRem(12), lineHeight: "16px" },
  s2_1: { fontSize: pxToRem(13), lineHeight: "20px" },
  s3: { fontSize: pxToRem(12), lineHeight: "16px" },
  // Page titles: compact, like Linear and PostHog.
  m1: { fontSize: pxToRem(16), lineHeight: "24px" },
  m2: { fontSize: pxToRem(16), lineHeight: "24px" },
  // 16px is only for drawer/dialog titles (m2); m3 was used for body and
  // card text, so it is the 14px section/body size.
  m3: { fontSize: pxToRem(14), lineHeight: "22px" },
  l1: { fontSize: pxToRem(24), lineHeight: "32px" },
  l2: { fontSize: pxToRem(24), lineHeight: "32px" },
  l3: { fontSize: pxToRem(20), lineHeight: "28px" },
};
