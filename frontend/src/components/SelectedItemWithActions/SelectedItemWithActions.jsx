import { IconButton, Stack, Typography, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import SvgColor from "../svg-color";

const actions = [
  {
    title: "Edit",
    src: "/assets/icons/ic_edit_pencil.svg",
  },
  {
    title: "Remove",
    src: "/assets/icons/ic_close.svg",
  },
];

export default function SelectedItemWithActions({
  label,
  iconSx,
  onEdit,
  onRemove,
  value,
  sx = {},
}) {
  const theme = useTheme();

  const handleAction = (title) => {
    switch (title) {
      case "Edit":
        onEdit();
        break;
      case "Remove":
        onRemove(value);
        break;

      default:
        break;
    }
  };
  return (
    <Stack
      direction={"row"}
      justifyContent={"space-between"}
      alignItems={"center"}
      sx={{
        backgroundColor: "background.neutral",
        borderRadius: theme.spacing(1),
        padding: theme.spacing(1),
        ...sx,
      }}
    >
      <Typography
        variant="s3"
        color={"text.primary"}
        fontWeight={"fontWeightRegular"}
      >
        {label}
      </Typography>
      <Stack direction={"row"} gap={0} alignItems={"center"}>
        {actions?.map((action, index) => (
          <IconButton
            onClick={() => handleAction(action.title)}
            key={index}
            title={action.title}
          >
            <SvgColor
              sx={{
                height: theme.spacing(2),
                width: theme.spacing(2),
                color: "text.primary",
                ...iconSx,
              }}
              src={action.src}
            />
          </IconButton>
        ))}
      </Stack>
    </Stack>
  );
}

SelectedItemWithActions.propTypes = {
  label: PropTypes.string,
  onEdit: PropTypes.func,
  onRemove: PropTypes.func,
  iconSx: PropTypes.object,
  value: PropTypes.string,
  sx: PropTypes.object,
};
