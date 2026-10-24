import React from "react";
import PropTypes from "prop-types";
import Link from "@mui/material/Link";
import Button from "@mui/material/Button";
import MenuItem from "@mui/material/MenuItem";
import TableRow from "@mui/material/TableRow";
import TableCell from "@mui/material/TableCell";
import ListItemText from "@mui/material/ListItemText";
import { useBoolean } from "src/hooks/use-boolean";
import { VictoryChart, VictoryLine, VictoryTheme, VictoryAxis } from "victory";
import Iconify from "src/components/iconify";
import { ConfirmDialog } from "src/components/custom-dialog";
import CustomPopover, { usePopover } from "src/components/custom-popover";
import { Stack } from "@mui/material";

import { getAlertingBreakdown } from "./utils/model";

// ----------------------------------------------------------------------

export default function ModelTableRow({
  row,
  selected,
  onDeleteRow,
  onEditRow,
  onViewRow,
}) {
  const { userModelId, modelType, volume, totalCount, monitors } = row;

  const confirm = useBoolean();

  const popover = usePopover();

  const monitorDetail = getAlertingBreakdown(monitors);

  const axisStyle = {
    axis: { stroke: "transparent" },
    ticks: { stroke: "transparent" },
    tickLabels: { fill: "transparent" },
    grid: { stroke: "transparent" }, // remove this line if grid lines are needed
  };

  return (
    <>
      <TableRow hover selected={selected}>
        <TableCell sx={{ display: "flex", alignItems: "center" }}>
          <ListItemText
            disableTypography
            primary={
              <Link
                noWrap
                color="inherit"
                variant="subtitle2"
                onClick={onViewRow}
                sx={{ cursor: "pointer" }}
              >
                {userModelId}
              </Link>
            }
          />
        </TableCell>

        <TableCell>
          <ListItemText
            primary={modelType}
            // primaryTypographyProps={{ typography: "body2", noWrap: true }}
          />
        </TableCell>

        <TableCell>
          <Stack alignItems="center" direction="row">
            {monitorDetail.numTriggers === null && (
              <Iconify
                icon="solar:danger-triangle-linear"
                style={{ color: "grey" }}
              />
            )}
            {monitorDetail.numTriggers > 0 && (
              <Iconify
                icon="solar:danger-triangle-linear"
                style={{ color: "red" }}
              />
            )}
            {monitorDetail.numTriggers === 0 && (
              <Iconify
                icon="solar:check-circle-linear"
                style={{ color: "green" }}
              />
            )}

            {monitorDetail.numTriggers}
          </Stack>
        </TableCell>

        <TableCell>
          <Stack alignItems="center" direction="row">
            {monitorDetail.performance === null && (
              <Iconify
                icon="solar:danger-triangle-linear"
                style={{ color: "grey" }}
              />
            )}
            {monitorDetail.performance === true && (
              <Iconify
                icon="solar:danger-triangle-linear"
                style={{ color: "red" }}
              />
            )}
            {monitorDetail.performance === false && (
              <Iconify
                icon="solar:check-circle-linear"
                style={{ color: "green" }}
              />
            )}
          </Stack>
        </TableCell>

        <TableCell>
          <Stack alignItems="center" direction="row">
            {monitorDetail.drift === null && (
              <Iconify
                icon="solar:danger-triangle-linear"
                style={{ color: "grey" }}
              />
            )}
            {monitorDetail.drift === true && (
              <Iconify
                icon="solar:danger-triangle-linear"
                style={{ color: "red" }}
              />
            )}
            {monitorDetail.drift === false && (
              <Iconify
                icon="solar:check-circle-linear"
                style={{ color: "green" }}
              />
            )}
          </Stack>
        </TableCell>

        <TableCell>
          <Stack alignItems="center" direction="row">
            {monitorDetail.analytics === null && (
              <Iconify
                icon="solar:danger-triangle-linear"
                style={{ color: "grey" }}
              />
            )}
            {monitorDetail.analytics === true && (
              <Iconify
                icon="solar:danger-triangle-linear"
                style={{ color: "red" }}
              />
            )}
            {monitorDetail.analytics === false && (
              <Iconify
                icon="solar:check-circle-linear"
                style={{ color: "green" }}
              />
            )}
          </Stack>
        </TableCell>

        <TableCell>
          <Stack direction="row">
            <div>
              <VictoryChart
                theme={VictoryTheme.material}
                domainPadding={10}
                height={250} // Set the height to fit in a table cell
                width={600} // Set the width as needed
              >
                <VictoryAxis style={axisStyle} />
                <VictoryAxis dependentAxis style={axisStyle} />
                <VictoryLine
                  style={{
                    data: { strokeWidth: 8 },
                    // parent: { border: "1px solid" },
                  }}
                  data={volume}
                />
              </VictoryChart>
            </div>
            <div>{totalCount}</div>
          </Stack>
        </TableCell>
      </TableRow>

      <CustomPopover
        open={popover.open}
        onClose={popover.onClose}
        arrow="right-top"
        sx={{ width: 140 }}
      >
        <MenuItem
          onClick={() => {
            onViewRow();
            popover.onClose();
          }}
        >
          <Iconify icon="solar:eye-bold" />
          View
        </MenuItem>

        <MenuItem
          onClick={() => {
            onEditRow();
            popover.onClose();
          }}
        >
          <Iconify icon="solar:pen-bold" />
          Edit
        </MenuItem>

        <MenuItem
          onClick={() => {
            confirm.onTrue();
            popover.onClose();
          }}
          // eslint-disable-next-line no-restricted-syntax -- ds: destructive menu item
          sx={{ color: "status.danger.fg" }}
        >
          <Iconify icon="solar:trash-bin-trash-bold" />
          Delete
        </MenuItem>
      </CustomPopover>

      <ConfirmDialog
        open={confirm.value}
        onClose={confirm.onFalse}
        title="Delete"
        content="Are you sure want to delete?"
        action={
          <Button variant="contained" color="error" onClick={onDeleteRow}>
            Delete
          </Button>
        }
      />
    </>
  );
}

ModelTableRow.propTypes = {
  onDeleteRow: PropTypes.func,
  onEditRow: PropTypes.func,
  onSelectRow: PropTypes.func,
  onViewRow: PropTypes.func,
  row: PropTypes.object,
  selected: PropTypes.bool,
};
