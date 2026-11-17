// Chart and series colours from the design-system data palette
// (theme.palette.data, 8 colours, light and dark). Use these instead of a
// hand-written colour list so every chart matches and dark mode works.

export const seriesColors = (theme) => theme.palette.data;

/** The colour for series `index`, wrapping after the palette's length. */
export const seriesColor = (theme, index) => {
  const palette = seriesColors(theme);
  return palette[((index % palette.length) + palette.length) % palette.length];
};

/** `count` colours for a chart's `colors` option, wrapping as needed. */
export const seriesColorList = (theme, count) =>
  Array.from({ length: count }, (_, i) => seriesColor(theme, i));

// Axis labels and grid lines for ApexCharts, from the theme instead of
// `isDark ? hex : undefined`.
export const chartAxisColors = (theme) => ({
  foreColor: theme.palette.text.secondary,
  gridColor: theme.palette.divider,
  surface: theme.palette.background.paper,
});
