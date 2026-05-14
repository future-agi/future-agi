import React, { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Checkbox,
  CircularProgress,
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
import { useDebounce } from "src/hooks/use-debounce";
import { useScrollEnd } from "src/hooks/use-scroll-end";
import { useEvalAttributesInfinite } from "src/hooks/use-eval-attributes";

// API may return strings or `{key, type}` dicts; unwrap to a string key.
const attrKey = (attr) =>
  typeof attr === "string" ? attr : attr?.key ?? String(attr);

const CustomColumnDialog = ({
  open,
  onClose,
  projectId,
  existingColumns,
  onAddColumns,
  onRemoveColumns,
}) => {
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebounce(search.trim(), 250);

  const {
    items,
    isLoading,
    isFetching,
    hasMore,
    fetchMore,
  } = useEvalAttributesInfinite({
    projectId,
    search: debouncedSearch,
    pageSize: 100,
    enabled: open && Boolean(projectId),
  });

  const existingCustomIds = useMemo(
    () =>
      new Set(
        (existingColumns || [])
          .filter((c) => c.groupBy === "Custom Columns")
          .map((c) => c.id),
      ),
    [existingColumns],
  );

  const [checked, setChecked] = useState(new Set());

  useEffect(() => {
    if (open) {
      setChecked(new Set(existingCustomIds));
      setSearch("");
    }
  }, [open, existingCustomIds]);

  const displayedAttributes = useMemo(() => {
    const standardIds = new Set(
      (existingColumns || [])
        .filter((c) => c.groupBy !== "Custom Columns")
        .map((c) => c.id),
    );
    const seen = new Set();
    const merged = [];
    for (const attr of items || []) {
      const key = attrKey(attr);
      if (standardIds.has(key) || seen.has(key)) continue;
      seen.add(key);
      merged.push(attr);
    }
    // Stale custom columns (rotated out of the API sample or restored
    // from a saved view) stay visible when not searching so they remain
    // unchecked-able. TH-4139.
    if (!debouncedSearch) {
      for (const c of existingColumns || []) {
        if (c.groupBy !== "Custom Columns" || seen.has(c.id)) continue;
        seen.add(c.id);
        merged.push(c.id);
      }
    }
    return merged;
  }, [items, existingColumns, debouncedSearch]);

  const scrollRef = useScrollEnd(
    () => {
      if (hasMore && !isFetching) fetchMore();
    },
    [hasMore, isFetching, fetchMore],
  );

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

  const showEmpty =
    !isLoading && displayedAttributes.length === 0;

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
          onChange={(e) => setSearch(e.target.value)}
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
          ref={scrollRef}
          sx={{
            maxHeight: 300,
            overflowY: "auto",
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 1,
          }}
        >
          {isLoading ? (
            <Box sx={{ p: 2, textAlign: "center" }}>
              <CircularProgress size={20} />
            </Box>
          ) : showEmpty ? (
            <Box sx={{ p: 2, textAlign: "center" }}>
              <Typography variant="body2" color="text.disabled">
                {debouncedSearch
                  ? "No matching attributes"
                  : "No attributes found for this project"}
              </Typography>
            </Box>
          ) : (
            <>
              {displayedAttributes.map((attr) => {
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
              })}
              {isFetching && !isLoading ? (
                <Box sx={{ py: 1, textAlign: "center" }}>
                  <CircularProgress size={16} />
                </Box>
              ) : null}
            </>
          )}
        </Box>
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
  projectId: PropTypes.string,
  existingColumns: PropTypes.array,
  onAddColumns: PropTypes.func,
  onRemoveColumns: PropTypes.func,
};

export default React.memo(CustomColumnDialog);
