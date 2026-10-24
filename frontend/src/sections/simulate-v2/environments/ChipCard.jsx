import PropTypes from "prop-types";
import { Box, Stack, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { focusRing } from "./landingStyles";

/* The selectable chip used across the connect flows — agent type, platform, and the landing's type filter. */
export default function ChipCard({
  icon,
  logo,
  label,
  on,
  onClick,
  comingSoon,
  tag,
  disabled,
}) {
  /* A quiet tag after the label — "Phase 2" — and "Coming soon" said, not only hovered.
     `disabled` is the same inert chip with its own tag ("Coming soon"). */
  const shownTag = comingSoon ? "Coming soon" : tag;
  const inert = comingSoon || disabled;
  const chip = (
    <Stack
      direction="row"
      alignItems="center"
      spacing={0.75}
      onClick={inert ? undefined : onClick}
      onKeyDown={
        inert || !onClick
          ? undefined
          : (e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onClick();
              }
            }
      }
      role={onClick ? "button" : undefined}
      tabIndex={onClick && !inert ? 0 : undefined}
      aria-pressed={onClick ? !!on : undefined}
      aria-disabled={inert || undefined}
      sx={{
        outline: "none",
        "&:focus-visible": focusRing,
        px: 1.25,
        py: 0.75,
        borderRadius: 1,
        cursor: inert ? "default" : "pointer",
        opacity: inert ? 0.6 : 1,
        border: "1px solid",
        borderColor: on ? "primary.main" : "divider",
        bgcolor: on ? "action.selected" : "background.paper",
      }}
    >
      {logo ||
        (icon && (
          <Iconify
            icon={icon}
            width={14}
            sx={{ color: on ? "primary.main" : "text.subtitle" }}
          />
        ))}
      {label != null && (
        <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>
          {label}
        </Typography>
      )}
      {shownTag && (
        <Typography
          noWrap
          component="span"
          sx={{
            typography: "s3",
            fontSize: 12,
            fontWeight: 600,
            color: "text.subtitle",
            px: 0.5,
            borderRadius: "4px",
            border: "1px solid",
            borderColor: "divider",
            lineHeight: "16px",
          }}
        >
          {shownTag}
        </Typography>
      )}
    </Stack>
  );
  if (comingSoon) {
    return (
      <Tooltip title="Coming soon" arrow placement="top">
        <Box sx={{ display: "inline-flex" }}>{chip}</Box>
      </Tooltip>
    );
  }
  return chip;
}
ChipCard.propTypes = {
  icon: PropTypes.string,
  logo: PropTypes.node,
  label: PropTypes.node,
  on: PropTypes.bool,
  onClick: PropTypes.func,
  comingSoon: PropTypes.bool,
  tag: PropTypes.string,
  disabled: PropTypes.bool,
};
