import { alpha } from "@mui/material/styles";

// ----------------------------------------------------------------------

// SETUP COLORS

export const grey = {
  0: "#FFFFFF",
  100: "#F0F0F5",
  200: "#F1F0F5",
  300: "#E1DFEC",
  400: "#C8C4D4",
  500: "#938FA3",
  600: "#605C70",
  700: "#403D4B",
  800: "#201E25",
  900: "#161C24",
};
export const primary = {
  50: "#ECE8FF",
  lighter: "#E4DDFE",
  light: "#A792FD",
  main: "#7857FC",
  dark: "#5A41BD",
  darker: "#573FCC",
  contrastText: "#FFFFFF",
};

export const secondary = {
  lighter: "#E4D9F7",
  light: "#A983E6",
  main: "#7A40D9",
  dark: "#5B30A3",
  darker: "#311A57",
  contrastText: "#FFFFFF",
};

export const info = {
  lighter: "#D5E5FD",
  light: "#78AAFA",
  main: "#2F7CF7",
  dark: "#235DB9",
  darker: "#133263",
  contrastText: "#FFFFFF",
  primary: "#FF3EFA",
  success: "#22B3B7",
  alert: "#E8F3FE",
  icon: "#1F5695",
};

export const success = {
  lighter: "#DEF5E2",
  light: "#94DFA0",
  main: "#5ACE6D",
  dark: "#439B52",
  darker: "#24522C",
  contrastText: "#ffffff",
  alert: "#E0F7EC",
  icon: "#005F2F",
};

export const warning = {
  lighter: "#FDFADF",
  light: "#F8EF97",
  main: "#F5E65F",
  dark: "#B8AC47",
  darker: "#625C26",
  contrastText: grey[800],
  alert: "#FDEEE2",
  icon: "#8C3F08",
};

export const error = {
  lighter: "#F8D5D5",
  light: "#E87876",
  main: "#DB2F2D",
  dark: "#A42322",
  darker: "#581312",
  contrastText: "#FFFFFF",
  alert: "#FCE8E7",
  icon: "#7F1A12",
};

export const cyan = {
  main: "#32ADE6",
};

export const yellow = {
  main: "#FFCC00",
  50: "#FFF9E6",
  100: "#FFF1B8",
  200: "#FFE680",
  300: "#FFD94D",
  400: "#FFCC1A",
  500: "#E6B800",
  600: "#CCA300",
  700: "#B38F00",
  800: "#997A00",
  900: "#997A00",
  1000: "#4D3F00",
  o5: "#E6B8000D",
  o10: "#E6B8001A",
  o20: "#E6B80033",
  o30: "#E6B8004D",
};

export const amber = {
  500: "#EAB308",
  600: "#CA8A04",
  700: "#A16207",
};

export const purple = {
  50: "#ECE8FF",
  100: "#D7D0FF",
  200: "#B8AFFF",
  300: "#9A8EFF",
  400: "#856EFF",
  500: "#7857FC",
  600: "#684BE3",
  700: "#573FCC",
  800: "#4531A6",
  900: "#32247F",
  1000: "#211859",
  o5: "#7857FC0D",
  o10: "#7857FC1A",
  o20: "#7857FC33",
  o30: "#7857FC4D",
};

export const pink = {
  50: "#F9E8FD",
  100: "#F2D1FA",
  200: "#E8A9F5",
  300: "#DE82F0",
  400: "#D65EEC",
  500: "#CF6BE8",
  600: "#B857D0",
  700: "#9F46B5",
  800: "#823794",
  900: "#622871",
  1000: "#461A52",
  o5: "#CF6BE80D",
  o10: "#CF6BE81A",
  o20: "#CF6BE833",
  o30: "#CF6BE84D",
};

export const blue = {
  50: "#E8F3FE",
  100: "#CFE5FD",
  200: "#A3CDFB",
  300: "#78B4F9",
  400: "#559FF7",
  500: "#348AEF",
  600: "#2E7CD6",
  700: "#276DBD",
  800: "#1F5695",
  900: "#183E6E",
  1000: "#102848",
  o5: "#348AEF0D",
  o10: "#348AEF1A",
  o20: "#348AEF33",
  o30: "#348AEF4D",
};

export const common = {
  black: "#000000",
  white: "#FFFFFF",
};

export const action = {
  hover: alpha(grey[500], 0.08),
  selected: alpha(grey[500], 0.16),
  disabled: alpha(grey[500], 0.8),
  disabledBackground: alpha(grey[500], 0.24),
  focus: alpha(grey[500], 0.24),
  hoverOpacity: 0.08,
  disabledOpacity: 0.48,
};

export const black = {
  50: "#EBEBED",
  100: "#D1D1D1",
  200: "#B6B6B6",
  300: "#9C9C9C",
  400: "#818181",
  500: "#666666",
  600: "#4D4D4D",
  700: "#343434",
  800: "#262626",
  900: "#1F1F1F",
  1000: "#1A1A1A",
  o5: "#1A1A1A0D",
  o10: "#1A1A1A1A",
  o20: "#1A1A1A33",
  o30: "#1A1A1A4D",
};

export const whiteScale = {
  50: "#FFFFFF",
  100: "#FBFBFB",
  200: "#F8F8F8",
  300: "#F2F2F2",
  400: "#ECECEC",
  500: "#E6E6E6",
};

export const red = {
  50: "#FCE8E7",
  100: "#F9CFCD",
  200: "#F3A7A4",
  300: "#ED7E7A",
  400: "#E85858",
  500: "#D92D20",
  600: "#C0271C",
  700: "#A62218",
  800: "#7F1A12",
  900: "#59110C",
  1000: "#3A0A08",
  o5: "#D92D200D",
  o10: "#D92D201A",
  o20: "#D92D2033",
  o30: "#D92D204D",
};

export const orange = {
  50: "#FDEEE2",
  100: "#FBD8BF",
  200: "#F8B98A",
  300: "#F49A54",
  400: "#F17F2B",
  500: "#E9690C",
  600: "#CF5E0B",
  700: "#B5520A",
  800: "#8C3F08",
  900: "#642D06",
  1000: "#411D04",
  o5: "#E9690C0D",
  o10: "#E9690C1A",
  o20: "#E9690C33",
  o30: "#E9690C4D",
};

export const green = {
  50: "#E0F7EC",
  100: "#B3EAD1",
  200: "#80DBB3",
  300: "#4DCC94",
  400: "#26BD78",
  500: "#00A251",
  600: "#008F47",
  700: "#007C3E",
  800: "#005F2F",
  900: "#004421",
  1000: "#002A14",
  o5: "#00A2510D",
  o10: "#00A2511A",
  o20: "#00A25133",
  o30: "#00A2514D",
};

const base = {
  primary,
  secondary,
  info,
  success,
  warning,
  error,
  grey,
  common,
  divider: alpha(grey[500], 0.2),
  action,
  cyan,
  yellow,
  amber,
  // New Colours
  purple,
  pink,
  blue,
  black,
  whiteScale,
  red,
  green,
  orange,
};

// ----------------------------------------------------------------------

// Brand guide: Deep Space Monochrome palette
const darkSpace = {
  void: "#0a0a0a",
  nebulaDark: "#111111",
  asteroid: "#18181b",
  dust: "#1a1a1a",
  shadow: "#141417",
};

const darkText = {
  starBright: "#fafafa",
  starGlow: "#ffffff",
  moonlight: "#d4d4d8",
  distantStar: "#a1a1aa",
  faintStar: "#71717a",
  starDust: "#52525b",
};

const darkBorder = {
  default: "#27272a",
  hover: "#3f3f46",
  active: "#52525b",
  bright: "#71717a",
};

const darkHull = {
  light: "#3f3f46",
  medium: "#27272a",
  dark: "#1f1f23",
  shadow: "#18181b",
};

export { darkSpace, darkText, darkBorder, darkHull };

export function palette(mode) {
  const light = {
    ...base,
    mode: "light",
    text: {
      primary: black[1000],
      secondary: black[800],
      disabled: black[300],
      muted: black[800],
      subtitle: black[600],
    },
    background: {
      paper: "#FFFFFF",
      default: "#FBFBFB",
      neutral: "#F8F8F8",
      subtle: "#F8F8F8",
      accent: grey[100],
      // Falcon AI surfaces (inline AI bar background + AI-action hover)
      aiSurface: "#fafafe",
      aiHover: "rgba(124, 77, 255, 0.06)",
    },
    action: {
      ...base.action,
      active: grey[600],
      selected: alpha(grey[400], 0.12),
    },
    border: {
      default: grey[300],
      hover: grey[400],
      active: grey[500],
      bright: grey[600],
      light: black[100],
    },
  };

  const dark = {
    ...base,
    mode: "dark",
    // Dark mode: monochrome primary (no purple) — matching brand guide
    primary: {
      lighter: "#E4E4E7",
      light: "#FFFFFF",
      main: "#FAFAFA",
      dark: "#D4D4D8",
      darker: "#A1A1AA",
      contrastText: "#0A0A0A",
    },
    text: {
      primary: darkText.starBright,
      secondary: darkText.distantStar,
      disabled: darkText.faintStar, // #71717a — readable helper text
      muted: darkText.distantStar,
      subtitle: darkText.distantStar, // #a1a1aa — same as secondary
    },
    background: {
      paper: darkSpace.nebulaDark,
      default: darkSpace.void,
      neutral: darkSpace.asteroid,
      subtle: darkSpace.dust,
      accent: darkSpace.shadow,
      // Falcon AI surfaces (inline AI bar background + AI-action hover)
      aiSurface: "#1a1a2e",
      aiHover: "rgba(124, 77, 255, 0.12)",
    },
    divider: darkBorder.default,
    action: {
      ...base.action,
      active: darkText.distantStar,
      hover: alpha(common.white, 0.08),
      selected: alpha(common.white, 0.12),
      disabled: alpha(common.white, 0.3),
      disabledBackground: alpha(common.white, 0.12),
      focus: alpha(common.white, 0.12),
    },
    border: darkBorder,
    hull: darkHull,
    space: darkSpace,
  };

  return mode === "light" ? light : dark;
}
