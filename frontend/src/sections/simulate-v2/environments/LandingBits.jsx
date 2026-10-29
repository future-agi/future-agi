import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, ButtonBase, Chip, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { PLATFORM_LOGOS } from "./connectOptions";
import { COLOR_LOGO } from "./colorLogos";
import { PANEL_SX, focusRing } from "./landingStyles";

/*
  The small pieces the Environments landing, the prebuilt-worlds page and the
  New environment journey share: brand marks in a tile, quiet tags, section
  heads and the world card.
*/

const SIMPLE_ICON = {
  github: "simple-icons:github",
  gitlab: "simple-icons:gitlab",
  bitbucket: "simple-icons:bitbucket",
  mcp: "simple-icons:modelcontextprotocol",
  langgraph: "simple-icons:langgraph",
  crewai: "simple-icons:crewai",
  claude: "simple-icons:claude",
};
/* Vapi and Retell ship as wordmarks; a square tile shows just the symbol. */
const MARK_VIEWBOX = { vapi: "0 5 38 24", retell: "0 0 20 20" };

export function BrandMark({ logo, icon, size = 16 }) {
  const local = logo && PLATFORM_LOGOS[logo];
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
          "& svg": { width: "100%", height: "100%", display: "block" },
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

export function LogoTile({ logo, icon, size = 28, muted = false }) {
  return (
    <Box
      sx={{
        width: size,
        height: size,
        flexShrink: 0,
        borderRadius: size >= 28 ? "6px" : 0.75,
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

export function QuietTag({ children }) {
  return (
    <Chip
      size="small"
      variant="outlined"
      label={children}
      sx={{ flexShrink: 0 }}
    />
  );
}
QuietTag.propTypes = { children: PropTypes.node };

export function SectionHead({ title, subtitle, action }) {
  return (
    <Stack
      direction={{ xs: "column", sm: "row" }}
      alignItems={{ sm: "flex-end" }}
      justifyContent="space-between"
      spacing={1.5}
      sx={{ mb: 1.75 }}
    >
      <Box minWidth={0}>
        <Typography
          component="h2"
          sx={{ typography: "m3", fontWeight: 600, textWrap: "balance" }}
        >
          {title}
        </Typography>
        {subtitle && (
          <Typography
            sx={{
              typography: "s2_1",
              color: "text.secondary",
              mt: 0.25,
              maxWidth: 760,
            }}
          >
            {subtitle}
          </Typography>
        )}
      </Box>
      {action && (
        <Stack
          direction="row"
          alignItems="center"
          spacing={1}
          sx={{ flexShrink: 0 }}
        >
          {action}
        </Stack>
      )}
    </Stack>
  );
}
SectionHead.propTypes = {
  title: PropTypes.node,
  subtitle: PropTypes.node,
  action: PropTypes.node,
};

/**
 * One prebuilt world: name, type, what it is, what's inside. A world for an
 * agent type that's Coming soon (computer use) is shown, named, and inert.
 */
export function WorldCard({ world, onOpen, selected = false, soon = false }) {
  return (
    <ButtonBase
      onClick={soon ? undefined : () => onOpen(world)}
      disabled={soon}
      aria-pressed={soon ? undefined : selected}
      aria-disabled={soon || undefined}
      sx={{
        ...PANEL_SX,
        p: 1.5,
        display: "flex",
        flexDirection: "column",
        alignItems: "stretch",
        textAlign: "left",
        borderColor: (t) =>
          selected && !soon
            ? alpha(t.palette.text.primary, 0.5)
            : t.palette.divider,
        transition: "border-color .12s ease",
        "&:hover": soon
          ? {}
          : { borderColor: (t) => alpha(t.palette.text.primary, 0.3) },
        "&.Mui-focusVisible": focusRing,
        "&.Mui-disabled": { cursor: "default", pointerEvents: "auto" },
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{ minWidth: 0 }}
      >
        <Typography
          noWrap
          sx={{
            typography: "s2_1",
            fontWeight: 600,
            flex: 1,
            minWidth: 0,
            color: soon ? "text.secondary" : "text.primary",
          }}
        >
          {world.name}
        </Typography>
        {soon && <QuietTag>Coming soon</QuietTag>}
        {/* A task that can't be adopted, or a retired release, says so on the card. */}
        {!soon && world.readiness && world.readiness !== "ready" && (
          <QuietTag>
            {world.readiness === "soon"
              ? "Coming soon"
              : world.readiness === "retired"
                ? "Retired"
                : "Withdrawn"}
          </QuietTag>
        )}
        <QuietTag>{world.typeLabel}</QuietTag>
      </Stack>
      <Typography
        sx={{
          typography: "s2",
          color: "text.secondary",
          mt: 0.5,
          mb: 1.5,
          display: "-webkit-box",
          WebkitLineClamp: 2,
          WebkitBoxOrient: "vertical",
          overflow: "hidden",
        }}
      >
        {world.tagline}
      </Typography>
      <Stack
        direction="row"
        justifyContent="space-between"
        spacing={1}
        sx={{
          mt: "auto",
          pt: 1.25,
          borderTop: "1px solid",
          borderColor: "divider",
        }}
      >
        <Typography
          noWrap
          sx={{
            typography: "s2",
            color: "text.secondary",
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {world.catalogueId
            ? `${world.catalogueId} · ${world.release.version} · ${world.tools} tools · ${world.scenarios} cases`
            : `${world.tools} tools · ${world.tables} tables · ${world.scenarios} scenarios`}
        </Typography>
        <Typography
          noWrap
          sx={{ typography: "s2", color: "text.secondary", flexShrink: 0 }}
        >
          {world.domainLabel}
        </Typography>
      </Stack>
    </ButtonBase>
  );
}
WorldCard.propTypes = {
  world: PropTypes.object.isRequired,
  onOpen: PropTypes.func.isRequired,
  selected: PropTypes.bool,
  soon: PropTypes.bool,
};
