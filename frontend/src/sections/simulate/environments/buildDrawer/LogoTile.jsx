import PropTypes from "prop-types";
import { Box } from "@mui/material";
import Iconify from "src/components/iconify";
import { PLATFORM_LOGOS } from "../components/platformLogos";
import { COLOR_LOGO } from "./brandLogos";

const SIMPLE_ICON = {
  github: "simple-icons:github",
  gitlab: "simple-icons:gitlab",
  bitbucket: "simple-icons:bitbucket",
  mcp: "simple-icons:modelcontextprotocol",
  langgraph: "simple-icons:langgraph",
  crewai: "simple-icons:crewai",
  claude: "simple-icons:claude",
};
// Vapi and Retell ship as wordmarks; a square tile shows just the symbol.
const MARK_VIEWBOX = { vapi: "0 5 38 24", retell: "0 0 20 20" };

/** A brand's mark: its bundled logo, its colour icon, or its mono icon. */
export function BrandMark({ logo, icon, size = 16 }) {
  // A registered colour mark wins over the bundled single-colour logo.
  const local = logo && !COLOR_LOGO[logo] && PLATFORM_LOGOS[logo];
  if (local) {
    const svg = MARK_VIEWBOX[logo]
      ? local.svg.replace(/viewBox="[^"]*"/, `viewBox="${MARK_VIEWBOX[logo]}"`)
      : local.svg;
    return (
      <Box
        aria-hidden
        sx={{
          width: size,
          height: size,
          display: "grid",
          placeItems: "center",
          color: "text.primary",
          "& svg": {
            width: "100%",
            height: "100%",
            display: "block",
            fill: "currentColor",
          },
        }}
        // eslint-disable-next-line react/no-danger
        dangerouslySetInnerHTML={{ __html: svg }}
      />
    );
  }
  const mono = SIMPLE_ICON[logo] || icon || "solar:widget-linear";
  return (
    <Iconify icon={COLOR_LOGO[logo] || COLOR_LOGO[mono] || mono} width={size} />
  );
}
BrandMark.propTypes = {
  logo: PropTypes.string,
  icon: PropTypes.string,
  size: PropTypes.number,
};

/** The mark on a small neutral tile, as every row in the drawer shows it. */
export default function LogoTile({ logo, icon, size = 28, muted = false }) {
  return (
    <Box
      sx={{
        width: size,
        height: size,
        flexShrink: 0,
        borderRadius: size >= 28 ? "6px" : "3px",
        display: "grid",
        placeItems: "center",
        border: "1px solid",
        borderColor: "divider",
        bgcolor: "action.hover",
        color: muted ? "text.disabled" : "text.primary",
      }}
    >
      <BrandMark logo={logo} icon={icon} size={Math.round(size * 0.5)} />
    </Box>
  );
}
LogoTile.propTypes = {
  logo: PropTypes.string,
  icon: PropTypes.string,
  size: PropTypes.number,
  muted: PropTypes.bool,
};
