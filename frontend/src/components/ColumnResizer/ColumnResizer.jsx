import { Button, Popover, Typography, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import React, { useState } from "react";

const ColumnResizer = (props) => {
  const theme = useTheme();
  const [active, setActive] = useState(props.defaultActive || "Short");
  return (
    <Popover
      open={props.open}
      onClose={props.onClose}
      anchorEl={props.anchorEl}
      anchorOrigin={{
        vertical: "bottom",
        horizontal: "center",
      }}
      transformOrigin={{
        vertical: -1,
        horizontal: 10,
      }}
      slotProps={{
        paper: {
          sx: {
            display: "flex",
            flexDirection: "column",
            padding: theme.spacing(1.5),
            gap: theme.spacing(0.25),
            borderRadius: theme.spacing(1),
          },
        },
      }}
      sx={{
        borderColor: "divider",
        borderWidth: "1px",
      }}
    >
      {Object.keys(props.sizeMapping).map((key, index) => (
        <Button
          size="small"
          key={index}
          sx={{
            width: "125px",
            bgcolor: active === key ? "border.default" : undefined,
          }}
          onClick={() => {
            if (active !== key) {
              setActive(key);
              props.setCellHeight(key);
              props.onSelect?.(props.sizeMapping[key]);
              props.onClose();
            }
          }}
        >
          <Typography
            typography={"s1"}
            sx={{
              width: "100%",
              textAlign: "left",
            }}
          >
            {key}
          </Typography>
        </Button>
      ))}
    </Popover>
  );
};

export default ColumnResizer;

ColumnResizer.propTypes = {
  anchorEl: PropTypes.object,
  open: PropTypes.bool,
  onClose: PropTypes.func,
  sizeMapping: PropTypes.object,
  defaultActive: PropTypes.string,
  setCellHeight: PropTypes.func,
  onSelect: PropTypes.func,
};
