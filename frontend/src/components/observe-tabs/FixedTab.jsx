import React from "react";
import PropTypes from "prop-types";
import { ButtonBase, Typography } from "@mui/material";
import EnterpriseMark from "src/components/capability-gate/EnterpriseMark";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip/CustomTooltip";

// `locked`: the tab's feature isn't in this deployment's license or plan. It
// still opens, onto the upgrade gate; a lock says so.
const FixedTab = ({
  tabKey,
  label,
  icon,
  shortcut,
  isActive,
  locked = false,
  onClick,
}) => (
  <CustomTooltip
    show={!isActive}
    title={
      locked ? `Enterprise feature · Press ${shortcut}` : `Press ${shortcut}`
    }
    placement="bottom"
    arrow
    size="small"
    type="black"
  >
    <ButtonBase
      onClick={() => onClick(tabKey)}
      // The tooltip would otherwise become the tab's name ("Press 3").
      aria-label={locked ? `${label}, Enterprise feature` : label}
      sx={{
        display: "inline-flex",
        alignItems: "center",
        gap: 1,
        height: 26,
        px: "5px",
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "4px",
        bgcolor: isActive ? "action.hover" : "background.paper",
        color: "text.primary",
        transition: "background-color 100ms",
        "&:hover": {
          bgcolor: isActive ? "action.selected" : "background.neutral",
        },
      }}
    >
      {icon && (
        <Iconify icon={icon} width={16} sx={{ color: "text.primary" }} />
      )}
      <Typography
        sx={{
          fontSize: 13,
          fontWeight: 500,
          fontFamily: "'IBM Plex Sans', sans-serif",
          color: "text.primary",
          whiteSpace: "nowrap",
          lineHeight: "20px",
        }}
      >
        {label}
      </Typography>
      {locked && <EnterpriseMark compact />}
    </ButtonBase>
  </CustomTooltip>
);

FixedTab.propTypes = {
  tabKey: PropTypes.string.isRequired,
  label: PropTypes.string.isRequired,
  icon: PropTypes.string,
  shortcut: PropTypes.string.isRequired,
  isActive: PropTypes.bool.isRequired,
  locked: PropTypes.bool,
  onClick: PropTypes.func.isRequired,
};

export default React.memo(FixedTab);
