import React, { useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Checkbox,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  TextField,
  Typography,
} from "@mui/material";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import AttributeInventoryControls from "./AttributeInventoryControls";

// Normalize an attribute entry to a string key.
// The API may return plain strings OR objects like {key, type}.
const attrKey = (attr) =>
  typeof attr === "string" ? attr : attr?.key ?? String(attr);

const CustomColumnDialog = ({
  open,
  onClose,
  attributes,
  existingColumns,
  onAddColumns,
  onRemoveColumns,
  onAttributeSearchChange,
  hasNextAttributePage,
  fetchNextAttributePage,
  isFetchingNextAttributePage,
  inventoryControlProps,
}) => {
  const [search, setSearch] = useState("");
  const attributeListRef = useRef(null);

  // IDs of custom columns already added to the grid
  const existingCustomIds = useMemo(
    () =>
      new Set(
        (existingColumns || [])
          .filter((c) => c.groupBy === "Custom Columns")
          .map((c) => c.id),
      ),
    [existingColumns],
  );

  // Track checked state — starts from existing custom columns
  const [checked, setChecked] = useState(new Set());

  // Sync checked state when dialog opens
  useEffect(() => {
    if (open) {
      setChecked(new Set(existingCustomIds));
      setSearch("");
      onAttributeSearchChange?.("");
    }
  }, [open, existingCustomIds, onAttributeSearchChange]);

  const filteredAttributes = useMemo(() => {
    const allAttrs = attributes || [];
    // Visible first-class columns are already shown. Keep hidden first-class
    // attributes selectable: choosing one promotes it to a persisted custom
    // visibility override without creating a duplicate grid column.
    const visibleStandardIds = new Set(
      (existingColumns || [])
        .filter((c) => c.groupBy !== "Custom Columns" && c.isVisible !== false)
        .map((c) => c.id),
    );
    // Build the union of API-surfaced attributes and currently-added
    // custom column ids. A custom column whose source attribute is no
    // longer returned by the API (restored from a saved view, or the
    // span attribute roll-up has rotated out the key) must still appear
    // here — otherwise it stays in state, invisibly checked, and the
    // panel's "X added" badge drifts above the user's selection (TH-4139).
    const seen = new Set();
    const merged = [];
    for (const attr of allAttrs) {
      const key = attrKey(attr);
      if (visibleStandardIds.has(key) || seen.has(key)) continue;
      seen.add(key);
      merged.push(attr);
    }
    for (const c of existingColumns || []) {
      if (c.groupBy !== "Custom Columns" || seen.has(c.id)) continue;
      seen.add(c.id);
      merged.push(c.id);
    }
    if (!search.trim()) return merged;
    const q = search.toLowerCase();
    return merged.filter((attr) => attrKey(attr).toLowerCase().includes(q));
  }, [attributes, existingColumns, search]);

  const handleToggle = (key) => {
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
  };

  const handleApply = () => {
    const toAdd = [...checked].filter((id) => !existingCustomIds.has(id));
    const toRemove = [...existingCustomIds].filter((id) => !checked.has(id));

    if (toAdd.length > 0) {
      onAddColumns?.(
        toAdd.map((key) => ({
          id: key,
          name: key,
          isVisible: true,
          groupBy: "Custom Columns",
        })),
      );
    }
    if (toRemove.length > 0) {
      onRemoveColumns?.(toRemove);
    }

    if (toAdd.length > 0 && toRemove.length > 0) {
      enqueueSnackbar(
        `${toAdd.length} column${toAdd.length > 1 ? "s" : ""} added, ${toRemove.length} removed`,
        { variant: "success" },
      );
    } else if (toAdd.length > 0) {
      enqueueSnackbar(
        toAdd.length === 1
          ? `Column "${toAdd[0]}" added`
          : `${toAdd.length} columns added`,
        { variant: "success" },
      );
    } else if (toRemove.length > 0) {
      enqueueSnackbar(
        toRemove.length === 1
          ? `Column "${toRemove[0]}" removed`
          : `${toRemove.length} columns removed`,
        { variant: "success" },
      );
    }

    onClose();
  };

  const hasChanges = useMemo(() => {
    if (checked.size !== existingCustomIds.size) return true;
    for (const id of checked) {
      if (!existingCustomIds.has(id)) return true;
    }
    return false;
  }, [checked, existingCustomIds]);

  return (
    <Dialog open={open} onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle sx={{ pb: 1 }}>
        <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
          <Iconify icon="mdi:table-column-plus-after" width={20} />
          <Typography variant="subtitle1" fontWeight={600}>
            Custom Columns
          </Typography>
        </Box>
        <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
          Select span attributes to show as columns
        </Typography>
      </DialogTitle>
      <DialogContent sx={{ px: 3, pt: 1 }}>
        <TextField
          fullWidth
          size="small"
          placeholder="Search attributes..."
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            onAttributeSearchChange?.(e.target.value);
          }}
          sx={{ mb: 1.5 }}
          InputProps={{
            startAdornment: (
              <Iconify
                icon="mdi:magnify"
                width={18}
                sx={{ mr: 0.5, color: "text.disabled" }}
              />
            ),
          }}
        />
        <Box
          ref={attributeListRef}
          data-attribute-inventory-scroll-container=""
          sx={{
            maxHeight: 300,
            overflowY: "auto",
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 1,
          }}
        >
          {filteredAttributes.length === 0 ? (
            <Box sx={{ p: 2, textAlign: "center" }}>
              <Typography variant="body2" color="text.disabled">
                {attributes?.length === 0
                  ? "No attributes found for this project"
                  : "No matching attributes"}
              </Typography>
            </Box>
          ) : (
            filteredAttributes.map((attr) => {
              const key = attrKey(attr);
              return (
                <FormControlLabel
                  key={key}
                  control={
                    <Checkbox
                      size="small"
                      checked={checked.has(key)}
                      onChange={() => handleToggle(key)}
                      sx={{ p: 0.5 }}
                    />
                  }
                  label={
                    <Typography variant="body2" sx={{ fontSize: 13 }}>
                      {key}
                    </Typography>
                  }
                  sx={{
                    mx: 0,
                    px: 1.5,
                    py: 0.25,
                    width: "100%",
                    "&:hover": { bgcolor: "action.hover" },
                  }}
                />
              );
            })
          )}
        </Box>
        <AttributeInventoryControls
          hasNextPage={hasNextAttributePage}
          isFetchingNextPage={isFetchingNextAttributePage}
          onLoadMore={fetchNextAttributePage}
          {...inventoryControlProps}
          showSearch={false}
          search={search}
          scrollContainerRef={attributeListRef}
        />
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2 }}>
        <Button onClick={onClose} size="small" color="inherit">
          Cancel
        </Button>
        <Button
          onClick={handleApply}
          size="small"
          variant="contained"
          disabled={!hasChanges}
        >
          Apply
        </Button>
      </DialogActions>
    </Dialog>
  );
};

CustomColumnDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  attributes: PropTypes.array,
  existingColumns: PropTypes.array,
  onAddColumns: PropTypes.func,
  onRemoveColumns: PropTypes.func,
  onAttributeSearchChange: PropTypes.func,
  hasNextAttributePage: PropTypes.bool,
  fetchNextAttributePage: PropTypes.func,
  isFetchingNextAttributePage: PropTypes.bool,
  inventoryControlProps: PropTypes.object,
};

export default React.memo(CustomColumnDialog);
