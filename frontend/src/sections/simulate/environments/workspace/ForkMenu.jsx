import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Typography, IconButton, Menu, MenuItem } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { WORKSPACE_COPY } from "./workspace.constants";

// The header overflow menu. A regular environment keeps Fork tucked inside the
// overflow; a template-seeded env surfaces Fork inside the Overview card
// instead, so the shell hides this menu for those (renders nothing when locked).
export default function ForkMenu({ onFork }) {
  const [anchor, setAnchor] = useState(null);
  const close = () => setAnchor(null);
  const fork = () => {
    close();
    onFork();
  };

  return (
    <>
      <CustomTooltip show title={WORKSPACE_COPY.moreActions} size="small" arrow>
        <IconButton
          size="small"
          aria-label={WORKSPACE_COPY.moreActions}
          onClick={(e) => setAnchor(e.currentTarget)}
          sx={{ color: "text.subtitle" }}
        >
          <Iconify icon="solar:menu-dots-bold" width={18} />
        </IconButton>
      </CustomTooltip>
      <Menu
        anchorEl={anchor}
        open={!!anchor}
        onClose={close}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{ paper: { sx: { minWidth: 260, mt: 0.5 } } }}
      >
        <MenuItem onClick={fork} sx={{ alignItems: "flex-start", gap: 1.25, py: 1 }}>
          <Iconify
            icon="solar:copy-linear"
            width={16}
            sx={{ color: "text.subtitle", mt: "2px", flexShrink: 0 }}
          />
          <Box minWidth={0}>
            <Typography sx={{ typography: "s2", fontWeight: "fontWeightSemiBold" }}>
              {WORKSPACE_COPY.fork}
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.subtitle", whiteSpace: "normal" }}>
              {WORKSPACE_COPY.forkHint}
            </Typography>
          </Box>
        </MenuItem>
      </Menu>
    </>
  );
}

ForkMenu.propTypes = {
  onFork: PropTypes.func.isRequired,
};
