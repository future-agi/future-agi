// The one line every unbuilt part of the product carries. The part stays on
// screen, visible and disabled, and this line says why, with no promise about
// when it arrives. Features that are built but withheld by a deployment use
// their own "Not available in this deployment" wording instead.
export const NOT_IN_RELEASE = "Not available in this release";

// The chip word where the whole line cannot fit, such as three disabled tabs
// in one strip. It abbreviates the same line (not available) and, like it,
// says nothing about when; the whole line stays in the chip's tooltip and in
// what a screen reader reads.
export const NOT_IN_RELEASE_SHORT = "N/A";

// Dims every child of a disabled card except the line itself, so the line
// keeps its full contrast while the rest of the card reads as inert.
export const dimExceptLine = {
  "& > :not([data-not-in-release])": { opacity: 0.72 },
};

// Hides text visually while keeping it for screen readers, for the accessible
// description of a small disabled button whose line sits in a tooltip.
export const visuallyHidden = {
  border: 0,
  clip: "rect(0 0 0 0)",
  height: "1px",
  margin: "-1px",
  overflow: "hidden",
  padding: 0,
  position: "absolute",
  whiteSpace: "nowrap",
  width: "1px",
};
