// Providers whose logos ship as solid-black PNGs and disappear on the dark
// menu background — they need inverting to white in dark mode.
const DARK_INVERT_PROVIDERS = ["openai"];

// Returns the sx `filter` for a model provider's logo. Unknown, missing, or
// non-inverting providers get an empty object, so the logo renders untouched.
export function getProviderLogoFilterSx(providers) {
  const name = (providers ?? "").toString().toLowerCase();
  const needsInvert = DARK_INVERT_PROVIDERS.some((p) => name.includes(p));
  if (!needsInvert) return {};
  return {
    filter: (theme) =>
      theme.palette.mode === "dark" ? "brightness(0) invert(1)" : "none",
  };
}
