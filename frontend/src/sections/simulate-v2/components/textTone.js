/*
  Amber and yellow never colour words. They may mark a state on a dot, an icon,
  a bar or a border, but any text that would have picked the colour up reads
  neutral instead. Use `textTone(color)` wherever a colour that can be amber
  (a status map, a data-driven palette, a mid-band threshold) lands on text.
*/
const AMBER_HUES = new Set([
  /* eslint-disable no-restricted-syntax -- ds: data colour */
  "#CA8A04", "#D97706", "#F59E0B", "#EAB308", "#B45309",
  "#FBBF24", "#FACC15", "#FDE68A", "#FCD34D", "#FDE047",
  "#EA580C", "#F97316", "#B98A3C",
  /* eslint-enable no-restricted-syntax */
  /* Error Feed's own amber / orange / yellow tokens. */
  // eslint-disable-next-line no-restricted-syntax -- ds: data colour
  "#F5A623", "#E8A13A", "#C47D00", "#8C7A00", "#F5E65F", "#B8AC47",
  // eslint-disable-next-line no-restricted-syntax -- ds: data colour
  "#E9690C", "#F49A54", "#8C3F08",
]);

export const isAmberHue = (color) => typeof color === "string" && AMBER_HUES.has(color.toUpperCase());

export const textTone = (color, fallback = "text.primary") => (isAmberHue(color) ? fallback : color);
