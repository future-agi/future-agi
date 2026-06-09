import PropTypes from "prop-types";
import { useState, useMemo } from "react";
import {
  Box,
  Button,
  Checkbox,
  Chip,
  InputAdornment,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { useAnnotationLabelsList } from "src/api/annotation-labels/annotation-labels";
import CreateLabelDrawer from "src/sections/annotations/labels/create-label-drawer";

const TYPE_CHIP_COLORS = {
  text: { bg: "#f0f4ff", color: "#3b6ce7" },
  numeric: { bg: "#f0faf4", color: "#1a8a4a" },
  categorical: { bg: "#fef6ee", color: "#c4631a" },
  thumbs_up_down: { bg: "#fdf2f8", color: "#c026a3" },
  star: { bg: "#fffbeb", color: "#b45309" },
};

TypeChip.propTypes = {
  type: PropTypes.string,
};

function TypeChip({ type }) {
  const label = (type || "")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
  const colors = TYPE_CHIP_COLORS[type] || { bg: "#f5f5f5", color: "#666" };
  return (
    <Box
      sx={{
        px: 1,
        py: 0.25,
        borderRadius: 0.5,
        bgcolor: colors.bg,
        fontSize: 11,
        fontWeight: 500,
        color: colors.color,
        whiteSpace: "nowrap",
      }}
    >
      {label}
    </Box>
  );
}

function mergeLabelsById(...labelLists) {
  const labelsById = new Map();
  labelLists.flat().forEach((label) => {
    if (!label?.id) return;
    const id = String(label.id);
    labelsById.set(id, { ...(labelsById.get(id) || {}), ...label, id });
  });
  return Array.from(labelsById.values());
}

LabelPicker.propTypes = {
  selectedIds: PropTypes.array,
  onChange: PropTypes.func.isRequired,
};

export default function LabelPicker({ selectedIds = [], onChange }) {
  const [search, setSearch] = useState("");
  const [createDrawerOpen, setCreateDrawerOpen] = useState(false);
  const [createdLabels, setCreatedLabels] = useState([]);
  const { data, refetch } = useAnnotationLabelsList({ search, limit: 100 });
  // Also fetch all labels (no search) to resolve selected label names
  const { data: allData } = useAnnotationLabelsList({ search: "", limit: 100 });
  const allLabels = useMemo(
    () => mergeLabelsById(data?.results || [], createdLabels),
    [data, createdLabels],
  );
  const allLabelsUnfiltered = useMemo(
    () => mergeLabelsById(allData?.results || [], createdLabels),
    [allData, createdLabels],
  );
  const selectedSet = useMemo(() => new Set(selectedIds), [selectedIds]);

  const handleToggle = (id) => {
    if (selectedSet.has(id)) {
      onChange(selectedIds.filter((i) => i !== id));
    } else {
      onChange([...selectedIds, id]);
    }
  };

  const handleCreatedLabel = (label) => {
    const labelId = label?.id || label?.label_id;
    if (!labelId) return;
    const normalizedId = String(labelId);
    const normalizedLabel = {
      ...label,
      id: normalizedId,
    };
    setCreatedLabels((prev) => mergeLabelsById(prev, [normalizedLabel]));
    if (!selectedSet.has(normalizedId)) {
      onChange([...selectedIds, normalizedId]);
    }
    setSearch("");
    refetch();
  };

  // Selected labels always resolved from the unfiltered list
  const selectedLabels = useMemo(
    () => allLabelsUnfiltered.filter((l) => selectedSet.has(l.id)),
    [allLabelsUnfiltered, selectedSet],
  );
  const filteredLabels = search
    ? allLabels.filter((l) =>
        l.name?.toLowerCase().includes(search.toLowerCase()),
      )
    : allLabels;

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
      {/* Selected labels as removable chips */}
      {selectedLabels.length > 0 && (
        <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.5, mb: 0.5 }}>
          {selectedLabels.map((label) => (
            <Chip
              key={label.id}
              label={label.name}
              size="small"
              color="primary"
              onDelete={() => handleToggle(label.id)}
              sx={{
                borderRadius: 0.5,
                fontWeight: 500,
              }}
            />
          ))}
        </Box>
      )}

      {/* Search */}
      <TextField
        size="small"
        fullWidth
        placeholder="Search labels..."
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        InputProps={{
          startAdornment: (
            <InputAdornment position="start">
              <Iconify
                icon="eva:search-fill"
                sx={{ color: "text.disabled", width: 16, height: 16 }}
              />
            </InputAdornment>
          ),
        }}
      />

      {/* Checkbox list */}
      <Box
        sx={{
          maxHeight: 200,
          overflow: "auto",
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 0.5,
        }}
      >
        {filteredLabels.map((label) => (
          <Box
            key={label.id}
            onClick={() => handleToggle(label.id)}
            sx={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              px: 1,
              py: 0.5,
              cursor: "pointer",
              borderBottom: "1px solid",
              borderColor: "divider",
              "&:last-child": { borderBottom: 0 },
              "&:hover": { bgcolor: "action.hover" },
            }}
          >
            <Box
              sx={{
                display: "flex",
                alignItems: "center",
                gap: 1,
                minWidth: 0,
              }}
            >
              <Checkbox
                checked={selectedSet.has(label.id)}
                size="small"
                sx={{ p: 0.5 }}
              />
              <Typography variant="body2" noWrap>
                {label.name}
              </Typography>
            </Box>
            <TypeChip type={label.type} />
          </Box>
        ))}
        {filteredLabels.length === 0 && (
          <Typography
            variant="body2"
            color="text.secondary"
            sx={{ p: 2, textAlign: "center" }}
          >
            No labels found
          </Typography>
        )}
      </Box>

      {/* Create new label */}
      <Button
        variant="outlined"
        color="primary"
        startIcon={<Iconify icon="mingcute:add-line" width={16} />}
        onClick={() => setCreateDrawerOpen(true)}
        sx={{ alignSelf: "flex-start", fontSize: 12 }}
      >
        Create new label
      </Button>

      <CreateLabelDrawer
        open={createDrawerOpen}
        onClose={() => {
          setCreateDrawerOpen(false);
          refetch();
        }}
        onCreated={handleCreatedLabel}
      />
    </Box>
  );
}
