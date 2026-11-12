import React, { useState, useRef } from "react";
import PropTypes from "prop-types";
import {
  ButtonBase,
  Popover,
  List,
  ListItemButton,
  ListItemText,
  Typography,
  useTheme,
} from "@mui/material";

const OverflowButton = ({
  count,
  hiddenTabs,
  activeTabInOverflow,
  onTabChange,
}) => {
  const theme = useTheme();
  const [open, setOpen] = useState(false);
  const anchorRef = useRef(null);

  if (count <= 0) return null;

  const label = activeTabInOverflow
    ? `${activeTabInOverflow.name} \u25BE`
    : `+${count}`;

  return (
    <>
      <ButtonBase
        ref={anchorRef}
        onClick={() => setOpen(true)}
        sx={{
          px: 1,
          py: 0.75,
          borderRadius: "4px",
          fontSize: 13,
          fontWeight: "fontWeightMedium",
          color: activeTabInOverflow ? "primary.main" : "text.secondary",
          "&:hover": {
            backgroundColor: "action.hover",
            color: "primary.main",
          },
        }}
      >
        <Typography
          variant="body2"
          sx={{ fontSize: 13, fontWeight: "inherit" }}
        >
          {label}
        </Typography>
      </ButtonBase>

      <Popover
        open={open}
        anchorEl={anchorRef.current}
        onClose={() => setOpen(false)}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        transformOrigin={{ vertical: "top", horizontal: "left" }}
        slotProps={{
          paper: {
            sx: { minWidth: 200, maxHeight: 300 },
          },
        }}
      >
        <List dense disablePadding>
          {hiddenTabs.map((tab) => (
            <ListItemButton
              key={tab.key}
              selected={tab.key === activeTabInOverflow?.key}
              onClick={() => {
                onTabChange(tab.key);
                setOpen(false);
              }}
              sx={{ py: 0.75 }}
            >
              <ListItemText
                primary={tab.name}
                primaryTypographyProps={{ variant: "body2", fontSize: 13 }}
              />
            </ListItemButton>
          ))}
        </List>
      </Popover>
    </>
  );
};

OverflowButton.propTypes = {
  count: PropTypes.number.isRequired,
  hiddenTabs: PropTypes.arrayOf(
    PropTypes.shape({
      key: PropTypes.string.isRequired,
      name: PropTypes.string.isRequired,
    }),
  ).isRequired,
  activeTabInOverflow: PropTypes.shape({
    key: PropTypes.string.isRequired,
    name: PropTypes.string.isRequired,
  }),
  onTabChange: PropTypes.func.isRequired,
};

export default React.memo(OverflowButton);
