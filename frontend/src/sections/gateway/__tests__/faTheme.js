import { createTheme } from "@mui/material/styles";

import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import { defaultProps } from "src/theme/overrides/default-props";

/**
 * The test theme with the Future AGI button defaults the app runs with. In
 * the app a Button's default colour is "inherit" (theme/overrides/
 * default-props.jsx), so a contained button is drawn black unless it names
 * color="primary"; MUI's own default would hide that. Tests that check a
 * button's colour render with this theme.
 */
export function faTestTheme(mode = "light") {
  const base = createTheme({ palette: palette(mode), typography });
  return createTheme({
    palette: palette(mode),
    typography,
    components: { MuiButton: defaultProps(base).MuiButton },
  });
}
