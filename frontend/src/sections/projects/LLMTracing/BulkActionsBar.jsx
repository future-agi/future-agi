import React, { useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Button,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Tooltip,
} from "@mui/material";
import Iconify from "src/components/iconify";
import SelectionBar from "src/components/selection-bar/SelectionBar";

const DEFAULT_ACTIONS = [
  {
    id: "dataset",
    label: "Move to dataset",
    icon: "solar:move-to-folder-linear",
  },
  {
    id: "tags",
    label: "Add tags",
    icon: "solar:tag-linear",
  },
  {
    id: "annotation-queue",
    label: "Add to annotation queue",
    icon: "solar:clipboard-list-linear",
  },
  {
    id: "annotate",
    label: "Annotate",
    icon: "solar:pen-new-square-linear",
    requiresSingle: true,
  },
];

const BulkActionsBar = ({
  selectedCount,
  onClearSelection,
  onAction,
  isSimulator,
  actions = DEFAULT_ACTIONS,
  allMatching = false,
  selectedCountIsLowerBound = false,
}) => {
  const [menuOpen, setMenuOpen] = useState(false);
  const anchorRef = useRef(null);

  if (selectedCount <= 0) return null;

  const visibleActions = actions.filter(
    (a) =>
      (!a.simulatorOnly || isSimulator) &&
      (!a.requiresSingle ||
        (!selectedCountIsLowerBound && selectedCount === 1)),
  );

  const formattedCount = `${selectedCountIsLowerBound ? "≥" : ""}${selectedCount.toLocaleString()}`;

  const label = allMatching
    ? selectedCountIsLowerBound
      ? `All matching filter (${formattedCount})`
      : `All ${formattedCount} matching filter`
    : `${formattedCount} selected`;

  const menu = (
    <>
      <Button
        ref={anchorRef}
        size="small"
        variant="text"
        color="inherit"
        endIcon={<Iconify icon="solar:alt-arrow-down-linear" width={16} />}
        onClick={() => setMenuOpen(true)}
        sx={{ color: "text.secondary", "&:hover": { color: "text.primary" } }}
      >
        Actions
      </Button>

      <Menu
        open={menuOpen}
        anchorEl={anchorRef.current}
        onClose={() => setMenuOpen(false)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{
          paper: { sx: { minWidth: 240, mt: 0.5 } },
        }}
      >
        {visibleActions.map((action) => {
          const disabled = Boolean(action.disabled);
          const item = (
            <MenuItem
              key={action.id}
              disabled={disabled}
              onClick={() => {
                if (disabled) return;
                setMenuOpen(false);
                onAction(action.id, { currentTarget: anchorRef.current });
              }}
              dense
            >
              <ListItemIcon>
                <Iconify icon={action.icon} width={16} />
              </ListItemIcon>
              <ListItemText
                primaryTypographyProps={{ variant: "body2", fontSize: 13 }}
              >
                {action.label}
              </ListItemText>
            </MenuItem>
          );

          if (!disabled || !action.disabledReason) return item;

          return (
            <Tooltip
              key={action.id}
              title={action.disabledReason}
              placement="left"
            >
              <span>{item}</span>
            </Tooltip>
          );
        })}
      </Menu>
    </>
  );

  return (
    <SelectionBar
      count={selectedCount}
      label={label}
      actions={[{ label: "Actions", node: menu }]}
      onCancel={onClearSelection}
    />
  );
};

BulkActionsBar.propTypes = {
  selectedCount: PropTypes.number.isRequired,
  onClearSelection: PropTypes.func.isRequired,
  onAction: PropTypes.func.isRequired,
  isSimulator: PropTypes.bool,
  actions: PropTypes.array,
  allMatching: PropTypes.bool,
  selectedCountIsLowerBound: PropTypes.bool,
};

export default React.memo(BulkActionsBar);
