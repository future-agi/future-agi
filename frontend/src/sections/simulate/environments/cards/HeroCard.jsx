import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import NotInReleaseChip from "src/components/NotInRelease/NotInReleaseChip";
import { dimExceptLine } from "src/components/NotInRelease/notInRelease";
import { activateOnKey } from "../helpers/activateOnKey";

export default function HeroCard({
  icon,
  title,
  tag,
  description,
  chips,
  moreLabel,
  selected,
  onClick,
  comingSoon,
}) {
  // A hero not built in this release stays visible but inert: no click, no
  // keyboard target, dimmed, with the shared line under its title.
  // The dim takes text.subtitle sub-labels to about 2.8:1 and text.secondary
  // to about 3.8:1; text.primary under it reads at about 7:1 in light and
  // 9:1 in dark.
  const subLabelColor = comingSoon ? "text.primary" : "text.subtitle";
  return (
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
        borderRadius: "8px",
        cursor: comingSoon ? "default" : "pointer",
        ...(comingSoon && dimExceptLine),
        border: "1px solid",
        minHeight: 172,
        borderColor: selected ? "text.primary" : "divider",
        bgcolor: selected ? "action.selected" : "background.paper",
        transition: "border-color .12s ease, background-color .12s ease",
        "&:hover": {
          borderColor: comingSoon || selected ? undefined : "text.disabled",
        },
      }}
    >
      <Stack direction="row" spacing={2} alignItems="center">
        <Box
          sx={{
            width: 44,
            height: 44,
            borderRadius: "8px",
            display: "grid",
            placeItems: "center",
            flexShrink: 0,
            bgcolor: "background.neutral",
            color: "text.primary",
          }}
        >
          <Iconify icon={icon} width={22} />
        </Box>
        <Box minWidth={0}>
          <Stack direction="row" alignItems="center" spacing={1}>
            <Typography sx={{ typography: "m1", fontWeight: 500 }}>
              {title}
            </Typography>
            {tag && (
              <Typography
                sx={{
                  typography: "s3",
                  fontWeight: 500,
                  color: subLabelColor,
                }}
              >
                · {tag}
              </Typography>
            )}
          </Stack>
        </Box>
      </Stack>
      {comingSoon && <NotInReleaseChip sx={{ alignSelf: "flex-start" }} />}
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
              px: 0.75,
              height: 18,
              display: "inline-flex",
              alignItems: "center",
              borderRadius: "4px",
              bgcolor: "action.selected",
              color: "text.secondary",
              typography: "s3",
              fontWeight: 500,
            }}
          >
            {name}
          </Box>
        ))}
        {moreLabel && (
          <Box
            sx={{
              typography: "s3",
              fontWeight: 500,
              color: subLabelColor,
              px: 0.5,
            }}
          >
            {moreLabel}
          </Box>
        )}
      </Stack>
      {selected && (
        <Iconify
          icon="solar:check-circle-bold"
          width={16}
          sx={{
            position: "absolute",
            top: 10,
            right: 10,
            color: "text.primary",
          }}
        />
      )}
    </Stack>
  );
}
HeroCard.propTypes = {
  icon: PropTypes.string,
  title: PropTypes.node,
  tag: PropTypes.node,
  description: PropTypes.node,
  chips: PropTypes.arrayOf(PropTypes.string),
  moreLabel: PropTypes.node,
  selected: PropTypes.bool,
  onClick: PropTypes.func,
  comingSoon: PropTypes.bool,
};
