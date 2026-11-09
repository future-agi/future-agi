import "@mui/material/styles";
import type { customShadows } from "./custom-shadows";

type CustomShadows = ReturnType<typeof customShadows>;

declare module "@mui/material/styles" {
  interface Theme {
    customShadows: CustomShadows;
  }
  interface ThemeOptions {
    customShadows?: CustomShadows;
  }
}
