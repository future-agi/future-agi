import { useState, useCallback, useRef } from "react";
import {
  Box,
  Button,
  InputAdornment,
  MenuItem,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import {
  useAnnotationLabelsList,
  useRestoreAnnotationLabel,
} from "src/api/annotation-labels/annotation-labels";
import AnnotationsTabs from "../../view/annotations-tabs";
import AnnotationLabelTable from "../annotation-label-table";
import AnnotationLabelEmpty from "../annotation-label-empty";
import CreateLabelDrawer from "../create-label-drawer";
import ArchiveLabelDialog from "../archive-label-dialog";

const TYPE_OPTIONS = [
  { value: "", label: "All Types" },
  { value: "categorical", label: "Categorical" },
  { value: "numeric", label: "Numeric" },
  { value: "text", label: "Text" },
  { value: "star", label: "Star Rating" },
  { value: "thumbs_up_down", label: "Thumbs Up/Down" },
];

export default function AnnotationLabelsView() {
  const [filters, setFilters] = useState({
    search: "",
    type: "",
    page: 0,
    limit: 10,
    include_usage_count: true,
    archived: false,
  });

  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editLabel, setEditLabel] = useState(null);
  const [archiveLabel, setArchiveLabel] = useState(null);
  const [searchInput, setSearchInput] = useState("");
  const searchTimerRef = useRef(null);

  const { data, isLoading } = useAnnotationLabelsList({
    ...filters,
    page: filters.page + 1,
    archived: filters.archived || undefined,
  });
  const { mutate: restoreLabel } = useRestoreAnnotationLabel();

  const results = data?.results || [];
  const totalCount = data?.count || 0;

  const handleSearch = useCallback((event) => {
    const value = event.target.value;
    setSearchInput(value);
    clearTimeout(searchTimerRef.current);
    searchTimerRef.current = setTimeout(() => {
      setFilters((prev) => ({
        ...prev,
        search: value,
        page: 0,
      }));
    }, 300);
  }, []);

  const handleTypeFilter = useCallback((event) => {
    setFilters((prev) => ({
      ...prev,
      type: event.target.value,
      page: 0,
    }));
  }, []);

  const handleArchiveViewChange = useCallback((_, value) => {
    if (!value) return;
    setFilters((prev) => ({
      ...prev,
      archived: value === "archived",
      page: 0,
    }));
  }, []);

  const handlePageChange = useCallback((newPage) => {
    setFilters((prev) => ({ ...prev, page: newPage }));
  }, []);

  const handleEdit = useCallback((label) => {
    setEditLabel(label);
    setDrawerOpen(true);
  }, []);

  const handleDuplicate = useCallback((label) => {
    setEditLabel({
      ...label,
      id: undefined,
      name: `Copy of ${label.name}`,
      _isDuplicate: true,
    });
    setDrawerOpen(true);
  }, []);

  const handleArchive = useCallback((label) => {
    setArchiveLabel(label);
  }, []);

  const handleRestore = useCallback(
    (label) => {
      if (!label?.id) return;
      restoreLabel(label.id);
    },
    [restoreLabel],
  );

  const handleCreateNew = useCallback(() => {
    setEditLabel(null);
    setDrawerOpen(true);
  }, []);

  const handleDrawerClose = useCallback(() => {
    setDrawerOpen(false);
    setEditLabel(null);
  }, []);

  const isEmpty =
    !isLoading &&
    results.length === 0 &&
    !filters.search &&
    !filters.type &&
    !filters.archived;

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        height: "100vh",
        backgroundColor: "background.paper",
      }}
    >
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          gap: "2px",
          px: 3,
          pt: 2,
        }}
      >
        <Typography
          color="text.primary"
          typography="m2"
          fontWeight={"fontWeightSemiBold"}
        >
          Annotation labels
        </Typography>
        <Box sx={{ display: "flex", gap: 0.5, alignItems: "center" }}>
          <Typography
            typography="s1"
            color="text.primary"
            fontWeight={"fontWeightRegular"}
          >
            Create and manage labels used to annotate traces, spans, and
            datasets
          </Typography>
        </Box>
      </Box>
      <Box sx={{ flexShrink: 0, px: 3 }}>
        <AnnotationsTabs />
      </Box>

      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          flex: 1,
          overflow: "hidden",
          px: 3,
        }}
      >
        <Stack
          direction="row"
          spacing={2}
          mb={2}
          flexShrink={0}
          alignItems="center"
        >
          <TextField
            size="small"
            placeholder="Search labels..."
            value={searchInput}
            onChange={handleSearch}
            sx={{ minWidth: 280 }}
            InputProps={{
              startAdornment: (
                <InputAdornment position="start">
                  <Iconify
                    icon="eva:search-fill"
                    sx={{ color: "text.disabled" }}
                  />
                </InputAdornment>
              ),
            }}
          />
          <TextField
            size="small"
            select
            value={filters.type}
            onChange={handleTypeFilter}
            sx={{ minWidth: 160 }}
            SelectProps={{
              displayEmpty: true,
              renderValue: (v) =>
                TYPE_OPTIONS.find((o) => o.value === v)?.label || "All Types",
            }}
          >
            {TYPE_OPTIONS.map((opt) => (
              <MenuItem key={opt.value} value={opt.value}>
                {opt.label}
              </MenuItem>
            ))}
          </TextField>
          <ToggleButtonGroup
            exclusive
            size="small"
            value={filters.archived ? "archived" : "active"}
            onChange={handleArchiveViewChange}
            sx={{
              height: 36,
              "& .MuiToggleButton-root": {
                px: 1.25,
                borderRadius: "4px",
                typography: "s2",
                fontWeight: "fontWeightMedium",
              },
            }}
          >
            <ToggleButton value="active">Active</ToggleButton>
            <ToggleButton value="archived">Archived</ToggleButton>
          </ToggleButtonGroup>
          <Box sx={{ flex: 1 }} />
          <Button
            variant="contained"
            color="primary"
            startIcon={<Iconify icon="mingcute:add-line" />}
            onClick={handleCreateNew}
          >
            Create Label
          </Button>
        </Stack>

        {isEmpty ? (
          <AnnotationLabelEmpty onCreateClick={handleCreateNew} />
        ) : (
          <AnnotationLabelTable
            data={results}
            loading={isLoading}
            page={filters.page}
            rowsPerPage={filters.limit}
            totalCount={totalCount}
            onPageChange={handlePageChange}
            onRowsPerPageChange={(rpp) => {
              setFilters((prev) => ({ ...prev, limit: rpp, page: 0 }));
            }}
            onEdit={handleEdit}
            onDuplicate={handleDuplicate}
            onArchive={handleArchive}
            onRestore={handleRestore}
            archivedView={filters.archived}
          />
        )}
      </Box>

      <CreateLabelDrawer
        open={drawerOpen}
        onClose={handleDrawerClose}
        editLabel={editLabel}
      />

      <ArchiveLabelDialog
        label={archiveLabel}
        onClose={() => setArchiveLabel(null)}
      />
    </Box>
  );
}
