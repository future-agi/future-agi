const MIN_DIGITS = 7;
const MAX_DIGITS = 15;
const KEEPS_TRUNK_ZERO = new Set(["39"]);
const FIXED_NATIONAL_DIGITS = [
  ["91", 10],
  ["1", 10],
];

export function nationalDigits(dial, number) {
  const raw = String(number || "").trim();
  const local = raw.replace(/\D/g, "");
  if (raw.startsWith("+")) return local;
  const dialDigits = String(dial || "").replace(/\D/g, "");
  return local.startsWith("0") && !KEEPS_TRUNK_ZERO.has(dialDigits) ? local.slice(1) : local;
}

export function phoneNumberError(dial, number) {
  const raw = String(number || "").trim();
  if (!raw) return null;
  const typed = raw.startsWith("+");
  const dialDigits = typed ? "" : String(dial || "").replace(/\D/g, "");
  const digits = dialDigits + nationalDigits(dial, raw);
  if (!/^[1-9]/.test(digits)) return "Enter a valid country code";
  const fixed = FIXED_NATIONAL_DIGITS.find(([code]) => digits.startsWith(code));
  if (fixed) {
    const [code, length] = fixed;
    return digits.length - code.length === length ? null : `Enter exactly ${length} digits after +${code}`;
  }
  if (digits.length >= MIN_DIGITS && digits.length <= MAX_DIGITS) return null;
  if (typed) return `Enter ${MIN_DIGITS} to ${MAX_DIGITS} digits including the country code`;
  return digits.length < MIN_DIGITS
    ? `Enter at least ${MIN_DIGITS - dialDigits.length} digits`
    : `Enter at most ${MAX_DIGITS - dialDigits.length} digits`;
}

export const isValidPhoneNumber = (dial, number) =>
  Boolean(String(number || "").trim()) && phoneNumberError(dial, number) === null;
