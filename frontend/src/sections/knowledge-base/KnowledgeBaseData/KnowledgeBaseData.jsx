import { Box, Button, Divider, Typography } from "@mui/material";
import React, { useCallback, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useDebounce } from "src/hooks/use-debounce";
import axios, { endpoints } from "src/utils/axios";
import DeleteKnowledgeBase from "../DeleteKnowledgeBase/DeleteKnowledgeBase";
import PropTypes from "prop-types";
import { ProcessingStatusCell } from "./CellRenderer";
import { useNavigate } from "react-router";
import { format } from "date-fns";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { useAuthContext } from "src/auth/hooks";
import { formatNumberWithCommas } from "src/sections/projects/UsersView/common";
import SvgColor from "src/components/svg-color";
import { DataTable, DataTablePagination } from "src/components/data-table";

const KnowledgeBaseData = React.forwardRef(
  ({ setHasData, setShowHelp, setCreateKnowledgeBase }, _ref) => {
    const navigate = useNavigate();
    const queryClient = useQueryClient();
    const { role } = useAuthContext();
    const [selected, setSelected] = useState([]);
    const [rowSelection, setRowSelection] = useState({});
    const [searchQuery, setSearchQuery] = useState("");
    const [openDelete, setOpenDelete] = useState(false);
    const [page, setPage] = useState(0);
    const [pageSize, setPageSize] = useState(25);

    const debouncedSearchQuery = useDebounce(searchQuery.trim(), 500);

    const { data, isLoading } = useQuery({
      queryKey: ["knowledge-base", page, pageSize, debouncedSearchQuery],
      queryFn: () =>
        axios.get(endpoints.knowledge.list, {
          params: {
            search: debouncedSearchQuery || "",
            page_number: page,
            page_size: pageSize,
          },
        }),
      select: (d) => d.data?.result,
      keepPreviousData: true,
      refetchInterval: 10000,
      onSuccess: (result) => {
        const rows = result?.tableData ?? [];
        const hasSearchQuery = !!debouncedSearchQuery;
        setHasData(rows.length > 0 || hasSearchQuery);
      },
    });

    const items = useMemo(() => data?.tableData ?? [], [data]);
    const total = data?.totalRows ?? 0;

    // Sync hasData on data changes (onSuccess may not fire with keepPreviousData)
    useMemo(() => {
      if (data !== undefined) {
        const rows = data?.tableData ?? [];
        const hasSearchQuery = !!debouncedSearchQuery;
        setHasData(rows.length > 0 || hasSearchQuery);
      }
    }, [data, debouncedSearchQuery, setHasData]);

    const selectedItems = useMemo(
      () =>
        Object.keys(rowSelection)
          .filter((k) => rowSelection[k])
          .map((k) => items[parseInt(k, 10)])
          .filter(Boolean),
      [rowSelection, items],
    );

    const closeModal = useCallback(() => {
      setOpenDelete(false);
      setRowSelection({});
      setSelected([]);
    }, []);

    const refreshGrid = useCallback(() => {
      queryClient.invalidateQueries({ queryKey: ["knowledge-base"] });
    }, [queryClient]);

    // Expose refreshGrid on the forwarded ref for parent compatibility
    React.useImperativeHandle(_ref, () => ({
      api: { refreshServerSide: refreshGrid },
    }));

    const handleRowSelectionChange = useCallback(
      (newSelection) => {
        setRowSelection(newSelection);
        const newSelected = Object.keys(newSelection)
          .filter((k) => newSelection[k])
          .map((k) => items[parseInt(k, 10)])
          .filter(Boolean);
        setSelected(newSelected);
      },
      [items],
    );

    const columns = useMemo(
      () => [
        {
          id: "name",
          accessorKey: "name",
          header: "Title",
          meta: { flex: 1 },
          enableSorting: false,
          cell: ({ getValue }) => (
            <Typography variant="body2" noWrap sx={{ fontWeight: 500 }}>
              {getValue()}
            </Typography>
          ),
        },
        {
          id: "files_uploaded",
          accessorKey: "files_uploaded",
          header: "Files uploaded",
          meta: { flex: 1 },
          enableSorting: false,
          cell: ({ getValue }) => (
            <Typography variant="body2" sx={{ fontSize: 13 }}>
              {formatNumberWithCommas(getValue())}
            </Typography>
          ),
        },
        {
          id: "status",
          accessorKey: "status",
          header: "Status",
          meta: { flex: 1 },
          enableSorting: false,
          cell: ({ getValue, row }) => (
            <ProcessingStatusCell value={getValue()} data={row.original} />
          ),
        },
        {
          id: "updated_at",
          accessorKey: "updated_at",
          header: "Updated",
          meta: { flex: 1 },
          enableSorting: false,
          cell: ({ getValue }) => {
            const val = getValue();
            if (!val) return null;
            try {
              return (
                <Typography variant="body2" sx={{ fontSize: 13 }}>
                  {format(new Date(val), "dd/MM/yyyy, h:mm aaa")}
                </Typography>
              );
            } catch {
              return null;
            }
          },
        },
        {
          id: "created_by",
          accessorKey: "created_by",
          header: "Created by",
          meta: { flex: 1 },
          enableSorting: false,
          cell: ({ getValue }) => (
            <Typography variant="body2" noWrap sx={{ fontSize: 13 }}>
              {getValue() || "-"}
            </Typography>
          ),
        },
        {
          id: "actions",
          header: "",
          meta: { flex: 0.5 },
          enableSorting: false,
          cell: ({ row }) => {
            const kb = row.original;
            const handleDownload = async (e) => {
              e.stopPropagation();
              try {
                const res = await axios.get(endpoints.knowledge.downloadFile(kb.id), {
                  params: { file_id: kb.id, file_name: kb.name },
                });
                const url = res.data?.result?.download_url;
                if (url) window.open(url, "_blank");
              } catch {
                console.error("Download failed");
              }
            };
            return (
              <Button
                size="small"
                variant="outlined"
                onClick={handleDownload}
                sx={{ fontSize: 12, py: 0.3 }}
              >
                Download
              </Button>
            );
          },
        },
      ],
      [],
    );

    const handleRowClick = useCallback(
      (row) => {
        if (row?.id) {
          navigate(`/dashboard/knowledge/${row.id}`);
        }
      },
      [navigate],
    );

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
        <Box
          sx={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
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
          <Box>
            {selectedItems.length > 0 ? (
              <Box
                sx={{
                  display: "flex",
                  alignItems: "center",
                  gap: 2,
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: 1,
                  px: 2,
                  py: 0.5,
                }}
              >
                <Typography
                  typography="s1"
                  fontWeight="fontWeightMedium"
                  color="primary.main"
                >
                  {selectedItems.length} Selected
                </Typography>
                <Divider
                  orientation="vertical"
                  flexItem
                  sx={{ borderRightWidth: 0.25 }}
                />
                <Button
                  size="small"
                  startIcon={
                    <SvgColor
                      src="/assets/icons/ic_delete.svg"
                      sx={{ width: 20, height: 20, color: "text.disabled" }}
                    />
                  }
                  sx={{ color: "text.secondary" }}
                  onClick={() => {
                    if (
                      RolePermission.KNOWLEDGE_BASE[PERMISSIONS.DELETE][role]
                    ) {
                      setOpenDelete(true);
                    }
                  }}
                >
                  <Typography
                    typography="s1"
                    fontWeight="fontWeightRegular"
                    color="text.primary"
                  >
                    Delete
                  </Typography>
                </Button>
                <Button
                  size="small"
                  sx={{ color: "text.secondary" }}
                  onClick={closeModal}
                >
                  <Typography
                    typography="s1"
                    fontWeight="fontWeightRegular"
                    color="text.primary"
                  >
                    Cancel
                  </Typography>
                </Button>
              </Box>
            ) : (
              <Box sx={{ display: "flex", gap: 1, alignItems: "center" }}>
                <Button
                  variant="outlined"
                  size="small"
                  sx={{
                    color: "text.primary",
                    borderColor: "divider",
                    padding: 1.5,
                    fontSize: "14px",
                    height: "38px",
                  }}
                  startIcon={
                    <SvgColor src="/assets/icons/ic_docs_single.svg" />
                  }
                  onClick={() => setShowHelp(true)}
                >
                  View Docs
                </Button>
                <Button
                  variant="contained"
                  color="primary"
                  onClick={() => {
                    if (
                      RolePermission.KNOWLEDGE_BASE[PERMISSIONS.UPDATE][role]
                    ) {
                      setCreateKnowledgeBase(true);
                    }
                  }}
                >
                  Create Knowledge Base
                </Button>
              </Box>
            )}
          </Box>
        </Box>

        <DataTable
          columns={columns}
          data={items}
          isLoading={isLoading}
          rowCount={total}
          rowSelection={rowSelection}
          onRowSelectionChange={handleRowSelectionChange}
          onRowClick={handleRowClick}
          getRowId={(row) => row.id}
          enableSelection
          emptyMessage="No knowledge bases found"
        />

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

        <DeleteKnowledgeBase
          open={openDelete}
          onClose={closeModal}
          refreshGrid={refreshGrid}
          selected={selected}
        />
      </Box>
    );
  },
);

export default KnowledgeBaseData;

KnowledgeBaseData.displayName = "KnowledgeBaseData";

KnowledgeBaseData.propTypes = {
  setHasData: PropTypes.func,
  setShowHelp: PropTypes.func,
  setCreateKnowledgeBase: PropTypes.func,
};
