// Designer literals (prototype f9201bb4d). Kept as hex on purpose: dark-mode
// primary is monochrome (theme/palette.js:428) and the accent tokens are
// lifted in dark, so tokens would change pixels. This is the ONLY build module
// allowed to hold hex colour literals — every other module imports from here.
export const BUILD_TONES = {
  accent: "#7857FC",
  accentHover: "#6B4EE6",
  amber: "#CA8A04",
  amberDeep: "#B45309",
  red: "#DC2626",
  redHover: "#B91C1C",
  green: "#16A34A",
  teal: "#0891B2",
  blue: "#2563EB",
  sky: "#0EA5E9",
};

// DerivingAnimation file-header traffic dots (red / amber / green).
export const TRAFFIC_LIGHTS = ["#EF4444", "#F59E0B", "#10B981"];

// DerivingAnimation stage surfaces — the mock IDE panel that plays behind the
// deriving copy. Dark/light split; sandboxDark is the inner code frame.
export const SURFACE_INK = {
  dark: "#0B0B0B",
  light: "#FAFAFA",
  panelDark: "#131313",
  panelLight: "#FFFFFF",
  sandboxDark: "#111111",
};
