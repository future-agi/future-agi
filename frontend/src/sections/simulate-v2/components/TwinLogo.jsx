import PropTypes from "prop-types";
import { useTheme } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import { twinIconFor } from "../_mock/twins";

/**
 * A twin service's brand mark, rendered as a bare Iconify icon with
 * the correct multi-color logo. Some brand marks are near-black
 * fills (Notion, GitHub, Linear, Stripe wordmark) and would vanish
 * on a dark UI; for those, this component swaps to a monochrome
 * variant tinted for readability on dark theme. Everything else
 * renders as its native colored mark in both themes.
 *
 * No wrapping tile, no colored box — a bare inline logo, sized by
 * the `width` prop.
 */
export default function TwinLogo({ twin, width = 16, sx }) {
  const theme = useTheme();
  const { icon, sx: modeSx } = twinIconFor(twin, theme.palette.mode);
  return (
    <Iconify
      icon={icon}
      width={width}
      sx={{
        flexShrink: 0,
        ...modeSx,
        ...sx,
      }}
    />
  );
}

TwinLogo.propTypes = {
  twin: PropTypes.object,
  width: PropTypes.number,
  sx: PropTypes.object,
};
