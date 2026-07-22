import React, { useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Button,
  IconButton,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";

const DEFAULT_ACTIONS = [
  {
    id: "dataset",
    label: "Move to dataset",
    icon: "mdi:folder-move-outline",
  },
  {
    id: "tags",
    label: "Add tags",
    icon: "mdi:tag-outline",
  },
  {
    id: "annotation-queue",
    label: "Add to annotation queue",
    icon: "mdi:clipboard-list-outline",
  },
  {
    id: "annotate",
    label: "Annotate",
    icon: "mdi:pencil-box-outline",
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
}) => {
  const [menuOpen, setMenuOpen] = useState(false);
  const anchorRef = useRef(null);

  if (selectedCount <= 0) return null;

  const visibleActions = actions.filter(
    (a) =>
      (!a.simulatorOnly || isSimulator) &&
      (!a.requiresSingle || selectedCount === 1),
  );

  return (
    <Stack direction="row" spacing={1} alignItems="center">
      <Typography
        variant="body2"
        sx={{ fontSize: 13, color: "text.secondary", whiteSpace: "nowrap" }}
      >
        {allMatching
          ? `All ${selectedCount.toLocaleString()} matching filter`
          : `${selectedCount} selected`}
      </Typography>

      <Button
        ref={anchorRef}
        variant="outlined"
        size="small"
        endIcon={<Iconify icon="mdi:chevron-down" width={16} />}
        onClick={() => setMenuOpen(true)}
        sx={{
          textTransform: "none",
          fontWeight: 500,
          fontSize: 13,
          borderColor: "divider",
          color: "text.primary",
          height: 32,
          "&:hover": { borderColor: "text.secondary" },
        }}
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
          paper: { sx: { minWidth: 220, mt: 0.5 } },
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
                <Iconify icon={action.icon} width={18} />
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

      <IconButton
        size="small"
        onClick={onClearSelection}
        sx={{ color: "text.secondary", p: 0.5 }}
      >
        <Iconify icon="mdi:close" width={18} />
      </IconButton>
    </Stack>
  );
};

BulkActionsBar.propTypes = {
  selectedCount: PropTypes.number.isRequired,
  onClearSelection: PropTypes.func.isRequired,
  onAction: PropTypes.func.isRequired,
  isSimulator: PropTypes.bool,
  actions: PropTypes.array,
  allMatching: PropTypes.bool,
};

export default React.memo(BulkActionsBar);
