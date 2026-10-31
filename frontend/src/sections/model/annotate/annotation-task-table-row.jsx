import React from "react";
import { alpha, useTheme } from "@mui/material/styles";
import {
  Avatar,
  AvatarGroup,
  Divider,
  IconButton,
  MenuItem,
  Stack,
  TableCell,
  TableRow,
  Typography,
  avatarGroupClasses,
  tableCellClasses,
  tableRowClasses,
} from "@mui/material";
import PropTypes from "prop-types";

import { useBoolean } from "src/hooks/use-boolean";
import CustomPopover, { usePopover } from "src/components/custom-popover";
import Iconify from "src/components/iconify";

export default function AnnotationTaskTableRow({ row }) {
  const theme = useTheme();

  const details = useBoolean();
  const popover = usePopover();

  const defaultStyles = {
    borderTop: `solid 1px ${alpha(theme.palette.text.disabled, 0.16)}`,
    borderBottom: `solid 1px ${alpha(theme.palette.text.disabled, 0.16)}`,
    "&:first-of-type": {
      borderTopLeftRadius: 16,
      borderBottomLeftRadius: 16,
      borderLeft: `solid 1px ${alpha(theme.palette.text.disabled, 0.16)}`,
    },
    "&:last-of-type": {
      borderTopRightRadius: 16,
      borderBottomRightRadius: 16,
      borderRight: `solid 1px ${alpha(theme.palette.text.disabled, 0.16)}`,
    },
  };

  function handleClick() {
    throw new Error("Function not implemented.");
  }

  function stringAvatar(name) {
    return {
      children: `${name.split(" ")[0][0]}${name.split(" ")[1][0]}`,
    };
  }

  return (
    <>
      <TableRow
        // selected={selected}
        sx={{
          borderRadius: 2,
          [`&.${tableRowClasses.selected}, &:hover`]: {
            backgroundColor: "background.paper",
            transition: theme.transitions.create(["background-color"], {
              duration: theme.transitions.duration.shortest,
            }),
            "&:hover": {
              backgroundColor: "background.paper",
            },
          },
          [`& .${tableCellClasses.root}`]: {
            ...defaultStyles,
          },
          ...(details.value && {
            [`& .${tableCellClasses.root}`]: {
              ...defaultStyles,
            },
          }),
        }}
      >
        {/* <TableCell padding="checkbox">
          <Checkbox
            checked={selected}
            onDoubleClick={() => console.info('ON DOUBLE CLICK')}
            onClick={onSelectRow}
          />
        </TableCell> */}

        <TableCell onClick={handleClick}>
          <Stack direction="row" alignItems="center" spacing={2}>
            <Typography
              noWrap
              variant="inherit"
              sx={{
                maxWidth: 360,
                cursor: "pointer",
                ...(details.value && { fontWeight: "fontWeightSemiBold" }),
              }}
            >
              {row.taskName}
            </Typography>
          </Stack>
        </TableCell>

        <TableCell onClick={handleClick} sx={{ whiteSpace: "nowrap" }}>
          {row.aiModel.userModelId}
        </TableCell>

        <TableCell
          onClick={handleClick}
          sx={{ whiteSpace: "nowrap" }}
        ></TableCell>

        <TableCell
          sx={{ fontVariantNumeric: "tabular-nums" }}
          align="right"
          onClick={handleClick}
        >
          <AvatarGroup
            max={4}
            sx={{
              display: "inline-flex",
              [`& .${avatarGroupClasses.avatar}`]: {
                width: 24,
                height: 24,
                "&:first-of-type": {
                  fontSize: 12,
                },
              },
            }}
          >
            {row.assignedUsers &&
              row.assignedUsers.map((person) => (
                <Avatar
                  key={person.id}
                  alt={person.name}
                  {...stringAvatar(person.name)}
                />
              ))}
          </AvatarGroup>
        </TableCell>

        <TableCell
          align="right"
          sx={{
            fontVariantNumeric: "tabular-nums",
            px: 1,
            whiteSpace: "nowrap",
          }}
        >
          <IconButton
            color={popover.open ? "inherit" : "default"}
            onClick={popover.onOpen}
          >
            <Iconify icon="solar:menu-dots-vertical-linear" />
          </IconButton>
        </TableCell>
      </TableRow>

      <CustomPopover
        open={popover.open}
        onClose={popover.onClose}
        arrow="right-top"
        sx={{ width: 160 }}
      >
        <MenuItem onClick={() => {}}>
          <Iconify icon="solar:link-linear" />
          Copy Link
        </MenuItem>

        <MenuItem onClick={() => {}}>
          <Iconify icon="solar:share-bold" />
          Share
        </MenuItem>

        <Divider sx={{ borderStyle: "dashed" }} />

        <MenuItem
          onClick={() => {}}
          // eslint-disable-next-line no-restricted-syntax -- ds: destructive menu item
          sx={{ color: "status.danger.fg" }}
        >
          <Iconify icon="solar:trash-bin-trash-bold" />
          Delete
        </MenuItem>
      </CustomPopover>
    </>
  );
}

AnnotationTaskTableRow.propTypes = {
  onDeleteRow: PropTypes.func,
  onSelectRow: PropTypes.func,
  row: PropTypes.object,
  selected: PropTypes.bool,
};
