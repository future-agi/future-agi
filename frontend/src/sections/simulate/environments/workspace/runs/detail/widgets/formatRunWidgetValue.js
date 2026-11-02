import {
  formatValueWithConfig,
  getUnitRendering,
} from "src/sections/dashboards/widgetUtils";
import { formatSpan } from "../runHeaderFormat";

// A duration in ms the way the Analytics tab reads one: "850 ms", "56.7s",
// "2m 20s". The dashboards formatter would read 140000 ms as "140.00K ms".
function formatMs(value) {
  if (Math.abs(value) < 1000) return `${Math.round(value)} ms`;
  if (Math.abs(value) < 60000) {
    return `${Number((value / 1000).toFixed(1))}s`;
  }
  return formatSpan(value);
}

/**
 * One value on a run widget: a metric's own unit unless the axis sets one,
 * with durations in seconds and minutes rather than thousands of ms.
 */
export function formatRunWidgetValue(value, axis, unit) {
  if (value == null || !Number.isFinite(Number(value))) return "-";
  if (unit === "ms" && !axis?.unit) return formatMs(Number(value));
  return formatValueWithConfig(
    value,
    unit && !axis?.unit ? { ...axis, ...getUnitRendering(unit) } : axis,
  );
}
