import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Iconify from "src/components/iconify";
import { getSurface } from "src/api/simulate-environments/_fixtures/surfaces";

// A rounded tile carrying the channel's icon. The designer's neutral treatment
// is tinted here so the surface tone reads at a glance; the icon alone still
// names the channel. Icon-only, so the tile carries an aria-label.
export default function SurfaceIcon({ surface, size = 40, radius = 1.25 }) {
  const s = getSurface(surface);
  return (
    <Box
      role="img"
      aria-label={s.label}
      sx={{
        width: size,
        height: size,
        borderRadius: radius,
        flexShrink: 0,
        display: "grid",
        placeItems: "center",
        color: s.color,
        bgcolor: "background.neutral",
      }}
    >
      <Iconify icon={s.icon} width={size * 0.52} />
    </Box>
  );
}

SurfaceIcon.propTypes = {
  surface: PropTypes.string,
  size: PropTypes.number,
  radius: PropTypes.number,
};
