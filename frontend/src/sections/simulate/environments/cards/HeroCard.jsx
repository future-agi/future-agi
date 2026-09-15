import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import ComingSoonChip from "../components/ComingSoonChip";
import { activateOnKey } from "../helpers/activateOnKey";

export default function HeroCard({ icon, title, tag, description, chips, moreLabel, selected, onClick, comingSoon }) {
  const card = (
    <Stack
      onClick={comingSoon ? undefined : onClick}
      role="button"
      tabIndex={comingSoon ? -1 : 0}
      onKeyDown={comingSoon ? undefined : activateOnKey((e) => onClick?.(e))}
      aria-pressed={selected}
      aria-disabled={comingSoon || undefined}
      spacing={1.75}
      sx={{
        position: "relative",
        p: 2.5,
        borderRadius: 1.5,
        cursor: comingSoon ? "default" : "pointer",
        opacity: comingSoon ? 0.72 : 1,
        border: "1px solid",
        minHeight: 172,
        borderColor: (th) => selected
          ? (th.palette.mode === "dark" ? alpha(th.palette.text.primary, 0.5) : th.palette.text.primary)
          : th.palette.divider,
        bgcolor: (th) => selected
          ? alpha(th.palette.text.primary, th.palette.mode === "dark" ? 0.06 : 0.03)
          : "background.paper",
        transition: "border-color .12s ease, background-color .12s ease",
        "&:hover": {
          borderColor: (th) => (comingSoon || selected) ? undefined : (th.palette.mode === "dark" ? alpha(th.palette.text.primary, 0.32) : th.palette.text.disabled),
        },
      }}
    >
      <Stack direction="row" spacing={2} alignItems="center">
        <Box
          sx={{
            width: 44, height: 44, borderRadius: 1.5,
            display: "grid", placeItems: "center", flexShrink: 0,
            bgcolor: (th) => alpha(th.palette.text.primary, th.palette.mode === "dark" ? 0.1 : 0.06),
            color: "text.primary",
          }}
        >
          <Iconify icon={icon} width={22} />
        </Box>
        <Box minWidth={0}>
          <Stack direction="row" alignItems="center" spacing={1}>
            <Typography sx={{ typography: "m2", fontWeight: "fontWeightBold" }}>
              {title}
            </Typography>
            {tag && (
              <Typography sx={{ typography: "s3", fontWeight: "fontWeightBold", letterSpacing: 0.4, textTransform: "uppercase", color: "text.subtitle" }}>
                · {tag}
              </Typography>
            )}
          </Stack>
        </Box>
      </Stack>
      <Typography sx={{ typography: "s1", color: "text.secondary", flex: 1 }}>
        {description}
      </Typography>
      <Stack
        direction="row"
        spacing={0.5}
        useFlexGap
        flexWrap="wrap"
        alignItems="center"
        sx={{
          pt: 1.25,
          borderTop: "1px solid",
          borderColor: "divider",
        }}
      >
        {(chips ?? []).map((name) => (
          <Box
            key={name}
            sx={{
              px: 1, py: 0.5, borderRadius: 0.75,
              border: "1px solid", borderColor: "divider",
              bgcolor: (th) => alpha(th.palette.text.primary, 0.02),
              typography: "s3", fontWeight: "fontWeightSemiBold",
            }}
          >
            {name}
          </Box>
        ))}
        {moreLabel && (
          <Box sx={{ typography: "s3", fontWeight: "fontWeightBold", color: "text.subtitle", px: 0.5 }}>{moreLabel}</Box>
        )}
      </Stack>
      {selected && (
        <Iconify
          icon="solar:check-circle-bold"
          width={16}
          sx={{ position: "absolute", top: 10, right: 10, color: "text.primary" }}
        />
      )}
    </Stack>
  );
  if (comingSoon) {
    return (
      <Box sx={{ position: "relative", display: "grid" }}>
        {card}
        <ComingSoonChip sx={{ position: "absolute", top: 14, right: 14, zIndex: 1 }} />
      </Box>
    );
  }
  return card;
}
HeroCard.propTypes = {
  icon: PropTypes.string, title: PropTypes.node, tag: PropTypes.node,
  description: PropTypes.node, chips: PropTypes.arrayOf(PropTypes.string),
  moreLabel: PropTypes.node, selected: PropTypes.bool, onClick: PropTypes.func,
  comingSoon: PropTypes.bool,
};
