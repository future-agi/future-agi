import PropTypes from "prop-types";
import { Box, ButtonBase, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import SideDrawer from "../components/SideDrawer";
import { LogoTile } from "./LandingBits";
import { focusRing } from "./landingStyles";

/*
  The setup for whatever was picked on the landing screen — a platform, a
  repository, a pasted ID — in a drawer, so the directory stays where it was.
  The panel's own Build environment row sticks to the bottom.
*/
export default function SetupDrawer({ open, onClose, meta, subtitle, children, width = 660 }) {
  return (
    <SideDrawer open={open} onClose={onClose} width={width}>
      {open && (
        <Box sx={{ height: "100%", display: "flex", flexDirection: "column", minHeight: 0 }}>
          <Stack direction="row" alignItems="center" spacing={1.5} sx={{ px: 2.5, py: 1.75, borderBottom: "1px solid", borderColor: "divider", flexShrink: 0 }}>
            <LogoTile logo={meta?.logo} icon={meta?.icon || "solar:link-circle-linear"} size={36} />
            <Box flex={1} minWidth={0}>
              <Stack direction="row" alignItems="baseline" spacing={1} sx={{ minWidth: 0 }}>
                <Typography noWrap sx={{ typography: "m3", fontWeight: 600, flexShrink: 0 }}>{meta?.name || "Connect your agent"}</Typography>
                {meta?.detail && (
                  <Typography noWrap sx={{ typography: "s2", color: "text.secondary", fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", minWidth: 0 }}>
                    {meta.detail}
                  </Typography>
                )}
              </Stack>
              {subtitle && (!meta?.name || meta?.withSubtitle) && <Typography noWrap sx={{ typography: "s2", color: "text.secondary" }}>{subtitle}</Typography>}
            </Box>
            <ButtonBase
              onClick={onClose}
              aria-label="Close"
              sx={{ width: 28, height: 28, borderRadius: 0.75, color: "text.secondary", flexShrink: 0, "&:hover": { color: "text.primary" }, "&.Mui-focusVisible": focusRing }}
            >
              <Iconify icon="eva:close-fill" width={18} />
            </ButtonBase>
          </Stack>
          <Box
            sx={{
              flex: 1, minHeight: 0, overflow: "auto",
              /* The panel's Build environment row stays in reach at the bottom. */
              "& .continue-row": {
                position: "sticky", bottom: 0, zIndex: 2,
                bgcolor: "background.paper", pt: 1.5, pb: 1.5, mt: 1,
              },
            }}
          >
            {children}
          </Box>
        </Box>
      )}
    </SideDrawer>
  );
}
SetupDrawer.propTypes = {
  open: PropTypes.bool, onClose: PropTypes.func.isRequired, meta: PropTypes.object, subtitle: PropTypes.node, children: PropTypes.node, width: PropTypes.number,
};
