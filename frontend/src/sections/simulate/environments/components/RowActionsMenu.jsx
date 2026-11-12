import PropTypes from "prop-types";
import { Menu, MenuItem, ListItemIcon, ListItemText } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import {
  ENV_STATUS,
  ROW_ACTION_LABEL,
  BUILDING_TOOLTIP,
  DELETE_TONE,
} from "../myEnvironments.constants";
import { WORKSPACE_COPY } from "../workspace/workspace.constants";
import useSimulationAccess from "../hooks/useSimulationAccess";
import ViewerLock from "./ViewerLock";

// Icon sits tight against the label — a bare icon width plus one step of margin,
// not MUI's default 56px list-icon gutter.
const ICON_SX = { minWidth: 0, mr: 1 };

export default function RowActionsMenu({ menuFor, onClose, onOpen, onRun, onDeleteRequest }) {
  const active = menuFor?.row;
  const { canEdit, canRun } = useSimulationAccess();
  const buildingActive = [
    ENV_STATUS.BUILDING,
    ENV_STATUS.FINALIZING,
    ENV_STATUS.CANCELLING,
  ].includes(active?.status);
  const runLabel =
    active?.runsTotal > 0 && !buildingActive
      ? ROW_ACTION_LABEL.rerun
      : ROW_ACTION_LABEL.run;

  const runItem = (
    <MenuItem
      disabled={buildingActive || !canRun}
      onClick={() => {
        onRun?.(active);
        onClose?.();
      }}
      sx={{ typography: "s2" }}
    >
      <ListItemIcon sx={ICON_SX}>
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
      <MenuItem
        onClick={() => {
          onOpen?.(active);
          onClose?.();
        }}
        sx={{ typography: "s2" }}
      >
        <ListItemIcon sx={ICON_SX}>
          <Iconify icon="solar:arrow-right-linear" width={16} />
        </ListItemIcon>
        <ListItemText
          primary={ROW_ACTION_LABEL.open}
          primaryTypographyProps={{ sx: { typography: "s2" } }}
        />
      </MenuItem>
      {buildingActive || !canRun ? (
        <CustomTooltip
          show
          arrow
          placement="left"
          size="small"
          title={buildingActive ? BUILDING_TOOLTIP : WORKSPACE_COPY.viewer.run}
        >
          <span>{runItem}</span>
        </CustomTooltip>
      ) : (
        runItem
      )}
      <ViewerLock locked={!canEdit} title={WORKSPACE_COPY.viewer.delete}>
        <MenuItem
          disabled={!canEdit}
          onClick={() => {
            onDeleteRequest?.(active);
            onClose?.();
          }}
          sx={{ typography: "s2" }}
        >
          <ListItemIcon sx={{ ...ICON_SX, color: DELETE_TONE.main }}>
            <Iconify icon="solar:trash-bin-trash-linear" width={16} />
          </ListItemIcon>
          <ListItemText
            primary={ROW_ACTION_LABEL.delete}
            primaryTypographyProps={{
              sx: { typography: "s2", color: DELETE_TONE.main },
            }}
          />
        </MenuItem>
      </ViewerLock>
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
  onOpen: PropTypes.func,
  onRun: PropTypes.func,
  onDeleteRequest: PropTypes.func,
};
