// MONEY-QUAL-31: one decimal-string renderer. Number conversion is allowed
// only for approximate text and legacy minimum-padding choices, never exact digits.
const DECIMAL = /^([+-]?)(?:(\d+)(?:\.(\d*))?|\.(\d+))(?:e([+-]?\d+))?$/i;

function decimal(value) {
  if (typeof value !== "string" && typeof value !== "number") return null;
  if (typeof value === "number" && !Number.isFinite(value)) return null;
  const source = String(value).trim();
  const match = DECIMAL.exec(source);
  if (!match) return null;
  const whole = match[2] ?? "0";
  const fraction = match[3] ?? match[4] ?? "";
  const digits = whole + fraction;
  const negative = match[1] === "-" && /[1-9]/.test(digits);
  const exponent = Number(match[5] ?? 0);
  const point = whole.length + exponent;
  // Extremely large exponents remain exact scientific text instead of allocating
  // an unbounded string. Ordinary API amounts are expanded, including sub-cents.
  if (!Number.isSafeInteger(point) || Math.abs(point) > 10000) {
    return /[1-9]/.test(digits)
      ? { negative, scientific: source.replace(/^[+-]/, "") }
      : { negative: false, whole: "0", fraction: "" };
  }
  let integer;
  let tail;
  if (point <= 0) {
    integer = "0";
    tail = "0".repeat(-point) + digits;
  } else if (point >= digits.length) {
    integer = digits + "0".repeat(point - digits.length);
    tail = "";
  } else {
    integer = digits.slice(0, point);
    tail = digits.slice(point);
  }
  return {
    negative,
    whole: integer.replace(/^0+(?=\d)/, ""),
    fraction: tail.replace(/0+$/, ""),
  };
}

/** Exact USD. Minimum padding is a style choice, never a maximum precision. */
export function formatExactMoney(
  value,
  {
    grouping = false,
    minimumFractionDigits = 2,
    prefix = "$",
    missing = "—",
  } = {},
) {
  const parts = decimal(value);
  if (!parts) return missing;
  const sign = parts.negative ? "-" : "";
  if (parts.scientific) return `${sign}${prefix}${parts.scientific}`;
  const whole = grouping
    ? parts.whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",")
    : parts.whole;
  const minimum = Math.max(
    0,
    Math.min(20, Math.trunc(minimumFractionDigits) || 0),
  );
  const fraction = parts.fraction.padEnd(minimum, "0");
  return `${sign}${prefix}${whole}${fraction ? `.${fraction}` : ""}`;
}

/** Only for aggregates/axes/KPIs with a separately accessible exact value. */
export function formatCompactMoney(value, options = {}) {
  const parts = decimal(value);
  if (!parts) return options.missing === undefined ? "—" : options.missing;
  const exact = formatExactMoney(value, options);
  const number = Number(value);
  const amount = Object.is(number, -0) && !parts.negative ? 0 : number;
  if (!Number.isFinite(amount)) return `≈ ${exact}`;
  const compact = new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    notation: Math.abs(amount) >= 1000 ? "compact" : "standard",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(amount);
  return `≈ ${compact.replace("$", options.prefix ?? "$")}`;
}

/** Signed exact deltas without converting a decimal string through Number. */
export function formatMoneyDelta(
  value,
  { positiveSign = "+", negativeSign = "−", zeroSign = "±", ...options } = {},
) {
  const parts = decimal(value);
  if (!parts) return options.missing === undefined ? "—" : options.missing;
  const text = formatExactMoney(value, options).replace(/^-/, "");
  const zero = !parts.scientific && !/[1-9]/.test(parts.whole + parts.fraction);
  return `${zero ? zeroSign : parts.negative ? negativeSign : positiveSign}${text}`;
}

/** Exact client-side sum of decimal API components; unknown never means zero. */
export function sumMoneyValues(values) {
  if (!Array.isArray(values)) return null;
  const parts = values.map(decimal);
  if (parts.some((p) => !p || p.scientific)) return null;
  const scale = parts.reduce((max, p) => Math.max(max, p.fraction.length), 0);
  const total = parts.reduce((sum, p) => {
    const units =
      BigInt(p.whole + p.fraction) * 10n ** BigInt(scale - p.fraction.length);
    return sum + (p.negative ? -units : units);
  }, 0n);
  return decimalText(total, scale);
}

function decimalText(units, scale) {
  const digits = (units < 0n ? -units : units)
    .toString()
    .padStart(scale + 1, "0");
  return `${units < 0n ? "-" : ""}${scale ? `${digits.slice(0, -scale)}.${digits.slice(-scale)}` : digits}`;
}

export const isMoneyValue = (value) => decimal(value) !== null;

/** Sign without losing a tiny nonzero decimal to Number underflow. */
export function moneySign(value) {
  const p = decimal(value);
  if (!p) return null;
  if (!p.scientific && !/[1-9]/.test(p.whole + p.fraction)) return 0;
  return p.negative ? -1 : 1;
}

/** Whole percentage, rounded as Math.round, using exact decimal operands.
 * The display contract is a whole percent, not a rounded USD intermediary.
 * Unknown operands, zero divisors and unexpanded exponents remain unknown.
 */
export function roundMoneyPercent(value, base) {
  const a = decimal(value);
  const b = decimal(base);
  if (!a || !b || a.scientific || b.scientific) return null;
  const numerator =
    BigInt(a.whole + a.fraction) * 100n * 10n ** BigInt(b.fraction.length);
  const denominator =
    BigInt(b.whole + b.fraction) * 10n ** BigInt(a.fraction.length);
  if (denominator === 0n) return null;
  const negative = a.negative !== b.negative;
  const whole = numerator / denominator;
  const remainder = numerator % denominator;
  // Math.round ties go toward +infinity, including negative half percentages.
  const up = negative
    ? remainder * 2n > denominator
    : remainder * 2n >= denominator;
  const rounded = whole + (up ? 1n : 0n);
  return `${negative && rounded !== 0n ? "-" : ""}${rounded}`;
}

/** Exact subtraction for displayed cost breakdowns and confidence intervals. */
export function subtractMoneyValues(left, right) {
  const p = decimal(right);
  if (!p || p.scientific) return null;
  const opposite = `${p.negative ? "" : "-"}${p.whole}.${p.fraction || "0"}`;
  return sumMoneyValues([left, opposite]);
}

/** Division by two terminates in decimal; retain every interval digit. */
export function halfMoneyValue(value) {
  const p = decimal(value);
  if (!p || p.scientific) return null;
  const units = BigInt(p.whole + p.fraction) * (p.negative ? -5n : 5n);
  return decimalText(units, p.fraction.length + 1);
}

/** Exact multiplication for unit conversions and finite decimal rate arithmetic. */
export function multiplyMoneyValues(left, right) {
  const a = decimal(left);
  const b = decimal(right);
  if (!a || !b || a.scientific || b.scientific) return null;
  const units =
    BigInt(a.whole + a.fraction) *
    BigInt(b.whole + b.fraction) *
    (a.negative !== b.negative ? -1n : 1n);
  return decimalText(units, a.fraction.length + b.fraction.length);
}
