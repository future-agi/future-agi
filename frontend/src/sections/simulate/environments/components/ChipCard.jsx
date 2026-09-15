import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { activateOnKey } from "../helpers/activateOnKey";

export default function ChipCard({ icon, logo, label, on, onClick, comingSoon }) {
  const chip = (
    <Stack
      direction="row" alignItems="center" spacing={0.75}
      onClick={comingSoon ? undefined : onClick}
      role="button"
      tabIndex={comingSoon ? -1 : 0}
      aria-pressed={on}
      aria-disabled={comingSoon || undefined}
      onKeyDown={comingSoon ? undefined : activateOnKey((e) => onClick?.(e))}
      sx={{
        px: 1.25, py: 0.75, borderRadius: 1,
        cursor: comingSoon ? "default" : "pointer",
        opacity: comingSoon ? 0.6 : 1,
        border: "1px solid",
        borderColor: (th) => on
          ? (th.palette.mode === "dark" ? alpha(th.palette.text.primary, 0.4) : th.palette.primary.main)
          : th.palette.divider,
        bgcolor: (th) => on
          ? (th.palette.mode === "dark" ? alpha(th.palette.text.primary, 0.06) : alpha(th.palette.primary.main, 0.05))
          : "background.paper",
      }}
    >
      {logo || (icon && <Iconify icon={icon} width={13} sx={{ color: on ? "primary.main" : "text.subtitle" }} />)}
      {label != null && <Typography noWrap sx={{ typography: "s2", fontWeight: "fontWeightSemiBold" }}>{label}</Typography>}
    </Stack>
  );
  if (comingSoon) {
    return (
      <CustomTooltip title="Coming soon" arrow placement="top" size="small">
        {/* CustomTooltip doesn't mirror its title onto the child as an
            aria-label (MUI's does), so name the wrapper explicitly. */}
        <Box aria-label="Coming soon" sx={{ display: "inline-flex" }}>{chip}</Box>
      </CustomTooltip>
    );
  }
  return chip;
}
ChipCard.propTypes = { icon: PropTypes.string, logo: PropTypes.node, label: PropTypes.node, on: PropTypes.bool, onClick: PropTypes.func, comingSoon: PropTypes.bool };
