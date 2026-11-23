import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
  IconButton,
  Menu,
  MenuItem,
  Tooltip,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";

/**
 * The epoch page's ⋯ — everything that isn't the page's one primary:
 * copy its ID or CLI command, export it, its receipt, a brief for a coding
 * agent. Each item says what it does in one quiet line; a copy confirms on
 * the item itself.
 */
export default function EpochMoreMenu({ items }) {
  const [anchor, setAnchor] = useState(null);
  const [done, setDone] = useState(null);
  const shown = items.filter(Boolean);
  if (!shown.length) return null;
  const pick = (it) => {
    it.onClick?.();
    if (it.done) {
      setDone(it.label);
      setTimeout(() => {
        setDone(null);
        setAnchor(null);
      }, 900);
    } else setAnchor(null);
  };
  return (
    <>
      <Tooltip arrow title="More actions">
        <IconButton
          size="small"
          aria-label="More actions"
          onClick={(e) => setAnchor(e.currentTarget)}
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 1,
            flexShrink: 0,
          }}
        >
          <Iconify icon="solar:menu-dots-bold" width={16} />
        </IconButton>
      </Tooltip>
      <Menu
        anchorEl={anchor}
        open={!!anchor}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{ paper: { sx: { width: 300, mt: 0.5 } } }}
      >
        {shown.map((it) => (
          <MenuItem
            key={it.label}
            onClick={() => pick(it)}
            sx={{
              gap: 1,
              py: 1,
              alignItems: "flex-start",
              whiteSpace: "normal",
            }}
          >
            <Iconify
              icon={done === it.label ? "solar:check-circle-linear" : it.icon}
              width={16}
              sx={{ flexShrink: 0, mt: "2px", color: "text.secondary" }}
            />
            <Box sx={{ minWidth: 0 }}>
              <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                {done === it.label ? it.done : it.label}
              </Typography>
              {it.hint && (
                <Typography
                  sx={{
                    typography: "s3",
                    color: "text.tertiary",
                    wordBreak: "break-word",
                  }}
                >
                  {it.hint}
                </Typography>
              )}
            </Box>
          </MenuItem>
        ))}
      </Menu>
    </>
  );
}

EpochMoreMenu.propTypes = {
  /* [{ label, hint, icon, onClick, done: "Copied" }] — falsy items are skipped. */
  items: PropTypes.array.isRequired,
};
