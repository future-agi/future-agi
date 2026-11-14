import PropTypes from "prop-types";
import SelectionBar from "src/components/selection-bar";

const PersonasBulkActionsBar = ({
  selectedCount,
  deletableCount,
  onDelete,
  onCancel,
}) => {
  const hasProtected = selectedCount !== deletableCount;
  return (
    <SelectionBar
      count={selectedCount}
      onCancel={onCancel}
      actions={[
        {
          label: "Delete",
          icon: "solar:trash-bin-trash-linear",
          onClick: onDelete,
          disabled: deletableCount === 0,
          tooltip:
            deletableCount === 0
              ? "System personas cannot be deleted"
              : hasProtected
                ? `${selectedCount - deletableCount} system persona${selectedCount - deletableCount !== 1 ? "s" : ""} will be skipped`
                : undefined,
        },
      ]}
    />
  );
};

PersonasBulkActionsBar.propTypes = {
  selectedCount: PropTypes.number.isRequired,
  deletableCount: PropTypes.number.isRequired,
  onDelete: PropTypes.func.isRequired,
  onCancel: PropTypes.func.isRequired,
};

export default PersonasBulkActionsBar;
