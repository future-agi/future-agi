import {
  Popover,
  Box,
  Typography,
  ListItem,
  ListItemText,
  ListItemButton,
  List,
  ListItemIcon,
} from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { useController } from "react-hook-form";
import Iconify from "src/components/iconify";
import { DynamicColumns, StaticColumns } from "./common";

const ColumnTypeDropDown = React.forwardRef(
  ({ open, onClose, openDynamicColumnTypeDrawer, control }, ref) => {
    const { field } = useController({ name: "columnType", control });

    return (
      <Popover
        open={open}
        onClose={onClose}
        anchorEl={ref?.current}
        anchorOrigin={{
          vertical: "bottom",
          horizontal: "left",
        }}
        transformOrigin={{
          vertical: "top",
          horizontal: "left",
        }}
        PaperProps={{
          sx: {
            width: ref?.current?.clientWidth,
          },
        }}
      >
        <Box sx={{ width: "100%", paddingX: 1 }}>
          <Typography
            sx={{ paddingTop: 1 }}
            color="text.secondary"
            fontWeight={600}
            fontSize={12}
          >
            Static Columns
          </Typography>
          <List dense>
            {StaticColumns.map(({ label, value, icon }) => (
              <ListItem key={value} sx={{ paddingX: 0 }}>
                <ListItemButton
                  sx={{ paddingX: 0, borderRadius: 1 }}
                  selected={field.value === value}
                  onClick={() => {
                    field.onChange(value);
                    onClose();
                  }}
                >
                  <ListItemIcon sx={{ marginRight: 1, marginLeft: 1 }}>
                    <Iconify icon={icon} />
                  </ListItemIcon>
                  <ListItemText
                    primary={label}
                    primaryTypographyProps={{
                      sx: { fontSize: "13px", fontWeight: 400 },
                    }}
                  />
                </ListItemButton>
              </ListItem>
            ))}
          </List>
          <Typography
            sx={{ paddingX: 1, paddingTop: 1 }}
            color="text.secondary"
            fontWeight={600}
            fontSize={12}
          >
            Dynamic Columns
          </Typography>
          <List dense>
            {DynamicColumns.map(({ label, value, icon, color }) => (
              <ListItem key={value} sx={{ paddingX: 0 }}>
                <ListItemButton
                  sx={{ paddingX: 0, borderRadius: 1 }}
                  onClick={() => {
                    openDynamicColumnTypeDrawer(value);
                  }}
                >
                  <ListItemIcon sx={{ marginRight: 1, marginLeft: 1 }}>
                    <Iconify icon={icon} sx={{ color: color }} />
                  </ListItemIcon>
                  <ListItemText
                    primary={label}
                    primaryTypographyProps={{
                      sx: {
                        fontSize: "13px",
                        fontWeight: 400,
                        color: color,
                      },
                    }}
                  />
                </ListItemButton>
              </ListItem>
            ))}
          </List>
        </Box>
      </Popover>
    );
  },
);

ColumnTypeDropDown.displayName = "ColumnTypeDropDown";

ColumnTypeDropDown.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  anchorEl: PropTypes.instanceOf(Element).isRequired,
  openDynamicColumnTypeDrawer: PropTypes.func,
  control: PropTypes.any,
};

export default ColumnTypeDropDown;
