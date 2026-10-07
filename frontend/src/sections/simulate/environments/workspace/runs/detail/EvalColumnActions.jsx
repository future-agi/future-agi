import PropTypes from "prop-types";
import { ListItemIcon, ListItemText, Menu, MenuItem } from "@mui/material";
import Iconify from "src/components/iconify";
import { useEnvironmentRunTest } from "src/api/simulate-environments/environments";
import {
  EVAL_GONE_TOOLTIP,
  GRADING_TOOLTIP,
  HARNESS_ONLY_TOOLTIP,
  NOT_COMPLETED_TOOLTIP,
  NOT_EDITABLE_TOOLTIP,
} from "./allEvaluationsDrawer.constants";

// Set on the item, not the icon: MenuItem's own 36px icon slot outranks an
// icon-level sx. 16px matches the Columns picker's checkbox-to-label gap.
const ITEM_SX = {
  typography: "s2",
  alignItems: "flex-start",
  "& .MuiListItemIcon-root": { minWidth: 0, mr: 2, mt: 0.25 },
};

/**
 * The menu behind an eval column header's ⋮ on a run's table: re-run that eval
 * on this run, or edit it and then re-run it. Same rules as the run's All
 * Evaluations drawer, and the run page answers both through the same edit form
 * and confirm dialog (`onRerun`, `onEdit`).
 *
 * One instance serves every column; `menuFor` says which column's menu is
 * open.
 */
export default function EvalColumnActions({
  runTestId,
  canRun = false,
  grading = false,
  rerunPending = false,
  onRerun,
  onEdit,
  menuFor = null,
  onClose,
}) {
  const { data: configs, isPending: configsPending } = useEnvironmentRunTest(
    runTestId,
    { enabled: Boolean(runTestId) },
  );

  const config = menuFor
    ? (configs ?? []).find((c) => c.id === menuFor.evalId) ?? null
    : null;
  // Reasons only make sense once the column's eval has been looked up.
  const known = Boolean(menuFor) && !configsPending;
  const gone = known && !config;

  const rerunReason = !known
    ? null
    : gone
      ? EVAL_GONE_TOOLTIP
      : config.regradable !== true
        ? HARNESS_ONLY_TOOLTIP
        : !canRun
          ? NOT_COMPLETED_TOOLTIP
          : null;
  const rerunDisabled = !known || Boolean(rerunReason) || rerunPending;

  const editReason = !known
    ? null
    : gone
      ? EVAL_GONE_TOOLTIP
      : config.editable !== true
        ? NOT_EDITABLE_TOOLTIP
        : grading
          ? GRADING_TOOLTIP
          : !canRun
            ? NOT_COMPLETED_TOOLTIP
            : null;
  const editDisabled = !known || Boolean(editReason);

  const menuItem = ({ label, icon, reason, disabled, onClick }) => (
    <MenuItem
      disabled={disabled}
      onClick={() => {
        onClose?.();
        onClick();
      }}
      sx={ITEM_SX}
    >
      <ListItemIcon>
        <Iconify icon={icon} width={16} />
      </ListItemIcon>
      {/* A disabled menu item can't take focus, so its reason is written
          under it rather than hidden in a tooltip. */}
      <ListItemText
        primary={label}
        secondary={reason}
        primaryTypographyProps={{ sx: { typography: "s2" } }}
        secondaryTypographyProps={{
          sx: { typography: "s3", whiteSpace: "normal" },
        }}
      />
    </MenuItem>
  );

  return (
    <Menu
      anchorEl={menuFor?.anchorEl}
      open={Boolean(menuFor)}
      onClose={onClose}
      anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
      transformOrigin={{ vertical: "top", horizontal: "right" }}
      slotProps={{ paper: { sx: { minWidth: 240, maxWidth: 300 } } }}
    >
      {menuItem({
        label: "Re-run",
        icon: "solar:play-circle-linear",
        reason: rerunReason,
        disabled: rerunDisabled,
        onClick: () => onRerun?.([config]),
      })}
      {menuItem({
        label: "Edit",
        icon: "solar:pen-linear",
        reason: editReason,
        disabled: editDisabled,
        onClick: () => onEdit?.(config),
      })}
    </Menu>
  );
}

EvalColumnActions.propTypes = {
  runTestId: PropTypes.string,
  canRun: PropTypes.bool,
  grading: PropTypes.bool,
  rerunPending: PropTypes.bool,
  onRerun: PropTypes.func,
  onEdit: PropTypes.func,
  menuFor: PropTypes.shape({
    evalId: PropTypes.string,
    name: PropTypes.string,
    anchorEl: PropTypes.any,
  }),
  onClose: PropTypes.func,
};
