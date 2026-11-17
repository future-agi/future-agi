// Value labels on the analytics bar charts.
//
// ApexCharts drew a bar's value in white over the bar, centred on it: on a
// short bar it ran onto the white plot and read "$0.0", or nothing, in light
// mode. A first fix gave each value a pill (ApexCharts' label background),
// which reads in Chromium; Firefox draws no pill (dataLabelsBackground needs
// a non-zero getBBox, which Firefox reads as 0 there), so the text kept the
// pill's paper colour, white on white, and a short bar's label, centred on
// its end, ran into the axis labels ("0066").
//
// A value is now the bar's total label: each chart is a stack of one series,
// and ApexCharts writes a stack's total in its own colour (text.primary),
// anchored at the bar's end and offset past it, with no background and no
// clamp that pulls it back onto the bar. The plot keeps VALUE_LABEL_ROOM on
// its right, so the longest bar's label is never cut by the chart's edge.
// The per-bar labels are off.

// The widest value ("≥ $1,234.56", "<$0.0001", "No price": about 70 px at
// 11 px 600) and its offset.
export const VALUE_LABEL_ROOM = 80;

/** Spread into a horizontal bar chart's options: its values past each bar. */
export function barValueOptions(theme, formatter) {
  return {
    stacked: true,
    dataLabels: { enabled: false },
    total: {
      enabled: true,
      formatter,
      offsetX: 6,
      offsetY: 0,
      style: {
        color: theme.palette.text.primary,
        fontSize: "11px",
        fontFamily: theme.typography.fontFamily,
        fontWeight: 600,
      },
    },
    gridPadding: { right: VALUE_LABEL_ROOM },
  };
}
