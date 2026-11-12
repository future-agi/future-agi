// Amount contract for admission caps.
//
// The form accepts plain decimal USD with at most six decimal places (whole
// micro-dollars) and REJECTS anything finer instead of rounding it. The
// gateway enforces floor(limit x 1,000,000) micro-dollars computed in binary
// floating point (capMicros), so an accepted six-decimal amount can still
// enforce one micro-dollar below what was typed, and a stored sub-micro
// amount enforces as zero. effectiveMicros repeats that exact expression so
// the panel can show the threshold actually enforced.
export const MAX_USD = 1_000_000_000;
const MICROS = 1_000_000;
const PLAIN_DECIMAL = /^(\d*)(?:\.(\d*))?$/;

export const AMOUNT_CONTRACT =
  "Amounts are USD with at most six decimal places. The gateway enforces whole micro-dollars (1 micro-dollar = $0.000001) and rounds any fraction down, so this form rejects more than six decimals instead of rounding.";

export function parseUsd(raw) {
  const text = String(raw ?? "").trim();
  if (text === "")
    throw new Error(
      "Enter a limit; 0 is a hard zero-dollar cap, not unlimited.",
    );
  const match = PLAIN_DECIMAL.exec(text);
  const [, whole = "", fraction = ""] = match || [];
  if (!match || (whole === "" && fraction === ""))
    throw new Error(
      "Limits must be plain USD amounts from 0 to 1,000,000,000: digits and at most one decimal point.",
    );
  if (fraction.length > 6)
    throw new Error(
      "Use at most six decimal places (whole micro-dollars, $0.000001). The gateway rounds any fraction of a micro-dollar down, so this form does not accept one.",
    );
  const value = Number(text);
  if (!Number.isFinite(value) || value > MAX_USD)
    throw new Error("Limits must be USD from 0 to 1,000,000,000.");
  return {
    value,
    micros: Number(whole || "0") * MICROS + Number(fraction.padEnd(6, "0")),
  };
}

export function effectiveMicros(value) {
  if (
    typeof value !== "number" ||
    !Number.isFinite(value) ||
    value < 0 ||
    value > MAX_USD
  )
    return null;
  return Math.floor(value * MICROS);
}

export function formatMicros(micros) {
  const whole = Math.floor(micros / MICROS);
  const fraction = String(micros % MICROS)
    .padStart(6, "0")
    .replace(/0+$/, "");
  return `$${whole}${fraction ? `.${fraction}` : ""}`;
}

// A stored number as plain decimal text, never exponent notation.
export function usdText(value) {
  if (value === undefined || value === null) return "";
  const text = String(value);
  if (!/e/i.test(text) || typeof value !== "number" || !Number.isFinite(value))
    return text;
  return value.toFixed(20).replace(/0+$/, "").replace(/\.$/, "");
}

export function amountView(value) {
  const configured = `$${usdText(value)}`;
  const micros = effectiveMicros(value);
  if (micros === null)
    return { configured, micros: null, enforced: "", exact: false };
  const scaled = value * MICROS;
  return {
    configured,
    micros,
    enforced: formatMicros(micros),
    // Exact: the gateway enforces what was configured. Float dust of a
    // millionth of a micro-dollar is not a difference; a lost micro is.
    exact: micros === Math.round(scaled) && Math.abs(scaled - micros) < 1e-6,
  };
}

export function amountNote(value) {
  const view = amountView(value);
  if (view.micros === null)
    return "Not a valid amount: the gateway refuses this policy until it is replaced.";
  if (view.exact) return "";
  if (view.micros === 0)
    return "Below one micro-dollar: enforced as $0, so every request in this scope is refused.";
  return `Rounded down to whole micro-dollars: enforced as ${view.enforced}, the gateway floors the limit times 1,000,000.`;
}
