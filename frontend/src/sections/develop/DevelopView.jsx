/* eslint-disable react/prop-types */
import { Box, Button, Divider, Tooltip, Typography } from "@mui/material";
import { formatDistanceToNow } from "date-fns";
import { useCallback, useMemo, useState } from "react";
import Iconify from "src/components/iconify";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import { DataTable, DataTablePagination } from "src/components/data-table";
import { useDebounce } from "src/hooks/use-debounce";
import { useNavigate } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import EmptyLayout from "src/components/EmptyLayout/EmptyLayout";
import { ShowComponent } from "src/components/show";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import {
  trackEvent,
  Events,
  PropertyName,
  handleOnDocsClicked,
} from "src/utils/Mixpanel";
import { formatNumberWithCommas } from "../projects/UsersView/common";

import AddDatasetDrawer from "./AddDatasetDrawer/AddDatasetDrawer";
import DuplicateDataset from "./DuplicateDataset/DuplicateDataset";
import DeleteDataset from "./DeleteDataset/DeleteDataset";
import DerivedDatasetsDrawer from "./DerivedDatasetsDrawer/DerivedDatasetsDrawer";
import BaseColumnDrawer from "../develop-detail/BaseColumnsDrawer";
import { useDatasetsList } from "./hooks/useDatasetsList";

// ── Derived datasets cell ──
const DerivedDatasetsCell = ({ datasetId, datasetName, count }) => {
  const [open, setOpen] = useState(false);
  if (!count)
    return (
      <Typography variant="body2" color="text.disabled">
        —
      </Typography>
    );
  return (
    <>
      <Box
        onClick={(e) => {
          e.stopPropagation();
          setOpen(true);
        }}
        sx={{
          display: "inline-flex",
          alignItems: "center",
          gap: 0.5,
          px: 1,
          py: 0.25,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "6px",
          cursor: "pointer",
          "&:hover": { borderColor: "primary.main" },
        }}
      >
        <Typography variant="body2" sx={{ fontSize: "13px" }}>
          {count}
        </Typography>
        <Iconify
          icon="eva:eye-outline"
          width={16}
          sx={{ color: "text.disabled" }}
        />
      </Box>
      {open && (
        <DerivedDatasetsDrawer
          datasetName={datasetName}
          open={open}
          onClose={() => setOpen(false)}
          id={datasetId}
          datasetCount={count}
        />
      )}
    </>
  );
};

// ── Bulk actions bar ──
const BulkActionsBar = ({
  selectedCount,
  onDuplicate,
  onCompare,
  onDelete,
  onCancel,
  canCreate,
  canDelete,
}) => (
  <Box
    sx={{
      display: "flex",
      alignItems: "center",
      gap: 2,
      px: 2,
      py: 0.5,
      borderRadius: "8px",
      border: "1px solid",
      borderColor: "divider",
    }}
  >
    <Typography
      variant="body2"
      fontWeight={600}
      color="primary.main"
      sx={{ pr: 1 }}
    >
      {selectedCount} Selected
    </Typography>
    <Divider orientation="vertical" flexItem />

    {selectedCount === 1 && (
      <Button
        size="small"
        startIcon={<Iconify icon="gg:duplicate" width={16} />}
        onClick={onDuplicate}
        disabled={!canCreate}
        sx={{
          fontSize: "13px",
          textTransform: "none",
          color: "text.secondary",
        }}
      >
        Duplicate
      </Button>
    )}

    {selectedCount > 1 && (
      <Button
        size="small"
        startIcon={<Iconify icon="mdi:compare-horizontal" width={16} />}
        onClick={onCompare}
        sx={{
          fontSize: "13px",
          textTransform: "none",
          color: "text.secondary",
        }}
      >
        Compare
      </Button>
    )}

    <Button
      size="small"
      color="error"
      startIcon={<Iconify icon="solar:trash-bin-trash-bold" width={16} />}
      onClick={onDelete}
      disabled={!canDelete}
      sx={{ fontSize: "13px", textTransform: "none" }}
    >
      Delete
    </Button>

    <Typography
      variant="body2"
      color="text.secondary"
      fontWeight={600}
      sx={{ cursor: "pointer" }}
      onClick={onCancel}
    >
      Cancel
    </Typography>
  </Box>
);

// ── Sort field map ──
// Maps tanstack column IDs (camelCase) → backend column IDs (snake_case).
// Backend expects snake_case column identifiers for sort.
const SORT_FIELD_MAP = {
  name: "name",
  numberOfDatapoints: "number_of_datapoints",
};

// ══════════════════════════════════════════════════════════════
// Main Component
// ══════════════════════════════════════════════════════════════

const DevelopView = () => {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { role } = useAuthContext();

  const [searchQuery, setSearchQuery] = useState("");
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(25);
  const [sorting, setSorting] = useState([]);
  const [rowSelection, setRowSelection] = useState({});

  const [addDatasetDrawerOpen, setAddDatasetDrawerOpen] = useState(false);
  const [openDuplicate, setOpenDuplicate] = useState(false);
  const [openDelete, setOpenDelete] = useState(false);
  const [baseColumnDrawerVisible, setBaseColumnDrawerVisible] = useState(false);

  const debouncedSearch = useDebounce(searchQuery.trim(), 500);

  const sortBy = sorting[0] ? SORT_FIELD_MAP[sorting[0].id] || null : null;
  const sortOrder = sorting[0]?.desc ? "desc" : "asc";

  const { data, isLoading } = useDatasetsList({
    page,
    pageSize,
    search: debouncedSearch || null,
    sortBy,
    sortOrder,
  });

  const items = data?.items || [];
  const total = data?.total || 0;

  // Selected rows
  const selectedItems = useMemo(() => {
    return Object.keys(rowSelection)
      .filter((key) => rowSelection[key])
      .map((key) => items[parseInt(key, 10)])
      .filter(Boolean);
  }, [rowSelection, items]);

  const handleCancelSelection = useCallback(() => setRowSelection({}), []);

  const handleCloseModals = useCallback(() => {
    setOpenDelete(false);
    setOpenDuplicate(false);
    handleCancelSelection();
    queryClient.invalidateQueries({ queryKey: ["datasets", "list"] });
    queryClient.invalidateQueries({
      queryKey: ["develop", "dataset-name-list"],
    });
  }, [handleCancelSelection, queryClient]);

  const refreshGrid = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ["datasets", "list"] });
  }, [queryClient]);

  // ── Columns ──
  const columns = useMemo(
    () => [
      {
        id: "name",
        accessorKey: "name",
        header: "Dataset Name",
        meta: { flex: 1.5 },
        minSize: 200,
        cell: ({ getValue }) => (
          <Typography variant="body2" noWrap sx={{ fontWeight: 500 }}>
            {getValue()}
          </Typography>
        ),
      },
      {
        id: "numberOfDatapoints",
        accessorKey: "numberOfDatapoints",
        header: "Datapoints",
        size: 120,
        cell: ({ getValue }) => (
          <Typography variant="body2" sx={{ fontSize: "13px" }}>
            {formatNumberWithCommas(getValue())}
          </Typography>
        ),
      },
      {
        id: "numberOfExperiments",
        accessorKey: "numberOfExperiments",
        header: "Experiments",
        size: 120,
        enableSorting: false,
        cell: ({ getValue }) => (
          <Typography variant="body2" sx={{ fontSize: "13px" }}>
            {formatNumberWithCommas(getValue())}
          </Typography>
        ),
      },
      {
        id: "numberOfOptimisations",
        accessorKey: "numberOfOptimisations",
        header: "Optimizations",
        size: 120,
        enableSorting: false,
        cell: ({ getValue }) => (
          <Typography variant="body2" sx={{ fontSize: "13px" }}>
            {formatNumberWithCommas(getValue())}
          </Typography>
        ),
      },
      {
        id: "derivedDatasets",
        accessorKey: "derivedDatasets",
        header: "Derived",
        size: 100,
        enableSorting: false,
        cell: ({ getValue, row }) => (
          <DerivedDatasetsCell
            datasetId={row.original.id}
            datasetName={row.original.name}
            count={getValue()}
          />
        ),
      },
      {
        id: "createdAt",
        accessorKey: "createdAt",
        header: "Created",
        size: 130,
        enableSorting: false,
        cell: ({ getValue }) => {
          const val = getValue();
          if (!val) return null;
          try {
            return (
              <Typography variant="body2" noWrap sx={{ fontSize: "13px" }}>
                {formatDistanceToNow(
                  new Date(/[Z+-]/.test(val.slice(-6)) ? val : val + "Z"),
                  { addSuffix: true },
                )}
              </Typography>
            );
          } catch {
            return null;
          }
        },
      },
    ],
    [],
  );

  // ── Empty state ──
  if (!isLoading && total === 0 && !debouncedSearch) {
    return (
      <Box sx={{ height: "100%", display: "flex", flexDirection: "column" }}>
        <EmptyLayout
          title="Add your first dataset"
          description="Manage datasets across your development lifecycle—create, update, and use them to evaluate prompts and workflows."
          link="https://docs.futureagi.com/docs/dataset"
          linkText="Check docs"
          onLinkClick={() => handleOnDocsClicked("datasets_main_page")}
          action={
            <Button
              variant="contained"
              color="primary"
              sx={{ px: "24px", borderRadius: "8px", height: "38px" }}
              disabled={!RolePermission.DATASETS[PERMISSIONS.CREATE][role]}
              startIcon={
                <Iconify
                  icon="octicon:plus-24"
                  color="primary.contrastText"
                  sx={{ width: 20, height: 20 }}
                />
              }
              onClick={() => {
                trackEvent(Events.addDatasetCLicked);
                setAddDatasetDrawerOpen(true);
              }}
            >
              Add Dataset
            </Button>
          }
          icon="/assets/icons/navbar/hugeicons.svg"
        />
        <AddDatasetDrawer
          open={addDatasetDrawerOpen}
          onClose={() => setAddDatasetDrawerOpen(false)}
          refreshGrid={refreshGrid}
        />
      </Box>
    );
  }

  return (
    <Box
      sx={{
        height: "100%",
        display: "flex",
        flexDirection: "column",
        gap: 1.5,
        overflow: "hidden",
        minHeight: 0,
      }}
    >
      {/* Top Controls */}
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          gap: 1.5,
        }}
      >
        <Box sx={{ display: "flex", alignItems: "center", gap: 1.5 }}>
          <FormSearchField
            size="small"
            placeholder="Search"
            sx={{
              minWidth: "250px",
              "& .MuiOutlinedInput-root": { height: "30px" },
            }}
            searchQuery={searchQuery}
            onChange={(e) => {
              setSearchQuery(e.target.value);
              setPage(0);
            }}
          />
        </Box>

        <Box>
          {selectedItems.length > 0 ? (
            <BulkActionsBar
              selectedCount={selectedItems.length}
              onDuplicate={() => setOpenDuplicate(true)}
              onCompare={() => {
                setBaseColumnDrawerVisible(true);
              }}
              onDelete={() => setOpenDelete(true)}
              onCancel={handleCancelSelection}
              canCreate={RolePermission.DATASETS[PERMISSIONS.CREATE][role]}
              canDelete={RolePermission.DATASETS[PERMISSIONS.DELETE][role]}
            />
          ) : (
            <Button
              variant="contained"
              color="primary"
              disabled={!RolePermission.DATASETS[PERMISSIONS.CREATE][role]}
              startIcon={<Iconify icon="mingcute:add-line" width={18} />}
              onClick={() => {
                trackEvent(Events.addDatasetCLicked);
                setAddDatasetDrawerOpen(true);
              }}
              sx={{ px: 2.5, typography: "body2", textTransform: "none" }}
            >
              Add Dataset
            </Button>
          )}
        </Box>
      </Box>

      {/* Table */}
      <DataTable
        columns={columns}
        data={items}
        isLoading={isLoading}
        rowCount={total}
        sorting={sorting}
        onSortingChange={setSorting}
        rowSelection={rowSelection}
        onRowSelectionChange={setRowSelection}
        onRowClick={(row) => {
          trackEvent(Events.datasetOpened, {
            [PropertyName.id]: row.id,
            [PropertyName.name]: row.name,
          });
          navigate(`/dashboard/develop/${row.id}`, { state: { dataset: row } });
        }}
        getRowId={(row) => row.id}
        enableSelection
        emptyMessage="No datasets found"
      />

      {/* Pagination */}
      <DataTablePagination
        page={page}
        pageSize={pageSize}
        total={total}
        onPageChange={setPage}
        onPageSizeChange={(size) => {
          setPageSize(size);
          setPage(0);
        }}
      />

      {/* Drawers / Dialogs */}
      <AddDatasetDrawer
        open={addDatasetDrawerOpen}
        onClose={() => setAddDatasetDrawerOpen(false)}
        refreshGrid={refreshGrid}
      />
      <DuplicateDataset
        open={openDuplicate}
        onClose={handleCloseModals}
        refreshGrid={refreshGrid}
        selected={selectedItems?.[0]}
      />
      <DeleteDataset
        open={openDelete}
        onClose={handleCloseModals}
        refreshGrid={refreshGrid}
        selected={selectedItems}
      />
      {selectedItems.length > 1 && (
        <BaseColumnDrawer
          baseColumnDrawerVisible={baseColumnDrawerVisible}
          selectedDatasets={selectedItems.map((d) => d.id)}
          onBaseColumnDrawerClose={() => setBaseColumnDrawerVisible(false)}
          selectedDatasetsValues={selectedItems.map((d) => d.name)}
          compareFromOutside={true}
        />
      )}
    </Box>
  );
};

export default DevelopView;
