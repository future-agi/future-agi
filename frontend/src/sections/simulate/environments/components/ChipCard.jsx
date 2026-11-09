import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { activateOnKey } from "../helpers/activateOnKey";

export default function ChipCard({
  icon,
  logo,
  label,
  on,
  onClick,
  comingSoon,
}) {
  if (comingSoon) return null;
  const chip = (
    <Stack
      direction="row"
      alignItems="center"
      spacing={0.75}
      onClick={onClick}
      role="button"
      tabIndex={0}
      aria-pressed={on}
      onKeyDown={activateOnKey((e) => onClick?.(e))}
      sx={{
        px: 1.25,
        py: 0.75,
        borderRadius: 1,
        cursor: "pointer",
        border: "1px solid",
        borderColor: (th) =>
          on
            ? th.palette.mode === "dark"
              ? alpha(th.palette.text.primary, 0.4)
              : th.palette.primary.main
            : th.palette.divider,
        bgcolor: (th) =>
          on
            ? th.palette.mode === "dark"
              ? alpha(th.palette.text.primary, 0.06)
              : alpha(th.palette.primary.main, 0.05)
            : "background.paper",
      }}
    >
      {logo ||
        (icon && (
          <Iconify
            icon={icon}
            width={13}
            sx={{ color: on ? "primary.main" : "text.subtitle" }}
          />
        ))}
      {label != null && (
        <Typography
          noWrap
          sx={{ typography: "s2", fontWeight: "fontWeightSemiBold" }}
        >
          {label}
        </Typography>
      )}
    </Stack>
  );
  return chip;
}
ChipCard.propTypes = {
  icon: PropTypes.string,
  logo: PropTypes.node,
  label: PropTypes.node,
  on: PropTypes.bool,
  onClick: PropTypes.func,
  comingSoon: PropTypes.bool,
};
