import React from "react";
import PropTypes from "prop-types";
import { ButtonBase, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip/CustomTooltip";

const FixedTab = ({ tabKey, label, icon, shortcut, isActive, onClick }) => (
  <CustomTooltip
    show={!isActive}
    title={`Press ${shortcut}`}
    placement="bottom"
    arrow
    size="small"
    type="black"
  >
    <ButtonBase
      onClick={() => onClick(tabKey)}
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
    </ButtonBase>
  </CustomTooltip>
);

FixedTab.propTypes = {
  tabKey: PropTypes.string.isRequired,
  label: PropTypes.string.isRequired,
  icon: PropTypes.string,
  shortcut: PropTypes.string.isRequired,
  isActive: PropTypes.bool.isRequired,
  onClick: PropTypes.func.isRequired,
};

export default React.memo(FixedTab);
