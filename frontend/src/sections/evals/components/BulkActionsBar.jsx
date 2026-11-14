import PropTypes from "prop-types";
import SelectionBar from "src/components/selection-bar";

const BulkActionsBar = ({
  selectedCount,
  onDelete,
  onCancel,
  canEditEvals = true,
}) => (
  <SelectionBar
    count={selectedCount}
    onCancel={onCancel}
    actions={[
      {
        label: "Delete",
        icon: "solar:trash-bin-trash-linear",
        onClick: onDelete,
        disabled: !canEditEvals,
      },
    ]}
  />
);

BulkActionsBar.propTypes = {
  selectedCount: PropTypes.number.isRequired,
  onDelete: PropTypes.func.isRequired,
  onCancel: PropTypes.func.isRequired,
  canEditEvals: PropTypes.bool,
};

export default BulkActionsBar;
