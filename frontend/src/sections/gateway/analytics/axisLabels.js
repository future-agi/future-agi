// Y-axis tick labels for the Analytics charts. With little traffic a chart
// puts its ticks between whole numbers (0.4, 0.8, 1.2 ...), and rounding each
// tick would label the axis "0 0 1 1 1 2". A tick between whole counts, or
// between whole milliseconds, gets no label instead. The tolerance absorbs the
// float error in the chart's tick steps (3.0000000000000004 is 3).

export function isWholeTick(value) {
  const n = Number(value);
  return Number.isFinite(n) && Math.abs(n - Math.round(n)) < 1e-9;
}

// A count axis (requests, tokens): whole counts, then K and M.
export function countAxisLabel(value) {
  if (value == null) return "0";
  const n = Number(value);
  if (Number.isNaN(n)) return "";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return isWholeTick(n) ? String(Math.round(n)) : "";
}
