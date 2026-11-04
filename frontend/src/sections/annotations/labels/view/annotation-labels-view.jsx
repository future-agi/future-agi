import { useState, useCallback, useRef } from "react";
import {
  Box,
  Button,
  MenuItem,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
} from "@mui/material";
import Iconify from "src/components/iconify";
import {
  useAnnotationLabelsList,
  useRestoreAnnotationLabel,
} from "src/api/annotation-labels/annotation-labels";
import AnnotationsPageLayout from "../../view/annotations-page-layout";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
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
    <AnnotationsPageLayout
      title="Annotation labels"
      description="Create and manage labels used to annotate traces, spans, and datasets"
      toolbar={
        <>
          <FormSearchField
            size="small"
            placeholder="Search"
            searchQuery={searchInput}
            onChange={handleSearch}
            sx={{ width: 280 }}
          />
          <ToggleButtonGroup
            exclusive
            size="small"
            value={filters.archived ? "archived" : "active"}
            onChange={handleArchiveViewChange}
          >
            <ToggleButton value="active">Active</ToggleButton>
            <ToggleButton value="archived">Archived</ToggleButton>
          </ToggleButtonGroup>
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
          <Box sx={{ flex: 1 }} />
          <Button
            variant="contained"
            color="primary"
            startIcon={<Iconify icon="solar:add-linear" />}
            onClick={handleCreateNew}
          >
            Create Label
          </Button>
        </>
      }
    >
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

      <CreateLabelDrawer
        open={drawerOpen}
        onClose={handleDrawerClose}
        editLabel={editLabel}
      />

      <ArchiveLabelDialog
        label={archiveLabel}
        onClose={() => setArchiveLabel(null)}
      />
    </AnnotationsPageLayout>
  );
}
