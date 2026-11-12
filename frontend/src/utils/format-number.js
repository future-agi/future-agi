import { formatExactMoney } from "src/sections/common/format/money";
import numeral from "numeral";

// ----------------------------------------------------------------------

export function fNumber(number) {
  return numeral(number).format();
}
export function fCurrency(number) {
  // Retain the legacy optional argument; it no longer caps monetary precision.
  return formatExactMoney(number, {
    grouping: true,
    minimumFractionDigits: 0,
    missing: number == null ? "" : "—",
  });
}

export function fUsage(value, unit) {
  const suffix = unit ? ` ${unit}` : "";
  if (value == null || value === 0) return `0${suffix}`;
  if (value >= 1e9) return `${(value / 1e9).toFixed(1)}B${suffix}`;
  if (value >= 1e6) return `${(value / 1e6).toFixed(1)}M${suffix}`;
  if (value >= 1e3) return `${(value / 1e3).toFixed(1)}K${suffix}`;
  if (Number.isInteger(value)) return `${value.toLocaleString()}${suffix}`;
  if (value < 1) {
    const order = Math.floor(Math.log10(Math.abs(value)));
    const decimals = Math.min(6, Math.max(2, -order + 1));
    return `${Number(value.toFixed(decimals))}${suffix}`;
  }
  return `${value.toFixed(1)}${suffix}`;
}

export function fPercent(number) {
  const format = number ? numeral(Number(number) / 100).format("0.0%") : "";

  return result(format, ".0");
}

export function fShortenNumber(number) {
  const format = number ? numeral(number).format("0.00a") : "";

  return result(format, ".00");
}

export function fData(number) {
  const format = number ? numeral(number).format("0.0 b") : "";

  return result(format, ".0");
}

function result(format, key = ".00") {
  const isInteger = format.includes(key);

  return isInteger ? format.replace(key, "") : format;
}
