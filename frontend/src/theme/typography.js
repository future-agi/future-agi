import { dsTypography } from "./ds-tokens";

export function remToPx(value) {
  return Math.round(parseFloat(value) * 16);
}
export function pxToRem(value) {
  return `${value / 16}rem`;
}
export function responsiveFontSizes({ sm, md, lg }) {
  return {
    "@media (min-width:600px)": { fontSize: pxToRem(sm) },
    "@media (min-width:900px)": { fontSize: pxToRem(md) },
    "@media (min-width:1200px)": { fontSize: pxToRem(lg) },
  };
}
export const primaryFont = dsTypography.fontFamily;
export const secondaryFont = dsTypography.fontFamily;

// Legacy variants adapt to the v0.2 roles; generated roles stay untouched.
export const typography = {
  ...dsTypography,
  overline: {
    ...dsTypography.overline,
    textTransform: /** @type {"uppercase"} */ (dsTypography.overline.textTransform),
  },
  button: {
    ...dsTypography.button,
    textTransform: /** @type {"none"} */ (dsTypography.button.textTransform),
  },
  fontSize: remToPx(dsTypography.body_sm.fontSize),
  fontSecondaryFamily: secondaryFont,
  subheader: dsTypography.label_sm,
  s1: dsTypography.body_sm,
  s1_2: dsTypography.body_md,
  s2: dsTypography.body_sm,
  s2_1: dsTypography.body_md,
  s3: dsTypography.caption,
  m1: dsTypography.title_md,
  m2: dsTypography.title_sm,
  m3: dsTypography.title_xs,
  l1: dsTypography.display,
  l2: dsTypography.title_lg,
  l3: dsTypography.title_md,
};
