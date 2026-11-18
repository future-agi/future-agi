import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Button, Stack, Typography } from "@mui/material";
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

/* Design system Tag: a label, not a control — 11/500 in fg.secondary on
   bg.selected, no border, so it never reads as a button or a tab. */
export function QuietTag({ children }) {
  return (
    <Box
      component="span"
      sx={{
        display: "inline-flex",
        alignItems: "center",
        height: 20,
        px: 0.75,
        borderRadius: "4px",
        bgcolor: "action.selected",
        color: "text.secondary",
        typography: "s3",
        fontWeight: 500,
        whiteSpace: "nowrap",
        flexShrink: 0,
      }}
    >
      {children}
    </Box>
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
 * One prebuilt world: name, type, what it is, what's inside.
 */

export function WorldCard({
  world,
  onOpen,
  actions = false,
  selected = false,
}) {
  /* Ready to use: the two things to do with a world sit on its card (the
     catalogue's Use with my agent, the builder's Customize). */
  const actionable =
    actions && (!world.readiness || world.readiness === "ready");
  const count = world.splits
    ? Object.values(world.splits).reduce((n, v) => n + (Number(v) || 0), 0)
    : world.scenarios;
  const open = (tab) => (e) => {
    e.stopPropagation();
    onOpen(world, tab);
  };
  return (
    <Box
      role="button"
      tabIndex={0}
      aria-pressed={selected}
      onClick={() => onOpen(world)}
      onKeyDown={(e) => {
        if (
          e.target === e.currentTarget &&
          (e.key === "Enter" || e.key === " ")
        ) {
          e.preventDefault();
          onOpen(world);
        }
      }}
      sx={{
        ...PANEL_SX,
        p: 1.5,
        minWidth: 0,
        display: "flex",
        flexDirection: "column",
        alignItems: "stretch",
        textAlign: "left",
        cursor: "pointer",
        borderColor: (t) =>
          selected ? alpha(t.palette.text.primary, 0.5) : t.palette.divider,
        transition: "border-color .12s ease",
        "&:hover": { borderColor: (t) => alpha(t.palette.text.primary, 0.3) },
        "&:focus-visible": focusRing,
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
            color: "text.primary",
          }}
        >
          {world.name}
        </Typography>
        {/* A third-party world says whether it runs here: listing isn't support. */}
        {world.host && (
          <QuietTag>
            {world.host.support === "runs" ? "Runs here" : "Listed only"}
          </QuietTag>
        )}
        {/* Only an unusual state is a tag; RL access is on the count line, versions in the drawer. */}
        {!world.host && world.readiness && world.readiness !== "ready" && (
          <QuietTag>
            {world.readiness === "soon"
              ? "Checks pending"
              : world.readiness === "retired"
                ? "Retired"
                : world.readiness === "unsupported"
                  ? "Discovered · Unsupported"
                  : world.readiness === "access"
                    ? "Needs access"
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
        alignItems="center"
        spacing={1}
        sx={{
          mt: "auto",
          pt: 1.25,
          borderTop: "1px solid",
          borderColor: "divider",
          minHeight: 40,
          /* In a narrow column the actions drop under the count. */
          flexWrap: "wrap",
          rowGap: 0.75,
        }}
      >
        <Typography
          noWrap
          sx={{
            typography: "s2",
            color: "text.secondary",
            fontVariantNumeric: "tabular-nums",
            flex: "1 1 140px",
            minWidth: 0,
          }}
        >
          {world.readiness === "access" && world.release?.owner
            ? `Ask ${world.release.owner.name} · ${world.release.owner.role}`
            : [
                count != null && `${count} scenarios`,
                world.domainLabel,
                /* RL access per environment (T29): open on a locked release
                   with splits; a world without its own entry says nothing. */
                world.entry && (world.rl ? "RL access" : "No RL access yet"),
              ]
                .filter(Boolean)
                .join(" · ")}
        </Typography>
        {/* Their own row under the count, the same on every card. */}
        {actionable && (
          <Stack direction="row" spacing={1} sx={{ flexBasis: "100%" }}>
            <Button
              size="small"
              variant="outlined"
              onClick={open("start")}
              sx={{ color: "text.primary", borderColor: "divider" }}
            >
              Use with my agent
            </Button>
            <Button
              size="small"
              variant="outlined"
              onClick={open("customize")}
              sx={{ color: "text.primary", borderColor: "divider" }}
            >
              Customize
            </Button>
          </Stack>
        )}
      </Stack>
    </Box>
  );
}
WorldCard.propTypes = {
  world: PropTypes.object.isRequired,
  /* (world, tab?) — tab is "start" or "customize" from the card's actions. */
  onOpen: PropTypes.func.isRequired,
  /* Show Use with my agent and Customize on the card. */
  actions: PropTypes.bool,
  selected: PropTypes.bool,
};
