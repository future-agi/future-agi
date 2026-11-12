// A DataTable group header row's content (W9-SCOPE §5 A3): a chevron that
// opens or closes the group, `Asha Rao · 4 datasets · 1 below baseline`,
// and `Show all 37` when the group holds more rows than it shows (the
// caller's `group.onShowAll`, e.g. adding that value as a filter chip).
import React from "react";
import PropTypes from "prop-types";
import { Box, Button, ButtonBase, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { groupHeaderText } from "./groupRows";

export default function GroupHeaderCell({ group, noun, onToggle }) {
  const text = groupHeaderText(group, noun);
  const shown = group?.rows?.length ?? 0;
  const more = !group?.collapsed && group?.onShowAll && group.count > shown;
  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 1,
        minWidth: 0,
        width: "100%",
      }}
      data-testid="group-header"
      data-group-key={group?.key}
    >
      <ButtonBase
        onClick={(e) => {
          e.stopPropagation();
          onToggle?.(group.key);
        }}
        aria-expanded={!group?.collapsed}
        aria-label={`${group?.collapsed ? "Open" : "Close"} group ${group?.label}`}
        sx={{ borderRadius: 0.5, p: 0.25, color: "text.secondary" }}
        disabled={!onToggle}
      >
        <Iconify
          icon={group?.collapsed ? "mdi:chevron-right" : "mdi:chevron-down"}
          width={18}
        />
      </ButtonBase>
      <Typography
        component="span"
        typography="s1"
        fontWeight="fontWeightMedium"
        noWrap
        sx={{ minWidth: 0 }}
      >
        {text}
      </Typography>
      {more && (
        <Button
          size="small"
          color="inherit"
          onClick={(e) => {
            e.stopPropagation();
            group.onShowAll();
          }}
          sx={{ typography: "s2", color: "text.secondary", flexShrink: 0 }}
        >
          {`Show all ${group.count}`}
        </Button>
      )}
    </Box>
  );
}

GroupHeaderCell.propTypes = {
  group: PropTypes.shape({
    key: PropTypes.string,
    label: PropTypes.string,
    count: PropTypes.number,
    below_baseline: PropTypes.number,
    collapsed: PropTypes.bool,
    rows: PropTypes.array,
    onShowAll: PropTypes.func,
  }).isRequired,
  noun: PropTypes.string,
  onToggle: PropTypes.func,
};
