// W5-3 (C-H1): the leaderboard selection bar's `⋯` with `Tag…` and
// `Filter to selected`. One icon button keeps the one-line bar inside 1440
// px with every existing action visible (two labelled buttons pushed
// `Clear  Esc` off the bar). `Tag…` opens the one TagPicker on the
// experiments of the selected runs; `Filter to selected` writes the
// leaderboard's `run_id` filter row (filterToSelected.js).
import React, { useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  IconButton,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import TagPicker from "src/sections/common/Tags/TagPicker";
import { useExperimentTagging } from "src/sections/common/Tags/useTags";

export default function RunSelectionMore({ runs, onFilterToSelected }) {
  const btnRef = useRef(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [pickerAnchor, setPickerAnchor] = useState(null);
  const tagging = useExperimentTagging(runs);
  const runIds = [
    ...new Set((runs ?? []).map((r) => r?.run_id).filter(Boolean)),
  ];
  return (
    <>
      <CustomTooltip
        show
        title="Tag… · Filter to selected"
        placement="top"
        size="small"
        arrow
      >
        <IconButton
          ref={btnRef}
          size="small"
          aria-label="More actions on the selected runs"
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          disabled={!runIds.length}
          onClick={() => setMenuOpen(true)}
          data-testid="run-selection-more"
          sx={{ flexShrink: 0, width: 30, height: 30 }}
        >
          <Iconify icon="mdi:dots-horizontal" width={18} />
        </IconButton>
      </CustomTooltip>
      <Menu
        anchorEl={btnRef.current}
        open={menuOpen}
        onClose={() => setMenuOpen(false)}
        anchorOrigin={{ vertical: "top", horizontal: "right" }}
        transformOrigin={{ vertical: "bottom", horizontal: "right" }}
      >
        <MenuItem
          disabled={!tagging.n}
          onClick={() => {
            setMenuOpen(false);
            setPickerAnchor(btnRef.current);
          }}
        >
          <ListItemIcon>
            <Iconify icon="mdi:tag-outline" width={16} />
          </ListItemIcon>
          <ListItemText primary="Tag…" />
        </MenuItem>
        {onFilterToSelected ? (
          <MenuItem
            data-testid="filter-to-selected"
            onClick={() => {
              setMenuOpen(false);
              onFilterToSelected(runIds);
            }}
          >
            <ListItemIcon>
              <Iconify icon="mdi:filter-outline" width={16} />
            </ListItemIcon>
            <ListItemText primary="Filter to selected" />
          </MenuItem>
        ) : null}
      </Menu>
      <TagPicker
        anchorEl={pickerAnchor}
        onClose={() => setPickerAnchor(null)}
        itemTagIds={tagging.lists}
        onChange={tagging.onChange}
        title={tagging.title}
      />
    </>
  );
}

RunSelectionMore.propTypes = {
  runs: PropTypes.array,
  onFilterToSelected: PropTypes.func,
};
