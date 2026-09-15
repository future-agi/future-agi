import PropTypes from "prop-types";
import {
  Tooltip,
  Menu,
  MenuItem,
  ListItemIcon,
  ListItemText,
} from "@mui/material";
import Iconify from "src/components/iconify";
import {
  ENV_STATUS,
  ROW_ACTION_LABEL,
  BUILDING_TOOLTIP,
  DELETE_TONE,
} from "../myEnvironments.constants";

export default function RowActionsMenu({ menuFor, onClose, onRun, onDeleteRequest }) {
  const active = menuFor?.row;
  const buildingActive = active?.status === ENV_STATUS.BUILDING;
  const runLabel =
    active?.runsTotal > 0 && active?.status !== ENV_STATUS.BUILDING
      ? ROW_ACTION_LABEL.rerun
      : ROW_ACTION_LABEL.run;

  const runItem = (
    <MenuItem
      disabled={buildingActive}
      onClick={() => {
        onRun?.(active);
        onClose?.();
      }}
      sx={{ typography: "s2" }}
    >
      <ListItemIcon sx={{ minWidth: 28 }}>
        <Iconify icon="solar:play-linear" width={16} />
      </ListItemIcon>
      <ListItemText
        primary={runLabel}
        primaryTypographyProps={{ sx: { typography: "s2" } }}
      />
    </MenuItem>
  );

  return (
    <Menu
      anchorEl={menuFor?.anchorEl}
      open={!!menuFor}
      onClose={onClose}
      anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
      transformOrigin={{ vertical: "top", horizontal: "right" }}
      slotProps={{ paper: { sx: { minWidth: 200 } } }}
    >
      {buildingActive ? (
        <Tooltip arrow placement="left" title={BUILDING_TOOLTIP}>
          <span>{runItem}</span>
        </Tooltip>
      ) : (
        runItem
      )}
      <MenuItem
        onClick={() => {
          onDeleteRequest?.(active);
          onClose?.();
        }}
        sx={{ typography: "s2", color: DELETE_TONE.main }}
      >
        <ListItemIcon sx={{ minWidth: 28, color: DELETE_TONE.main }}>
          <Iconify icon="solar:trash-bin-trash-linear" width={16} />
        </ListItemIcon>
        <ListItemText
          primary={ROW_ACTION_LABEL.delete}
          primaryTypographyProps={{
            sx: { typography: "s2", color: DELETE_TONE.main },
          }}
        />
      </MenuItem>
    </Menu>
  );
}

RowActionsMenu.propTypes = {
  menuFor: PropTypes.shape({
    row: PropTypes.shape({
      id: PropTypes.string,
      name: PropTypes.string,
      status: PropTypes.string,
      runsTotal: PropTypes.number,
    }),
    anchorEl: PropTypes.object,
  }),
  onClose: PropTypes.func,
  onRun: PropTypes.func,
  onDeleteRequest: PropTypes.func,
};
