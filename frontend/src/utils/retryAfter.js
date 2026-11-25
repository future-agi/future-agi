// Only delta-seconds are used in wait advice. HTTP dates, fractions, negative
// values, arrays and unsafe integers fall back to a generic wait instruction.
export function retryAfterSeconds(value) {
  if (typeof value !== "string" && typeof value !== "number") return undefined;
  const text = String(value).trim();
  if (!/^\d+$/.test(text)) return undefined;
  const seconds = Number(text);
  return Number.isSafeInteger(seconds) && seconds >= 0 ? seconds : undefined;
}
