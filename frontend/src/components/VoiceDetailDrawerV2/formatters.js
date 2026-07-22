export const fmtWpm = (n) => {
  if (n == null || !Number.isFinite(n)) return "—";
  return String(Math.round(n));
};
