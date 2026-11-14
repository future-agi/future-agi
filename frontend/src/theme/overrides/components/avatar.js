import { alpha } from "@mui/material/styles";
import { avatarGroupClasses } from "@mui/material/AvatarGroup";

// ----------------------------------------------------------------------

const COLORS = [
  "default",
  "primary",
  "secondary",
  "info",
  "success",
  "warning",
  "error",
];

// ----------------------------------------------------------------------

export function avatar(theme) {
  // One solid neutral tile (gray-150 / gray-800) so overlapping avatars in a
  // group stay crisp; initials in fg.default.
  const tile = theme.palette.mode === "dark" ? "#27272A" : "#EFEFF1";
  return {
    MuiAvatar: {
      variants: COLORS.map((color) =>
        color === "default"
          ? {
              props: { color: "default" },
              style: {
                color: theme.palette.text.primary,
                backgroundColor: tile,
              },
            }
          : {
              // Design system: avatars are neutral, never brand or status coloured.
              props: { color },
              style: {
                color: theme.palette.text.primary,
                backgroundColor: tile,
              },
            },
      ),

      styleOverrides: {
        rounded: {
          borderRadius: 4,
        },
        // Design system: every avatar is the same light neutral tile with
        // dark initials — never MUI's dark grey, never brand or status colour.
        colorDefault: {
          color: theme.palette.text.primary,
          backgroundColor: tile,
          fontWeight: 600,
        },
      },
    },
    MuiAvatarGroup: {
      styleOverrides: {
        root: ({ ownerState }) => ({
          justifyContent: "flex-end",
          ...(ownerState.variant === "compact" && {
            width: 40,
            height: 40,
            position: "relative",
            [`& .${avatarGroupClasses.avatar}`]: {
              margin: 0,
              width: 28,
              height: 28,
              position: "absolute",
              "&:first-of-type": {
                left: 0,
                bottom: 0,
                zIndex: 9,
              },
              "&:last-of-type": {
                top: 0,
                right: 0,
              },
            },
          }),
        }),
        avatar: {
          fontSize: 11,
          fontWeight: 500,
          color: theme.palette.text.primary,
          backgroundColor: tile,
          borderColor: theme.palette.background.paper,
          "&:first-of-type": {
            fontSize: 11,
            color: theme.palette.text.primary,
            backgroundColor: tile,
          },
        },
      },
    },
  };
}
