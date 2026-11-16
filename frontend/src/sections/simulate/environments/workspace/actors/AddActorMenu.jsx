import PropTypes from "prop-types";
import { useState } from "react";
import { Button, Menu, MenuItem, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { ACTORS_COPY } from "./actors.constants";

const C = ACTORS_COPY.worldActors;

export default function AddActorMenu({ label, disabled, onPick }) {
  const [anchor, setAnchor] = useState(null);
  return (
    <>
      <Button
        size="small"
        variant="outlined"
        disabled={disabled}
        onClick={(event) => setAnchor(event.currentTarget)}
        startIcon={<Iconify icon="solar:add-circle-linear" width={16} />}
        endIcon={<Iconify icon="solar:alt-arrow-down-linear" width={14} />}
        sx={{ color: "text.primary", borderColor: "divider", flexShrink: 0 }}
      >
        {label}
      </Button>
      <Menu
        anchorEl={anchor}
        open={Boolean(anchor)}
        onClose={() => setAnchor(null)}
        slotProps={{ paper: { sx: { width: 320 } } }}
      >
        {Object.entries(C.sides).map(([side, { label: sideLabel, note }]) => (
          <MenuItem
            key={side}
            onClick={() => {
              setAnchor(null);
              onPick(side);
            }}
            sx={{ display: "block", whiteSpace: "normal" }}
          >
            <Typography sx={{ typography: "s2", fontWeight: 600 }}>
              {sideLabel}
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.secondary" }}>
              {note}
            </Typography>
          </MenuItem>
        ))}
      </Menu>
    </>
  );
}

AddActorMenu.propTypes = {
  label: PropTypes.string.isRequired,
  disabled: PropTypes.bool,
  // (side): "caller" or "world".
  onPick: PropTypes.func.isRequired,
};
