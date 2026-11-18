// Bespoke "1.1k" compact count — kept local because the global fShortenNumber
// emits "1.10k" (trailing zero) for the same input.
export function formatCount(n) {
  if (n >= 1000) {
    const k = n / 1000;
    return `${k >= 10 ? Math.round(k) : k.toFixed(1).replace(/\.0$/, "")}k`;
  }
  return String(n);
}
