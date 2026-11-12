import React, { useState } from "react";
import PropTypes from "prop-types";
import { IconButton, ListSubheader, Menu, MenuItem } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";

/**
 * W4-8: "Translate section" (an icon button) with the target languages the
 * server reports for the section's text fields. Absent when none offers one.
 * `targets` = `[{code, name}]` (translateModel.sectionTargets).
 */
const TranslateMenu = ({
  targets,
  onPick,
  label = "Translate section",
  busy,
}) => {
  const [anchor, setAnchor] = useState(null);
  if (!targets?.length) return null;
  return (
    <>
      <CustomTooltip show size="small" placement="top" arrow title={label}>
        <IconButton
          size="small"
          aria-label={label}
          data-testid="translate-section"
          disabled={busy}
          onClick={(e) => setAnchor(e.currentTarget)}
          sx={{ p: 0.5, color: "text.secondary" }}
        >
          <Iconify icon="mdi:translate" width={15} />
        </IconButton>
      </CustomTooltip>
      <Menu
        anchorEl={anchor}
        open={Boolean(anchor)}
        onClose={() => setAnchor(null)}
        MenuListProps={{ dense: true, "data-testid": "translate-menu" }}
      >
        <ListSubheader sx={{ typography: "s3", lineHeight: "28px" }}>
          Translate to
        </ListSubheader>
        {targets.map((t) => (
          <MenuItem
            key={t.code}
            data-testid={`translate-to-${t.code}`}
            sx={{ typography: "s1" }}
            onClick={() => {
              setAnchor(null);
              onPick(t);
            }}
          >
            {t.name}
          </MenuItem>
        ))}
      </Menu>
    </>
  );
};

TranslateMenu.propTypes = {
  targets: PropTypes.array,
  onPick: PropTypes.func.isRequired,
  label: PropTypes.string,
  busy: PropTypes.bool,
};

export default TranslateMenu;
