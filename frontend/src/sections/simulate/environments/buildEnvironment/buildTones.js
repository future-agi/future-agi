// RETIRING. This was the build area's own hex palette. The build flow files now
// use the theme (status.<role>.*, primary.main, palette.data[i]) and no longer
// import it; the workspace files still do. Delete this module once the last
// importer is moved to a token, and remove the disable below with it.
/* eslint-disable no-restricted-syntax -- legacy palette, kept only until the remaining importers are migrated */
export const BUILD_TONES = {
  accent: "#7857FC",
  accentHover: "#6B4EE6",
  amber: "#CA8A04",
  amberDeep: "#B45309",
  red: "#DC2626",
  redHover: "#B91C1C",
  green: "#16A34A",
  teal: "#0891B2",
  blue: "#2563EB",
  sky: "#0EA5E9",
  grey: "#6B7280",
  orange: "#EA580C",
  violet: "#6D28D9",
  purple: "#9333EA",
  pink: "#DB2777",
  slate: "#525252",
  lilac: "#8B5CF6",
  rose: "#F43F5E",
  tealDeep: "#0D9488",
  fuchsia: "#C026D3",
  zinc: "#71717A",
  amberBright: "#D97706",
  ash: "#9AA0A6",
  indigo: "#4F46E5",
};
