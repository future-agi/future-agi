import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, ButtonBase, Radio, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { PANEL_SX } from "./connectStyles";

export const focusRing = (theme) => ({
  outline: `2px solid ${alpha(theme.palette.text.primary, 0.5)}`,
  outlineOffset: 2,
});

export function LogoTile({ icon, name, muted = false, size = 28 }) {
  return (
    <Box
      aria-hidden
      sx={{
        width: size,
        height: size,
        flexShrink: 0,
        borderRadius: "6px",
        display: "grid",
        placeItems: "center",
        border: "1px solid",
        borderColor: "divider",
        bgcolor: "action.hover",
        color: muted ? "text.disabled" : "text.primary",
      }}
    >
      {icon ? (
        <Iconify icon={icon} width={Math.round(size * 0.5)} />
      ) : (
        <Typography sx={{ typography: "s3", fontWeight: 600, color: "inherit" }}>
          {String(name || "?").charAt(0).toUpperCase()}
        </Typography>
      )}
    </Box>
  );
}
LogoTile.propTypes = {
  icon: PropTypes.string,
  name: PropTypes.string,
  muted: PropTypes.bool,
  size: PropTypes.number,
};

export function TextLink({ onClick, children, sx }) {
  return (
    <ButtonBase
      onClick={onClick}
      sx={{
        typography: "s3",
        color: "text.secondary",
        borderRadius: "4px",
        verticalAlign: "baseline",
        "&:hover": { color: "text.primary", textDecoration: "underline" },
        "&.Mui-focusVisible": focusRing,
        ...sx,
      }}
    >
      {children}
    </ButtonBase>
  );
}
TextLink.propTypes = { onClick: PropTypes.func, children: PropTypes.node, sx: PropTypes.object };

export function OptionCard({ icon, label, short, selected, onSelect }) {
  return (
    <Stack
      component="label"
      direction="row"
      spacing={1.25}
      alignItems="flex-start"
      sx={{
        px: 1.5,
        py: 1.25,
        borderRadius: "6px",
        border: "1px solid",
        cursor: "pointer",
        borderColor: selected ? "text.primary" : "divider",
        bgcolor: selected ? "action.selected" : "transparent",
        "&:hover": { bgcolor: selected ? "action.selected" : "action.hover" },
      }}
    >
      <Iconify icon={icon} width={16} sx={{ color: "text.secondary", mt: 0.25, flexShrink: 0 }} />
      <Box flex={1} minWidth={0}>
        <Typography sx={{ typography: "s1", fontWeight: 500 }}>{label}</Typography>
        <Typography sx={{ typography: "s3", color: "text.secondary", mt: 0.25 }}>{short}</Typography>
      </Box>
      <Radio size="small" checked={selected} onChange={onSelect} sx={{ p: 0, flexShrink: 0 }} />
    </Stack>
  );
}
OptionCard.propTypes = {
  icon: PropTypes.string.isRequired,
  label: PropTypes.string.isRequired,
  short: PropTypes.string.isRequired,
  selected: PropTypes.bool,
  onSelect: PropTypes.func.isRequired,
};

export function Panel({ title, reach, children }) {
  return (
    <Box sx={PANEL_SX}>
      {(title || reach) && (
        <Stack
          direction={{ xs: "column", sm: "row" }}
          alignItems={{ sm: "baseline" }}
          spacing={{ xs: 0.25, sm: 1.25 }}
          sx={{ px: 2, py: 1, borderBottom: "1px solid", borderColor: "divider" }}
        >
          {title && (
            <Typography component="h3" sx={{ typography: "m3", fontWeight: 600, flexShrink: 0 }}>
              {title}
            </Typography>
          )}
          {reach && (
            <Typography sx={{ typography: "s3", color: "text.secondary", minWidth: 0 }}>
              {reach}
            </Typography>
          )}
        </Stack>
      )}
      {children}
    </Box>
  );
}
Panel.propTypes = { title: PropTypes.node, reach: PropTypes.string, children: PropTypes.node };
