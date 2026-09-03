import {
  Box,
  Button,
  IconButton,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Typography,
  useTheme,
} from "@mui/material";
import React, { useState } from "react";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import PropTypes from "prop-types";
import { formatDistanceToNow } from "date-fns";
import SvgColor from "../svg-color";
import { isBlackBackgroundLogo } from "../custom-model-dropdown/common";

const SimulatorAgentCellRenderer = (params) => {
  const theme = useTheme();
  const { data, value, column, colDef } = params;
  const field = colDef?.field;
  const cellParams = column?.colDef?.cellRendererParams;
  const onEdit = cellParams?.onEdit;
  const onDelete = cellParams?.onDelete;
  const [anchorEl, setAnchorEl] = useState(null);
  const open = Boolean(anchorEl);

  const handleClick = (event) => {
    event.stopPropagation();
    setAnchorEl(event.currentTarget);
  };

  const handleClose = () => {
    setAnchorEl(null);
  };

  const handleEdit = () => {
    handleClose();
    onEdit?.(data);
    if (data?.id) {
      trackEvent(Events.simulatorAgentEditClicked, {
        [PropertyName.click]: true,
        [PropertyName.id]: data?.id,
      });
    }
  };

  const handleDelete = () => {
    handleClose();
    onDelete?.(data);
    handleClose();
  };

  const renderContent = () => {
    switch (field) {
      case "name":
        return (
          <Typography
            variant="body2"
            sx={{ fontWeight: 500, fontSize: "14px", color: "text.primary" }}
          >
            {value}
          </Typography>
        );
      case "model":
        return value && typeof value === "string" ? (
          <IconButton
            sx={{
              borderRadius: theme.spacing(0.5),
              backgroundColor: "background.paper",
              border: "1px solid",
              borderColor: "divider",
              paddingY: theme.spacing(0.5),
              paddingX: theme.spacing(1),
              gap: theme.spacing(1),
              display: "flex",
              alignItems: "center",
              justifyContent: "flex-start",
              maxWidth: "200px",
              minWidth: "100px",
            }}
          >
            {data?.logoUrl ? (
              <Box
                component="img"
                src={data?.logoUrl}
                alt={data?.model_name}
                sx={{
                  width: theme.spacing(2),
                  height: theme.spacing(2),
                  objectFit: "cover",
                  ...(theme.palette.mode === "dark" &&
                    isBlackBackgroundLogo(data?.logoUrl) && {
                      filter: "invert(1) brightness(2)",
                    }),
                }}
              />
            ) : null}
            <Typography
              typography="s3"
              fontWeight="fontWeightMedium"
              color="text.primary"
              sx={{
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
                overflow: "hidden",
              }}
            >
              {value}
            </Typography>
          </IconButton>
        ) : (
          "-"
        );

      case "actions":
        return (
          <>
            <Button
              variant="outlined"
              size="small"
              onClick={handleClick}
              sx={{
                display: "flex",
                gap: 1,
                borderRadius: theme.spacing(1),
                borderColor: `${theme.palette.divider} !important`,
                minWidth: 71,
                px: 1,
                py: 0.25,
              }}
            >
              <SvgColor
                sx={{
                  bgcolor: "text.disabled",
                  height: 16.5,
                  width: 16.5,
                }}
                src="/assets/icons/action_buttons/ic_configure.svg"
              />
              <SvgColor
                sx={{
                  bgcolor: "text.disabled",
                  height: 16.5,
                  width: 16.5,
                  transform: "rotate(90deg)",
                }}
                src="/assets/icons/custom/lucide--chevron-right.svg"
              />
            </Button>
            <Menu
              anchorEl={anchorEl}
              open={open}
              onClose={handleClose}
              PaperProps={{
                sx: {
                  mt: 1,
                  minWidth: 120,
                },
              }}
              transformOrigin={{ horizontal: "right", vertical: "top" }}
              anchorOrigin={{ horizontal: "right", vertical: "bottom" }}
            >
              <MenuItem onClick={handleEdit}>
                <ListItemIcon sx={{ minWidth: "unset", mr: 0 }}>
                  <SvgColor
                    src="/assets/icons/ic_pen.svg"
                    sx={{
                      width: 16,
                      height: 16,
                      ml: 0.5,
                    }}
                  />
                </ListItemIcon>
                <ListItemText
                  primary="Edit"
                  primaryTypographyProps={{
                    typography: "s1",
                    fontWeight: "500",
                  }}
                />
              </MenuItem>
              <MenuItem onClick={handleDelete} sx={{ color: "error.main" }}>
                <ListItemIcon sx={{ minWidth: "unset", mr: 0 }}>
                  <SvgColor
                    src="/assets/icons/ic_delete.svg"
                    sx={{
                      height: 20,
                      width: 20,
                    }}
                  />
                </ListItemIcon>
                <ListItemText
                  primary="Delete"
                  primaryTypographyProps={{
                    typography: "s1",
                    fontWeight: "500",
                  }}
                />
              </MenuItem>
            </Menu>
          </>
        );
      case "created_at":
        return (
          <Typography variant="body2">
            {formatDistanceToNow(new Date(value), { addSuffix: true })}
          </Typography>
        );
      case "llmTemperature":
        return (
          <Typography variant="body2">{params.value.toFixed(1)}</Typography>
        );
      default:
        return <Typography typography={"s1"}>{value}</Typography>;
    }
  };

  return (
    <Box height={"100%"} display={"flex"} alignItems={"center"}>
      {renderContent()}
    </Box>
  );
};

SimulatorAgentCellRenderer.propTypes = {
  onEdit: PropTypes.func,
  onDelete: PropTypes.func,
};

export default SimulatorAgentCellRenderer;
