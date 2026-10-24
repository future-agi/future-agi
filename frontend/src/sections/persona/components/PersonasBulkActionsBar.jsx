import PropTypes from "prop-types";
import SelectionBar from "src/components/selection-bar";

const PersonasBulkActionsBar = ({
  selectedCount,
  deletableCount,
  onUse,
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
          label: "Use in environment",
          icon: "solar:add-square-linear",
          onClick: onUse,
          hidden: !onUse,
        },
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
  onUse: PropTypes.func,
  onDelete: PropTypes.func.isRequired,
  onCancel: PropTypes.func.isRequired,
};

export default PersonasBulkActionsBar;
