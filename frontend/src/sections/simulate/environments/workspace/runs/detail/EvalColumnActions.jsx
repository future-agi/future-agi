import PropTypes from "prop-types";
import { useState } from "react";
import { ListItemIcon, ListItemText, Menu, MenuItem } from "@mui/material";
import Iconify from "src/components/iconify";
import ConfirmRunEvaluations from "src/sections/common/EvaluationDrawer/ConfirmRunEvaluations";
import { useEnvironmentRunTest } from "src/api/simulate-environments/environments";
import AddEvaluationDrawer from "../../evals/AddEvaluationDrawer";
import {
  EVAL_GONE_TOOLTIP,
  GRADING_TOOLTIP,
  HARNESS_NOTE,
  HARNESS_ONLY_TOOLTIP,
  NOT_COMPLETED_TOOLTIP,
  NOT_EDITABLE_TOOLTIP,
} from "./allEvaluationsDrawer.constants";
import { useRegradeEvals } from "./useRegradeEvals";

const ICON_SX = { minWidth: 0, mr: 1 };

/**
 * The menu behind an eval column header's ⋮ on a run's table: re-run that eval
 * on this run, or edit it and then re-run it. Same rules, confirm dialog and
 * edit form as the run's All Evaluations drawer.
 *
 * One instance serves every column; `menuFor` says which column's menu is
 * open. The confirm dialog and the edit form outlive the menu.
 */
export default function EvalColumnActions({
  env,
  runTestId,
  executionId,
  canRun = false,
  grading = false,
  menuFor = null,
  onClose,
}) {
  // The configs the confirm dialog is about, or null while it's closed.
  const [confirming, setConfirming] = useState(null);
  // The eval open for editing, or null.
  const [editing, setEditing] = useState(null);

  const { data: configs, isPending: configsPending } = useEnvironmentRunTest(
    runTestId,
    { enabled: Boolean(runTestId) },
  );
  const runEvals = useRegradeEvals({ envId: env?.id, executionId });

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
  const rerunDisabled = !known || Boolean(rerunReason) || runEvals.isPending;

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
      sx={{ typography: "s2", alignItems: "flex-start" }}
    >
      <ListItemIcon sx={{ ...ICON_SX, mt: 0.25 }}>
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
    <>
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
          onClick: () => setConfirming([config]),
        })}
        {menuItem({
          label: "Edit",
          icon: "solar:pen-linear",
          reason: editReason,
          disabled: editDisabled,
          onClick: () => setEditing(config),
        })}
      </Menu>

      {/* No executionId: given one, the drawer would grade the run by name
          itself; here the confirm below does it. */}
      <AddEvaluationDrawer
        open={Boolean(editing)}
        env={env}
        editingEval={editing}
        onClose={() => setEditing(null)}
        onEdited={(updated) => {
          setEditing(null);
          setConfirming(updated ? [updated] : null);
        }}
      />

      <ConfirmRunEvaluations
        open={Boolean(confirming)}
        onClose={() => setConfirming(null)}
        onConfirm={(list) =>
          runEvals.regrade(list, { onSuccess: () => setConfirming(null) })
        }
        selectedUserEvalList={confirming || []}
        loading={runEvals.isPending}
        getNote={(list) =>
          list.some((c) => Object.keys(c.mapping || {}).length === 0)
            ? HARNESS_NOTE
            : null
        }
      />
    </>
  );
}

EvalColumnActions.propTypes = {
  env: PropTypes.shape({ id: PropTypes.string }),
  runTestId: PropTypes.string,
  executionId: PropTypes.string,
  canRun: PropTypes.bool,
  grading: PropTypes.bool,
  menuFor: PropTypes.shape({
    evalId: PropTypes.string,
    name: PropTypes.string,
    anchorEl: PropTypes.any,
  }),
  onClose: PropTypes.func,
};
