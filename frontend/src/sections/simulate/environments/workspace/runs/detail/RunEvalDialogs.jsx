import PropTypes from "prop-types";
import ConfirmRunEvaluations from "src/sections/common/EvaluationDrawer/ConfirmRunEvaluations";
import AddEvaluationDrawer from "../../evals/AddEvaluationDrawer";
import { HARNESS_NOTE } from "./allEvaluationsDrawer.constants";

/**
 * The edit form and the re-run confirm behind `useRunEvalActions`, rendered
 * once for the whole run page. Both are modals that open after whatever asked
 * for them, so they stack above the All Evaluations drawer.
 */
export default function RunEvalDialogs({
  env,
  editing = null,
  confirming = null,
  loading = false,
  onEditClose,
  onEdited,
  onConfirmClose,
  onConfirm,
}) {
  return (
    <>
      {/* No executionId: given one, the drawer would grade the run by name
          itself; here the confirm below does it. */}
      <AddEvaluationDrawer
        open={Boolean(editing)}
        env={env}
        editingEval={editing}
        onClose={onEditClose}
        onEdited={onEdited}
      />

      <ConfirmRunEvaluations
        open={Boolean(confirming)}
        onClose={onConfirmClose}
        onConfirm={onConfirm}
        selectedUserEvalList={confirming || []}
        loading={loading}
        // Only runnable configs ever reach the dialog, so an empty mapping
        // there means one of the harness's built-in suite evals; its score
        // came from the harness and will be replaced by the platform's.
        getNote={(list) =>
          list.some((c) => Object.keys(c.mapping || {}).length === 0)
            ? HARNESS_NOTE
            : null
        }
      />
    </>
  );
}

RunEvalDialogs.propTypes = {
  env: PropTypes.shape({ id: PropTypes.string }),
  editing: PropTypes.shape({ id: PropTypes.string }),
  confirming: PropTypes.arrayOf(PropTypes.shape({ id: PropTypes.string })),
  loading: PropTypes.bool,
  onEditClose: PropTypes.func,
  onEdited: PropTypes.func,
  onConfirmClose: PropTypes.func,
  onConfirm: PropTypes.func,
};
