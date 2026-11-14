import numeral from "numeral";

// ----------------------------------------------------------------------

export function fNumber(number) {
  return numeral(number).format();
}
export function fCurrency(number, showThreeDecimals = false) {
  if (number == null) return "";
  if (typeof number === "string") number = parseFloat(number);
  if (!Number.isFinite(number) || number === 0) return "$0";

  const absNumber = Math.abs(number);
  if (absNumber < (showThreeDecimals ? 0.0000001 : 0.000001)) return "$0";

  let decimals;
  if (Number.isInteger(number)) {
    decimals = 0;
  } else if (!showThreeDecimals) {
    decimals = 2;
  } else if (absNumber >= 1) {
    decimals = 3;
  } else {
    // Capture first 3 significant digits for sub-1 values, capped at 7
    // to support micro-pricing like $0.000002/token.
    decimals = Math.min(7, -Math.floor(Math.log10(absNumber)) + 2);
  }

  let formatted = new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(number);

  if (decimals > 0) {
    formatted = formatted.replace(/(\.\d*?[1-9])0+$/g, "$1");
    formatted = formatted.replace(/\.0+$/g, "");
  }
  return formatted;
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
