import PropTypes from "prop-types";
import { Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import NotInReleaseChip from "src/components/NotInRelease/NotInReleaseChip";
import { dimExceptLine } from "src/components/NotInRelease/notInRelease";
import { activateOnKey } from "../helpers/activateOnKey";

export default function ChipCard({
  icon,
  logo,
  label,
  on,
  onClick,
  comingSoon,
}) {
  // A choice not built in this release stays visible but inert: no click, no
  // keyboard target, dimmed, with the shared line beside its name.
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={0.75}
      onClick={comingSoon ? undefined : onClick}
      role="button"
      tabIndex={comingSoon ? -1 : 0}
      aria-pressed={on}
      aria-disabled={comingSoon || undefined}
      onKeyDown={comingSoon ? undefined : activateOnKey((e) => onClick?.(e))}
      sx={{
        px: 1.25,
        py: 0.75,
        borderRadius: 1,
        cursor: comingSoon ? "default" : "pointer",
        ...(comingSoon && dimExceptLine),
        border: "1px solid",
        borderColor: on ? "text.primary" : "divider",
        bgcolor: on ? "action.selected" : "background.paper",
      }}
    >
      {logo ||
        (icon && (
          <Iconify
            icon={icon}
            width={13}
            sx={{ color: on ? "text.primary" : "text.subtitle" }}
          />
        ))}
      {label != null && (
        <Typography noWrap sx={{ typography: "s1", fontWeight: 500 }}>
          {label}
        </Typography>
      )}
      {comingSoon && <NotInReleaseChip />}
    </Stack>
  );
}
ChipCard.propTypes = {
  icon: PropTypes.string,
  logo: PropTypes.node,
  label: PropTypes.node,
  on: PropTypes.bool,
  onClick: PropTypes.func,
  comingSoon: PropTypes.bool,
};
